"""Uji ujung-ke-ujung dengan data sintetis: 3 kanal mempromosikan BUMI bersamaan."""
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import collect  # noqa: E402
from analysis import bursts, db, dedup, graph, prices, tickers  # noqa: E402
from analysis.config import load_config  # noqa: E402
from report import build_report  # noqa: E402

PROMO = ("Saham $BUMI siap terbang! Akumulasi bandar sudah terlihat jelas, entry di area 120-125 "
         "target 150 dalam seminggu. Join VIP sekarang sebelum terlambat, slot terbatas.")
T0 = datetime(2026, 5, 1, tzinfo=timezone.utc)
BURST = datetime(2026, 5, 4, 3, tzinfo=timezone.utc)  # Senin 10:00 WIB


def iso(dt):
    return dt.isoformat()


@pytest.fixture
def env(tmp_path):
    cfg = load_config()
    cfg["paths"] = {
        "db": tmp_path / "messages.db",
        "raw_dir": tmp_path / "raw",
        "tickers_csv": tmp_path / "idx_tickers.csv",
        "prices_csv": tmp_path / "prices.csv",
        "output_dir": tmp_path / "analysis",
        "reports_dir": tmp_path / "reports",
    }
    cfg["tickers"]["common_words"] = ["BUMI"]
    cfg["paths"]["tickers_csv"].write_text("ticker\nBBCA\nBUMI\nGOTO\n", encoding="utf-8")

    rows = []  # channel, id, date, text, fwd_from_channel_id
    for i in range(16):  # latar: kanal a menyebut BBCA tiap 6 jam
        rows.append(("a", i + 1, T0 + timedelta(hours=6 * i), f"Update BBCA stabil {i}", None))
    burst_posts = [("a", 5), ("a", 10), ("b", 12), ("c", 20), ("b", 40), ("c", 50)]
    for n, (ch, minute) in enumerate(burst_posts):
        text = PROMO if n % 2 == 0 else "🚀 " + PROMO.upper()
        rows.append((ch, 100 + n, BURST + timedelta(minutes=minute), text, None))
    rows.append(("c", 200, T0 + timedelta(days=1, hours=2), "Diteruskan: BBCA kuat", 1001))

    conn = collect.open_db(str(cfg["paths"]["db"]))
    conn.executemany(
        "INSERT INTO messages VALUES (?, ?, ?, ?, 1000, 5, ?, NULL, NULL, 0, ?)",
        [(ch, mid, iso(dt), text, fwd, iso(T0)) for ch, mid, dt, text, fwd in rows],
    )
    conn.commit()
    conn.close()

    cfg["paths"]["raw_dir"].mkdir()
    (cfg["paths"]["raw_dir"] / "a.jsonl").write_text(
        json.dumps({"channel": "a", "message": {"id": 1, "peer_id": {"_": "PeerChannel", "channel_id": 1001}}}) + "\n",
        encoding="utf-8")

    days = pd.bdate_range("2026-04-01", "2026-05-08")
    vol = [5000 if d.date() == date(2026, 5, 5) else 1000 for d in days]
    pd.DataFrame({"date": days.date, "ticker": "BUMI", "close": 120, "volume": vol}).to_csv(
        cfg["paths"]["prices_csv"], index=False)

    conn = db.connect(cfg["paths"]["db"])
    for step in (tickers.run, dedup.run, graph.run, bursts.run, prices.run):
        step(conn, cfg)
    yield conn, cfg
    conn.close()


def q(conn, sql):
    return [tuple(r) for r in conn.execute(sql).fetchall()]


def test_mentions(env):
    conn, _ = env
    assert q(conn, "SELECT ticker, COUNT(*), COUNT(DISTINCT channel) FROM mentions GROUP BY ticker") == [
        ("BBCA", 17, 2), ("BUMI", 6, 3)]


def test_dup_cluster(env):
    conn, _ = env
    clusters = q(conn, "SELECT size, n_channels, span_minutes FROM dup_clusters")
    assert clusters == [(6, 3, 45.0)]


def test_graph_edges(env):
    conn, cfg = env
    edges = {(s, t, k): (raw, round(norm, 4)) for s, t, k, raw, norm in q(conn, "SELECT * FROM graph_edges")}
    # n pesan: a=18, b=2, c=3
    assert edges[("a", "b", "coposting")] == (3, round(3 / 6, 4))
    assert edges[("a", "c", "coposting")] == (2, round(2 / (18 * 3) ** 0.5, 4))
    assert edges[("b", "c", "coposting")] == (3, round(3 / 6 ** 0.5, 4))
    assert edges[("a", "c", "forward")] == (1, round(1 / 3, 4))
    assert (cfg["paths"]["output_dir"] / "coposting.gexf").exists()


def test_burst_and_volume(env):
    conn, _ = env
    burst = q(conn, "SELECT ticker, hour, count, n_channels, z FROM bursts")
    assert burst == [("BUMI", iso(BURST), 6, 3, 6.0)]
    vol = q(conn, "SELECT burst_date, h1_date, h1_volume, avg_volume, volume_ratio_h1 FROM burst_volume")
    assert vol == [("2026-05-04", "2026-05-05", 5000.0, 1000.0, 5.0)]


def test_report(env):
    conn, cfg = env
    summary, rows = build_report(conn, cfg, "BUMI", date(2026, 5, 1), date(2026, 5, 31))
    assert "6 pesan dari 3 kanal" in summary
    assert "5.00x" in summary
    assert "6 pesan, 3 kanal" in summary
    assert "@a - @b: 3" in summary
    assert len(rows) == 6
    assert rows["in_burst_hour"].tolist() == [1] * 6
    assert rows["dup_cluster_channels"].tolist() == [3] * 6

    empty, rows = build_report(conn, cfg, "GOTO", date(2026, 5, 1), date(2026, 5, 31))
    assert "Tidak ada mention" in empty and rows.empty
