#!/usr/bin/env python3
"""Jalur beli cepat AGC — BACOT SCALPING ronde 3 (all-in).

Dibuat 2026-11-09 ~11:10 WIB sesudah entry ronde 3 telat ~7 menit
(trigger 10:43:04, fill 10:50, +9,8% di atas harga trigger) karena
persiapan dilakukan berurutan lewat banyak panggilan shell yang
sebagian timeout. Skrip ini melakukan SEMUA persiapan dalam satu
proses: audit + GoPlus + saldo + allowance + quote + harga, lalu
preview + eksekusi + verifikasi + tulis state.

Dipanggil parent saat trigger kena (cron tetap read-only + lapor saja).
TIDAK PERNAH dijalankan otomatis oleh cron.

Penggunaan:
  python3 fast_buy.py                 # check-only (default): semua cek
                                      # baca-saja + tampilkan calldata
                                      # yang akan dipakai. Tanpa preview,
                                      # tanpa tx, tanpa tulis state.
  python3 fast_buy.py --execute       # jalur penuh: cek -> preview bersih
                                      # -> eksekusi -> verifikasi -> state
                                      # holding. Hanya bila state=watching.
  python3 fast_buy.py --size-wbnb 0.0048 --tp 1.30 --sl 0.90

Guard (fail-closed, sama seperti eksekusi manual):
  - state harus watching (kecuali --check-only)
  - audit Binance segar: code 000000, hasResult true, riskLevel <= 1,
    riskAlert null, tanpa hit RISK
  - GoPlus segar: bukan honeypot, hidden_owner 0, tax beli/jual <= 3%
  - saldo WBNB >= size; allowance >= size (bila kurang, approve exact
    dulu via preview bersih — normalnya sudah ter-cover pre-approve
    0,02 WBNB ke router PCS)
  - quote segar via getAmountsOut; min-out = 95% quote
  - preview swap bersih: simulationCode 000000000, tanpa error,
    perubahan saldo sesuai (WBNB -size, AGC >= min-out),
    tanpa authority changes, tanpa risk
  - receipt status 1 + delta AGC terverifikasi on-chain
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
PAIR = "0xE56041CdDBB46DB01c67B7A62684F428a04Db8C6"
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
        except Exception as e:  # noqa: BLE001 - fallback ke RPC berikut
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
            except Exception:  # noqa: BLE001 - coba RPC berikut / ulang
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
    ok = (d.get("code") == "000000" and data.get("hasResult") is True
          and data.get("riskLevel") is not None and data.get("riskLevel") <= 1
          and data.get("riskAlert") is None and not risk_hit)
    return ok, {"riskLevel": data.get("riskLevel"), "hasResult": data.get("hasResult"),
                "riskAlert": data.get("riskAlert"), "risk_hit": risk_hit}


def goplus_check():
    d = http_json(f"https://api.gopluslabs.io/api/v1/token_security/56?contract_addresses={TOKEN}",
                  timeout=20)
    r = d["result"][TOKEN.lower()]
    ok = (r.get("is_honeypot") in (0, "0") and r.get("hidden_owner") in (0, "0")
          and float(r.get("buy_tax", 99)) <= 0.03 and float(r.get("sell_tax", 99)) <= 0.03)
    return ok, {"is_honeypot": r.get("is_honeypot"), "hidden_owner": r.get("hidden_owner"),
                "buy_tax": r.get("buy_tax"), "sell_tax": r.get("sell_tax")}


def pad(addr):
    return "0" * 24 + addr[2:].lower()


def u(n):
    return format(n, "064x")


def balance_of(token, holder):
    return int(eth_call(token, "0x70a08231" + pad(holder)), 16)


def allowance(token, owner, spender):
    return int(eth_call(token, "0xdd62ed3e" + pad(owner) + pad(spender)), 16)


def quote_out(amount_in):
    enc = ("0xd06ca61f" + u(amount_in) + u(64) + u(2) + pad(WBNB) + pad(TOKEN))
    r = eth_call(ROUTER, enc)
    words = [int(r[i:i + 64], 16) for i in range(2, len(r) - 1, 64)]
    return words[3]  # amountOut


def ds_price():
    d = http_json(f"https://api.dexscreener.com/latest/dex/pairs/bsc/{PAIR}", timeout=20)
    p = d["pairs"][0]
    return float(p["priceUsd"]), float(p.get("marketCap") or 0), p.get("priceChange", {}).get("m5")


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


def check_preview_clean(prev, expect):
    """expect: dict dengan kunci yang harus cocok di balanceChanges."""
    sim = prev["data"]["simulationResult"]
    if sim.get("simulationCode") != "000000000":
        return False, f"simulationCode {sim.get('simulationCode')}"
    if sim.get("simulationErrorDetail"):
        return False, f"simulationError {sim['simulationErrorDetail']}"
    if sim.get("authorityChanges"):
        return False, f"authorityChanges {sim['authorityChanges']}"
    if prev["data"].get("risks", {}).get("riskDetails"):
        return False, f"risks {prev['data']['risks']['riskDetails']}"
    for ch in sim.get("balanceChanges") or []:
        key = (ch["contractAddress"].lower(), ch["owner"].lower())
        if key in expect:
            want = expect[key]
            if ch["change"] != want:
                return False, f"balanceChange {key} = {ch['change']} (harap {want})"
    return True, "bersih"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true", help="jalur penuh sampai eksekusi")
    ap.add_argument("--size-wbnb", type=float, default=0.0048)
    ap.add_argument("--tp", type=float, default=1.30)
    ap.add_argument("--sl", type=float, default=0.90)
    args = ap.parse_args()
    size = int(round(args.size_wbnb * 1e18))
    state_path = os.path.join(BASE, "state.json")
    state = json.load(open(state_path))
    log(f"state: {state.get('status')} ronde {state.get('round')}")
    if args.execute and state.get("status") != "watching":
        print("ABORT: state bukan watching — tidak ada pembelian.")
        return 2

    # ---- semua cek baca-saja dalam satu proses ----
    t0 = time.time()
    wbnb_bal = balance_of(WBNB, AGENT) / 1e18
    agc_before = balance_of(TOKEN, AGENT) / 1e18
    allow = allowance(WBNB, AGENT, ROUTER) / 1e18
    log(f"WBNB {wbnb_bal:.6f} | allowance router {allow:.6f} | AGC {agc_before:,.2f}")
    if wbnb_bal < args.size_wbnb:
        print(f"ABORT: WBNB {wbnb_bal} < size {args.size_wbnb}")
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
    q = quote_out(size)
    min_out = int(q * 0.95)
    log(f"quote: {size/1e18} WBNB -> {q/1e18:,.2f} AGC | min-out 95%: {min_out/1e18:,.2f}")
    try:
        price_usd, mcap, m5 = ds_price()
        log(f"DexScreener: price ${price_usd:.10g} MCAP ${mcap:,.0f} m5 {m5}%")
    except Exception as e:  # noqa: BLE001 - harga DS opsional; entry dihitung dari preview
        price_usd, mcap, m5 = None, None, None
        log(f"DexScreener gagal dibaca ({type(e).__name__}) — entry dihitung dari preview")
    deadline = int(time.time()) + 1200
    swap_data = ("0x5c11d795" + u(size) + u(min_out) + u(160) + pad(AGENT) + u(deadline)
                 + u(2) + pad(WBNB) + pad(TOKEN))
    log(f"cek selesai dalam {time.time()-t0:.1f} dtk")
    if not args.execute:
        print("CHECK-ONLY selesai. Swap calldata yang akan dipakai:")
        print(swap_data)
        return 0

    # ---- approve bila perlu (normalnya sudah ter-cover pre-approve 0,02) ----
    if allow < args.size_wbnb:
        approve_data = "0x095ea7b3" + pad(ROUTER) + u(size)
        prev = baw_preview(WBNB, approve_data)
        ok, why = check_preview_clean(prev, {})
        if not ok:
            print(f"ABORT: preview approve tidak bersih: {why}")
            return 2
        ex = baw_execute(prev["data"]["requestId"])
        log(f"approve broadcasted: {ex['data'].get('txHash')}")
        rc = eth_receipt(ex["data"]["txHash"])
        if not rc or int(rc["status"], 16) != 1:
            print("ABORT: receipt approve tidak status 1")
            return 2
        log(f"approve receipt 1 blok {int(rc['blockNumber'], 16)}")
    else:
        log("allowance cukup — langkah approve dilewati")

    # ---- preview + eksekusi swap ----
    prev = baw_preview(ROUTER, swap_data)
    expect = {(WBNB.lower(), AGENT.lower()): -size}
    ok, why = check_preview_clean(prev, expect)
    if not ok:
        print(f"ABORT: preview swap tidak bersih: {why}")
        return 2
    sim_out = None
    for ch in prev["data"]["simulationResult"].get("balanceChanges") or []:
        if ch["contractAddress"].lower() == TOKEN.lower():
            sim_out = ch["change"] / 1e18
    log(f"preview swap bersih; simulasi terima ~{sim_out:,.2f} AGC (min-out {min_out/1e18:,.2f})")
    if sim_out is not None and sim_out * 1e18 < min_out:
        print("ABORT: simulasi di bawah min-out")
        return 2
    ex = baw_execute(prev["data"]["requestId"])
    tx = ex["data"]["txHash"]
    log(f"swap broadcasted: {tx}")
    rc = eth_receipt(tx)
    if not rc or int(rc["status"], 16) != 1:
        print(f"ABORT: receipt swap tidak status 1 (tx {tx}) — CEK MANUAL")
        return 3
    block = int(rc["blockNumber"], 16)
    agc_after = balance_of(TOKEN, AGENT) / 1e18
    wbnb_after = balance_of(WBNB, AGENT) / 1e18
    delta = agc_after - agc_before
    log(f"receipt 1 blok {block} gas {int(rc['gasUsed'], 16)} | AGC +{delta:,.2f} -> {agc_after:,.2f} | WBNB {wbnb_after}")
    if delta * 1e18 < min_out:
        print("ABORT: delta AGC di bawah min-out — CEK MANUAL")
        return 3
    # harga entry USD: dari preview tokenInfos bila ada, else DexScreener
    entry_usd = price_usd
    infos = prev["data"].get("tokenInfos", {})
    wbnb_usd = float(infos.get(WBNB, {}).get("price", 0) or 0)
    if wbnb_usd:
        entry_usd = (size / 1e18) * wbnb_usd / delta
    target = entry_usd * args.tp
    stop = entry_usd * args.sl
    new_state = dict(state)
    new_state.update({"status": "holding", "entry_price_usd": entry_usd,
                      "target": target, "stop": stop,
                      "entry_wbnb": args.size_wbnb, "amount_agc": delta,
                      "amount_to_sell": delta,
                      "entry_price_wbnb": args.size_wbnb / delta,
                      "bought_wib": time.strftime("%Y-%m-%d %H:%M:%S WIB", time.localtime()),
                      "tx": tx, "block": block, "gas": int(rc["gasUsed"], 16),
                      "min_out_agc": min_out / 1e18,
                      "note": "Dibeli via fast_buy.py (jalur cepat). Saldo AGC wallet total "
                              "termasuk klaim Mode B lama — exit jual HANYA delta ini."})
    json.dump(new_state, open(state_path, "w"), indent=2, ensure_ascii=False)
    # catat di ledger uji terpadu
    pos_path = "/home/hatch/workspace/scalp-flap-new/positions.json"
    pos = json.load(open(pos_path))
    pos["positions"].append({
        "round": state.get("round"), "status": "open", "token": "AGC",
        "ca": TOKEN, "pair": PAIR, "entry_wbnb": args.size_wbnb, "amount": delta,
        "entry_price_wbnb": args.size_wbnb / delta,
        "target_wbnb": args.size_wbnb * args.tp, "stop_wbnb": args.size_wbnb * args.sl,
        "bought_wib": new_state["bought_wib"], "tx": tx, "block": block,
        "gas": int(rc["gasUsed"], 16),
        "kind": "dip-trigger-all-in", "source": "scalp-agc-watch + fast_buy.py",
        "entry_price_usd": entry_usd, "target_usd": target, "stop_usd": stop})
    if "rounds" in pos:
        pos["rounds"]["current"] = state.get("round")
    json.dump(pos, open(pos_path, "w"), indent=2, ensure_ascii=False)
    print(f"BELI SELESAI: {args.size_wbnb} WBNB -> {delta:,.2f} AGC | entry ${entry_usd:.10g} "
          f"| TP ${target:.10g} | SL ${stop:.10g} | tx {tx} blok {block}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
