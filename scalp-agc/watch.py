#!/usr/bin/env python3
"""Bounce-watch AGC (BACOT SCALPING, kaki dip) — READ-ONLY.

Menilai trigger entry / exit tiap run dan menulis hasilnya ke watch-result.json.
TIDAK PERNAH sign, approve, swap, mint, atau mengirim tx apa pun. Eksekusi
beli/jual dilakukan parent di chat utama sesudah menerima laporan trigger.

Dipakai atas perintah user 2026-11-09 ~09:15 WIB ("Pakai trigger dip: beli saat
mantul dari bawah $400 rb"). Pola AGC: turun di bawah MCAP $400 rb lalu mantul
ke ~$450 rb. Beli di dip (~$388 rb) ke $450 rb = +16%, pas target scalp +15%;
beli di reclaim $400 rb ke $450 rb cuma +12,5%.

Entry trigger (status=watching, ronde 3 uji 10 ronde): MCAP < $400.000 (di dip),
m5 > 0 (mulai mantul), VOLUME beli 1 jam > VOLUME jual 1 jam (arus uang beli
dominan, dihitung dari event Swap on-chain via swap_flow.py), liq >= $150 rb,
saldo WBNB >= 0,0048 (all-in, keputusan user 2026-11-09 ~12:38 WIB, dikecilkan dari 0,0055),
GoPlus bersih (bukan honeypot/mintable/hidden-owner,
tax <= 3%), audit Binance bersih (code 000000, hasResult, riskLevel <= 1,
riskAlert null, tanpa hit RISK). Satu leg saja tidak terverifikasi (RPC/API
gagal) -> trigger False (fail-closed).

FALLBACK VOLUME 2026-11-09 ~19:50 WIB: swap_flow.py membaca event Swap via
Ankr getLogs — Ankr mati (kuota 0% s.d. 1 Des 2026) dan eth_getLogs RPC
publik semua -32005, jadi volume on-chain TIDAK bisa diukur. Bila swap_flow
gagal, gate volume memakai PROXY DexScreener (DICATAT sebagai proxy di
hasil, bukan pengukuran): priceChange h1 > 0 DAN volume h1 >= $10.000.
Alasan: di AMM, harga naik 1 jam + volume riil = arus beli bersih dominan
(harga hanya naik bila volume beli > volume jual). Proxy ini lebih lemah
dari pengukuran on-chain — bila Ankr hidup lagi, swap_flow kembali jadi
sumber utama dan proxy tidak dipakai.

CATATAN 2026-11-09 ~09:35 WIB (user): AGC adalah koin mining — reward Mode A/B
terus dicetak dan dijual miner, jadi JUMLAH transaksi jual secara struktural
mendominasi jumlah beli. Karena itu leg hitungan (buys h1 > sells h1) TIDAK
lagi jadi gate — hanya info. Yang di-gate adalah VOLUME: jual miner itu
banyak tapi kecil-kecil, sementara beli lebih sedikit tapi lebih besar
(contoh nyata: 143 beli vs 653 jual, tapi volume beli 26,55 WBNB > volume
jual 12,12 WBNB). Volume 1 jam di-cache max 10 menit supaya cron 5 menit
tidak membebani Ankr tiap run; bacaan cache dicatat di hasil.

LP burn: AGC pair 0xE56041CdDBB46DB01c67B7A62684F428a04Db8C6 terukur 46,25%
dead-burn (2026-11-09 ~09:14 WIB) — di bawah patokan 50% kaki binance-life.
Karena AGC token mapan (bukan koin baru) dan parent tetap cek audit + preview
segar sebelum beli, LP burn di sini DILAPORKAN sebagai info, bukan gate keras.

GoPlus: is_mintable=1 adalah SIFAT DASAR AGC (reward Mode A/B dicetak oleh
kontrak penjualan terverifikasi — bukan celah mint sembarangan), jadi leg
GoPlus di sini hanya meng-gate honeypot + hidden-owner + pajak (<= 3%);
is_mintable dicatat sebagai info, bukan penghalang.

Exit signal (status=holding): harga >= target -> SELL_TARGET; <= stop ->
SELL_STOP. Target/stop = entry x 1,30 / x 0,90 (all-in, SL −10% / TP +30%,
keputusan user 2026-11-09 ~09:44 WIB), ditulis parent
sesudah eksekusi; skrip ini hanya membaca state.json.
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

BASE = "/home/hatch/workspace/scalp-agc"
sys.path.insert(0, "/home/hatch/workspace")
from fast_exec.fast_rpc import race  # noqa: E402

TOKEN = "0x63dea5aCdbE1F27c968907D08610A5f0A0284bA6"
PAIR = "0xE56041CdDBB46DB01c67B7A62684F428a04Db8C6"
WBNB = "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c"
WALLET = "0xeb237F6f9FB4e826739F0954e9768914F28b2c2E"
DEAD = "0x000000000000000000000000000000000000dead"
SIZE_WBNB = 0.0048
MCAP_DIP = 400000.0
LIQ_MIN = 150000
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"}


WIB = timezone(timedelta(hours=7))


def wib_now():
    return datetime.now(timezone.utc).astimezone(WIB).strftime("%Y-%m-%d %H:%M:%S WIB")


def log_line(s):
    with open(os.path.join(BASE, "watch-runs.log"), "a") as f:
        f.write(wib_now() + " " + s + "\n")


def get_json(url, headers=None, data=None, timeout=15):
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


def h1_volumes():
    """Volume beli/jual 1 jam (WBNB) dari event Swap on-chain via swap_flow.py.

    Mengembalikan (buy_vol, sell_vol, cached). Cache max 10 menit di
    h1-flow.json supaya cron 5 menit tidak memanggil Ankr tiap run.
    Raise bila bacaan incomplete / ada failed_chunks / timeout / rc != 0
    -> fail-closed di pemanggil."""
    out = os.path.join(BASE, "h1-flow.json")
    if os.path.exists(out) and time.time() - os.path.getmtime(out) < 600:
        d = json.load(open(out))
        if not d.get("incomplete") and not d.get("failed_chunks"):
            return float(d["buy_volume"]), float(d["sell_volume"]), True
    cmd = [sys.executable, "/home/hatch/workspace/money-flow/swap_flow.py",
           "--pair", PAIR, "--hours", "1", "--json", out]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=150)
    if r.returncode != 0:
        raise RuntimeError(f"swap_flow rc={r.returncode}: {r.stderr.strip()[:120]}")
    d = json.load(open(out))
    if d.get("incomplete") or d.get("failed_chunks"):
        raise RuntimeError(f"swap_flow incomplete: {d.get('failed_chunks')}")
    return float(d["buy_volume"]), float(d["sell_volume"]), False


def goplus_ok():
    d = get_json(f"https://api.gopluslabs.io/api/v1/token_security/56?contract_addresses={TOKEN}")
    r = d["result"][TOKEN.lower()]
    # is_mintable TIDAK di-gate untuk AGC: reward Mode A/B memang dicetak oleh
    # kontrak penjualan (sifat dasar proyek, bukan celah). Gate: honeypot,
    # hidden-owner, dan pajak.
    return (r.get("is_honeypot") in (0, "0")
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
        state = {"status": "watching", "round": 3, "token": "AGC"}
        json.dump(state, open(state_path, "w"), indent=1)
    result = {"mode": state.get("status", "watching"), "round": state.get("round", 3),
              "checked_at": wib_now(),
              "trigger": False, "signal": None, "checks": {}, "reason": ""}
    try:
        p = ds_pair()
        if not p:
            raise RuntimeError("dexscreener_empty")
        price = float(p.get("priceUsd") or 0)
        mcap = float(p.get("marketCap") or p.get("fdv") or 0)
        m5 = float((p.get("priceChange") or {}).get("m5") or 0)
        txns = (p.get("txns") or {}).get("h1") or {}
        buys, sells = int(txns.get("buys") or 0), int(txns.get("sells") or 0)
        liq = float((p.get("liquidity") or {}).get("usd") or 0)
        result.update({"price": price, "mcap": mcap, "m5": m5,
                       "h1_buys": buys, "h1_sells": sells, "liq": liq})
        if state.get("status") == "holding":
            target, stop = state.get("target"), state.get("stop")
            if target and price >= target:
                result["signal"] = "SELL_TARGET"
            elif stop and price <= stop:
                result["signal"] = "SELL_STOP"
            result["reason"] = result["signal"] or f"holding, price {price} di antara stop {stop} dan target {target}"
        else:
            checks = {}
            checks["mcap_below_400k"] = mcap < MCAP_DIP
            checks["m5_positive"] = m5 > 0
            # Hitungan beli/jual HANYA info (koin mining: jual pasti dominan
            # secara jumlah) — bukan gate. Gate-nya volume, di bawah ini.
            result["buys_dominant"] = buys > sells
            checks["liq_ok"] = liq >= LIQ_MIN
            try:
                buy_vol, sell_vol, cached = h1_volumes()
                checks["buy_vol_dominant"] = buy_vol > sell_vol
                result["h1_buy_vol_wbnb"] = round(buy_vol, 4)
                result["h1_sell_vol_wbnb"] = round(sell_vol, 4)
                result["h1_vol_cached"] = cached
            except Exception as e:  # noqa: BLE001 - swap_flow mati, pakai proxy
                result["vol_error"] = f"swap_flow mati: {type(e).__name__}"
                try:
                    h1chg = float((p.get("priceChange") or {}).get("h1") or 0)
                    h1vol = float((p.get("volume") or {}).get("h1") or 0)
                    proxy_ok = h1chg > 0 and h1vol >= 10000
                    checks["buy_vol_dominant"] = proxy_ok
                    result["h1_vol_proxy"] = True
                    result["h1_vol_proxy_detail"] = (
                        f"h1chg={h1chg:.2f}% vol=${h1vol:,.0f}"
                    )
                except Exception:
                    checks["buy_vol_dominant"] = False
                    result["h1_vol_proxy"] = False
            try:
                wbnb = balance_of(WBNB, WALLET)
                checks["wbnb_ok"] = wbnb >= SIZE_WBNB
                result["wbnb_balance"] = wbnb
            except Exception as e:  # noqa: BLE001
                checks["wbnb_ok"] = False
                result["wbnb_error"] = f"unverified: {type(e).__name__}"
            try:
                burn = lp_burn_pct()
                result["lp_burn_pct"] = round(burn, 2)
            except Exception as e:  # noqa: BLE001
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
            result["reason"] = "SEMUA LEG LOLOS — trigger beli (dip mantul)" if result["trigger"] else "gagal: " + ",".join(failed)
    except Exception as e:  # noqa: BLE001
        result["reason"] = f"check_error: {type(e).__name__}: {e}"
        result["trigger"] = False
    json.dump(result, open(os.path.join(BASE, "watch-result.json"), "w"), indent=1)
    log_line(f"mode={result['mode']} trigger={result['trigger']} signal={result['signal']} price={result.get('price')} mcap={result.get('mcap')} m5={result.get('m5')} | {result['reason']}")
    print(json.dumps(result, indent=1))


if __name__ == "__main__":
    main()
