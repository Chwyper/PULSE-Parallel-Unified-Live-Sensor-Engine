# Panduan Pengguna (User Manual)

## 1. Persiapan
Pastikan Python sudah terinstal di sistem Anda dan semua dependensi di `req.txt` sudah terpasang.
```bash
pip install -r req.txt
```

## 2. Menjalankan Aplikasi
Gunakan perintah berikut di terminal. (Tambahkan `-X utf8` di Windows jika grafis tabel ASCII tampak berantakan).
```bash
python main.py
```

## 3. Kontrol Sesi Atlet
- **Tombol ▶ START:** Memulai sesi latihan atlet. Fase fisiologis akan secara dinamis berubah dari *Resting* $\rightarrow$ *Warmup* $\rightarrow$ *Sprint* $\rightarrow$ *Cooldown* $\rightarrow$ *Recovery*. (Anda bisa mempercepat laju antar-fase lewat variabel `PHASE_DURATION_S` di `config.py`).
- **Tombol ⏹ STOP:** Menghentikan paksa sesi berjalan.

## 4. Membaca Antarmuka (Dashboard)
1. **Athlete Status (Kiri):** Panel ini menampilkan indikator keselamatan ringkas seperti *Exertion Level*, akumulasi langkah (*Steps*), dan status *Fall Detected*. Jika sensor mendeteksi ambang batas anomali kritis (misalnya *Heart Rate* terlalu ekstrim), panel ini akan berkedip.
2. **Live Metrics Chart (Tengah):** Panel pemantauan visual. Atas untuk sinyal *ECG Lead-II* asli yang dirender dalam 500 Hz, dan bawah untuk tren metrik komposit gabungan yang melacak *Heart Rate*, SpO2, dan Indeks Stres.
3. **System Monitor (Kanan Atas):** Melihat kesehatan mesin paralel. Kotak yang berkedip menyala hijau adalah sensor *node* yang merespons normal. Merah berarti *Node Deadlock* (sistem mengaktifkan *Fallback Recovery*).
4. **Benchmark (Kanan Bawah):** Tombol kuning `📊 BENCHMARK` memicu uji kecepatan komputasi. Jangan khawatir, grafik *live* di tengah tidak akan terganggu, pengujian berjalan di *Background Thread* terpisah.

## 5. Ekspor Data (Logging)
Klik **💾 SAVE LOG** di akhir sesi untuk mengekspor rangkuman performa sesi dalam file CSV:
- `logs/summary.csv` — Metrik kuantitatif.
- `logs/pulse.log` — Data mentah transisi fase dan anomali.
