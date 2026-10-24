"""Kumpulkan pesan dari kanal Telegram PUBLIK (hanya membaca) ke raw JSONL + SQLite.

Contoh:
    python collect.py --since 2026-04-01 --limit 500
    python collect.py --channel wigunainvestment --limit 50

Skrip ini tidak pernah join kanal/grup dan tidak pernah mengirim pesan:
username di-resolve lalu riwayatnya dibaca lewat messages.getHistory.
"""
import argparse
import asyncio
import base64
import json
import logging
import os
import random
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.errors import (
    ChannelInvalidError,
    ChannelPrivateError,
    FloodWaitError,
    UsernameInvalidError,
    UsernameNotOccupiedError,
)
from telethon.tl.types import Channel, MessageMediaWebPage, PeerChannel

from masking import mask_pii

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_SINCE = "2026-04-01"
BATCH_SIZE = 100
FLOOD_PAD_SECONDS = 5
HISTORY_WAIT_SECONDS = 1  # jeda antar-request getHistory (per 100 pesan)

USERNAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{3,31}$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    channel             TEXT    NOT NULL,
    message_id          INTEGER NOT NULL,
    date                TEXT    NOT NULL,
    text                TEXT,
    views               INTEGER,
    forwards            INTEGER,
    fwd_from_channel_id INTEGER,
    fwd_from_name       TEXT,
    reply_to            INTEGER,
    has_media           INTEGER NOT NULL,
    collected_at        TEXT    NOT NULL,
    PRIMARY KEY (channel, message_id)
);
CREATE INDEX IF NOT EXISTS idx_messages_date ON messages(date);
CREATE INDEX IF NOT EXISTS idx_messages_fwd ON messages(fwd_from_channel_id);
"""

INSERT_SQL = """
INSERT OR IGNORE INTO messages (
    channel, message_id, date, text, views, forwards,
    fwd_from_channel_id, fwd_from_name, reply_to, has_media, collected_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

log = logging.getLogger("collect")


class SkipChannel(Exception):
    """Kanal dilewati (privat, bukan kanal, tidak ada, dsb.)."""


def parse_args():
    p = argparse.ArgumentParser(description="Kumpulkan pesan kanal Telegram publik (read-only).")
    p.add_argument("--since", default=DEFAULT_SINCE,
                   help=f"Tanggal awal UTC YYYY-MM-DD untuk kanal yang belum pernah diambil (default {DEFAULT_SINCE})")
    p.add_argument("--limit", type=int, default=None,
                   help="Maksimum pesan baru per kanal per eksekusi (default: tanpa batas)")
    p.add_argument("--channels", default=str(BASE_DIR / "channels.txt"),
                   help="File daftar username, satu per baris")
    p.add_argument("--channel", action="append",
                   help="Ambil hanya username ini (abaikan --channels); boleh diulang")
    p.add_argument("--db", default=str(BASE_DIR / "data" / "messages.db"), help="Path SQLite")
    p.add_argument("--raw-dir", default=str(BASE_DIR / "data" / "raw"), help="Folder raw JSONL")
    p.add_argument("--log-file", default=str(BASE_DIR / "logs" / "collect.log"), help="File log")
    p.add_argument("--delay", type=float, default=10.0,
                   help="Jeda dasar antarkanal dalam detik (+ jitter acak hingga 50%%)")
    p.add_argument("--include-groups", action="store_true",
                   help="Ikut ambil supergroup publik (default: hanya kanal broadcast)")
    args = p.parse_args()

    try:
        args.since_dt = datetime.strptime(args.since, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        p.error("--since harus berformat YYYY-MM-DD")
    if args.limit is not None and args.limit <= 0:
        p.error("--limit harus bilangan positif")
    if args.delay < 0:
        p.error("--delay tidak boleh negatif")
    return args


def setup_logging(log_file):
    Path(log_file).parent.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(fmt)
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    logging.basicConfig(level=logging.INFO, handlers=[file_handler, console])
    logging.getLogger("telethon").setLevel(logging.WARNING)


def load_credentials():
    """Baca kredensial dari .env. Nilainya tidak pernah dicetak atau ditulis ke log."""
    load_dotenv(BASE_DIR / ".env")
    missing = [name for name in ("TG_API_ID", "TG_API_HASH") if not os.getenv(name)]
    if missing:
        sys.exit(f"Variabel {', '.join(missing)} belum diisi di .env (lihat .env.example).")
    try:
        api_id = int(os.environ["TG_API_ID"])
    except ValueError:
        sys.exit("TG_API_ID harus berupa angka.")
    session = os.getenv("TG_SESSION", "sorot")
    return api_id, os.environ["TG_API_HASH"], str(BASE_DIR / session)


def normalize_username(line):
    """Ubah '@nama', 't.me/nama', 'https://t.me/s/nama' menjadi 'nama'. None jika tidak valid."""
    s = line.strip()
    if not s or s.startswith("#"):
        return None
    if "joinchat" in s or "/+" in s or s.startswith("+"):
        raise ValueError("tautan undangan privat tidak didukung")
    s = re.sub(r"^(?:https?://)?(?:www\.)?(?:t|telegram)\.me/(?:s/)?", "", s)
    s = s.lstrip("@").split("/")[0].split("?")[0]
    if not USERNAME_RE.match(s):
        raise ValueError("bukan username Telegram yang valid")
    return s


def load_channels(args):
    lines = args.channel if args.channel else Path(args.channels).read_text(encoding="utf-8").splitlines()
    channels = []
    seen = set()
    for line in lines:
        try:
            name = normalize_username(line)
        except ValueError as e:
            log.warning("Lewati %r: %s", line.strip(), e)
            continue
        if name and name.lower() not in seen:
            seen.add(name.lower())
            channels.append(name)
    return channels


def open_db(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    return conn


def last_message_id(conn, channel):
    row = conn.execute("SELECT MAX(message_id) FROM messages WHERE channel = ?", (channel,)).fetchone()
    return row[0] or 0


def _json_default(obj):
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, bytes):
        return base64.b64encode(obj).decode("ascii")
    return repr(obj)


def to_row(channel, msg, collected_at):
    fwd_id = fwd_name = None
    # getattr karena MessageService (pin, ganti judul, dsb.) tidak punya semua atribut Message.
    fwd = getattr(msg, "fwd_from", None)
    if fwd is not None:
        if isinstance(fwd.from_id, PeerChannel):
            fwd_id = fwd.from_id.channel_id
            # Judul kanal sumber diambil dari entitas yang sudah ikut di respons (tanpa request tambahan).
            source = getattr(getattr(msg, "forward", None), "chat", None)
            fwd_name = getattr(source, "title", None)
        fwd_name = fwd_name or fwd.from_name

    reply_to = getattr(getattr(msg, "reply_to", None), "reply_to_msg_id", None)
    media = getattr(msg, "media", None)
    has_media = media is not None and not isinstance(media, MessageMediaWebPage)

    return (
        channel,
        msg.id,
        msg.date.astimezone(timezone.utc).isoformat(),
        mask_pii(getattr(msg, "message", None)),
        getattr(msg, "views", None),
        getattr(msg, "forwards", None),
        fwd_id,
        fwd_name,
        reply_to,
        int(has_media),
        collected_at,
    )


def flush(conn, raw_path, channel, batch):
    """Simpan batch. Raw ditulis lebih dulu, baru SQLite di-commit: jika proses mati di
    tengah, raw mungkin berisi duplikat (dedupe dengan channel+id) tapi tidak ada yang hilang."""
    if not batch:
        return 0
    collected_at = datetime.now(timezone.utc).isoformat()
    raw_lines = []
    cur = conn.cursor()
    for msg in batch:
        cur.execute(INSERT_SQL, to_row(channel, msg, collected_at))
        if cur.rowcount == 1:
            record = {"channel": channel, "collected_at": collected_at, "message": msg.to_dict()}
            raw_lines.append(json.dumps(record, ensure_ascii=False, default=_json_default))
    if raw_lines:
        with open(raw_path, "a", encoding="utf-8") as f:
            f.write("\n".join(raw_lines) + "\n")
            f.flush()
            os.fsync(f.fileno())
    conn.commit()
    batch.clear()
    return len(raw_lines)


async def resolve_public(client, username, include_groups):
    """Resolve username tanpa join. Tolak entitas yang bukan kanal/grup publik."""
    while True:
        try:
            entity = await client.get_entity(username)
            break
        except FloodWaitError as e:
            log.warning("FloodWait %ss saat resolve @%s, tidur dulu", e.seconds, username)
            await asyncio.sleep(e.seconds + FLOOD_PAD_SECONDS)
        except (UsernameNotOccupiedError, UsernameInvalidError, ValueError) as e:
            raise SkipChannel(f"username tidak ditemukan ({type(e).__name__})")
        except (ChannelPrivateError, ChannelInvalidError) as e:
            raise SkipChannel(f"kanal privat/tidak bisa diakses ({type(e).__name__})")

    if not isinstance(entity, Channel):
        raise SkipChannel(f"bukan kanal ({type(entity).__name__})")
    if not (entity.username or getattr(entity, "usernames", None)):
        raise SkipChannel("tidak punya username publik")
    if entity.megagroup and not include_groups:
        raise SkipChannel("supergroup (pakai --include-groups untuk ikut mengambil)")
    return entity


async def collect_channel(client, conn, args, username):
    channel = username.lower()
    entity = await resolve_public(client, username, args.include_groups)
    kind = "grup" if entity.megagroup else "kanal"
    raw_path = Path(args.raw_dir) / f"{channel}.jsonl"
    raw_path.parent.mkdir(parents=True, exist_ok=True)

    start_id = last_message_id(conn, channel)
    if start_id:
        log.info("@%s (%s): lanjut dari message_id > %s", channel, kind, start_id)
    else:
        log.info("@%s (%s): ambil pertama kali sejak %s", channel, kind, args.since)

    saved = 0
    fetched = 0
    batch = []
    while True:
        last_id = last_message_id(conn, channel)
        remaining = None if args.limit is None else args.limit - fetched
        if remaining is not None and remaining <= 0:
            break
        # reverse=True: dari terlama ke terbaru, jadi --limit yang memotong tidak meninggalkan celah.
        kwargs = {"limit": remaining, "reverse": True, "wait_time": HISTORY_WAIT_SECONDS}
        if last_id:
            kwargs["min_id"] = last_id
        else:
            kwargs["offset_date"] = args.since_dt
        try:
            async for msg in client.iter_messages(entity, **kwargs):
                if msg.date < args.since_dt:
                    continue
                batch.append(msg)
                fetched += 1
                if len(batch) >= BATCH_SIZE:
                    saved += flush(conn, raw_path, channel, batch)
                    log.info("@%s: %s pesan baru tersimpan (id terakhir %s)", channel, saved, msg.id)
            saved += flush(conn, raw_path, channel, batch)
            break
        except FloodWaitError as e:
            saved += flush(conn, raw_path, channel, batch)
            log.warning("FloodWait %ss di @%s setelah %s pesan, tidur lalu lanjut", e.seconds, channel, saved)
            await asyncio.sleep(e.seconds + FLOOD_PAD_SECONDS)

    log.info("@%s: selesai, %s pesan baru, id terakhir %s", channel, saved, last_message_id(conn, channel))
    return saved


async def main():
    args = parse_args()
    setup_logging(args.log_file)
    api_id, api_hash, session_path = load_credentials()
    channels = load_channels(args)
    if not channels:
        sys.exit("Tidak ada username kanal yang valid untuk diproses.")
    log.info("Mulai: %s kanal, since=%s, limit=%s", len(channels), args.since, args.limit)

    conn = open_db(args.db)
    client = TelegramClient(session_path, api_id, api_hash)
    client.flood_sleep_threshold = 0  # semua FloodWait dilempar ke kode ini supaya tercatat di log
    totals = {}
    try:
        await client.start()  # run pertama meminta nomor HP + OTP di terminal
        for i, username in enumerate(channels):
            started = datetime.now(timezone.utc)
            try:
                totals[username] = await collect_channel(client, conn, args, username)
            except SkipChannel as e:
                log.warning("Lewati @%s: %s", username, e)
                totals[username] = None
            except Exception:  # satu kanal gagal tidak boleh menghentikan yang lain
                log.exception("Gagal mengambil @%s", username)
                totals[username] = None
            log.info("@%s: durasi %.1fs", username, (datetime.now(timezone.utc) - started).total_seconds())
            if i < len(channels) - 1 and args.delay:
                await asyncio.sleep(args.delay + random.uniform(0, args.delay / 2))
    finally:
        conn.close()
        await client.disconnect()

    ok = {k: v for k, v in totals.items() if v is not None}
    log.info("Selesai: %s kanal berhasil, %s dilewati/gagal, %s pesan baru total",
             len(ok), len(totals) - len(ok), sum(ok.values()))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.warning("Dihentikan pengguna; batch yang sudah di-commit tetap tersimpan.")
