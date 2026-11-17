#!/usr/bin/env python3
"""Position watch for BACOT SCALPING flap rounds — READ-ONLY.

Reads ~/workspace/scalp-flap-new/positions.json, checks each OPEN position's
current value in WBNB via DexScreener, and writes position-watch-result.json
with signal SELL_TARGET / SELL_STOP / null. NEVER trades — the parent
executes the sell in the main chat when a signal is reported.
"""
import json
import os
import time
import urllib.request
from datetime import datetime, timedelta, timezone

BASE = os.path.dirname(os.path.abspath(__file__))
POSITIONS = os.path.join(BASE, "positions.json")
RESULT = os.path.join(BASE, "position-watch-result.json")
LOG = os.path.join(BASE, "position-watch-runs.log")


WIB = timezone(timedelta(hours=7))


def wib_now():
    # Jam WIB eksplisit, tidak tergantung TZ proses: cron jalan dengan TZ=UTC
    # sehingga time.localtime() mengembalikan jam UTC yang dulu salah dilabeli
    # "WIB" (perbaikan 2026-11-08 atas tap user "Ya, selidiki dan perbaiki").
    return datetime.now(timezone.utc).astimezone(WIB).strftime("%Y-%m-%d %H:%M:%S WIB")


def log_line(msg):
    ts = wib_now()
    with open(LOG, "a") as f:
        f.write(f"{ts} {msg}\n")


def get_json(url, timeout=20):
    # curl dulu (DexScreener kerap menutup koneksi urllib); fallback urllib.
    import subprocess
    for attempt in range(2):
        try:
            out = subprocess.run(
                ["curl", "-s", "--max-time", str(timeout), "-A", "Mozilla/5.0", url],
                capture_output=True, text=True, timeout=timeout + 5)
            if out.stdout.strip():
                return json.loads(out.stdout)
        except Exception:
            pass
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.load(r)
        except Exception:
            if attempt == 1:
                raise
            time.sleep(2)


def main():
    now = wib_now()
    result = {"checked_at": now, "positions": [], "signals": []}
    try:
        with open(POSITIONS) as f:
            data = json.load(f)
    except FileNotFoundError:
        result["error"] = "positions.json tidak ada"
        with open(RESULT, "w") as f:
            json.dump(result, f, indent=1)
        return 0
    for pos in data.get("positions", []):
        if pos.get("status") != "open":
            continue
        entry = {"round": pos.get("round"), "token": pos.get("token"),
                 "pair": pos.get("pair"), "signal": None}
        try:
            d = get_json(f"https://api.dexscreener.com/latest/dex/pairs/bsc/{pos['pair']}")
            pairs = d.get("pairs") or []
            if not pairs:
                entry["error"] = "pair tidak ditemukan di DexScreener"
                result["positions"].append(entry)
                continue
            p = pairs[0]
            price_native = float(p["priceNative"])
            price_usd = float(p["priceUsd"])
            value_wbnb = pos["amount"] * price_native
            entry.update({"price_native": price_native, "price_usd": price_usd,
                          "value_wbnb": round(value_wbnb, 6),
                          "target_wbnb": pos["target_wbnb"],
                          "stop_wbnb": pos["stop_wbnb"],
                          "m5": p.get("priceChange", {}).get("m5"),
                          "h1": p.get("priceChange", {}).get("h1"),
                          "liq_usd": (p.get("liquidity") or {}).get("usd")})
            if value_wbnb >= pos["target_wbnb"]:
                entry["signal"] = "SELL_TARGET"
            elif value_wbnb <= pos["stop_wbnb"]:
                entry["signal"] = "SELL_STOP"
            if entry["signal"]:
                result["signals"].append(entry)
        except Exception as e:  # noqa: BLE001 - fail-closed, never sell blind
            entry["error"] = f"gagal baca harga: {e}"
        result["positions"].append(entry)
    with open(RESULT, "w") as f:
        json.dump(result, f, indent=1)
    sigs = [f"R{s['round']} {s['token']} {s['signal']}" for s in result["signals"]]
    log_line("OK " + ("; ".join(sigs) if sigs else "tidak ada sinyal"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
