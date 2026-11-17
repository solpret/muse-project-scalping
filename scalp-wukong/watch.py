#!/usr/bin/env python3
"""Bounce-watch WUKONG (BACOT SCALPING) — READ-ONLY.

Pola sama seperti ~/workspace/scalp-binance-life/watch.py. Menilai trigger
entry / exit tiap run dan menulis hasilnya ke watch-result.json.
TIDAK PERNAH sign, approve, swap, mint, atau mengirim tx apa pun. Eksekusi
beli/jual dilakukan parent di chat utama sesudah menerima laporan trigger.

Entry trigger (status=watching, SEMUA wajib, dicek SEGAR tiap run):
  m5 > +2%, buys h1 > sells h1, liq >= $15 ribu, saldo WBNB >= 0,002,
  LP dead-burn >= 50%, GoPlus bersih (bukan honeypot/mintable/hidden-owner,
  tax <= 2%), audit Binance bersih (code 000000, hasResult, riskLevel <= 1,
  riskAlert null, tanpa hit RISK), top wallet bebas <= 5% — termasuk dua
  whale yang dipantau (0x2D22...16F1 dan 0xC95B...9b24B) yang dicek ulang
  saldonya via Ankr tiap run; gate ini hanya lolos bila keduanya < 5%.
Satu leg saja tidak terverifikasi (RPC/API gagal) -> trigger False (fail-closed).

Exit signal (status=holding): harga >= target -> SELL_TARGET; <= stop -> SELL_STOP.
State (entry/target/stop/round) ditulis parent sesudah eksekusi; skrip ini
hanya membaca state.json (dan mencatat flag whales_below_5pct bila kedua
whale terverifikasi < 5%).
"""
import json
import os
import subprocess
import sys
import time
import urllib.request  # noqa: F401  (kept for parity; curl used for fetches)
import urllib.error  # noqa: F401
import uuid

BASE = "/home/hatch/workspace/scalp-wukong"
sys.path.insert(0, "/home/hatch/workspace")
from fast_exec.fast_rpc import race  # noqa: E402

TOKEN = "0xfead44c70c51a03d3c14b257dfc69b0b0e227777"
PAIR = "0x3f29137fCCAA23a6cd9C6849F7B9e087e41eAe0b"
WBNB = "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c"
WALLET = "0xeb237F6f9FB4e826739F0954e9768914F28b2c2E"
DEAD = "0x000000000000000000000000000000000000dead"
ZERO = "0x0000000000000000000000000000000000000000"
ANKR_PY = "/home/hatch/workspace/custom-connectors/custom.ankr/bin/ankr.py"
WHALES = ["0x2D222aab2806580CE0f687AB694Cd42B339d16F1",
          "0xC95B035dd0a56bAEB5cDA921d27b9D2b2409b24B"]
SIZE_WBNB = 0.002
M5_MIN = 2.0
LIQ_MIN = 15000
LP_BURN_MIN = 50.0
TOP_FREE_MAX = 5.0
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"}


def log_line(s):
    with open(os.path.join(BASE, "watch-runs.log"), "a") as f:
        f.write(time.strftime("%Y-%m-%d %H:%M:%S WIB ", time.localtime()) + s + "\n")


def get_json(url, headers=None, data=None, timeout=15):
    # curl, bukan urllib: urllib sering IncompleteRead di lingkungan ini.
    cmd = ["curl", "-sS", "-m", str(timeout)]
    for k, v in (headers or {}).items():
        cmd += ["-H", f"{k}: {v}"]
    if data is not None:
        cmd += ["-X", "POST", "-d", data.decode() if isinstance(data, bytes) else data]
    cmd.append(url)
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 5)
    if out.returncode != 0:
        raise RuntimeError(f"curl rc={out.returncode}: {out.stderr.strip()[:150]}")
    return json.loads(out.stdout)


def gt_m5():
    # DexScreener tidak mengembalikan kunci m5 untuk pair ini; GT selalu ada.
    d = get_json(f"https://api.geckoterminal.com/api/v2/networks/bsc/pools/{PAIR}")
    a = (d.get("data") or {}).get("attributes") or {}
    chg = a.get("price_change_percentage") or {}
    return float(chg.get("m5") or 0)


def ds_pair():
    # latest/dex/tokens mengembalikan priceChange.m5 (tokens/v1 sering None).
    d = get_json(f"https://api.dexscreener.com/latest/dex/tokens/{TOKEN}")
    pairs = [p for p in d.get("pairs", [])
             if p.get("dexId") == "pancakeswap"
             and p.get("quoteToken", {}).get("symbol") == "WBNB"]
    for p in pairs:
        if p.get("pairAddress", "").lower() == PAIR.lower():
            return p
    return pairs[0] if pairs else None


def eth_call(to, data):
    return race("eth_call", [{"to": to, "data": data}, "latest"])


def balance_of(token, holder):
    data = "0x70a08231" + holder[2:].rjust(64, "0")
    return int(eth_call(token, data), 16) / 1e18


def total_supply(token):
    return int(eth_call(token, "0x18160ddd"), 16) / 1e18


def lp_burn_pct():
    ts = int(eth_call(PAIR, "0x18160ddd"), 16)
    bal = int(eth_call(PAIR, "0x70a08231" + DEAD[2:].rjust(64, "0")), 16)
    return bal / ts * 100 if ts else 0.0


def ankr_holders(ca, page_size=100):
    params = json.dumps({"blockchain": "bsc", "contractAddress": ca, "pageSize": page_size})
    out = subprocess.run(["python3", ANKR_PY, "raw", "ankr_getTokenHolders", params],
                         capture_output=True, text=True, timeout=60)
    if out.returncode != 0:
        raise RuntimeError(f"ankr rc={out.returncode}: {out.stderr.strip()[:150]}")
    return json.loads(out.stdout)["result"]


def goplus_ok():
    d = get_json(f"https://api.gopluslabs.io/api/v1/token_security/56?contract_addresses={TOKEN}")
    r = d["result"][TOKEN.lower()]
    ok = (r.get("is_honeypot") in (0, "0") and r.get("is_mintable") in (0, "0")
          and r.get("hidden_owner") in (0, "0")
          and float(r.get("buy_tax", 99)) <= 0.02 and float(r.get("sell_tax", 99)) <= 0.02)
    return ok, r


def binance_ok():
    body = json.dumps({"binanceChainId": "56", "contractAddress": TOKEN,
                       "requestId": str(uuid.uuid4())}).encode()
    h = dict(UA)
    h.update({"Content-Type": "application/json", "source": "agent",
              "Accept-Encoding": "identity", "User-Agent": "binance-web3/1.4 (Skill)"})
    d = get_json("https://web3.binance.com/bapi/defi/v1/public/wallet-direct/security/token/audit",
                 headers=h, data=body)
    data = d.get("data") or {}
    risk_hit = False
    for item in data.get("riskItems") or []:
        for det in item.get("details") or []:
            if det.get("isHit") and det.get("riskType") == "RISK":
                risk_hit = True
    ok = (d.get("code") == "000000" and data.get("hasResult") is True
          and data.get("riskLevel") is not None and data.get("riskLevel") <= 1
          and data.get("riskAlert") is None and not risk_hit)
    return ok, {"riskLevel": data.get("riskLevel"), "hasResult": data.get("hasResult"),
                "riskAlert": data.get("riskAlert"), "risk_hit": risk_hit}


def whale_check():
    """Cek dua whale terpantau via Ankr (primer), fallback fast_rpc balanceOf.

    Mengembalikan (ok, detail): ok True hanya bila KEDUA whale terverifikasi
    < 5% DAN top wallet bebas keseluruhan <= 5%. Bila ada yang tidak
    terverifikasi -> ok False (fail-closed).
    """
    detail = {"whales": {}, "top_free_pct": None, "top_free_addr": None, "method": None}
    try:
        res = ankr_holders(TOKEN)
        holders = res.get("holders", [])
        bal_by_addr = {}
        for h in holders:
            addr = (h.get("holderAddress") or "").lower()
            if addr:
                bal_by_addr[addr] = float(h.get("balance") or 0)
        ts = total_supply(TOKEN)
        if not ts:
            raise RuntimeError("totalSupply 0")
        detail["method"] = "ankr"
        min_listed = min(bal_by_addr.values()) if bal_by_addr else None
        for w in WHALES:
            bal = bal_by_addr.get(w.lower())
            if bal is not None:
                detail["whales"][w] = round(bal / ts * 100, 2)
            elif min_listed is not None:
                # Whale tidak masuk top-100 Ankr -> saldonya < saldo terkecil
                # yang terdaftar; pakai batas atas itu (konservatif).
                detail["whales"][w] = round(min_listed / ts * 100, 2)
                detail.setdefault("whales_bounded", []).append(w)
            else:
                raise RuntimeError("ankr holders kosong")
        top_free, top_addr = 0.0, None
        for addr, bal in bal_by_addr.items():
            if addr in (PAIR.lower(), DEAD.lower(), ZERO.lower()):
                continue
            pct = bal / ts * 100
            if pct > top_free:
                top_free, top_addr = pct, addr
        detail["top_free_pct"] = round(top_free, 2)
        detail["top_free_addr"] = top_addr
    except Exception as e:  # noqa: BLE001
        # Fallback: fast_rpc balanceOf per whale + totalSupply.
        try:
            ts = total_supply(TOKEN)
            if not ts:
                raise RuntimeError("totalSupply 0")
            detail["method"] = f"fast_rpc (ankr gagal: {type(e).__name__})"
            for w in WHALES:
                detail["whales"][w] = round(balance_of(TOKEN, w) / ts * 100, 2)
            # Top-free keseluruhan tidak bisa dihitung tanpa daftar holder.
            detail["top_free_pct"] = None
            detail["top_free_addr"] = None
        except Exception as e2:  # noqa: BLE001
            detail["error"] = f"{type(e).__name__}/{type(e2).__name__}"
            return False, detail
    whales_ok = all(pct is not None and pct < TOP_FREE_MAX
                    for pct in detail["whales"].values()) and len(detail["whales"]) == 2
    top_ok = detail.get("top_free_pct") is not None and detail["top_free_pct"] <= TOP_FREE_MAX
    # Fail-closed: top-free keseluruhan harus TERVERIFIKASI <= 5%. Bila Ankr
    # gagal (fallback fast_rpc, top_free None) -> gate tidak lolos.
    ok = whales_ok and top_ok is True
    detail["whales_ok"] = whales_ok
    detail["top_free_ok"] = top_ok
    return ok, detail


def main():
    state_path = os.path.join(BASE, "state.json")
    if os.path.exists(state_path):
        state = json.load(open(state_path))
    else:
        state = {"status": "watching", "round": 1, "wins": 0, "losses": 0, "pnl_bnb": 0.0}
        json.dump(state, open(state_path, "w"), indent=1)
    result = {"mode": state.get("status", "watching"), "round": state.get("round", 1),
              "checked_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
              "trigger": False, "signal": None, "checks": {}, "reason": ""}
    try:
        p = ds_pair()
        if not p:
            raise RuntimeError("dexscreener_empty")
        price = float(p.get("priceUsd") or 0)
        try:
            m5 = gt_m5()
            result["m5_source"] = "geckoterminal"
        except Exception as e:  # noqa: BLE001
            m5 = float((p.get("priceChange") or {}).get("m5") or 0)
            result["m5_source"] = f"dexscreener_fallback ({type(e).__name__})"
        txns = (p.get("txns") or {}).get("h1") or {}
        buys, sells = int(txns.get("buys") or 0), int(txns.get("sells") or 0)
        liq = float((p.get("liquidity") or {}).get("usd") or 0)
        result.update({"price": price, "m5": m5, "h1_buys": buys, "h1_sells": sells, "liq": liq})
        if state.get("status") == "holding":
            target, stop = state.get("target"), state.get("stop")
            if target and price >= target:
                result["signal"] = "SELL_TARGET"
            elif stop and price <= stop:
                result["signal"] = "SELL_STOP"
            result["reason"] = result["signal"] or f"holding, price {price} di antara stop {stop} dan target {target}"
        else:
            checks = {}
            checks["m5_gt_2"] = m5 > M5_MIN
            checks["buys_dominant"] = buys > sells
            checks["liq_ok"] = liq >= LIQ_MIN
            try:
                wbnb = balance_of(WBNB, WALLET)
                checks["wbnb_ok"] = wbnb >= SIZE_WBNB
                result["wbnb_balance"] = wbnb
            except Exception as e:  # noqa: BLE001
                checks["wbnb_ok"] = False
                result["wbnb_error"] = f"unverified: {type(e).__name__}"
            try:
                burn = lp_burn_pct()
                checks["lp_burn_ok"] = burn >= LP_BURN_MIN
                result["lp_burn_pct"] = round(burn, 2)
            except Exception as e:  # noqa: BLE001
                checks["lp_burn_ok"] = False
                result["lp_error"] = f"unverified: {type(e).__name__}"
            try:
                ok, raw = goplus_ok()
                checks["goplus_ok"] = ok
                result["goplus"] = {k: raw.get(k) for k in ("is_honeypot", "is_mintable", "hidden_owner", "buy_tax", "sell_tax")}
            except Exception as e:  # noqa: BLE001
                checks["goplus_ok"] = False
                result["goplus_error"] = f"unverified: {type(e).__name__}"
            try:
                ok, raw = binance_ok()
                checks["binance_ok"] = ok
                result["binance"] = raw
            except Exception as e:  # noqa: BLE001
                checks["binance_ok"] = False
                result["binance_error"] = f"unverified: {type(e).__name__}"
            try:
                ok, detail = whale_check()
                checks["whales_ok"] = bool(ok)
                result["whales"] = detail
                if ok:
                    state["whales_below_5pct"] = True
                    state.setdefault("whales_below_5pct_since", result["checked_at"])
                    json.dump(state, open(state_path, "w"), indent=1)
            except Exception as e:  # noqa: BLE001
                checks["whales_ok"] = False
                result["whales_error"] = f"unverified: {type(e).__name__}"
            result["checks"] = checks
            failed = [k for k, v in checks.items() if not v]
            result["trigger"] = not failed
            if result["trigger"]:
                result["entry_price"] = price
                result["target"] = round(price * 1.15, 8)
                result["stop"] = round(price * 0.95, 8)
                result["reason"] = "SEMUA LEG LOLOS — trigger beli"
            else:
                result["reason"] = "gagal: " + ",".join(failed)
    except Exception as e:  # noqa: BLE001
        result["reason"] = f"check_error: {type(e).__name__}: {e}"
        result["trigger"] = False
    json.dump(result, open(os.path.join(BASE, "watch-result.json"), "w"), indent=1)
    log_line(f"mode={result['mode']} trigger={result['trigger']} signal={result['signal']} price={result.get('price')} m5={result.get('m5')} | {result['reason']}")
    print(json.dumps(result, indent=1))


if __name__ == "__main__":
    main()
