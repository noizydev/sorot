# SOROT

Deteksi promosi saham terkoordinasi dan risiko kepemilikan saham IDX dari data publik.

SOROT menggabungkan dua sumber publik:

1. **Kanal Telegram publik** berbahasa Indonesia yang membahas saham. SOROT mencari pesan yang hampir
   identik di banyak kanal, kanal yang menyebut ticker yang sama dalam selang waktu berdekatan, lonjakan
   sebutan per jam, dan apakah volume transaksi ikut naik setelahnya.
2. **Daftar pemegang saham di atas 1% (KSEI)** yang dipublikasikan BEI setiap bulan. Dari sini SOROT
   menghitung free float efektif (setelah nama-nama yang merujuk ke entitas atau kelompok yang sama
   digabung), mendeteksi pemegang yang menghilang tepat di bawah ambang 1%, dan menyusun label risiko
   per emiten. Setiap label disertai alasan tertulis dan dicatat di transparency log (Merkle tree).

Hasilnya ditampilkan di dashboard web lokal (Streamlit). Pengumpulan data hanya membaca: SOROT tidak
bergabung ke kanal atau grup dan tidak mengirim pesan.

> **Penafian.** Skor dan label SOROT adalah indikator statistik dari data publik untuk riset dan
> pengawasan. Label tersebut bukan tuduhan manipulasi, bukan bukti pelanggaran, dan bukan rekomendasi
> jual/beli saham.

## Daftar isi

- [Struktur repositori](#struktur-repositori)
- [Mulai cepat](#mulai-cepat)
- [Dashboard](#dashboard)
- [Pengumpulan pesan](#pengumpulan-pesan)
- [Analisis promosi terkoordinasi](#analisis-promosi-terkoordinasi)
- [Kepemilikan, ambang 1%, dan label risiko](#kepemilikan-ambang-1-dan-label-risiko)
- [Laporan](#laporan)
- [Konfigurasi](#konfigurasi)
- [Pengujian dan kualitas kode](#pengujian-dan-kualitas-kode)
- [Etika dan keamanan data](#etika-dan-keamanan-data)

## Struktur repositori

```text
SOROT/
├── app.py                  # dashboard Streamlit (titik masuk)
├── collect.py              # pengumpul pesan Telegram publik (Telethon, hanya membaca)
├── masking.py              # penyamaran nomor telepon dan email
├── analyze.py              # pipeline analisis promosi terkoordinasi
├── ownership.py            # pipeline kepemilikan KSEI dan label risiko
├── report.py               # laporan teks + CSV per ticker
├── fetch_prices.py         # pengunduh harga harian (EOD)
├── config.yaml             # semua parameter dan lokasi berkas
├── analysis/               # modul inti: ticker, dedup, graf, burst, harga, KSEI, entitas,
│                           #   free float, ambang 1%, skor risiko, transparency log
├── dashboard/              # data, grafik, dan halaman dashboard
│   └── pages/              #   ringkasan, saham, kanal, risiko, freefloat, ambang, pipeline, kelola
├── inputs/                 # input yang ikut repo
│   ├── channels.txt        #   daftar kanal yang dipantau
│   ├── idx_tickers.csv     #   831 kode emiten (+ tanda kode yang juga kata umum)
│   ├── prices.csv          #   harga dan volume harian
│   └── entity_aliases.csv  #   koreksi manual entity resolution
├── research/               # skrip laporan riset HTML + hasil pencarian kandidat kanal
├── transparency/
│   └── heads.jsonl         # tree head Merkle, jangkar publik untuk audit label risiko
├── tests/                  # 114 pengujian otomatis (tanpa jaringan dan tanpa login)
├── data/                   # hasil pengumpulan dan analisis (tidak ikut repo)
├── reports/                # keluaran laporan (tidak ikut repo)
└── logs/                   # log proses (tidak ikut repo)
```

Data hasil pengumpulan (`data/`) tidak ikut repo. Pada clone baru, dashboard tetap berjalan dan setiap
halaman menampilkan langkah untuk mengisi datanya.

## Mulai cepat

Butuh Python 3.10 atau lebih baru.

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt    # Linux/macOS: .venv/bin/python
cp .env.example .env                                       # isi TG_API_ID dan TG_API_HASH dari https://my.telegram.org
```

Urutan pemakaian lengkap:

```bash
.venv/Scripts/python collect.py --since 2026-04-01   # 1. kumpulkan pesan (login OTP sekali di terminal)
.venv/Scripts/python analyze.py                      # 2. analisis promosi terkoordinasi
.venv/Scripts/python ownership.py all                # 3. kepemilikan KSEI + label risiko
.venv/Scripts/streamlit run app.py                   # 4. buka http://localhost:8501
```

Run pertama `collect.py` meminta nomor HP dan OTP di terminal lalu membuat `sorot.session`. File session
setara akses penuh ke akun Telegram: jangan dibagikan atau di-commit. Setelah session ada, semua langkah
juga bisa dijalankan dari halaman **Jalankan pipeline** di dashboard.

## Dashboard

```bash
.venv/Scripts/streamlit run app.py
```

Server hanya mendengarkan `localhost` (lihat `.streamlit/config.toml`). Halaman dikelompokkan menjadi tiga:

| Kelompok | Halaman | Isi |
|---|---|---|
| Analisis | Ringkasan | Jumlah pesan, kanal, sebutan ticker, pesan per minggu, ticker teratas, burst lintas kanal terbaru |
| | Jelajah saham | Grafik pesan per hari + harga + volume + penanda burst; pesan, burst, near-duplicate, co-posting, unduh CSV |
| | Jaringan & kanal | Graf co-posting/forward interaktif, profil kanal, tabel semua kanal |
| Kepemilikan & risiko | Label risiko | Peringkat skor, alasan per poin, verifikasi bukti inklusi, audit seluruh log |
| | Free float efektif | Free float proksi vs efektif, kelompok terpecah, pemilik akhir, hasil entity resolution |
| | Ambang 1% | Pemegang yang keluar tepat di bawah 1%, riwayat per emiten, koreksi data bitemporal |
| Operasional | Jalankan pipeline | Tombol pengumpulan, harga, analisis, laporan riset, kepemilikan; log tampil langsung |
| | Kelola & ekspor | Edit daftar kanal, ticker, dan `config.yaml` dengan validasi dan cadangan `.bak`; unduh CSV/ZIP/GEXF/HTML |

Hanya satu proses pipeline berjalan pada satu waktu.

## Pengumpulan pesan

```bash
.venv/Scripts/python collect.py --since 2026-04-01                     # semua kanal di inputs/channels.txt
.venv/Scripts/python collect.py --channel wigunainvestment --limit 50  # tes satu kanal
```

| Opsi | Default | Keterangan |
|---|---|---|
| `--since` | `2026-04-01` | Tanggal awal (UTC) untuk kanal yang belum pernah diambil |
| `--limit` | tanpa batas | Maksimum pesan baru per kanal per eksekusi |
| `--channels` | `inputs/channels.txt` | File daftar kanal (`#` = komentar) |
| `--channel` | tidak ada | Ambil hanya kanal ini (boleh diulang) |
| `--delay` | `10` | Jeda antarkanal (detik) + jitter acak hingga 50% |
| `--include-groups` | mati | Ikut ambil supergroup publik |
| `--db`, `--raw-dir`, `--log-file` | `data/...`, `logs/...` | Lokasi output |

Eksekusi ulang bersifat **incremental**: tiap kanal dilanjutkan dari `MAX(message_id)` yang sudah
tersimpan. Pesan dibaca dari terlama ke terbaru, jadi `--limit` atau run yang terhenti tidak meninggalkan
celah. Jika `--since` diubah setelah kanal pernah diambil, kanal itu tetap dilanjutkan dari id terakhir.
Saat Telegram meminta jeda (`FloodWaitError`), pengumpul menunggu sesuai durasi yang diminta.

Keluaran:

- `data/raw/<kanal>.jsonl`: satu baris per pesan, `{"channel", "collected_at", "message": <Message.to_dict()>}`.
  Isinya apa adanya (tanpa penyamaran). Setelah crash bisa ada duplikat; dedupe dengan `channel` + `message.id`.
- `data/messages.db`: tabel `messages` dengan kunci `(channel, message_id)`.
- `logs/collect.log`: progres per kanal, FloodWait, dan kanal yang dilewati.

| Kolom `messages` | Isi |
|---|---|
| `channel` | username kanal (huruf kecil) |
| `message_id` | id pesan di kanal |
| `date` | waktu kirim, ISO 8601 UTC |
| `text` | teks pesan; nomor telepon menjadi `[PHONE]`, email menjadi `[EMAIL]` |
| `views`, `forwards` | jumlah tayang dan diteruskan saat diambil |
| `fwd_from_channel_id`, `fwd_from_name` | kanal sumber jika pesan diteruskan |
| `reply_to` | id pesan yang dibalas |
| `has_media` | 1 jika ada foto/dokumen (pratinjau tautan tidak dihitung) |
| `collected_at` | waktu pengambilan, UTC |

Pesan yang sudah dihapus admin tidak dikembalikan Telegram sehingga tampak sebagai celah pada
`message_id`. Pesan servis (pin, ganti judul) ikut tersimpan dengan `text` kosong.

## Analisis promosi terkoordinasi

```bash
.venv/Scripts/python analyze.py                      # semua langkah
.venv/Scripts/python analyze.py --steps dedup,graph  # sebagian langkah
```

Input: `inputs/idx_tickers.csv` (kolom `ticker`, opsional `ambiguous` = 1 untuk kode yang juga kata umum)
dan `inputs/prices.csv` (kolom `date`, `ticker`, `volume`; hanya untuk langkah `prices`). Setiap langkah
membangun ulang tabelnya di `messages.db`:

| Langkah | Tabel | Ringkas |
|---|---|---|
| `mentions` | `mentions` | Token 4 huruf kapital yang ada di daftar ticker. Kode yang juga kata umum (`tickers.common_words` atau kolom `ambiguous`) hanya dihitung jika berawalan `$`/`#` atau ada kata konteks (TP, target, entry, SL, buy, ...) dalam 5 kata. |
| `dedup` | `dup_clusters`, `dup_members` | MinHash-LSH atas shingle 5 karakter dari teks ternormalisasi, Jaccard ≥ 0,8, kandidat LSH diverifikasi ulang. Mencatat ukuran, jumlah kanal, dan rentang waktu cluster. |
| `graph` | `graph_edges` + `data/analysis/coposting.gexf` | Edge co-posting: dua kanal menyebut ticker sama dalam 30 menit, bobot `raw / sqrt(n_a * n_b)`. Edge forward: kanal sumber ke kanal penerus. GEXF bisa dibuka di Gephi. |
| `bursts` | `bursts` | Mention per ticker per jam, z-score terhadap rolling 7 hari sebelumnya; burst bila z > 3 dan minimal 3 mention. `min_std` mencegah z tak hingga saat baseline nol. |
| `prices` | `burst_volume` | H+1 = hari bursa pertama setelah burst (WIB); rasio = volume H+1 / rata-rata volume 20 hari bursa sebelumnya. |

## Kepemilikan, ambang 1%, dan label risiko

Sumber: PDF **"Pemegang Saham di atas 1% (KSEI)"** dari Pengumuman BEI (sejak Maret 2026). Simpan lampiran
data sebagai `data/shareholders/pdf/YYYYMMDD_lamp1_data.pdf` dan surat pengantarnya sebagai
`YYYYMMDD_pengantar.pdf` (berisi waktu terbit, nomor pengumuman, dan nomor yang dikoreksi).

```bash
.venv/Scripts/python ownership.py all      # import -> freefloat -> threshold -> risk (+ publikasi ke log)
.venv/Scripts/python ownership.py verify   # audit transparency log
```

| Langkah | Isi |
|---|---|
| `import` | Parse PDF (tabel bergaris, atau per posisi kolom untuk bulan tanpa garis; PDF terpotong diperbaiki dan ditandai). Disimpan **bitemporal** di `data/ownership.db`: `valid_date` = posisi menurut KSEI, `tx_from`/`tx_to` = kapan versi itu berlaku menurut waktu terbit BEI. Koreksi menambah versi baru dan hanya menutup versi lama. |
| `freefloat` | **Entity resolution**: normalisasi nama, pencocokan kabur RapidFuzz per blok, kesinambungan jumlah saham antarbulan, alias manual `inputs/entity_aliases.csv`. **Kepemilikan terintegrasi** `T = W(I - W)^-1` memberi pemilik akhir lewat rantai perusahaan tercatat. **Free float efektif** = 100% dikurangi kelompok ≥ 5%; akun nominee/kustodian dianggap publik. |
| `threshold` | Pemegang yang tercatat di satu posisi lalu hilang di posisi berikutnya. **Tepat di bawah ambang** bila porsinya < 1% + 0,5 poin dan tidak terjelaskan dilusi atau pengalihan utuh; diperkuat bila muncul kembali, keluar serentak, atau diganti pemegang baru dekat ambang. |
| `risk` | Skor 0 sampai 100 per emiten dari poin di `config.yaml` (`risk.points`), setiap poin dengan kalimat alasan. Semua label masuk **transparency log** (Merkle tree RFC 6962): entri di `data/transparency/log.jsonl`, tree head di `transparency/heads.jsonl` (ikut git sebagai jangkar). `verify` menghitung ulang semua leaf, root, dan consistency proof. |

## Laporan

```bash
.venv/Scripts/python report.py --ticker BUMI --from 2026-04-01 --to 2026-06-30
```

`report.py` menulis `reports/<TICKER>_<from>_<to>.txt` (mention per kanal, burst + rasio volume, cluster
near-duplicate, pasangan kanal co-posting) dan `.csv` (satu baris per pesan dengan penanda jam burst dan
cluster). Tanggal dibaca sebagai UTC, inklusif.

Laporan riset HTML (`reports/laporan_riset_sorot.html`) dibangun dari hasil `analyze.py`:

```bash
.venv/Scripts/python research/validate_tickers.py   # opsional: bangun ulang daftar ticker + harga dari kode di pesan
.venv/Scripts/python research/research_data.py      # skor, uji volume, jaringan, studi kasus -> data/analysis/research.json
.venv/Scripts/python research/research_kinds.py     # jenis pesan di jam burst, volume H0 vs H+1
.venv/Scripts/python research/build_report.py       # research.json + template.html -> reports/
```

`validate_tickers.py` memeriksa setiap token 4 huruf kapital yang muncul di ≥ 5 pesan ke Yahoo Finance
(`.JK`) dan mempertahankan tanda `ambiguous` yang sudah ada. Studi kasus bisa diganti dengan
`research_data.py --cases TICKER1,TICKER2 --hours TICKER1=YYYY-MM-DDTHH`.

## Konfigurasi

Semua parameter ada di `config.yaml`; path relatif dihitung dari folder file tersebut. Variabel lingkungan
`SOROT_CONFIG` dapat menunjuk ke file konfigurasi lain.

| Bagian | Isi |
|---|---|
| `paths` | Lokasi input (`inputs/`), basis data, keluaran, dan transparency log |
| `tickers` | Kata umum yang juga kode saham dan kata konteks |
| `dedup`, `coposting`, `bursts`, `prices` | Parameter analisis promosi |
| `ownership`, `threshold` | Ambang pencocokan nama, batas pengendali, kelompok terkonsentrasi, ambang 1% |
| `risk` | Poin setiap sinyal dan batas label Sedang/Tinggi |

## Pengujian dan kualitas kode

```bash
.venv/Scripts/python -m pytest -q    # 114 tes, tanpa jaringan dan tanpa login
ruff check .                         # lint, aturan di pyproject.toml (pip install ruff)
```

## Etika dan keamanan data

- Hanya kanal publik; tautan undangan privat ditolak, grup dilewati secara default, dan identitas
  pengirim tidak disimpan di SQLite.
- `.env`, `*.session`, `data/`, `logs/`, dan `reports/` tidak masuk git.
- Kredensial tidak pernah dicetak atau ditulis ke log.
- Data kepemilikan berasal dari pengumuman resmi BEI. PDF dan basis data turunannya tidak ikut repo.
- Label risiko adalah indikator untuk diperiksa lebih lanjut, bukan kesimpulan hukum (lihat penafian di atas).
