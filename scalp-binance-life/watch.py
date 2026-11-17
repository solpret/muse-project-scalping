#!/usr/bin/env python3
"""Bounce-watch 币安人生 (BACOT SCALPING, koin baru) — READ-ONLY.

Menilai trigger entry / exit tiap run dan menulis hasilnya ke watch-result.json.
TIDAK PERNAH sign, approve, swap, mint, atau mengirim tx apa pun. Eksekusi
beli/jual dilakukan parent di chat utama sesudah menerima laporan trigger.

Entry trigger (status=watching): harga 8,20–8,80 USD, m5 > 0, buys h1 > sells h1,
liq >= $15 ribu, saldo WBNB >= 0,002, LP dead-burn >= 50%, GoPlus bersih
(bukan honeypot/mintable/hidden-owner, tax <= 3%), audit Binance bersih
(code 000000, hasResult, riskLevel <= 1, riskAlert null, tanpa hit RISK).
Satu leg saja tidak terverifikasi (RPC/API gagal) -> trigger False (fail-closed).

Exit signal (status=holding): harga >= target -> SELL_TARGET; <= stop -> SELL_STOP.
State (entry/target/stop/round) ditulis parent sesudah eksekusi; skrip ini
hanya membaca state.json.
"""
import json
import os
import subprocess
import sys
import time
import urllib.request
import urllib.error
import uuid
from datetime import datetime, timedelta, timezone

BASE = "/home/hatch/workspace/scalp-binance-life"
sys.path.insert(0, "/home/hatch/workspace")
from fast_exec.fast_rpc import race  # noqa: E402

TOKEN = "0x0aa4f116014fc09490fd4b5766f5c94c6c377777"
PAIR = "0x6a6a781e01a4D76FC59eFa08A377354066E9237F"
WBNB = "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c"
WALLET = "0xeb237F6f9FB4e826739F0954e9768914F28b2c2E"
DEAD = "0x000000000000000000000000000000000000dead"
SIZE_WBNB = 0.002
PRICE_LO = 8.20
PRICE_HI = 8.80
LIQ_MIN = 15000
LP_BURN_MIN = 50.0
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"}


WIB = timezone(timedelta(hours=7))


def wib_now():
    # Jam WIB eksplisit, tidak tergantung TZ proses: cron jalan dengan TZ=UTC
    # sehingga time.localtime() mengembalikan jam UTC yang dulu salah dilabeli
    # "WIB" (perbaikan 2026-11-08 atas tap user "Ya, perbaiki juga").
    # Hanya label jam yang diubah; hitungan epoch tidak disentuh.
    return datetime.now(timezone.utc).astimezone(WIB).strftime("%Y-%m-%d %H:%M:%S WIB")


def log_line(s):
    with open(os.path.join(BASE, "watch-runs.log"), "a") as f:
        f.write(wib_now() + " " + s + "\n")


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


def ds_pair():
    d = get_json(f"https://api.dexscreener.com/tokens/v1/bsc/{TOKEN}")
    for p in d:
        if p.get("pairAddress", "").lower() == PAIR.lower():
            return p
    return d[0] if d else None


def eth_call(to, data):
    return race("eth_call", [{"to": to, "data": data}, "latest"])


def balance_of(token, holder):
    data = "0x70a08231" + holder[2:].rjust(64, "0")
    return int(eth_call(token, data), 16) / 1e18


def lp_burn_pct():
    ts = int(eth_call(PAIR, "0x18160ddd"), 16)
    bal = int(eth_call(PAIR, "0x70a08231" + DEAD[2:].rjust(64, "0")), 16)
    return bal / ts * 100 if ts else 0.0


def goplus_ok():
    d = get_json(f"https://api.gopluslabs.io/api/v1/token_security/56?contract_addresses={TOKEN}")
    r = d["result"][TOKEN.lower()]
    return (r.get("is_honeypot") in (0, "0") and r.get("is_mintable") in (0, "0")
            and r.get("hidden_owner") in (0, "0")
            and float(r.get("buy_tax", 99)) <= 0.03 and float(r.get("sell_tax", 99)) <= 0.03), r


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


def main():
    state_path = os.path.join(BASE, "state.json")
    if os.path.exists(state_path):
        state = json.load(open(state_path))
    else:
        state = {"status": "watching", "round": 1, "wins": 0, "losses": 0, "pnl_bnb": 0.0}
        json.dump(state, open(state_path, "w"), indent=1)
    result = {"mode": state.get("status", "watching"), "round": state.get("round", 1),
              "checked_at": wib_now(),
              "trigger": False, "signal": None, "checks": {}, "reason": ""}
    try:
        p = ds_pair()
        if not p:
            raise RuntimeError("dexscreener_empty")
        price = float(p.get("priceUsd") or 0)
        m5 = float((p.get("priceChange") or {}).get("m5") or 0)
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
            checks["price_range"] = PRICE_LO <= price <= PRICE_HI
            checks["m5_positive"] = m5 > 0
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
            result["checks"] = checks
            failed = [k for k, v in checks.items() if not v]
            result["trigger"] = not failed
            result["reason"] = "SEMUA LEG LOLOS — trigger beli" if result["trigger"] else "gagal: " + ",".join(failed)
    except Exception as e:  # noqa: BLE001
        result["reason"] = f"check_error: {type(e).__name__}: {e}"
        result["trigger"] = False
    json.dump(result, open(os.path.join(BASE, "watch-result.json"), "w"), indent=1)
    log_line(f"mode={result['mode']} trigger={result['trigger']} signal={result['signal']} price={result.get('price')} m5={result.get('m5')} | {result['reason']}")
    print(json.dumps(result, indent=1))


if __name__ == "__main__":
    main()
