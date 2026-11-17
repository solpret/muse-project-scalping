#!/usr/bin/env python3
"""Jual SEMUA AGC cair ke BNB — perintah langsung user 2026-11-11 ~05:50 WIB.

Perintah user: "Swap semua AGC ke bnb sekarang".
Bukan ronde scalp: TIDAK menyentuh state.json / positions.json / ledger uji.
AGC yang dijual = saldo cair wallet (hasil klaim Mode B), BUKAN posisi LP
Mode B (7 share, tempo 17/19 Nov) — posisi LP tidak disentuh sama sekali.

Jalur (terbukti di workspace, pola fast_buy.py):
  1. approve exact-delta AGC -> router PCS (allowance saat ini 0)
  2. swapExactTokensForTokensSupportingFeeOnTransferTokens AGC->WBNB,
     min-out 95% quote getAmountsOut
  3. WBNB.withdraw(seluruh WBNB hasil swap) -> BNB native
Setiap langkah: preview baw bersih WAJIB baru eksekusi; receipt status 1
+ verifikasi saldo on-chain. Guard fail-closed:
  - audit Binance segar: code 000000, hasResult true, riskLevel <= 1,
    riskAlert null, tanpa hit RISK (CAUTION saja tidak menghentikan)
  - GoPlus segar: bukan honeypot, hidden_owner 0, sell_tax <= 3%,
    cannot_sell_all 0
  - quote > 0 dan quote >= 2x estimasi gas total (approve+swap+unwrap)
  - preview bersih tiap langkah (simulationCode 000000000, tanpa error,
    tanpa authority changes, tanpa risk; balanceChanges sesuai harapan)
  - simulasi WBNB terima >= min-out, kalau tidak ABORT
  - BNB cukup untuk gas (saldo BNB live dicek di awal)

Penggunaan:
  python3 fast_sell_all.py --execute    # jalur penuh
  python3 fast_sell_all.py              # check-only: cek + tampilkan calldata
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
import uuid

BASE = os.path.dirname(os.path.abspath(__file__))
BAW = "/home/hatch/workspace/.baw-cli/bin/baw"
AGENT = "0xeb237F6f9FB4e826739F0954e9768914F28b2c2E"
TOKEN = "0x63dea5aCdbE1F27c968907D08610A5f0A0284bA6"   # AGC
WBNB = "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c"
ROUTER = "0x10ED43C718714eb63d5aA57B78B54704E256024E"
RPCS = ["https://bsc-dataseed2.binance.org",
        "https://bsc-dataseed1.binance.org",
        "https://bsc-dataseed3.binance.org"]
UA = {"User-Agent": "curl"}


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def http_json(url, data=None, headers=None, timeout=15):
    req = urllib.request.Request(url, data=data,
                                 headers=headers or UA, method="POST" if data else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def eth_call(to, data):
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "eth_call",
                          "params": [{"to": to, "data": data}, "latest"]}).encode()
    last = None
    for rpc in RPCS:
        try:
            d = http_json(rpc, payload, {"Content-Type": "application/json"}, timeout=12)
            if "result" in d:
                return d["result"]
            last = d
        except Exception as e:  # noqa: BLE001
            last = e
    raise RuntimeError(f"eth_call gagal semua RPC: {last}")


def eth_receipt(txhash, tries=18, wait=5):
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "eth_getTransactionReceipt",
                          "params": [txhash]}).encode()
    for _ in range(tries):
        for rpc in RPCS:
            try:
                d = http_json(rpc, payload, {"Content-Type": "application/json"}, timeout=12)
                if d.get("result"):
                    return d["result"]
            except Exception:  # noqa: BLE001
                pass
        time.sleep(wait)
    return None


def binance_audit():
    body = json.dumps({"binanceChainId": "56", "contractAddress": TOKEN,
                       "requestId": str(uuid.uuid4())}).encode()
    h = {"Content-Type": "application/json", "source": "agent",
         "Accept-Encoding": "identity", "User-Agent": "binance-web3/1.4 (Skill)"}
    d = http_json("https://web3.binance.com/bapi/defi/v1/public/wallet-direct/security/token/audit",
                  body, h, timeout=20)
    data = d.get("data") or {}
    risk_hit = any(det.get("isHit") and det.get("riskType") == "RISK"
                   for it in (data.get("riskItems") or [])
                   for det in (it.get("details") or []))
    caution_hits = [(it.get("id"), det.get("title"))
                    for it in (data.get("riskItems") or [])
                    for det in (it.get("details") or []) if det.get("isHit")]
    ok = (d.get("code") == "000000" and data.get("hasResult") is True
          and data.get("riskLevel") is not None and data.get("riskLevel") <= 1
          and data.get("riskAlert") is None and not risk_hit)
    return ok, {"riskLevel": data.get("riskLevel"), "hasResult": data.get("hasResult"),
                "riskAlert": data.get("riskAlert"), "risk_hit": risk_hit,
                "caution_hits": caution_hits,
                "sellTax": (data.get("extraInfo") or {}).get("sellTax")}


def goplus_check():
    d = http_json(f"https://api.gopluslabs.io/api/v1/token_security/56?contract_addresses={TOKEN}",
                  timeout=20)
    r = list(d["result"].values())[0]
    ok = (r.get("is_honeypot") in (0, "0") and r.get("hidden_owner") in (0, "0")
          and float(r.get("sell_tax", 99)) <= 0.03
          and r.get("cannot_sell_all") in (0, "0"))
    return ok, {"is_honeypot": r.get("is_honeypot"), "hidden_owner": r.get("hidden_owner"),
                "buy_tax": r.get("buy_tax"), "sell_tax": r.get("sell_tax"),
                "cannot_sell_all": r.get("cannot_sell_all"),
                "is_mintable": r.get("is_mintable")}


def pad(addr):
    return "0" * 24 + addr[2:].lower()


def u(n):
    return format(n, "064x")


def balance_of(token, holder):
    return int(eth_call(token, "0x70a08231" + pad(holder)), 16)


def allowance(token, owner, spender):
    return int(eth_call(token, "0xdd62ed3e" + pad(owner) + pad(spender)), 16)


def quote_out(amount_in):
    enc = ("0xd06ca61f" + u(amount_in) + u(64) + u(2) + pad(TOKEN) + pad(WBNB))
    r = eth_call(ROUTER, enc)
    words = [int(r[i:i + 64], 16) for i in range(2, len(r) - 1, 64)]
    return words[3]  # amountOut


def bnb_balance():
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "eth_getBalance",
                          "params": [AGENT, "latest"]}).encode()
    for rpc in RPCS:
        try:
            d = http_json(rpc, payload, {"Content-Type": "application/json"}, timeout=12)
            if "result" in d:
                return int(d["result"], 16)
        except Exception:  # noqa: BLE001
            pass
    raise RuntimeError("eth_getBalance gagal semua RPC")


def gas_price():
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "eth_gasPrice",
                          "params": []}).encode()
    for rpc in RPCS:
        try:
            d = http_json(rpc, payload, {"Content-Type": "application/json"}, timeout=12)
            if "result" in d:
                return int(d["result"], 16)
        except Exception:  # noqa: BLE001
            pass
    raise RuntimeError("eth_gasPrice gagal semua RPC")


def baw_preview(to, input_data, value=0):
    cmd = [BAW, "contract-call", "preview", "--binanceChainId", "56",
           "--from", AGENT, "--to", to, "--value", str(value),
           "--inputData", input_data, "--json"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise RuntimeError(f"baw preview rc={r.returncode}: {r.stderr.strip()[:300]}")
    return json.loads(r.stdout)


def baw_execute(request_id):
    cmd = [BAW, "contract-call", "execute", "--requestId", request_id, "--json"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise RuntimeError(f"baw execute rc={r.returncode}: {r.stderr.strip()[:300]}")
    return json.loads(r.stdout)


def preview_base_clean(prev):
    sim = prev["data"]["simulationResult"]
    if sim.get("simulationCode") != "000000000":
        return False, f"simulationCode {sim.get('simulationCode')}"
    if sim.get("simulationErrorDetail"):
        return False, f"simulationError {sim['simulationErrorDetail']}"
    if sim.get("authorityChanges"):
        return False, f"authorityChanges {sim['authorityChanges']}"
    if prev["data"].get("risks", {}).get("riskDetails"):
        return False, f"risks {prev['data']['risks']['riskDetails']}"
    return True, "bersih"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true")
    args = ap.parse_args()
    t0 = time.time()

    # ---- cek baca-saja ----
    agc_before = balance_of(TOKEN, AGENT)
    allow = allowance(TOKEN, AGENT, ROUTER)
    bnb_before = bnb_balance()
    wbnb_before = balance_of(WBNB, AGENT)
    log(f"AGC {agc_before/1e18:,.2f} | allowance router {allow/1e18:,.2f} | "
        f"BNB {bnb_before/1e18:.6f} | WBNB {wbnb_before/1e18:.6f}")
    if agc_before <= 0:
        print("ABORT: saldo AGC 0 — tidak ada yang dijual")
        return 2
    audit_ok, audit = binance_audit()
    log(f"audit Binance: {audit} -> {'LOLOS' if audit_ok else 'GAGAL'}")
    if not audit_ok:
        print("ABORT: audit tidak lolos")
        return 2
    gp_ok, gp = goplus_check()
    log(f"GoPlus: {gp} -> {'LOLOS' if gp_ok else 'GAGAL'}")
    if not gp_ok:
        print("ABORT: GoPlus tidak lolos")
        return 2
    q = quote_out(agc_before)
    min_out = int(q * 0.95)
    gp_wei = gas_price()
    est_gas_total = int(300_000 * gp_wei)  # approve+swap+unwrap
    log(f"quote: {agc_before/1e18:,.2f} AGC -> {q/1e18:.6f} WBNB | min-out 95%: "
        f"{min_out/1e18:.6f} | gasPrice {gp_wei/1e9} gwei | est gas total "
        f"{est_gas_total/1e18:.6f} BNB")
    if q <= 0:
        print("ABORT: quote 0")
        return 2
    if q < 2 * est_gas_total:
        print(f"ABORT: quote {q/1e18:.6f} < 2x gas {2*est_gas_total/1e18:.6f}")
        return 2
    if bnb_before < est_gas_total:
        print("ABORT: BNB tidak cukup untuk gas")
        return 2
    deadline = int(time.time()) + 1200
    approve_data = "0x095ea7b3" + pad(ROUTER) + u(agc_before)
    swap_data = ("0x5c11d795" + u(agc_before) + u(min_out) + u(160) + pad(AGENT)
                 + u(deadline) + u(2) + pad(TOKEN) + pad(WBNB))
    log(f"cek selesai dalam {time.time()-t0:.1f} dtk")
    if not args.execute:
        print("CHECK-ONLY selesai.")
        print("approve:", approve_data)
        print("swap:", swap_data)
        return 0

    results = {"agc_before": agc_before / 1e18, "bnb_before": bnb_before / 1e18,
               "quote_wbnb": q / 1e18, "min_out_wbnb": min_out / 1e18,
               "audit": audit, "goplus": gp, "steps": {}}

    # ---- 1. approve exact-delta ----
    if allow < agc_before:
        prev = baw_preview(TOKEN, approve_data)
        ok, why = preview_base_clean(prev)
        if not ok:
            print(f"ABORT: preview approve tidak bersih: {why}")
            return 2
        ex = baw_execute(prev["data"]["requestId"])
        tx = ex["data"]["txHash"]
        log(f"approve broadcasted: {tx}")
        rc = eth_receipt(tx)
        if not rc or int(rc["status"], 16) != 1:
            print(f"ABORT: receipt approve tidak status 1 (tx {tx}) — CEK MANUAL")
            return 3
        log(f"approve receipt 1 blok {int(rc['blockNumber'], 16)} gas {int(rc['gasUsed'], 16)}")
        results["steps"]["approve"] = {"tx": tx, "block": int(rc["blockNumber"], 16),
                                       "gas": int(rc["gasUsed"], 16)}
    else:
        log("allowance cukup — approve dilewati")

    # ---- 2. swap AGC -> WBNB ----
    prev = baw_preview(ROUTER, swap_data)
    ok, why = preview_base_clean(prev)
    if not ok:
        print(f"ABORT: preview swap tidak bersih: {why}")
        return 2
    sim_agc = sim_wbnb = None
    for ch in prev["data"]["simulationResult"].get("balanceChanges") or []:
        if ch["contractAddress"].lower() == TOKEN.lower():
            sim_agc = ch["change"]
        if ch["contractAddress"].lower() == WBNB.lower():
            sim_wbnb = ch["change"]
    log(f"preview swap bersih; simulasi AGC {sim_agc} WBNB {sim_wbnb}")
    # NOTE: baw mengembalikan balance change sebagai float (notasi ilmiah);
    # float64 tidak bisa mewakili bilangan bulat 2.28e23 secara eksak, jadi
    # bandingkan dengan toleransi 1e12 raw unit (~1e-6 AGC), bukan == eksak.
    if sim_agc is None or abs(int(sim_agc) + agc_before) > 10**12:
        print(f"ABORT: simulasi AGC {sim_agc} != -{agc_before}")
        return 2
    if sim_wbnb is None or sim_wbnb < min_out:
        print(f"ABORT: simulasi WBNB {sim_wbnb} < min-out {min_out}")
        return 2
    ex = baw_execute(prev["data"]["requestId"])
    tx = ex["data"]["txHash"]
    log(f"swap broadcasted: {tx}")
    rc = eth_receipt(tx)
    if not rc or int(rc["status"], 16) != 1:
        print(f"ABORT: receipt swap tidak status 1 (tx {tx}) — CEK MANUAL")
        return 3
    block = int(rc["blockNumber"], 16)
    log(f"swap receipt 1 blok {block} gas {int(rc['gasUsed'], 16)}")
    results["steps"]["swap"] = {"tx": tx, "block": block, "gas": int(rc["gasUsed"], 16),
                                "sim_wbnb": sim_wbnb / 1e18 if sim_wbnb else None}
    wbnb_after_swap = balance_of(WBNB, AGENT)
    agc_after_swap = balance_of(TOKEN, AGENT)
    log(f"pasca-swap: AGC {agc_after_swap/1e18:,.6f} | WBNB {wbnb_after_swap/1e18:.6f}")
    if agc_after_swap != 0:
        print(f"PERINGATAN: sisa AGC {agc_after_swap/1e18} (bukan 0) — lanjut unwrap, laporkan sisa")
    if wbnb_after_swap < min_out:
        print(f"ABORT: WBNB diterima {wbnb_after_swap/1e18:.6f} < min-out {min_out/1e18:.6f} — CEK MANUAL")
        return 3

    # ---- 3. unwrap WBNB -> BNB ----
    unwrap_data = "0x2e1a7d4d" + u(wbnb_after_swap)
    prev = baw_preview(WBNB, unwrap_data)
    ok, why = preview_base_clean(prev)
    if not ok:
        print(f"ABORT: preview unwrap tidak bersih: {why} — WBNB {wbnb_after_swap/1e18:.6f} masih di wallet, CEK MANUAL")
        return 3
    sim_wbnb2 = None
    for ch in prev["data"]["simulationResult"].get("balanceChanges") or []:
        if ch["contractAddress"].lower() == WBNB.lower():
            sim_wbnb2 = ch["change"]
    log(f"preview unwrap bersih; simulasi WBNB {sim_wbnb2}")
    if sim_wbnb2 is None or abs(int(sim_wbnb2) + wbnb_after_swap) > 1000:
        print(f"ABORT: simulasi unwrap WBNB {sim_wbnb2} != -{wbnb_after_swap}")
        return 3
    ex = baw_execute(prev["data"]["requestId"])
    tx = ex["data"]["txHash"]
    log(f"unwrap broadcasted: {tx}")
    rc = eth_receipt(tx)
    if not rc or int(rc["status"], 16) != 1:
        print(f"ABORT: receipt unwrap tidak status 1 (tx {tx}) — CEK MANUAL")
        return 3
    log(f"unwrap receipt 1 blok {int(rc['blockNumber'], 16)} gas {int(rc['gasUsed'], 16)}")
    results["steps"]["unwrap"] = {"tx": tx, "block": int(rc["blockNumber"], 16),
                                  "gas": int(rc["gasUsed"], 16)}

    # ---- verifikasi akhir ----
    agc_final = balance_of(TOKEN, AGENT)
    wbnb_final = balance_of(WBNB, AGENT)
    bnb_final = bnb_balance()
    results["agc_final"] = agc_final / 1e18
    results["wbnb_final"] = wbnb_final / 1e18
    results["bnb_final"] = bnb_final / 1e18
    results["bnb_delta"] = (bnb_final - bnb_before) / 1e18
    out_path = os.path.join(BASE, "sell-all-2026-11-11.json")
    json.dump(results, open(out_path, "w"), indent=2, ensure_ascii=False)
    print(f"JUAL SELESAI: {agc_before/1e18:,.2f} AGC -> BNB {bnb_final/1e18:.6f} "
          f"(delta +{results['bnb_delta']:.6f} setelah gas) | AGC sisa {agc_final/1e18} | "
          f"WBNB sisa {wbnb_final/1e18} | log {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
