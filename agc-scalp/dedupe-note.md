# DEDUPE TRIGGER — CATATAN PENDAMPING auto-state.json

Ditulis 2026-11-06 ~07:52 WIB (usulan D, otorisasi user 07:48 WIB "Perbaiki semuanya").

## Masalah
Cron `agc-scalp-auto` dan hook `agc-live-trigger` sama-sama membaca
`live-triggers.jsonl` dan bisa mengevaluasi trigger yang sama dua kali
(contoh: trigger bounce 07:03:39 WIB sudah dievaluasi TIDAK VALID pada run
07:08, tapi tidak ada penanda yang mencegah evaluasi ulang).

## Patch (sudah diterapkan di auto-state.json)
Field baru top-level: `evaluated_trigger_ids` (array string).
Isi awal: `["1793923419661-8"]` — satu-satunya ID yang TERTELUSUR sudah
dievaluasi (trigger bounce 07:03:39 WIB, id `1793923419661-8`, dievaluasi
TIDAK VALID pada run cron 07:08 WIB; tercatat di `last_run_note`).

ID trigger lain di `live-triggers.jsonl` (total 68 baris, id `...-1` s.d.
`...-7` sebelum id di atas) TIDAK dimasukkan ke daftar awal karena status
evaluasinya tidak terlacak di catatan. Semuanya sudah basi (>7 jam pada
saat patch) — lihat instruksi run pertama di bawah.

## INSTRUKSI WAJIB — untuk diterapkan parent/subagent cron ke body
## cron `agc-scalp-auto` DAN hook `agc-live-trigger` (bukan oleh subagent ini)
1. Setiap selesai mengevaluasi satu trigger (valid ATAU tidak valid,
   beli ATAU diam), TULIS id trigger itu ke `evaluated_trigger_ids` di
   `auto-state.json` (append, tanpa duplikat) — tulis dilakukan sebagai
   bagian dari run yang sama, sebelum run berakhir.
2. Sebelum mengevaluasi trigger apa pun, BACA `evaluated_trigger_ids`;
   LEWATI (skip, tanpa evaluasi ulang, tanpa eksekusi) setiap trigger
   yang id-nya sudah ada di daftar.
3. Run pertama setelah patch ini: tandai juga SEMUA id yang sudah ada di
   `live-triggers.jsonl` pada saat itu sebagai sudah dievaluasi (append
   ke daftar) — semuanya basi dan kondisi pasar sudah berubah; jangan
   biarkan trigger lama dievaluasi ulang.
4. Penulisan daftar harus atomik (tulis file .tmp lalu rename), seperti
   pola penulisan state yang sudah ada, agar cron dan hook yang berjalan
   bersamaan tidak merusak JSON.
5. Bila `evaluated_trigger_ids` tidak ada (file lama), perlakukan sebagai
   `[]` dan buat field-nya pada penulisan berikutnya — jangan gagalkan run.

## Yang TIDAK diubah patch ini
- `target_pct` 0.15, `stop_pct` 0.05, saldo Buku A/B, dan semua field lain
  di `auto-state.json` tidak disentuh (verifikasi: `python3 -m json.tool`).
- Patch ini hanya menandai ID; ia TIDAK mengubah body cron/hook sendiri —
  itu tugas parent/subagent cron.
