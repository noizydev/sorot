"""Tes tanpa jaringan/login: parsing username, pemetaan kolom, penyimpanan batch."""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from telethon.tl.types import (
    Message,
    MessageFwdHeader,
    MessageMediaPhoto,
    MessageMediaWebPage,
    MessageReplyHeader,
    MessageService,
    MessageActionPinMessage,
    PeerChannel,
    WebPageEmpty,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import collect  # noqa: E402

DATE = datetime(2026, 10, 23, 2, 30, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    "line, expected",
    [
        ("wigunainvestment", "wigunainvestment"),
        ("@SahamTalkID", "SahamTalkID"),
        ("https://t.me/s/Research_PSI", "Research_PSI"),
        ("t.me/creativetrader/123", "creativetrader"),
        ("   ", None),
        ("# komentar", None),
    ],
)
def test_normalize_username(line, expected):
    assert collect.normalize_username(line) == expected


@pytest.mark.parametrize("line", ["https://t.me/+AbCdEf123", "t.me/joinchat/AAAAAEMKPak", "ab", "nama spasi"])
def test_normalize_rejects(line):
    with pytest.raises(ValueError):
        collect.normalize_username(line)


def make_msg(msg_id, text, **kw):
    return Message(id=msg_id, peer_id=PeerChannel(111), date=DATE, message=text, **kw)


def test_to_row_maps_and_masks():
    msg = make_msg(
        42,
        "BUY BBCA, chat 0812-3456-7890 atau admin@contoh.id",
        views=1500,
        forwards=12,
        fwd_from=MessageFwdHeader(date=DATE, from_id=PeerChannel(999), from_name=None),
        reply_to=MessageReplyHeader(reply_to_msg_id=40),
        media=MessageMediaPhoto(),
    )
    row = collect.to_row("kanal", msg, "2026-10-24T00:00:00+00:00")
    assert row == (
        "kanal", 42, "2026-10-23T02:30:00+00:00",
        "BUY BBCA, chat [PHONE] atau [EMAIL]",
        1500, 12, 999, None, 40, 1, "2026-10-24T00:00:00+00:00",
    )


def test_to_row_webpage_is_not_media_and_service_message():
    web = make_msg(1, "lihat https://idx.co.id", media=MessageMediaWebPage(webpage=WebPageEmpty(id=1)))
    assert collect.to_row("k", web, "t")[9] == 0
    svc = MessageService(id=2, peer_id=PeerChannel(111), date=DATE, action=MessageActionPinMessage())
    row = collect.to_row("k", svc, "t")
    assert row[1] == 2 and row[3] is None and row[6] is None and row[8] is None


def test_flush_is_idempotent(tmp_path):
    conn = collect.open_db(str(tmp_path / "m.db"))
    raw = tmp_path / "k.jsonl"
    msgs = [make_msg(i, f"pesan {i} wa.me/6281234567890") for i in (1, 2, 3)]

    assert collect.flush(conn, raw, "k", list(msgs)) == 3
    assert collect.flush(conn, raw, "k", list(msgs)) == 0  # rerun: tidak ada duplikat
    assert collect.last_message_id(conn, "k") == 3

    texts = [r[0] for r in conn.execute("SELECT text FROM messages ORDER BY message_id")]
    assert texts == ["pesan 1 [PHONE]", "pesan 2 [PHONE]", "pesan 3 [PHONE]"]

    lines = raw.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    first = json.loads(lines[0])
    assert first["channel"] == "k"
    assert first["message"]["message"] == "pesan 1 wa.me/6281234567890"  # raw apa adanya
    conn.close()


class FakeClient:
    """Meniru iter_messages: urut naik, menghormati min_id/limit, FloodWait sekali di tengah."""

    def __init__(self, ids, flood_after=None):
        self.ids = ids
        self.flood_after = flood_after
        self.calls = []

    def iter_messages(self, entity, limit=None, min_id=0, offset_date=None, reverse=False, wait_time=None):
        self.calls.append({"min_id": min_id, "limit": limit, "offset_date": offset_date})
        assert reverse is True
        client = self

        async def gen():
            out = 0
            for i in client.ids:
                if i <= min_id or (limit is not None and out >= limit):
                    continue
                if client.flood_after is not None and out == client.flood_after:
                    client.flood_after = None
                    raise collect.FloodWaitError(request=None, capture=3)
                out += 1
                yield make_msg(i, f"m{i}")

        return gen()


def run_collect(monkeypatch, tmp_path, client, limit):
    import argparse
    import asyncio
    from types import SimpleNamespace

    async def fake_resolve(c, username, include_groups):
        return SimpleNamespace(megagroup=False)

    async def no_sleep(_):
        return None

    monkeypatch.setattr(collect, "resolve_public", fake_resolve)
    monkeypatch.setattr(collect.asyncio, "sleep", no_sleep)
    args = argparse.Namespace(
        limit=limit, raw_dir=str(tmp_path / "raw"), include_groups=False,
        since="2026-04-01", since_dt=datetime(2026, 4, 1, tzinfo=timezone.utc),
    )
    conn = collect.open_db(str(tmp_path / "m.db"))
    return asyncio.run(collect.collect_channel(client, conn, args, "Kanal")), conn


def test_incremental_with_limit_and_floodwait(monkeypatch, tmp_path):
    ids = list(range(1, 251))
    client = FakeClient(ids, flood_after=120)
    saved, conn = run_collect(monkeypatch, tmp_path, client, limit=200)
    assert saved == 200
    assert collect.last_message_id(conn, "kanal") == 200
    # call pertama pakai offset_date (belum ada data), setelah FloodWait lanjut dari min_id=120
    assert client.calls[0]["offset_date"] is not None and client.calls[0]["min_id"] == 0
    assert client.calls[1]["min_id"] == 120 and client.calls[1]["limit"] == 80
    conn.close()

    client2 = FakeClient(ids)
    saved2, conn2 = run_collect(monkeypatch, tmp_path, client2, limit=200)
    assert saved2 == 50 and client2.calls[0]["min_id"] == 200
    assert conn2.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 250
    raw_lines = (tmp_path / "raw" / "kanal.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(raw_lines) == 250
    conn2.close()
