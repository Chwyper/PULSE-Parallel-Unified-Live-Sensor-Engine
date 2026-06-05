# Pemrosesan Paralel dan Hukum Amdahl

Proyek PULSE ditujukan untuk mata kuliah IFB-206 *Parallel & Distributed Systems*. Alih-alih menggunakan *Threading* biasa yang dibatasi oleh Python GIL (*Global Interpreter Lock*), PULSE menggunakan modul `multiprocessing`.

## Konsep MIMD (Multiple Instruction, Multiple Data)
Sistem menugaskan 8 proses pekerja (*workers*) untuk mengekstrak fitur dari 8 sensor secara bersamaan pada *core* CPU yang berbeda.
- **Instruksi berbeda:** Memproses ECG membutuhkan ekstraksi nilai absolut maksimum (R-Peak), berbeda dengan memproses Accelerometer yang mencari perubahan tanda (Zero-crossing).
- **Data berbeda:** Setiap pekerja memproses *payload* sensor masing-masing.

## Pengujian Performa (Benchmarker)
Modul `benchmarker.py` menyimulasikan ratusan paket data sensor dan membandingkan waktu yang dibutuhkan antara pemrosesan *Sequential* (satu per satu) dan *Parallel* (bersamaan).

### Hasil Benchmark
Berdasarkan log yang diekspor sistem:
- **Waktu Sequential (Rata-rata):** ~58.71 detik
- **Waktu Parallel (Rata-rata):** ~11.50 detik
- **Speedup yang terukur:** **5.10x** lipat lebih cepat.
- **Efisiensi:** 63.8% (Mengingat ada hambatan kecil dari komunikasi antar-proses/IPC via antrean).

### Hukum Amdahl (Amdahl's Law)
Sistem secara otomatis menghitung *Parallel Fraction* ($P$) menggunakan rumus Amdahl:

$$ S = \frac{1}{(1 - P) + \frac{P}{N}} $$

Dengan *Speedup* ($S$) = 5.10x dan *Processors* ($N$) = 8, sistem mengkalkulasi bahwa **$P \approx 91.8\%$** dari beban kerja aplikasi ini adalah pekerjaan paralel murni, dan sisanya 8.2% adalah porsi sekuensial tak terhindarkan (seperti mengumpulkan hasil akhirnya di *Fusion Engine* untuk dirender GUI).

### Grafik Distribusi Waktu
*(Anda dapat melihat visualisasi lengkapnya pada gambar `logs/benchmark_chart.png` setelah menjalankan fitur Benchmarker).*
