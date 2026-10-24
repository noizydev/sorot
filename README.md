# SOROT — Kolektor Pesan Kanal Telegram Saham

Mengumpulkan pesan dari kanal Telegram **publik** berbahasa Indonesia yang membahas saham IDX,
untuk riset deteksi promosi saham terkoordinasi. Hanya membaca: tidak join kanal/grup dan
tidak mengirim pesan.

## Isi proyek

| File | Fungsi |
|---|---|
| `collect.py` | CLI pengumpul pesan (Telethon) |
| `masking.py` | Penyamaran nomor telepon & email sebelum masuk SQLite |
| `channels.txt` | Daftar username kanal, satu per baris (`#` = komentar) |
| `channels_candidates.csv` | Hasil pencarian kandidat kanal beserta sumber & catatan |
| `tests/` | Tes offline (tanpa jaringan/login) |
| `.env.example` | Template kredensial |

## Setup

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt   # Linux/macOS: .venv/bin/python
cp .env.example .env    # isi TG_API_ID dan TG_API_HASH dari https://my.telegram.org
```

Run pertama meminta nomor HP + OTP di terminal dan membuat `sorot.session`.
File session setara akses penuh ke akun Telegram: jangan dibagikan atau di-commit.

## Pemakaian

```bash
# semua kanal di channels.txt sejak 1 April 2026
.venv/Scripts/python collect.py --since 2026-04-01

# tes satu kanal, maksimal 50 pesan baru
.venv/Scripts/python collect.py --channel wigunainvestment --limit 50
```

| Opsi | Default | Keterangan |
|---|---|---|
| `--since` | `2026-04-01` | Tanggal awal (UTC) untuk kanal yang belum pernah diambil |
| `--limit` | tanpa batas | Maksimum pesan baru per kanal per eksekusi |
| `--channels` | `channels.txt` | File daftar kanal |
| `--channel` | – | Ambil hanya kanal ini (boleh diulang) |
| `--delay` | `10` | Jeda antarkanal (detik) + jitter acak hingga 50% |
| `--include-groups` | mati | Ikut ambil supergroup publik |
| `--db`, `--raw-dir`, `--log-file` | `data/…`, `logs/…` | Lokasi output |

Eksekusi ulang bersifat **incremental**: tiap kanal dilanjutkan dari `MAX(message_id)` yang sudah
tersimpan. Pesan dibaca dari terlama ke terbaru, jadi `--limit` atau run yang terhenti tidak
meninggalkan celah. Jika `--since` diubah setelah kanal pernah diambil, kanal itu tetap
dilanjutkan dari id terakhir (tidak mengisi mundur).

## Output

- `data/raw/<kanal>.jsonl` — satu baris per pesan: `{"channel", "collected_at", "message": <Message.to_dict()>}`.
  Isinya **apa adanya** (tanpa penyamaran). Setelah crash bisa ada duplikat; dedupe dengan `channel` + `message.id`.
- `data/messages.db` — tabel `messages`, kunci `(channel, message_id)`:

| Kolom | Isi |
|---|---|
| `channel` | username kanal (huruf kecil) |
| `message_id` | id pesan di kanal |
| `date` | waktu kirim, ISO 8601 UTC |
| `text` | teks pesan, nomor telepon → `[PHONE]`, email → `[EMAIL]` |
| `views`, `forwards` | jumlah tayang & diteruskan saat diambil |
| `fwd_from_channel_id`, `fwd_from_name` | kanal sumber jika pesan diteruskan |
| `reply_to` | id pesan yang dibalas |
| `has_media` | 1 jika ada foto/dokumen/dsb. (pratinjau tautan tidak dihitung) |
| `collected_at` | waktu pengambilan, UTC |

- `logs/collect.log` — progres per kanal, FloodWait, dan kanal yang dilewati.

Catatan: pesan yang sudah dihapus admin tidak dikembalikan Telegram, sehingga tampak sebagai
celah pada `message_id`. Pesan servis (pin, ganti judul) ikut tersimpan dengan `text` kosong.

## Tes

```bash
.venv/Scripts/python -m pytest -q tests
```

## Etika & keamanan data

- Hanya kanal publik; tautan undangan privat ditolak, grup dilewati secara default dan identitas
  pengirim tidak disimpan di SQLite.
- `.env`, `*.session`, `data/`, dan `logs/` tidak masuk git.
- Kredensial tidak pernah dicetak atau ditulis ke log.
