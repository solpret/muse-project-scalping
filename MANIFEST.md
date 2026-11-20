# MANIFEST — muse-project-scalping

- **Tanggal:** 20 November 2026
- **Repo publik:** `muse-project-scalping` — https://github.com/solpret/muse-project-scalping
- **Ukuran:** 3,1 MB (3.074.721 byte), 46 file
- **Cakupan:** Project BACOT SCALPING — uji scalping (uang asli, kecil).

## Isi

| Bagian | Isi |
|---|---|
| `goals/agc-scalping-books-a-and-b/` | Goal utama scalping AGC |
| `scalp-flap-new/` | Watch + posisi flap (`watch.py`, `positions.json`) |
| `scalp-agc/` | Eksekusi/list bulk AGC (`fast_buy.py`, `fast_sell_all.py`, dsb.) |
| `scalp-binance-life/`, `scalp-flap77777/`, `scalp-wukong/` | Watch scalping lain (`watch.py` + state) |
| `agc-scalp/` | State + skrip auto scalping AGC lama |

## JUJUR: tree ini tipis — dan memang begitu adanya
- BACOT SCALPING uji terpadu (10 ronde, 0,002 WBNB/ronde, uang asli):
  rekor 0/4, bar vonis ≥4/10 (40%) — catatannya di `CHALLENGE.md`
  (ada di repo `muse-project-common`, bukan di sini, karena file itu
  dipakai semua track).
- AGC-only scalp test: 2 menang / 6 ronde, DIHENTIKAN sebelum vonis
  10 ronde — konteks saja.
- TIDAK ADA mesin paper untuk metode scalp (BSC off); tidak ada spec
  metode scalping yang berdiri sendiri selain yang tercatat di goal +
  skrip watch di atas.
- Catatan metode umum (`coin-patterns.md`, `method-adoptions-2026-11-05.md`)
  ada di `common/analysis/` (dipakai bersama Modal Bacot).

## Yang sengaja TIDAK ikut
- `*.log`, `*.bak*`, `__pycache__/` — dibuang. State JSON kecil
  (`positions.json`, `state.json`) dipertahankan sebagai bukti uji.

## Pembaruan 2026-11-20 (sejak 17 Nov)
- Baru: `agc-scalp/live-triggers.jsonl` (72 event stop_hit 5–6 Nov, 21 KB)
  — log trigger uji AGC, dipertahankan sebagai bukti uji.
- Pemecahan repo: TIDAK diperlukan — tree 3,1 MB; tidak ada file >90 MB.

## Audit rahasia
Bersih (20 Nov 2026, `audit-secret-2026-11-20.txt` di folder induk):
0 temuan — layak repo publik.
