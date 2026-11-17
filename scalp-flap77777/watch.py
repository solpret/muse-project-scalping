#!/usr/bin/env python3
"""Flap 77777 launch watch — BACOT SCALPING discovery (READ-ONLY).

Polls slime.today launches (flap.sh), watches for NEW tokens whose contract
address ends in 77777, checks fast-scalp momentum + hard gates, and writes
qualifiers to watch-result.json for the cron worker to report.

NEVER signs, previews, mints, swaps, or trades. Parent re-verifies every
qualifier fresh and executes; this script only reports.

Gates (all required, fail-closed per candidate):
  - CA ends with 77777 (case-insensitive), launch flap.sh (slime.today list)
  - DexScreener PCS V2 WBNB pair: liq >= $15K, m5 > +5%, buys/sells h1 >= 1.5,
    vol h1 >= $1K (flag thin if < $5K)
  - GoPlus clean: not honeypot/mintable/proxy/hidden-owner, tax <= 2%/2%
  - Binance audit: riskLevel <= 1, no RISK hit (hasResult=false = no result)
  - Top free wallet <= 5% (Ankr holders, pair/dead/burn excluded)
  - LP: dead-burn >= 50% OR ~100% LP held by a lock contract (PinkLock etc.)
First run seeds `seen` with all current launches and reports nothing.
"""
import json
import os
import subprocess
import sys
import time
import uuid

BASE = "/home/hatch/workspace/scalp-flap77777"
STATE = os.path.join(BASE, "state.json")
RESULT = os.path.join(BASE, "watch-result.json")
LOG = os.path.join(BASE, "watch-runs.log")
SLIME_URL = "https://slime.today/api/launches?chain=56&limit=60"
ANKR_PY = "/home/hatch/workspace/custom-connectors/custom.ankr/bin/ankr.py"
DEAD = "0x000000000000000000000000000000000000dead"
ZERO = "0x0000000000000000000000000000000000000000"
LIQ_MIN = 15000
M5_MIN = 5.0
VOL_H1_MIN = 1000
VOL_THIN = 5000
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"}


def log_line(s):
    with open(LOG, "a") as f:
        f.write(time.strftime("%Y-%m-%d %H:%M:%S WIB ", time.localtime()) + s + "\n")


def get_json(url, headers=None, data=None, timeout=20):
    # curl, bukan urllib: urllib sering IncompleteRead di lingkungan ini.
    cmd = ["curl", "-sS", "-m", str(timeout)]
    for k, v in (headers or {}).items():
        cmd += ["-H", f"{k}: {v}"]
    if data is not None:
        cmd += ["-X", "POST", "-d", data if isinstance(data, str) else data.decode()]
    cmd.append(url)
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 5)
    if out.returncode != 0:
        raise RuntimeError(f"curl rc={out.returncode}: {out.stderr.strip()[:150]}")
    return json.loads(out.stdout)


def ankr_holders(ca, page_size=15):
    params = json.dumps({"blockchain": "bsc", "contractAddress": ca, "pageSize": page_size})
    out = subprocess.run(["python3", ANKR_PY, "raw", "ankr_getTokenHolders", params],
                         capture_output=True, text=True, timeout=60)
    if out.returncode != 0:
        raise RuntimeError(f"ankr rc={out.returncode}: {out.stderr.strip()[:150]}")
    return json.loads(out.stdout)["result"]


def load_state():
    if os.path.exists(STATE):
        with open(STATE) as f:
            return json.load(f)
    return {"seen": {}, "seeded": False}


def save_state(st):
    with open(STATE, "w") as f:
        json.dump(st, f, indent=1)


def main():
    os.makedirs(BASE, exist_ok=True)
    st = load_state()
    now = time.strftime("%Y-%m-%d %H:%M:%S WIB", time.localtime())
    result = {"checked_at": now, "new_launches": 0, "new_77777": [],
              "qualifiers": [], "seeded_this_run": False, "notes": []}
    try:
        data = get_json(SLIME_URL)
    except Exception as e:
        result["notes"].append(f"slime.today fetch gagal: {e}")
        log_line(f"FAIL slime.today: {e}")
        with open(RESULT, "w") as f:
            json.dump(result, f, indent=1)
        return 0
    items = data if isinstance(data, list) else data.get("launches", data.get("data", []))
    if not st.get("seeded"):
        for it in items:
            ca = (it.get("token") or it.get("contract") or it.get("address") or "").lower()
            if ca and ca not in st["seen"]:
                st["seen"][ca] = {"name": it.get("name"), "first_seen_wib": now,
                                  "suffix77777": ca.endswith("77777"), "reported": False}
        st["seeded"] = True
        result["seeded_this_run"] = True
        result["notes"].append(f"seed: {len(st['seen'])} launch tercatat, tanpa laporan")
        log_line(f"SEED {len(st['seen'])} launch")
        save_state(st)
        with open(RESULT, "w") as f:
            json.dump(result, f, indent=1)
        return 0

    new_items = []
    for it in items:
        ca = (it.get("token") or it.get("contract") or it.get("address") or "").lower()
        if ca and ca not in st["seen"]:
            new_items.append((ca, it.get("name")))
            st["seen"][ca] = {"name": it.get("name"), "first_seen_wib": now,
                              "suffix77777": ca.endswith("77777"), "reported": False}
    result["new_launches"] = len(new_items)
    for ca, name in new_items:
        if not ca.endswith("77777"):
            continue
        result["new_77777"].append({"ca": ca, "name": name})
        q = check_candidate(ca, name)
        if q.get("qualifies"):
            result["qualifiers"].append(q)
            st["seen"][ca]["reported"] = True
        else:
            result["notes"].append(f"{name or ca}: gugur — {q.get('fail_reason', '?')}")
    save_state(st)
    with open(RESULT, "w") as f:
        json.dump(result, f, indent=1)
    log_line(f"OK new={len(new_items)} new77777={len(result['new_77777'])} qual={len(result['qualifiers'])}")
    return 0


def check_candidate(ca, name):
    """Full gate check for one new 77777 CA. Returns dict with qualifies bool."""
    q = {"ca": ca, "name": name, "qualifies": False}
    # --- momentum via DexScreener ---
    try:
        d = get_json(f"https://api.dexscreener.com/latest/dex/tokens/{ca}")
        pairs = [p for p in d.get("pairs", [])
                 if p.get("dexId") == "pancakeswap"
                 and p.get("quoteToken", {}).get("symbol") == "WBNB"]
        if not pairs:
            q["fail_reason"] = "tidak ada pair PCS V2 WBNB"
            return q
        p = pairs[0]
        price = float(p["priceUsd"])
        m5 = float(p["priceChange"]["m5"])
        h1 = float(p["priceChange"]["h1"])
        liq = float(p["liquidity"]["usd"])
        vol_h1 = float(p["volume"]["h1"])
        buys = int(p["txns"]["h1"]["buys"])
        sells = int(p["txns"]["h1"]["sells"])
        pair = p["pairAddress"]
        fdv = float(p.get("fdv") or 0)
    except Exception as e:
        q["fail_reason"] = f"DexScreener gagal dibaca: {e}"
        return q
    q.update({"price": price, "m5": m5, "h1": h1, "liq": liq, "vol_h1": vol_h1,
              "buys_h1": buys, "sells_h1": sells, "pair": pair,
              "thin_volume": vol_h1 < VOL_THIN})
    if liq < LIQ_MIN:
        q["fail_reason"] = f"liq ${liq:,.0f} < ${LIQ_MIN:,}"
        return q
    if m5 <= M5_MIN:
        q["fail_reason"] = f"m5 {m5}% <= +{M5_MIN}%"
        return q
    if sells == 0 or buys / sells < 1.5:
        q["fail_reason"] = f"buys/sells h1 {buys}/{sells} < 1,5:1"
        return q
    if vol_h1 < VOL_H1_MIN:
        q["fail_reason"] = f"vol h1 ${vol_h1:,.0f} < ${VOL_H1_MIN:,}"
        return q
    # --- GoPlus ---
    try:
        r = get_json(f"https://api.gopluslabs.io/api/v1/token_security/56?contract_addresses={ca}")
        g = r["result"][ca.lower()]
        bad = [k for k in ("is_honeypot", "is_mintable", "is_proxy", "hidden_owner")
               if str(g.get(k)) not in ("0", "None", "")]
        buy_tax = float(g.get("buy_tax") or 0)
        sell_tax = float(g.get("sell_tax") or 0)
        if bad or buy_tax > 0.02 or sell_tax > 0.02:
            q["fail_reason"] = f"GoPlus tidak bersih: {bad} tax {buy_tax}/{sell_tax}"
            return q
        q["goplus_clean"] = True
        q["goplus_tax"] = f"{buy_tax}/{sell_tax}"
        lp_holders = g.get("lp_holders") or []
        dead_pct = 0.0
        lock_pct = 0.0
        lock_tag = None
        for h in lp_holders:
            pct = float(h.get("percent") or 0) * 100
            if (h.get("address") or "").lower() in (DEAD, ZERO):
                dead_pct += pct
            if h.get("is_locked"):
                lock_pct += pct
                lock_tag = h.get("tag") or h.get("address")
        q["lp_dead_pct_goplus"] = round(dead_pct, 2)
        q["lp_lock_pct_goplus"] = round(lock_pct, 2)
        q["lp_lock_tag"] = lock_tag
    except Exception as e:
        q["fail_reason"] = f"GoPlus gagal dibaca: {e}"
        return q
    # --- Binance audit ---
    try:
        body = json.dumps({"binanceChainId": "56", "contractAddress": ca,
                           "requestId": str(uuid.uuid4())})
        d = get_json("https://web3.binance.com/bapi/defi/v1/public/wallet-direct/security/token/audit",
                     headers={"Content-Type": "application/json", "source": "agent",
                              "Accept-Encoding": "identity", "User-Agent": "binance-web3/1.4"},
                     data=body)
        dd = d.get("data", {})
        hits = [h for h in (dd.get("riskDetails") or []) if h.get("isHit")]
        q["binance"] = {"riskLevel": dd.get("riskLevel"), "hasResult": dd.get("hasResult"),
                        "riskAlert": dd.get("riskAlert"), "risk_hits": hits}
        if d.get("code") != "000000" or not dd.get("hasResult"):
            q["fail_reason"] = "audit Binance tanpa hasil (bukan lolos)"
            return q
        if dd.get("riskLevel", 99) > 1 or dd.get("riskAlert") or hits:
            q["fail_reason"] = f"audit Binance riskLevel {dd.get('riskLevel')} / hit RISK"
            return q
    except Exception as e:
        q["fail_reason"] = f"audit Binance gagal dibaca: {e}"
        return q
    # --- top free wallet via Ankr ---
    try:
        res = ankr_holders(ca)
        holders = res.get("holders", [])
        total = fdv / price if price and fdv else 0
        top_free = 0.0
        top_addr = None
        for h in holders:
            addr = (h.get("holderAddress") or "").lower()
            if addr in (pair.lower(), DEAD, ZERO):
                continue
            bal = float(h.get("balance") or 0)
            pct = (bal / total * 100) if total else 0
            if pct > top_free:
                top_free, top_addr = pct, h.get("holderAddress")
        q["top_free_pct"] = round(top_free, 2)
        q["top_free_addr"] = top_addr
        if top_free > 5:
            q["fail_reason"] = f"top wallet bebas {top_free:.2f}% > 5%"
            return q
    except Exception as e:
        q["fail_reason"] = f"Ankr holders gagal dibaca: {e}"
        return q
    # --- LP distribution via Ankr (pair = LP token) ---
    try:
        res = ankr_holders(pair)
        holders = res.get("holders", [])
        total_lp = sum(float(h.get("balance") or 0) for h in holders)
        dead_lp = sum(float(h.get("balance") or 0) for h in holders
                      if (h.get("holderAddress") or "").lower() in (DEAD, ZERO))
        burn_pct = (dead_lp / total_lp * 100) if total_lp else 0
        q["lp_burn_pct_ankr"] = round(burn_pct, 2)
    except Exception:
        q["lp_burn_pct_ankr"] = None
    burn_ok = (q.get("lp_burn_pct_ankr") or 0) >= 50 or q.get("lp_dead_pct_goplus", 0) >= 50
    lock_ok = q.get("lp_lock_pct_goplus", 0) >= 99
    if not (burn_ok or lock_ok):
        q["fail_reason"] = (f"LP burn {q.get('lp_burn_pct_ankr')}% / GoPlus dead "
                            f"{q.get('lp_dead_pct_goplus')}% / lock {q.get('lp_lock_pct_goplus')}% — "
                            f"tidak memenuhi burn ≥50% atau lock ~100%")
        return q
    q["lp_gate"] = "burn" if burn_ok else "lock"
    q["qualifies"] = True
    return q


if __name__ == "__main__":
    sys.exit(main())
