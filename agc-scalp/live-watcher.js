// AGC live watcher — polling cepat (bukan websocket).
//
// ALASAN: websocket eth_subscribe ke wss://bsc.publicnode.com DIBLOKIR rate limit
// (-32005) saat diuji 2026-11-05 — subscription publik tidak bisa diandalkan.
// Pengganti: eth_call getReserves() tiap 3 detik (rotasi 3 RPC HTTPS publik) +
// DexScreener pairs tiap 30 detik untuk txns m5/h1 dan cross-check harga.
// Latensi deteksi ~3-6 detik vs polling cron 10 menit — cukup untuk target/stop
// +15% / stop tetap (stop_usd di state, fallback -5%) dan pantulan m5. Read-only; TIDAK pernah mengeksekusi apa pun.
// Trigger ditulis ke live-triggers.jsonl; hook agc-live-trigger membangunkannya
// ke agen eksekusi. Cron agc-scalp-auto 10 menit tetap jalan sebagai cadangan.
//
// Dijalankan sebagai proses background (nohup + disown) — BUKAN systemd service.
// Restart HANYA dari parent (nohup + disown); restart dari worker TERBUKTI mati
// ~80-117 detik kemudian (SIGTERM berulang 2026-11-05).
// State live: ~/workspace/agc-scalp/live-state.json (ditulis atomik).
// Trigger: ~/workspace/agc-scalp/live-triggers.jsonl (append-only, satu JSON/baris).
"use strict";
const fs = require("fs");
const path = require("path");

const DIR = "/home/hatch/workspace/agc-scalp";
const STATE_FILE = path.join(DIR, "auto-state.json");
const LIVE_STATE = path.join(DIR, "live-state.json");
const TRIGGERS = path.join(DIR, "live-triggers.jsonl");
const PAIR = "0xE56041CdDBB46DB01c67B7A62684F428a04Db8C6";
const WBNB = "0xbb4cdB9CBd36B01bD1cBaEBF2De08d9173bc095c";
const RPCS = [
  "https://bsc-dataseed1.bnbchain.org",
  "https://bsc-dataseed1.ninicoin.io",
  "https://bsc.publicnode.com",
];
const DEX_URL = `https://api.dexscreener.com/latest/dex/pairs/bsc/${PAIR}`;

const log = (...a) => console.log(new Date().toISOString(), ...a);

let bnbPriceUsd = 716;
let autoState = null;
let autoStateMtime = 0;
let priceHist = []; // {t, priceUsd}
let liqHist = []; // {t, liqUsd}
let dexTxns = { m5: { buys: 0, sells: 0 }, h1: { buys: 0, sells: 0 } };
let dexHist = []; // {t, buys, sells} snapshot tiap tick Dex (untuk rasio vs 5 mnt lalu)
let dexPriceUsd = null;
let lastPriceWbnb = null;
let lastPriceUsd = null;
let lastLiqUsd = null;
let lastTickAt = 0;
let lastDexAt = 0;
let lastTriggerAt = {};
let lastStateWrite = 0;
let triggerSeq = 0;
let rpcIdx = 0;
let consecFails = 0;

function loadAutoState() {
  try {
    const st = fs.statSync(STATE_FILE);
    if (st.mtimeMs !== autoStateMtime) {
      autoState = JSON.parse(fs.readFileSync(STATE_FILE, "utf8"));
      autoStateMtime = st.mtimeMs;
    }
  } catch (e) { log("auto-state read fail:", e.message); }
}
loadAutoState();

async function rpcCall(payload) {
  for (let i = 0; i < RPCS.length; i++) {
    const url = RPCS[(rpcIdx + i) % RPCS.length];
    try {
      const r = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
        signal: AbortSignal.timeout(5000),
      });
      const j = await r.json();
      if (j.error) throw new Error(j.error.message || JSON.stringify(j.error));
      rpcIdx = (rpcIdx + i) % RPCS.length;
      return j.result;
    } catch (e) { /* coba RPC berikutnya */ }
  }
  throw new Error("all RPCs failed");
}

async function tickReserves() {
  try {
    const res = await rpcCall({
      jsonrpc: "2.0", id: 1, method: "eth_call",
      params: [{ to: PAIR, data: "0x0902f1ac" }, "latest"],
    });
    const b = Buffer.from(res.slice(2), "hex");
    if (b.length < 96) return;
    const u112 = (off) => {
      // uint112 dalam word 32 byte: 20 byte nol + 12 byte nilai (byte off+20..off+31)
      let v = 0n;
      for (let k = off + 20; k < off + 32; k++) v = (v << 8n) + BigInt(b[k]);
      return v;
    };
    const r0 = u112(0); // AGC
    const r1 = u112(32); // WBNB
    if (r0 === 0n) return;
    const priceWbnb = Number(r1) / Number(r0);
    lastPriceWbnb = priceWbnb;
    lastPriceUsd = priceWbnb * bnbPriceUsd;
    lastLiqUsd = 2 * Number(r1) / 1e18 * bnbPriceUsd;
    lastTickAt = Date.now();
    priceHist.push({ t: Date.now(), priceUsd: lastPriceUsd });
    liqHist.push({ t: Date.now(), liqUsd: lastLiqUsd });
    const now = Date.now();
    priceHist = priceHist.filter((p) => now - p.t < 15 * 60 * 1000);
    liqHist = liqHist.filter((l) => now - l.t < 70 * 60 * 1000);
    consecFails = 0;
    evaluate();
    writeLiveState();
  } catch (e) {
    consecFails++;
    if (consecFails % 20 === 0) log("reserves tick fail x" + consecFails + ":", e.message);
  }
}

// Fetch DexScreener dengan retry terbatas + backoff (maks 3x percobaan,
// jeda 1 dtk lalu 2 dtk; percobaan ke-3 gagal = lempar error ke pemanggil).
// Hanya untuk ketahanan baca API; TIDAK mengubah logika trigger (target_usd/stop_usd di state, fallback 1.15/0.95)
// atau interval polling (reserves 3 dtk, Dex 30 dtk, harga BNB 60 dtk).
async function fetchDexJson(url, timeoutMs) {
  let delayMs = 1000;
  for (let attempt = 1; attempt <= 3; attempt++) {
    try {
      const r = await fetch(url, { signal: AbortSignal.timeout(timeoutMs) });
      return await r.json();
    } catch (e) {
      if (attempt === 3) throw e;
      log("dex fetch retry", attempt, "gagal, coba lagi dalam", delayMs, "ms:", e.message);
      await new Promise((res) => setTimeout(res, delayMs));
      delayMs *= 2;
    }
  }
}

async function tickDex() {
  try {
    const j = await fetchDexJson(DEX_URL, 10000);
    const p = j && j.pairs && j.pairs[0];
    if (!p) return;
    dexPriceUsd = p.priceUsd ? parseFloat(p.priceUsd) : null;
    if (p.txns) {
      dexTxns = p.txns;
      if (p.txns.m5) {
        dexHist.push({ t: Date.now(), buys: p.txns.m5.buys || 0, sells: p.txns.m5.sells || 0 });
        dexHist = dexHist.filter((d) => Date.now() - d.t < 15 * 60 * 1000);
      }
    }
    // Perbarui harga BNB dari DexScreener via endpoint token WBNB (murah, 1x/menit)
    lastDexAt = Date.now();
    writeLiveState();
  } catch (e) { log("dex fetch fail (3x retry habis):", e.message); }
}

async function refreshBnbPrice() {
  try {
    const j = await fetchDexJson(`https://api.dexscreener.com/tokens/v1/bsc/${WBNB}`, 10000);
    const p = j && j[0] && j[0].priceUsd ? parseFloat(j[0].priceUsd) : null;
    if (p && p > 0) bnbPriceUsd = p;
  } catch (e) { /* pakai harga terakhir */ }
}

function priceAt(msAgo) {
  const target = Date.now() - msAgo;
  let best = null;
  for (const p of priceHist) {
    if (p.t <= target) best = p;
    else break;
  }
  return best ? best.priceUsd : (priceHist[0] ? priceHist[0].priceUsd : null);
}

function writeLiveState(force) {
  const now = Date.now();
  if (!force && now - lastStateWrite < 1000) return;
  lastStateWrite = now;
  const p5 = priceAt(300000), p10 = priceAt(600000);
  const m5 = p5 && lastPriceUsd ? (lastPriceUsd - p5) / p5 : null;
  const prevM5 = p10 && p5 ? (p5 - p10) / p10 : null;
  const state = {
    updated_at: new Date(now).toISOString(),
    mode: "https-polling",
    price_usd: lastPriceUsd,
    price_wbnb: lastPriceWbnb,
    dex_price_usd: dexPriceUsd,
    bnb_price_usd: bnbPriceUsd,
    liq_usd: lastLiqUsd,
    last_tick_at: lastTickAt ? new Date(lastTickAt).toISOString() : null,
    last_dex_at: lastDexAt ? new Date(lastDexAt).toISOString() : null,
    m5_change: m5,
    prev_m5_change: prevM5,
    buys_5m: dexTxns.m5 ? dexTxns.m5.buys : 0,
    sells_5m: dexTxns.m5 ? dexTxns.m5.sells : 0,
    buys_h1: dexTxns.h1 ? dexTxns.h1.buys : 0,
    sells_h1: dexTxns.h1 ? dexTxns.h1.sells : 0,
    book_a_asset: autoState && autoState.book_a ? autoState.book_a.asset : null,
    book_b_asset: autoState && autoState.book_b ? autoState.book_b.asset : null,
    consec_rpc_fails: consecFails,
    last_trigger: lastTriggerAt,
  };
  const tmp = LIVE_STATE + ".tmp";
  fs.writeFileSync(tmp, JSON.stringify(state, null, 2));
  fs.renameSync(tmp, LIVE_STATE);
}

function fireTrigger(type, detail) {
  const now = Date.now();
  if (lastTriggerAt[type] && now - lastTriggerAt[type] < 120000) return;
  lastTriggerAt[type] = now;
  triggerSeq += 1;
  const line = JSON.stringify({
    id: `${now}-${triggerSeq}`,
    ts: new Date(now).toISOString(),
    type,
    price_usd: lastPriceUsd,
    price_wbnb: lastPriceWbnb,
    bnb_price_usd: bnbPriceUsd,
    liq_usd: lastLiqUsd,
    detail,
  });
  fs.appendFileSync(TRIGGERS, line + "\n");
  log("TRIGGER", line);
  writeLiveState(true);
}

function evaluate() {
  if (!autoState || !lastPriceUsd) return;
  loadAutoState();
  const bookA = autoState.book_a || {};
  const bookB = autoState.book_b || {};
  const holdingAgc =
    (bookA.asset === "AGC" && (bookA.agc_amount || 0) > 0) ||
    (bookB.asset === "AGC" && (bookB.agc_amount || 0) > 0);
  const refA = bookA.ref_price_usd || bookA.entry_price_usd || bookA.entry_usd || null;
  const refB = bookB.entry_price_usd || bookB.ref_price_usd || bookB.entry_usd || null;
  // Stop: stop_usd tetap di state bila ada (override per ronde — Ronde 8:
  // $0,0000075 per perintah user 2026-11-06 ~08:54 WIB "Ganti target stop AGC
  // ke 0.0000075" + "Ayo kita lebarkan"), fallback ref*0.95.
  const stopA = bookA.stop_usd || (refA ? refA * 0.95 : null);
  const stopB = bookB.stop_usd || (refB ? refB * 0.95 : null);

  // Target: target_usd tetap di state bila ada (override per ronde — Ronde 8:
  // 2x entry per perintah user 2026-11-06 ~08:56 WIB), fallback ref*1.15.
  const tgtA = bookA.target_usd || (refA ? refA * 1.15 : null);
  const tgtB = bookB.target_usd || (refB ? refB * 1.15 : null);

  // Exit bisa dinonaktifkan penuh via exits_disabled di state
  // (perintah user 2026-11-06 ~09:10 WIB "tutup semua stop dan target" —
  // hold manual; tanpa flag ini, stop/target fallback 1.15/0.95 tetap jalan).
  const exitsDisabled = autoState.exits_disabled === true;

  if (holdingAgc) {
    if (bookA.asset === "AGC" && refA) {
      if (!exitsDisabled && tgtA && lastPriceUsd >= tgtA) fireTrigger("target_hit", { book: "A", ref_usd: refA, target_usd: tgtA });
      else if (!exitsDisabled && stopA && lastPriceUsd <= stopA) fireTrigger("stop_hit", { book: "A", ref_usd: refA, stop_usd: stopA });
    }
    if (bookB.asset === "AGC" && refB) {
      if (!exitsDisabled && tgtB && lastPriceUsd >= tgtB) fireTrigger("target_hit", { book: "B", ref_usd: refB, target_usd: tgtB });
      else if (!exitsDisabled && stopB && lastPriceUsd <= stopB) fireTrigger("stop_hit", { book: "B", ref_usd: refB, stop_usd: stopB });
    }
    const liq1h = liqHist.length ? liqHist[0].liqUsd : null;
    if (lastLiqUsd && liq1h && lastLiqUsd < liq1h * 0.7) {
      fireTrigger("liq_drop", { liq_now: lastLiqUsd, liq_1h_ago: liq1h });
    }
    return;
  }

  const canBuyA = bookA.asset === "WBNB" && ((bookA.wbnb_amount ?? bookA.bnb_amount) || 0) > 0;
  const canBuyB = bookB.asset === "WBNB" && ((bookB.wbnb_amount ?? bookB.bnb_amount) || 0) > 0;
  if (!canBuyA && !canBuyB) return;
  const p5 = priceAt(300000), p10 = priceAt(600000);
  if (!p5 || !p10 || !lastPriceUsd) return;
  const m5 = (lastPriceUsd - p5) / p5;
  const prevM5 = (p5 - p10) / p10;
  if (m5 < 0.005 || prevM5 >= 0) return;
  const m5t = dexTxns.m5 || { buys: 0, sells: 0 };
  // Pelemahan tekanan jual vs 5 menit lalu (snapshot DexScreener): rasio
  // buys/sells membaik >=20% DAN jumlah sells turun.
  let weakening = false;
  const target = Date.now() - 300000;
  let prevSnap = null;
  for (const d of dexHist) { if (d.t <= target) prevSnap = d; else break; }
  if (prevSnap && prevSnap.sells > 0 && m5t.sells > 0) {
    const rNow = m5t.buys / m5t.sells;
    const rPrev = prevSnap.buys / prevSnap.sells;
    weakening = rPrev > 0 && rNow >= rPrev * 1.2 && m5t.sells < prevSnap.sells;
  }
  if (m5t.buys > m5t.sells || weakening) {
    fireTrigger("bounce", {
      m5_change: m5, prev_m5_change: prevM5,
      buys_5m: m5t.buys, sells_5m: m5t.sells,
      books: [canBuyA ? "A" : null, canBuyB ? "B" : null].filter(Boolean),
    });
  }
}

setInterval(tickReserves, 3000);
setInterval(tickDex, 30000);
setInterval(refreshBnbPrice, 60000);
setInterval(() => { loadAutoState(); writeLiveState(); }, 5000);
refreshBnbPrice();
tickDex();
tickReserves();
process.on("SIGTERM", () => { log("SIGTERM, exiting"); process.exit(0); });
process.on("SIGINT", () => { log("SIGINT, exiting"); process.exit(0); });
log("live-watcher (https-polling) started");
