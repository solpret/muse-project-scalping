#!/usr/bin/env python3
"""Flap NEW launch watch — BACOT SCALPING discovery (READ-ONLY).

Pelengkap cron `flap-77777-watch` (30 mnt) yang sudah ada — ini TIDAK
menggantikannya. Pantauan ini menyaring SEMUA launch flap.sh baru (bukan
hanya CA 77777) dari dua sumber: slime.today DAN GeckoTerminal new_pools
(GT menangkap koin yang belum masuk indeks slime.today, contoh: 悟空, FLX).

NEVER signs, previews, mints, swaps, or trades. Parent re-verifies every
qualifier fresh and executes; this script only reports.

Kaki MID-CAP (amendment 2026-11-07 ~19:40 WIB): GeckoTerminal trending
pools hlm 1-2 + silang DexScreener; filter FDV $1jt-$100jt, volume h1
>= $50rb, m5 > 0, buys h1 > sells h1, liq >= $15rb, pair quote WBNB
atau rute WBNB likuid; deep gate sama seperti flap (tanpa batas umur,
lock LP >= 50%); konfirmasi dua sighting + pending-confirm 2 jam;
qualifier dilaporkan sekali dengan kind "midcap".

Kaki BURN (gabung 2026-11-07 ~20:20 WIB, perintah user "Burn gabungin
aja biar gak misah-misah yang mode scalping"): Burn Agent sebagai
named-coin leg di hunt terpadu — bukan launch baru, bukan mid-cap.
Trigger dari SETUP.md burn-scalp: Jalur A breakout MCAP > $400K
(m5 > 0, buys h1 > sells h1) atau Jalur B pulih dari bawah ke band
$340-360K lalu m5 flip >= +0,5% dengan buys > sells. Gate (b)
vol24/liq < 5x tetap berlaku (tidak pernah dilonggarkan). Deep gate
sama seperti flap via check_candidate(named=True) — tanpa batas umur
pair dan tanpa filter FDV/vol mid-cap. Konfirmasi dua sighting +
pending-confirm 2 jam; qualifier dilaporkan sekali dengan kind "burn".
Burn TIDAK pernah jadi "fail" permanen — selalu dipantau tiap run.
Setelah ronde Burn diputuskan, parent reset burn_watch disposition ke
"watching" supaya kaki bisa memantau lagi.

Kaki SLIME (gabung 2026-11-07 ~20:57 WIB, ralat user "masukan saja
kalau memang gate nya sudah lolos"): SLIME sebagai named-coin leg —
bukan launch baru, MCAP ~$113 rb di bawah floor mid-cap $1 jt.
Trigger "bounce": m5 > 0, buys h1 > sells h1, liq >= $15 rb. Gate (b)
vol24/liq < 5x tetap berlaku. Deep gate sama seperti flap via
check_candidate(named=True). Konfirmasi dua sighting +
pending-confirm 2 jam; qualifier dilaporkan sekali dengan kind "slime".
SLIME TIDAK pernah jadi "fail" permanen — selalu dipantau tiap run.
Setelah ronde SLIME diputuskan, parent reset slime_watch disposition
ke "watching" supaya kaki bisa memantau lagi. Pantauan slime-watch
30 mnt (read-only, monitor-only) tetap jalan terpisah.

Fast filter per run (dari data sumber):
  - pair age < 30 hari (720 jam), liq >= $15K, m5 > 0, buys h1 > sells h1
  - pair PCS V2, quote WBNB
Deep gate penuh (fail-closed per kandidat):
  - GoPlus bersih: not honeypot/mintable/proxy/hidden-owner, tax <= 2%/2%
  - Binance audit: riskLevel <= 1, no RISK hit (hasResult=false = no result,
    BUKAN lolos)
  - Top free wallet <= 5% (Ankr holders, pair/dead/burn excluded)
  - LP: dead-burn >= 50% OR ~100% LP held by a lock contract
  - ANTI INFLASI SALDO (ditambah 2026-11-07 pasca STOCKMEMES): reserve token
    di pair TIDAK BOLEH melebihi totalSupply — reserve > supply = saldo
    pair digelembungkan tersembunyi (mint yang tidak update totalSupply),
    gagal langsung. GoPlus is_mintable=0 TIDAK menangkap pola ini.
  - KONFIRMASI DUA SIGHTING (ditambah 2026-11-07 pasca STOCKMEMES):
    kandidat yang lolos deep gate TIDAK langsung jadi qualifier — ia masuk
    "pending-confirm" dengan snapshot reserve pair. Baru di run berikut
    (15 mnt kemudian), bila lolos deep gate LAGI dan reserve pair stabil
    (tumbuh <= 2x dan tetap <= totalSupply), ia jadi qualifier. Serangan
    inflasi terjadi di menit-menit pertama sesudah launch; jeda ini
    menyaringnya. Trade-off: entry tertunda ~15 mnt.
First run seeds `seen` with all current launches and reports nothing.
Kandidat yang gagal karena fetch error ditandai retry (maks 3x) lalu fail.
"""
import json
import os
import subprocess
import sys
import time
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone

BASE = "/home/hatch/workspace/scalp-flap-new"
STATE = os.path.join(BASE, "state.json")
RESULT = os.path.join(BASE, "watch-result.json")
LOG = os.path.join(BASE, "watch-runs.log")
BSC_RPC = "https://bsc-dataseed1.bnbchain.org"
RESERVE_GROWTH_MAX = 2.0  # reserve pair boleh tumbuh maks 2x antar sighting
CONFIRM_MAX_AGE_S = 2 * 3600  # pending-confirm kedaluwarsa sesudah 2 jam
SLIME_URL = "https://slime.today/api/launches?chain=56&limit=60"
GT_URL = "https://api.geckoterminal.com/api/v2/networks/bsc/new_pools?page={page}"
GT_TRENDING_URL = "https://api.geckoterminal.com/api/v2/networks/bsc/trending_pools?page={page}"
MIDCAP_FDV_MIN = 1_000_000
MIDCAP_FDV_MAX = 100_000_000
MIDCAP_VOL_H1_MIN = 50_000
MAX_DEEP_MIDCAP_PER_RUN = 8
ANKR_PY = "/home/hatch/workspace/custom-connectors/custom.ankr/bin/ankr.py"
DEAD = "0x000000000000000000000000000000000000dead"
ZERO = "0x0000000000000000000000000000000000000000"
WBNB = "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c"
LIQ_MIN = 15000
AGE_MAX_H = 720  # 30 hari — diubah 2026-11-07 ~22:00 WIB atas perintah user (sebelumnya 48 jam)
MAX_DEEP_PER_RUN = 8
MAX_RETRY = 3
# --- Kaki BURN (gabung 2026-11-07 ~20:20 WIB) ---
BURN_CA = "0x7de1606230aa70802caecf1a3e90039a6265b1c4"
BURN_PAIR = "0x53895464c524c6fDD82aD5189bF92D9b798c6a51"
BURN_BREAKOUT_MCAP = 400_000
BURN_BAND_LO = 340_000
BURN_BAND_HI = 360_000
BURN_VOL_LIQ_MAX = 5.0
BURN_M5_FLIP_MIN = 0.5
SLIME_CA = "0x8351311fE78e9045A5EeBcb11dB9D700658C7777"
SLIME_PAIR = "0x5D506893c5a4f44263B451b9a1065888Ea07740c"
SLIME_VOL_LIQ_MAX = 5.0
SLIME_LIQ_MIN = 15_000
# --- Cross-check money-flow volume (swap_flow.py, sambung 2026-11-07 ~22:40 WIB,
# perintah user "Sambungkan ke hunt untuk cross-check") ---
# swap_flow.py membaca event Swap PCS V2 on-chain dan menghitung net buy
# VOLUME (bukan net jumlah transaksi). Cross-check ini BUKAN pengganti gate
# dan BUKAN gate baru yang menggugurkan permanen: ia hanya jalan di tahap
# konfirmasi kedua (pending-confirm -> qualifier), untuk flap, mid-cap,
# BURN, dan SLIME. Aturan: hasil lengkap + net volume <= 0 -> qualifier
# DITAHAN (tetap pending-confirm sampai kedaluwarsa 2 jam, bisa lolos di
# run berikut bila volume berbalik positif); hasil tidak lengkap / tool
# gagal / timeout / budget habis -> FAIL-OPEN, qualifier tetap jalan
# dengan flag flow_check status "unknown" (infra tidak boleh memblokir
# trade). Maks 2 cek per run supaya cron 5 mnt tidak overrun.
SWAP_FLOW_PY = "/home/hatch/workspace/money-flow/swap_flow.py"
SWAP_FLOW_TIMEOUT_S = 120
SWAP_FLOW_MAX_PER_RUN = 2
_FLOW_STATE = {"done": 0}
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"}


WIB = timezone(timedelta(hours=7))


def wib_now():
    # Jam WIB eksplisit, tidak tergantung TZ proses: cron jalan dengan TZ=UTC
    # sehingga time.localtime() mengembalikan jam UTC yang dulu salah dilabeli
    # "WIB" (perbaikan 2026-11-08 atas tap user "Ya, selidiki dan perbaiki").
    # Hanya label jam yang diubah; hitungan epoch (time.time()/mktime) tidak
    # disentuh.
    return datetime.now(timezone.utc).astimezone(WIB).strftime("%Y-%m-%d %H:%M:%S WIB")


def log_line(s):
    with open(LOG, "a") as f:
        f.write(wib_now() + " " + s + "\n")


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


def rpc_call(to, data):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "eth_call",
                       "params": [{"to": to, "data": data}, "latest"]}).encode()
    req = urllib.request.Request(BSC_RPC, data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)["result"]


def onchain_reserve_supply(pair, ca):
    """Reserve token di pair + totalSupply, dalam satuan token.

    Mengembalikan (reserve, supply). Fail-closed: exception bila RPC gagal.
    token0 pair = alamat yang lebih kecil (PCS V2 mengurutkan begitu).
    """
    res = rpc_call(pair, "0x0902f1ac")  # getReserves
    r0 = int(res[2:66], 16)
    r1 = int(res[66:130], 16)
    dec = int(rpc_call(ca, "0x313ce567"), 16)  # decimals
    ts = int(rpc_call(ca, "0x18160ddd"), 16)   # totalSupply
    reserve_raw = r0 if ca.lower() < WBNB.lower() else r1
    scale = 10 ** dec
    return reserve_raw / scale, ts / scale


def run_swap_flow(pair):
    """Jalankan swap_flow.py 1 jam untuk satu pair; kembalikan dict ringkas.

    status "ok" = data lengkap, net_volume bisa dipakai sebagai cross-check.
    status "unknown" = tool gagal / timeout / data tidak lengkap / pair
    bukan V2 — pemanggil harus FAIL-OPEN (jangan blokir qualifier).
    status "skipped_budget" = sudah 2 cek di run ini.
    """
    if _FLOW_STATE["done"] >= SWAP_FLOW_MAX_PER_RUN:
        return {"status": "skipped_budget",
                "reason": f"max {SWAP_FLOW_MAX_PER_RUN} cek flow/run tercapai"}
    _FLOW_STATE["done"] += 1
    out_path = f"/tmp/swap_flow_watch_{pair.lower()}_{int(time.time())}.json"
    try:
        r = subprocess.run(
            [sys.executable, SWAP_FLOW_PY, "--pair", pair,
             "--hours", "1", "--json", out_path],
            capture_output=True, text=True, timeout=SWAP_FLOW_TIMEOUT_S)
        if r.returncode != 0:
            return {"status": "unknown",
                    "reason": f"swap_flow exit {r.returncode}: "
                              f"{(r.stdout + r.stderr).strip()[:200]}"}
        with open(out_path) as f:
            d = json.load(f)
        if d.get("error"):
            return {"status": "unknown", "reason": str(d["error"])[:200]}
        if d.get("incomplete"):
            return {"status": "unknown",
                    "reason": f"data tidak lengkap "
                              f"({len(d.get('failed_chunks') or [])} chunk gagal)",
                    "incomplete": True,
                    "buy_count": d.get("buy_count"),
                    "sell_count": d.get("sell_count")}
        return {"status": "ok",
                "quote": d.get("quote"),
                "net_volume": d.get("net_volume"),
                "net_volume_usd": d.get("net_volume_usd"),
                "buy_volume": d.get("buy_volume"),
                "sell_volume": d.get("sell_volume"),
                "buy_count": d.get("buy_count"),
                "sell_count": d.get("sell_count"),
                "wash_suspect_volume_usd": d.get("wash_suspect_volume_usd"),
                "unique_traders": d.get("unique_traders"),
                "elapsed_s": d.get("elapsed_s")}
    except subprocess.TimeoutExpired:
        return {"status": "unknown",
                "reason": f"swap_flow timeout {SWAP_FLOW_TIMEOUT_S} dtk"}
    except Exception as e:  # noqa: BLE001
        return {"status": "unknown", "reason": str(e)[:200]}
    finally:
        try:
            os.remove(out_path)
        except OSError:
            pass


def flow_check(q):
    """Cross-check volume untuk satu qualifier (dict q, sudah lolos gate).

    Menempelkan q["flow_check"] dan mengembalikan True bila qualifier
    boleh lanjut, False bila harus DITAHAN (data lengkap + net <= 0).
    Fail-open: status apa pun selain "ok" -> lanjut dengan flag unknown.
    """
    fc = run_swap_flow(q["pair"])
    q["flow_check"] = fc
    if fc.get("status") != "ok":
        return True
    net = fc.get("net_volume")
    if net is None:
        return True
    return net > 0


def load_state():
    if os.path.exists(STATE):
        with open(STATE) as f:
            return json.load(f)
    return {"seen": {}, "seeded": False}


def save_state(st):
    with open(STATE, "w") as f:
        json.dump(st, f, indent=1)


def ds_pair(ca, pair_hint=None):
    """Pair PCS V2 WBNB dari DexScreener (latest/dex/tokens).

    Bila pair_hint (alamat pair dari GeckoTerminal) diberikan tapi tidak
    ditemukan di DexScreener -> kembalikan None (jangan fallback ke pair
    lain: pair yang salah = evaluasi yang salah; DS mungkin belum mengindeks
    pool itu). Tanpa hint, pakai pair PCS WBNB pertama.
    """
    d = get_json(f"https://api.dexscreener.com/latest/dex/tokens/{ca}")
    pairs = [p for p in d.get("pairs", [])
             if p.get("dexId") == "pancakeswap"
             and p.get("quoteToken", {}).get("symbol") == "WBNB"]
    if not pairs:
        return None
    if pair_hint:
        for p in pairs:
            if p.get("pairAddress", "").lower() == pair_hint.lower():
                return p
        return None
    return pairs[0]


def collect_candidates():
    """Kumpulkan launch baru dari slime.today + GeckoTerminal.

    Mengembalikan dict ca -> {name, pair, source, age_h, liq, m5, buys, sells, price}.
    """
    cands = {}

    # --- slime.today (flap.sh launches) ---
    try:
        data = get_json(SLIME_URL)
        items = data if isinstance(data, list) else data.get("launches", data.get("data", []))
        for it in items:
            ca = (it.get("token") or it.get("contract") or it.get("address") or "").lower()
            if not ca or ca in cands:
                continue
            cands[ca] = {"name": it.get("name"), "pair": None, "source": "slime.today",
                         "age_h": None, "liq": None, "m5": None,
                         "buys": None, "sells": None, "price": None}
    except Exception as e:  # noqa: BLE001
        log_line(f"WARN slime.today gagal: {e}")

    # --- GeckoTerminal new_pools (halaman 1-4) ---
    # Hal. 3-4 ditambah 2026-11-07 ~22:00 WIB bersama pelonggaran umur ke
    # 30 hari — supaya pool yang berumur hari-minggu benar-benar masuk
    # corong, bukan cuma yang terbaru di hal. 1-2.
    for page in (1, 2, 3, 4):
        try:
            d = get_json(GT_URL.format(page=page))
        except Exception as e:  # noqa: BLE001
            log_line(f"WARN geckoterminal p{page} gagal: {e}")
            continue
        for item in d.get("data", []):
            rel = item.get("relationships", {})
            base = (rel.get("base_token") or {}).get("data", {}).get("id", "")
            quote = (rel.get("quote_token") or {}).get("data", {}).get("id", "")
            if not base.startswith("bsc_") or quote.lower() != "bsc_" + WBNB:
                continue
            ca = base[4:].lower()
            dex = (rel.get("dex") or {}).get("data", {}).get("id", "")
            if dex and "pancakeswap" not in dex.lower():
                continue
            a = item.get("attributes", {})
            pair = item.get("id", "")[4:] if item.get("id", "").startswith("bsc_") else None
            try:
                created = a.get("pool_created_at")
                age_h = (time.time() - time.mktime(time.strptime(created, "%Y-%m-%dT%H:%M:%SZ"))) / 3600 if created else None
            except Exception:  # noqa: BLE001
                age_h = None
            h1 = (a.get("transactions") or {}).get("h1") or {}
            chg = a.get("price_change_percentage") or {}
            if ca in cands:
                cands[ca]["source"] = "slime.today+geckoterminal"
                cands[ca]["pair"] = cands[ca].get("pair") or pair
                continue
            cands[ca] = {"name": a.get("name"), "pair": pair, "source": "geckoterminal",
                         "age_h": age_h, "liq": float(a.get("reserve_in_usd") or 0),
                         "m5": float(chg.get("m5") or 0),
                         "buys": int(h1.get("buys") or 0), "sells": int(h1.get("sells") or 0),
                         "price": float(a.get("base_token_price_usd") or 0)}
    return cands


def fast_filter(ca, c):
    """Fast filter; untuk entri slime.today, isi angka dari DexScreener."""
    if c.get("liq") is None:
        p = ds_pair(ca, c.get("pair"))
        if not p:
            return False, "tidak ada pair PCS V2 WBNB"
        c["pair"] = p.get("pairAddress")
        c["price"] = float(p.get("priceUsd") or 0)
        c["m5"] = float((p.get("priceChange") or {}).get("m5") or 0)
        c["liq"] = float((p.get("liquidity") or {}).get("usd") or 0)
        h1 = (p.get("txns") or {}).get("h1") or {}
        c["buys"] = int(h1.get("buys") or 0)
        c["sells"] = int(h1.get("sells") or 0)
        created = p.get("pairCreatedAt")
        c["age_h"] = (time.time() * 1000 - created) / 3600000 if created else None
    if c.get("age_h") is not None and c["age_h"] > AGE_MAX_H:
        return False, f"umur {c['age_h']:.1f} jam > {AGE_MAX_H} jam"
    if (c.get("liq") or 0) < LIQ_MIN:
        return False, f"liq ${c.get('liq') or 0:,.0f} < ${LIQ_MIN:,}"
    if (c.get("m5") or 0) <= 0:
        return False, f"m5 {c.get('m5')}% <= 0"
    if (c.get("buys") or 0) <= (c.get("sells") or 0):
        return False, f"buys/sells h1 {c.get('buys')}/{c.get('sells')} tidak dominan"
    return True, ""


def collect_midcap_candidates():
    """Kumpulkan pool trending BSC dari GeckoTerminal (halaman 1-2).

    Mengembalikan dict ca -> {name, pair, quote_wbnb, source, liq, m5,
    buys, sells, price, fdv, vol_h1}. Angka mentah dari GT; silang
    DexScreener terjadi di midcap_fast_filter.
    """
    cands = {}
    for page in (1, 2):
        try:
            d = get_json(GT_TRENDING_URL.format(page=page))
        except Exception as e:  # noqa: BLE001
            log_line(f"WARN geckoterminal trending p{page} gagal: {e}")
            continue
        for item in d.get("data", []):
            rel = item.get("relationships", {})
            base = (rel.get("base_token") or {}).get("data", {}).get("id", "")
            quote = (rel.get("quote_token") or {}).get("data", {}).get("id", "")
            if not base.startswith("bsc_"):
                continue
            ca = base[4:].lower()
            dex = (rel.get("dex") or {}).get("data", {}).get("id", "")
            if dex and "pancakeswap" not in dex.lower():
                continue
            a = item.get("attributes", {})
            pair = item.get("id", "")[4:] if item.get("id", "").startswith("bsc_") else None
            h1 = (a.get("transactions") or {}).get("h1") or {}
            chg = a.get("price_change_percentage") or {}
            vol = a.get("volume_usd") or {}
            if ca in cands:
                continue
            cands[ca] = {"name": a.get("name"), "pair": pair,
                         "quote_wbnb": quote.lower() == "bsc_" + WBNB,
                         "source": "geckoterminal-trending",
                         "liq": float(a.get("reserve_in_usd") or 0),
                         "m5": float(chg.get("m5") or 0),
                         "buys": int(h1.get("buys") or 0), "sells": int(h1.get("sells") or 0),
                         "price": float(a.get("base_token_price_usd") or 0),
                         "fdv": float(a.get("fdv_usd") or 0),
                         "vol_h1": float(vol.get("h1") or 0)}
    return cands


def ds_pair_by_address(pair):
    """Satu pair DexScreener dari /latest/dex/pairs/bsc/<pair> (silang GT)."""
    d = get_json(f"https://api.dexscreener.com/latest/dex/pairs/bsc/{pair}")
    pairs = [p for p in d.get("pairs", [])
             if p.get("dexId") == "pancakeswap"
             and p.get("quoteToken", {}).get("symbol") == "WBNB"]
    return pairs[0] if pairs else None


def midcap_fast_filter(ca, c):
    """Fast filter mid-cap: silang DexScreener + FDV/vol/momentum.

    Pair utama bukan WBNB -> cari rute WBNB likuid via ds_pair (PCS V2
    WBNB); tanpa rute WBNB yang bisa dieksekusi baw, bukan qualifier.
    """
    try:
        p = ds_pair_by_address(c["pair"]) if c.get("pair") else None
    except Exception as e:  # noqa: BLE001
        return False, f"DexScreener silang gagal dibaca: {e}"
    if p is None:
        if not c.get("quote_wbnb"):
            # Pair GT bukan WBNB dan tidak terindeks DS sebagai pair WBNB:
            # cari pair PCS V2 WBNB lain untuk token yang sama (rute).
            try:
                p = ds_pair(ca)
            except Exception as e:  # noqa: BLE001
                return False, f"DexScreener rute WBNB gagal dibaca: {e}"
            if not p:
                return False, "pair utama bukan WBNB dan tanpa rute WBNB likuid"
            c["route"] = f"rute WBNB via {p.get('pairAddress')}"
        else:
            return False, "pair (GT) belum terindeks DexScreener"
    c["pair"] = p.get("pairAddress")
    c["price"] = float(p.get("priceUsd") or 0)
    c["m5"] = float((p.get("priceChange") or {}).get("m5") or 0)
    c["liq"] = float((p.get("liquidity") or {}).get("usd") or 0)
    c["fdv"] = float(p.get("fdv") or 0)
    c["vol_h1"] = float((p.get("volume") or {}).get("h1") or 0)
    h1 = (p.get("txns") or {}).get("h1") or {}
    c["buys"] = int(h1.get("buys") or 0)
    c["sells"] = int(h1.get("sells") or 0)
    if (c.get("liq") or 0) < LIQ_MIN:
        return False, f"liq ${c.get('liq') or 0:,.0f} < ${LIQ_MIN:,}"
    if not (MIDCAP_FDV_MIN <= (c.get("fdv") or 0) <= MIDCAP_FDV_MAX):
        return False, f"FDV ${c.get('fdv') or 0:,.0f} di luar ${MIDCAP_FDV_MIN:,}-${MIDCAP_FDV_MAX:,}"
    if (c.get("vol_h1") or 0) < MIDCAP_VOL_H1_MIN:
        return False, f"volume h1 ${c.get('vol_h1') or 0:,.0f} < ${MIDCAP_VOL_H1_MIN:,}"
    if (c.get("m5") or 0) <= 0:
        return False, f"m5 {c.get('m5')}% <= 0"
    if (c.get("buys") or 0) <= (c.get("sells") or 0):
        return False, f"buys/sells h1 {c.get('buys')}/{c.get('sells')} tidak dominan"
    return True, ""


def check_candidate(ca, c, midcap=False, named=False, holder_source="ankr"):
    """Deep gate penuh. Mengembalikan dict; qualifies True bila lolos semua.

    midcap=True: tanpa batas umur pair, FDV $1jt-$100jt, volume h1 >= $50rb,
    dan lock LP >= 50% (bukan ~100% seperti flap).
    named=True: koin bernama (kaki BURN) — tanpa batas umur pair dan tanpa
    filter FDV/vol mid-cap; gate lainnya identik dengan flap.
    """
    q = {"ca": ca, "name": c.get("name"), "source": c.get("source"),
         "pair": c.get("pair"), "qualifies": False,
         "kind": "burn" if named else ("midcap" if midcap else "flap"),
         "price": c.get("price"), "m5": c.get("m5"), "liq": c.get("liq"),
         "buys_h1": c.get("buys"), "sells_h1": c.get("sells"), "age_h": c.get("age_h")}
    # --- DexScreener segar (harga/liq/m5 saat gate) ---
    try:
        p = ds_pair(ca, c.get("pair"))
        if not p:
            q["fail_reason"] = "pair (GT) belum terindeks DexScreener"
            q["retry"] = True
            return q
        q["pair"] = p.get("pairAddress")
        q["price"] = float(p.get("priceUsd") or 0)
        q["m5"] = float((p.get("priceChange") or {}).get("m5") or 0)
        q["h1"] = float((p.get("priceChange") or {}).get("h1") or 0)
        q["liq"] = float((p.get("liquidity") or {}).get("usd") or 0)
        h1 = (p.get("txns") or {}).get("h1") or {}
        q["buys_h1"] = int(h1.get("buys") or 0)
        q["sells_h1"] = int(h1.get("sells") or 0)
        q["fdv"] = float(p.get("fdv") or 0)
        q["vol_h1"] = float((p.get("volume") or {}).get("h1") or 0)
        created = p.get("pairCreatedAt")
        q["age_h"] = (time.time() * 1000 - created) / 3600000 if created else None
    except Exception as e:  # noqa: BLE001
        q["fail_reason"] = f"DexScreener gagal dibaca: {e}"
        q["retry"] = True
        return q
    if midcap:
        if not (MIDCAP_FDV_MIN <= q["fdv"] <= MIDCAP_FDV_MAX):
            q["fail_reason"] = f"FDV ${q['fdv']:,.0f} di luar ${MIDCAP_FDV_MIN:,}-${MIDCAP_FDV_MAX:,}"
            return q
        if q["vol_h1"] < MIDCAP_VOL_H1_MIN:
            q["fail_reason"] = f"volume h1 ${q['vol_h1']:,.0f} < ${MIDCAP_VOL_H1_MIN:,}"
            return q
    if q["liq"] < LIQ_MIN:
        q["fail_reason"] = f"liq ${q['liq']:,.0f} < ${LIQ_MIN:,}"
        return q
    if q["m5"] <= 0:
        q["fail_reason"] = f"m5 {q['m5']}% <= 0"
        return q
    if q["buys_h1"] <= q["sells_h1"]:
        q["fail_reason"] = f"buys/sells h1 {q['buys_h1']}/{q['sells_h1']} tidak dominan"
        return q
    if q.get("age_h") is not None and q["age_h"] > AGE_MAX_H and not midcap and not named:
        q["fail_reason"] = f"umur {q['age_h']:.1f} jam > {AGE_MAX_H} jam"
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
    except Exception as e:  # noqa: BLE001
        q["fail_reason"] = f"GoPlus gagal dibaca: {e}"
        q["retry"] = True
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
            q["retry"] = True
            return q
        if dd.get("riskLevel", 99) > 1 or dd.get("riskAlert") or hits:
            q["fail_reason"] = f"audit Binance riskLevel {dd.get('riskLevel')} / hit RISK"
            return q
    except Exception as e:  # noqa: BLE001
        q["fail_reason"] = f"audit Binance gagal dibaca: {e}"
        q["retry"] = True
        return q
    # --- top free wallet: Ankr (default) atau GoPlus (kaki SLIME) ---
    # Ankr getLogs mati (kuota 0% s.d. 1 Des 2026) — baca holder via Ankr
    # selalu gagal dan menggugurkan kandidat. Kaki SLIME dialihkan ke
    # GoPlus 2026-11-09 ~20:20 WIB (tap user "Ya, alihkan ke GoPlus"):
    # daftar holders dari respons token_security yang SUDAH dibaca di
    # seksi GoPlus di atas (tanpa panggilan baru). Ambang TETAP 5%,
    # exclude pair + DEAD + ZERO sama seperti jalur Ankr. GoPlus hanya
    # mengembalikan top-10 holder — pendekatan yang sama dengan
    # holder_check.py DEGEN (analyze_holders_goplus).
    try:
        if holder_source == "goplus":
            holders = g.get("holders") or []
            if not holders:
                raise ValueError("daftar holders GoPlus kosong")
            top_free = 0.0
            top_addr = None
            for h in holders:
                addr = (h.get("address") or "").lower()
                if addr in (q["pair"].lower(), DEAD, ZERO):
                    continue
                # Holder terkunci (mis. kontrak PinkLock — GoPlus
                # is_locked=1, tag PinkLock02) BUKAN wallet bebas: tidak
                # bisa jual, jadi tidak dihitung. Tanpa ini, SLIME gugur
                # di 6,78% padahal wallet bebas terbesarnya 2,18%
                # (terverifikasi live 2026-11-09 ~20:25 WIB).
                if str(h.get("is_locked")) not in ("0", "None", ""):
                    continue
                pct = float(h.get("percent") or 0) * 100
                if pct > top_free:
                    top_free, top_addr = pct, h.get("address")
            q["top_free_pct"] = round(top_free, 2)
            q["top_free_addr"] = top_addr
            q["top_free_source"] = "goplus"
            if top_free > 5:
                q["fail_reason"] = f"top wallet bebas {top_free:.2f}% > 5% (GoPlus)"
                return q
        else:
            res = ankr_holders(ca)
            holders = res.get("holders", [])
            total = q["fdv"] / q["price"] if q.get("price") and q.get("fdv") else 0
            top_free = 0.0
            top_addr = None
            for h in holders:
                addr = (h.get("holderAddress") or "").lower()
                if addr in (q["pair"].lower(), DEAD, ZERO):
                    continue
                bal = float(h.get("balance") or 0)
                pct = (bal / total * 100) if total else 0
                if pct > top_free:
                    top_free, top_addr = pct, h.get("holderAddress")
            q["top_free_pct"] = round(top_free, 2)
            q["top_free_addr"] = top_addr
            q["top_free_source"] = "ankr"
            if top_free > 5:
                q["fail_reason"] = f"top wallet bebas {top_free:.2f}% > 5%"
                return q
    except Exception as e:  # noqa: BLE001
        q["fail_reason"] = f"holders gagal dibaca ({holder_source}): {e}"
        q["retry"] = True
        return q
    # --- LP distribution via Ankr (pair = LP token) ---
    try:
        res = ankr_holders(q["pair"])
        holders = res.get("holders", [])
        total_lp = sum(float(h.get("balance") or 0) for h in holders)
        dead_lp = sum(float(h.get("balance") or 0) for h in holders
                      if (h.get("holderAddress") or "").lower() in (DEAD, ZERO))
        burn_pct = (dead_lp / total_lp * 100) if total_lp else 0
        q["lp_burn_pct_ankr"] = round(burn_pct, 2)
    except Exception:  # noqa: BLE001
        q["lp_burn_pct_ankr"] = None
    burn_ok = (q.get("lp_burn_pct_ankr") or 0) >= 50 or q.get("lp_dead_pct_goplus", 0) >= 50
    # Mid-cap: lock aktif >= 50% cukup. Lock dihitung dari fraksi LP yang
    # terkunci (GoPlus lp_holders is_locked, persen dari supply LP) — bukan
    # dari jumlah NFT lock PinkLock.
    lock_min = 50 if midcap else 99
    lock_ok = q.get("lp_lock_pct_goplus", 0) >= lock_min
    if not (burn_ok or lock_ok):
        q["fail_reason"] = (f"LP burn {q.get('lp_burn_pct_ankr')}% / GoPlus dead "
                            f"{q.get('lp_dead_pct_goplus')}% / lock {q.get('lp_lock_pct_goplus')}% — "
                            f"tidak memenuhi burn >=50% atau lock ~100%")
        return q
    q["lp_gate"] = "burn" if burn_ok else "lock"
    # --- Reserve pair vs totalSupply (anti inflasi saldo tersembunyi) ---
    # STOCKMEMES 2026-11-07: reserve pair 93,1 jt -> 8,7 TRILIUN sementara
    # totalSupply tetap 100 jt — mint tersembunyi yang tidak update
    # totalSupply, lolos dari GoPlus is_mintable=0. Reserve > supply secara
    # matematis mustahil untuk token legit: gagal langsung.
    try:
        reserve, supply = onchain_reserve_supply(q["pair"], ca)
        q["pair_token_reserve"] = reserve
        q["total_supply"] = supply
        if supply > 0 and reserve > supply:
            q["fail_reason"] = (f"reserve token pair {reserve:,.0f} > totalSupply "
                                f"{supply:,.0f} (inflasi saldo tersembunyi)")
            return q
    except Exception as e:  # noqa: BLE001
        q["fail_reason"] = f"reserve/totalSupply gagal dibaca: {e}"
        q["retry"] = True
        return q
    q["qualifies"] = True
    return q


def check_burn_leg(st, result, now):
    """Kaki BURN — Burn Agent sebagai named-coin leg di hunt terpadu.

    Trigger dari SETUP.md burn-scalp: Jalur A breakout MCAP > $400K
    (m5 > 0, buys h1 > sells h1) atau Jalur B pulih dari bawah ke band
    $340-360K lalu m5 flip >= +0,5% dengan buys > sells. Gate (b)
    vol24/liq < 5x tetap berlaku (tidak pernah dilonggarkan) dan dicek
    SEBELUM deep gate. Deep gate sama seperti flap via
    check_candidate(named=True). Konfirmasi dua sighting seperti kaki
    lain: trigger pertama + lolos deep gate = pending-confirm; qualifier
    hanya di run berikut bila trigger + deep gate masih lolos. Burn
    TIDAK pernah jadi "fail" permanen — selalu dipantau tiap run; bila
    disposition sudah "pass" (qualifier dilaporkan), kaki diam sampai
    parent reset disposition ke "watching" sesudah ronde diputuskan.
    """
    bw = st.setdefault("burn_watch", {
        "token": "BURN", "ca": BURN_CA, "pair": BURN_PAIR,
        "disposition": "watching", "last_mcap": None, "below_band": None,
        "merged_wib": "2026-11-07 ~20:20 WIB"})
    if bw.get("disposition") == "pass":
        return
    try:
        p = ds_pair_by_address(BURN_PAIR)
    except Exception as e:  # noqa: BLE001
        result["notes"].append(f"BURN: DexScreener gagal dibaca: {e}")
        return
    if not p:
        result["notes"].append("BURN: pair tidak terindeks DexScreener")
        return
    mcap = float(p.get("fdv") or 0)
    m5 = float((p.get("priceChange") or {}).get("m5") or 0)
    h1 = (p.get("txns") or {}).get("h1") or {}
    buys = int(h1.get("buys") or 0)
    sells = int(h1.get("sells") or 0)
    vol24 = float((p.get("volume") or {}).get("h24") or 0)
    liq = float((p.get("liquidity") or {}).get("usd") or 0)
    price = float(p.get("priceUsd") or 0)
    prev_below = bw.get("below_band")
    bw.update({"last_mcap": mcap, "last_price_usd": price, "last_m5": m5,
               "last_liq": liq, "below_band": mcap < BURN_BAND_LO,
               "last_vol_liq": round(vol24 / liq, 2) if liq else None})
    momentum = m5 > 0 and buys > sells
    trigger = None
    if mcap > BURN_BREAKOUT_MCAP and momentum:
        trigger = "A-breakout"
    elif (prev_below and BURN_BAND_LO <= mcap <= BURN_BAND_HI
          and m5 >= BURN_M5_FLIP_MIN and buys > sells):
        trigger = "B-pullback"
    pending = bw.get("disposition") == "pending-confirm"
    if pending and time.time() - bw.get("confirm_ts", 0) > CONFIRM_MAX_AGE_S:
        bw["disposition"] = "watching"
        bw["confirm_mcap"] = None
        result["notes"].append("BURN: pending-confirm kedaluwarsa (>2 jam) — kembali memantau")
        return
    if pending:
        # Konfirmasi: jalur A butuh MCAP tetap > $400K; jalur B cukup MCAP
        # bertahan >= $340K (boleh naik keluar band) + momentum.
        ct = bw.get("confirm_trigger")
        ok_now = False
        if ct == "A-breakout":
            ok_now = trigger == "A-breakout"
        elif ct == "B-pullback":
            ok_now = mcap >= BURN_BAND_LO and momentum
        if not ok_now:
            bw["disposition"] = "watching"
            bw["confirm_mcap"] = None
            result["notes"].append(
                f"BURN: trigger {ct} hilang sebelum konfirmasi "
                f"(mcap ${mcap:,.0f}) — kembali memantau")
            return
        trigger = ct  # pakai jalur yang dikonfirmasi
    if trigger is None:
        return
    # Gate (b): vol24/liq < 5x — berlaku SEBELUM deep gate.
    vol_liq = vol24 / liq if liq else None
    if vol_liq is not None and vol_liq >= BURN_VOL_LIQ_MAX:
        bw["last_fail_reason"] = f"gate (b) vol24/liq {vol_liq:.2f}x >= {BURN_VOL_LIQ_MAX}x"
        result["notes"].append(
            f"BURN: {trigger} tapi {bw['last_fail_reason']} — diblokir, tetap dipantau")
        return
    c = {"name": "Burn Agent (BURN)", "pair": BURN_PAIR, "source": "burn-watch",
         "age_h": None, "liq": liq, "m5": m5, "buys": buys, "sells": sells, "price": price}
    q = check_candidate(BURN_CA, c, named=True)
    q["kind"] = "burn"
    q["trigger"] = trigger
    q["mcap_usd"] = mcap
    q["vol_liq_ratio"] = round(vol_liq, 2) if vol_liq else None
    if not q.get("qualifies"):
        bw["last_fail_reason"] = q.get("fail_reason")
        result["notes"].append(f"BURN: {trigger} gugur deep gate — {q.get('fail_reason')}")
        return
    if not pending:
        bw["disposition"] = "pending-confirm"
        bw["confirm_mcap"] = mcap
        bw["confirm_trigger"] = trigger
        bw["confirm_ts"] = time.time()
        bw["confirm_at_wib"] = now
        result["notes"].append(
            f"BURN: {trigger} lolos gate pertama (mcap ${mcap:,.0f}), "
            f"menunggu konfirmasi run berikut")
        return
    # Cross-check volume (swap_flow): hanya di konfirmasi kedua, sebelum
    # qualifier. Net volume lengkap <= 0 -> DITAHAN (tetap pending-confirm,
    # bisa lolos run berikut); tool gagal -> fail-open, lanjut.
    if not flow_check(q):
        fc = q["flow_check"]
        bw["last_fail_reason"] = (
            f"cross-check volume: net {fc.get('net_volume')} "
            f"{fc.get('quote')} (${fc.get('net_volume_usd')}) <= 0 — ditahan")
        bw["flow_hold"] = bw.get("flow_hold", 0) + 1
        bw["last_flow_check"] = fc
        result["notes"].append(
            f"BURN: {trigger} lolos gate tapi {bw['last_fail_reason']}, "
            f"tetap pending-confirm")
        return
    result["qualifiers"].append(q)
    bw["disposition"] = "pass"
    bw["reported"] = True
    bw["confirm_mcap"] = None


def check_slime_leg(st, result, now):
    """Kaki SLIME — SLIME sebagai named-coin leg di hunt terpadu.

    Ditambah 2026-11-07 ~20:57 WIB atas ralat user ("masukan saja kalau
    memang gate nya sudah lolos") — sebelumnya SLIME hanya monitor-only
    (slime-watch 30 mnt, read-only). SLIME bukan launch baru dan MCAP-nya
    (~$113 rb) di bawah floor mid-cap $1 jt, jadi satu-satunya jalan
    masuk hunt adalah kaki named seperti BURN.

    Trigger "bounce": m5 > 0 DAN buys h1 > sells h1 DAN liq >= $15 rb
    (pantulan momentum — SLIME tidak punya tesis breakout MCAP seperti
    BURN). Gate (b) vol24/liq < 5x dicek SEBELUM deep gate. Deep gate
    sama seperti flap via check_candidate(named=True) — tanpa batas umur
    pair dan tanpa filter FDV/vol mid-cap. Top-free-wallet di deep gate
    kaki ini dibaca via GoPlus (dialihkan 2026-11-09 ~20:20 WIB, tap
    user "Ya, alihkan ke GoPlus"; Ankr mati) — ambang 5% tidak berubah.
    Konfirmasi dua sighting
    seperti kaki lain: trigger pertama + lolos deep gate =
    pending-confirm; qualifier hanya di run berikut bila trigger + deep
    gate masih lolos. SLIME TIDAK pernah jadi "fail" permanen — selalu
    dipantau tiap run; bila disposition sudah "pass" (qualifier
    dilaporkan), kaki diam sampai parent reset disposition ke "watching"
    sesudah ronde diputuskan.
    """
    sw = st.setdefault("slime_watch", {
        "token": "SLIME", "ca": SLIME_CA, "pair": SLIME_PAIR,
        "disposition": "watching", "last_mcap": None,
        "added_wib": "2026-11-07 ~20:57 WIB"})
    if sw.get("disposition") == "pass":
        return
    try:
        p = ds_pair_by_address(SLIME_PAIR)
    except Exception as e:  # noqa: BLE001
        result["notes"].append(f"SLIME: DexScreener gagal dibaca: {e}")
        return
    if not p:
        result["notes"].append("SLIME: pair tidak terindeks DexScreener")
        return
    mcap = float(p.get("fdv") or 0)
    m5 = float((p.get("priceChange") or {}).get("m5") or 0)
    h1 = (p.get("txns") or {}).get("h1") or {}
    buys = int(h1.get("buys") or 0)
    sells = int(h1.get("sells") or 0)
    vol24 = float((p.get("volume") or {}).get("h24") or 0)
    liq = float((p.get("liquidity") or {}).get("usd") or 0)
    price = float(p.get("priceUsd") or 0)
    sw.update({"last_mcap": mcap, "last_price_usd": price, "last_m5": m5,
               "last_liq": liq, "last_buys": buys, "last_sells": sells,
               "last_vol_liq": round(vol24 / liq, 2) if liq else None})
    momentum = m5 > 0 and buys > sells
    trigger = "bounce" if (momentum and liq >= SLIME_LIQ_MIN) else None
    pending = sw.get("disposition") == "pending-confirm"
    if pending and time.time() - sw.get("confirm_ts", 0) > CONFIRM_MAX_AGE_S:
        sw["disposition"] = "watching"
        sw["confirm_mcap"] = None
        result["notes"].append("SLIME: pending-confirm kedaluwarsa (>2 jam) — kembali memantau")
        return
    if pending:
        if trigger != "bounce":
            sw["disposition"] = "watching"
            sw["confirm_mcap"] = None
            result["notes"].append(
                f"SLIME: trigger bounce hilang sebelum konfirmasi "
                f"(mcap ${mcap:,.0f}, buys {buys}/sells {sells}) — kembali memantau")
            return
        trigger = "bounce"  # pakai jalur yang dikonfirmasi
    if trigger is None:
        return
    # Gate (b): vol24/liq < 5x — berlaku SEBELUM deep gate.
    vol_liq = vol24 / liq if liq else None
    if vol_liq is not None and vol_liq >= SLIME_VOL_LIQ_MAX:
        sw["last_fail_reason"] = f"gate (b) vol24/liq {vol_liq:.2f}x >= {SLIME_VOL_LIQ_MAX}x"
        result["notes"].append(
            f"SLIME: {trigger} tapi {sw['last_fail_reason']} — diblokir, tetap dipantau")
        return
    c = {"name": "SLIME", "pair": SLIME_PAIR, "source": "slime-watch",
         "age_h": None, "liq": liq, "m5": m5, "buys": buys, "sells": sells, "price": price}
    q = check_candidate(SLIME_CA, c, named=True, holder_source="goplus")
    q["kind"] = "slime"
    q["trigger"] = trigger
    q["mcap_usd"] = mcap
    q["vol_liq_ratio"] = round(vol_liq, 2) if vol_liq else None
    if not q.get("qualifies"):
        sw["last_fail_reason"] = q.get("fail_reason")
        result["notes"].append(f"SLIME: {trigger} gugur deep gate — {q.get('fail_reason')}")
        return
    if not pending:
        sw["disposition"] = "pending-confirm"
        sw["confirm_mcap"] = mcap
        sw["confirm_trigger"] = trigger
        sw["confirm_ts"] = time.time()
        sw["confirm_at_wib"] = now
        result["notes"].append(
            f"SLIME: {trigger} lolos gate pertama (mcap ${mcap:,.0f}), "
            f"menunggu konfirmasi run berikut")
        return
    # Cross-check volume (swap_flow): hanya di konfirmasi kedua, sebelum
    # qualifier. Net volume lengkap <= 0 -> DITAHAN (tetap pending-confirm,
    # bisa lolos run berikut); tool gagal -> fail-open, lanjut.
    if not flow_check(q):
        fc = q["flow_check"]
        sw["last_fail_reason"] = (
            f"cross-check volume: net {fc.get('net_volume')} "
            f"{fc.get('quote')} (${fc.get('net_volume_usd')}) <= 0 — ditahan")
        sw["flow_hold"] = sw.get("flow_hold", 0) + 1
        sw["last_flow_check"] = fc
        result["notes"].append(
            f"SLIME: {trigger} lolos gate tapi {sw['last_fail_reason']}, "
            f"tetap pending-confirm")
        return
    result["qualifiers"].append(q)
    sw["disposition"] = "pass"
    sw["reported"] = True
    sw["confirm_mcap"] = None


def main():
    os.makedirs(BASE, exist_ok=True)
    st = load_state()
    now = wib_now()
    result = {"checked_at": now, "new_launches": 0, "checked": 0,
              "qualifiers": [], "seeded_this_run": False, "notes": [],
              "midcap_new": 0, "midcap_checked": 0}
    cands = collect_candidates()
    if not st.get("seeded"):
        for ca, c in cands.items():
            st["seen"][ca] = {"name": c.get("name"), "first_seen_wib": now,
                              "source": c.get("source"), "disposition": "seed",
                              "reported": False, "attempts": 0}
        st["seeded"] = True
        result["seeded_this_run"] = True
        result["notes"].append(f"seed: {len(st['seen'])} launch tercatat, tanpa laporan")
        log_line(f"SEED {len(st['seen'])} launch")
        save_state(st)
        with open(RESULT, "w") as f:
            json.dump(result, f, indent=1)
        return 0

    # Daftarkan launch baru; kumpulkan yang perlu dicek (baru + retry).
    to_check = []
    for ca, c in cands.items():
        entry = st["seen"].get(ca)
        if entry is None:
            st["seen"][ca] = {"name": c.get("name"), "first_seen_wib": now,
                              "source": c.get("source"), "disposition": "new",
                              "reported": False, "attempts": 0}
            to_check.append((ca, c))
            result["new_launches"] += 1
        elif entry.get("disposition") == "retry" and entry.get("attempts", 0) < MAX_RETRY:
            to_check.append((ca, c))
        elif entry.get("disposition") == "pending-confirm":
            # Konfirmasi kedua: deep gate ulang + bandingkan reserve pair.
            if time.time() - entry.get("confirm_ts", 0) > CONFIRM_MAX_AGE_S:
                entry["disposition"] = "fail"
                entry["fail_reason"] = "pending-confirm kedaluwarsa (>2 jam tanpa konfirmasi)"
            else:
                to_check.append((ca, c))
    # Paling menjanjikan dulu (liq tertinggi), batasi deep-gate per run.
    to_check.sort(key=lambda t: (t[1].get("liq") or 0), reverse=True)
    deep_done = 0
    for ca, c in to_check:
        entry = st["seen"][ca]
        try:
            ok, reason = fast_filter(ca, c)
        except Exception as e:  # noqa: BLE001
            entry["disposition"] = "retry"
            entry["attempts"] = entry.get("attempts", 0) + 1
            entry["fail_reason"] = f"fast filter gagal dibaca: {e}"
            continue
        if not ok:
            entry["disposition"] = "fail"
            entry["fail_reason"] = reason
            continue
        if deep_done >= MAX_DEEP_PER_RUN:
            continue  # tetap "new", dicek run berikut
        deep_done += 1
        result["checked"] += 1
        q = check_candidate(ca, c)
        if q.get("qualifies"):
            prev_reserve = entry.get("confirm_reserve")
            now_reserve = q.get("pair_token_reserve")
            if prev_reserve is None:
                # Lolos pertama: catat snapshot reserve, BELUM jadi qualifier.
                # Konfirmasi di run berikut (15 mnt lagi).
                entry["disposition"] = "pending-confirm"
                entry["confirm_reserve"] = now_reserve
                entry["confirm_ts"] = time.time()
                entry["confirm_at_wib"] = now
                entry["fail_reason"] = None
                result["notes"].append(
                    f"{c.get('name') or ca}: lolos gate pertama, "
                    f"menunggu konfirmasi reserve run berikut "
                    f"(reserve {now_reserve:,.0f})")
            elif now_reserve is None or float(prev_reserve) <= 0:
                entry["disposition"] = "retry"
                entry["attempts"] = entry.get("attempts", 0) + 1
                entry["fail_reason"] = "reserve konfirmasi tidak terbaca"
            elif now_reserve > RESERVE_GROWTH_MAX * float(prev_reserve):
                entry["disposition"] = "fail"
                entry["fail_reason"] = (
                    f"reserve pair membengkak {float(prev_reserve):,.0f} -> "
                    f"{now_reserve:,.0f} (>{RESERVE_GROWTH_MAX}x dalam 15 mnt) — "
                    f"pola inflasi saldo STOCKMEMES")
                result["notes"].append(f"{c.get('name') or ca}: gugur — {entry['fail_reason']}")
            else:
                # Cross-check volume (swap_flow): hanya di konfirmasi
                # kedua, sebelum qualifier. Net volume lengkap <= 0 ->
                # DITAHAN (tetap pending-confirm sampai kedaluwarsa 2 jam,
                # bisa lolos run berikut); tool gagal -> fail-open, lanjut.
                if not flow_check(q):
                    fc = q["flow_check"]
                    entry["flow_hold"] = entry.get("flow_hold", 0) + 1
                    entry["last_flow_check"] = fc
                    entry["fail_reason"] = (
                        f"cross-check volume: net {fc.get('net_volume')} "
                        f"{fc.get('quote')} (${fc.get('net_volume_usd')}) "
                        f"<= 0 — ditahan, tetap pending-confirm")
                    result["notes"].append(
                        f"{c.get('name') or ca}: lolos gate tapi "
                        f"{entry['fail_reason']}")
                    continue
                result["qualifiers"].append(q)
                entry["disposition"] = "pass"
                entry["reported"] = True
        elif q.get("retry"):
            entry["disposition"] = "retry"
            entry["attempts"] = entry.get("attempts", 0) + 1
            entry["fail_reason"] = q.get("fail_reason")
            if entry["attempts"] >= MAX_RETRY:
                entry["disposition"] = "fail"
        else:
            entry["disposition"] = "fail"
            entry["fail_reason"] = q.get("fail_reason")
            result["notes"].append(f"{c.get('name') or ca}: gugur — {q.get('fail_reason', '?')}")
    # --- Kaki MID-CAP trending (amendment 2026-11-07 ~19:40 WIB) ---
    # GeckoTerminal trending pools hlm 1-2 + silang DexScreener, filter
    # FDV $1jt-$100jt, volume h1 >= $50rb, momentum; deep gate sama
    # seperti flap (tanpa batas umur, lock >= 50%); konfirmasi dua
    # sighting dengan pending-confirm + snapshot reserve, kedaluwarsa 2 jam.
    # State terpisah (midcap_seen) supaya tidak bentrok dengan kaki flap.
    mc_seen = st.setdefault("midcap_seen", {})
    mc_cands = collect_midcap_candidates()
    mc_to_check = []
    for ca, c in mc_cands.items():
        entry = mc_seen.get(ca)
        if entry is None:
            mc_seen[ca] = {"name": c.get("name"), "first_seen_wib": now,
                           "source": c.get("source"), "disposition": "new",
                           "reported": False, "attempts": 0}
            mc_to_check.append((ca, c))
            result["midcap_new"] = result.get("midcap_new", 0) + 1
        elif entry.get("disposition") == "retry" and entry.get("attempts", 0) < MAX_RETRY:
            mc_to_check.append((ca, c))
        elif entry.get("disposition") == "pending-confirm":
            if time.time() - entry.get("confirm_ts", 0) > CONFIRM_MAX_AGE_S:
                entry["disposition"] = "fail"
                entry["fail_reason"] = "pending-confirm kedaluwarsa (>2 jam tanpa konfirmasi)"
            else:
                mc_to_check.append((ca, c))
    mc_to_check.sort(key=lambda t: (t[1].get("vol_h1") or 0), reverse=True)
    mc_deep_done = 0
    for ca, c in mc_to_check:
        entry = mc_seen[ca]
        try:
            ok, reason = midcap_fast_filter(ca, c)
        except Exception as e:  # noqa: BLE001
            entry["disposition"] = "retry"
            entry["attempts"] = entry.get("attempts", 0) + 1
            entry["fail_reason"] = f"fast filter gagal dibaca: {e}"
            continue
        if not ok:
            entry["disposition"] = "fail"
            entry["fail_reason"] = reason
            continue
        if mc_deep_done >= MAX_DEEP_MIDCAP_PER_RUN:
            continue  # tetap "new", dicek run berikut
        mc_deep_done += 1
        result["midcap_checked"] = result.get("midcap_checked", 0) + 1
        q = check_candidate(ca, c, midcap=True)
        q["route"] = c.get("route")
        if q.get("qualifies"):
            prev_reserve = entry.get("confirm_reserve")
            now_reserve = q.get("pair_token_reserve")
            if prev_reserve is None:
                entry["disposition"] = "pending-confirm"
                entry["confirm_reserve"] = now_reserve
                entry["confirm_ts"] = time.time()
                entry["confirm_at_wib"] = now
                entry["fail_reason"] = None
                result["notes"].append(
                    f"MID-CAP {c.get('name') or ca}: lolos gate pertama, "
                    f"menunggu konfirmasi reserve run berikut "
                    f"(reserve {now_reserve:,.0f})")
            elif now_reserve is None or float(prev_reserve) <= 0:
                entry["disposition"] = "retry"
                entry["attempts"] = entry.get("attempts", 0) + 1
                entry["fail_reason"] = "reserve konfirmasi tidak terbaca"
            elif now_reserve > RESERVE_GROWTH_MAX * float(prev_reserve):
                entry["disposition"] = "fail"
                entry["fail_reason"] = (
                    f"reserve pair membengkak {float(prev_reserve):,.0f} -> "
                    f"{now_reserve:,.0f} (>{RESERVE_GROWTH_MAX}x dalam 15 mnt) — "
                    f"pola inflasi saldo STOCKMEMES")
                result["notes"].append(f"MID-CAP {c.get('name') or ca}: gugur — {entry['fail_reason']}")
            else:
                # Cross-check volume (swap_flow): hanya di konfirmasi
                # kedua, sebelum qualifier. Net volume lengkap <= 0 ->
                # DITAHAN (tetap pending-confirm sampai kedaluwarsa 2 jam,
                # bisa lolos run berikut); tool gagal -> fail-open, lanjut.
                if not flow_check(q):
                    fc = q["flow_check"]
                    entry["flow_hold"] = entry.get("flow_hold", 0) + 1
                    entry["last_flow_check"] = fc
                    entry["fail_reason"] = (
                        f"cross-check volume: net {fc.get('net_volume')} "
                        f"{fc.get('quote')} (${fc.get('net_volume_usd')}) "
                        f"<= 0 — ditahan, tetap pending-confirm")
                    result["notes"].append(
                        f"{c.get('name') or ca}: lolos gate tapi "
                        f"{entry['fail_reason']}")
                    continue
                result["qualifiers"].append(q)
                entry["disposition"] = "pass"
                entry["reported"] = True
        elif q.get("retry"):
            entry["disposition"] = "retry"
            entry["attempts"] = entry.get("attempts", 0) + 1
            entry["fail_reason"] = q.get("fail_reason")
            if entry["attempts"] >= MAX_RETRY:
                entry["disposition"] = "fail"
        else:
            entry["disposition"] = "fail"
            entry["fail_reason"] = q.get("fail_reason")
            result["notes"].append(f"MID-CAP {c.get('name') or ca}: gugur — {q.get('fail_reason', '?')}")
    # --- Kaki BURN (gabung 2026-11-07 ~20:20 WIB, perintah user) ---
    # Burn Agent sebagai named-coin leg: trigger breakout/pullback dari
    # SETUP.md burn-scalp + deep gate yang sama; qualifier dilaporkan
    # dengan kind "burn". Lihat check_burn_leg.
    try:
        check_burn_leg(st, result, now)
    except Exception as e:  # noqa: BLE001
        result["notes"].append(f"BURN: kaki gagal: {e}")
        log_line(f"WARN burn leg gagal: {e}")
    # --- Kaki SLIME (gabung 2026-11-07 ~20:57 WIB, ralat user) ---
    # SLIME sebagai named-coin leg: trigger bounce (m5 > 0 + buys > sells
    # + liq >= $15 rb) + gate (b) vol24/liq < 5x + deep gate yang sama;
    # qualifier dilaporkan dengan kind "slime". Lihat check_slime_leg.
    try:
        check_slime_leg(st, result, now)
    except Exception as e:  # noqa: BLE001
        result["notes"].append(f"SLIME: kaki gagal: {e}")
        log_line(f"WARN slime leg gagal: {e}")
    save_state(st)
    result["flow_checks_done"] = _FLOW_STATE["done"]
    with open(RESULT, "w") as f:
        json.dump(result, f, indent=1)
    log_line(f"OK new={result['new_launches']} checked={result['checked']} qual={len(result['qualifiers'])} midcap_new={result.get('midcap_new', 0)} midcap_checked={result.get('midcap_checked', 0)} flow_checks={_FLOW_STATE['done']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
