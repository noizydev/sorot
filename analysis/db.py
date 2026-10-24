"""Koneksi SQLite dan skema tabel hasil analisis. Setiap langkah membangun ulang tabelnya sendiri."""
import sqlite3
from pathlib import Path

import pandas as pd

TABLES = {
    "mentions": """
        CREATE TABLE mentions (
            channel       TEXT    NOT NULL,
            message_id    INTEGER NOT NULL,
            ticker        TEXT    NOT NULL,
            date          TEXT    NOT NULL,
            n_occurrences INTEGER NOT NULL,
            prefixed      INTEGER NOT NULL,
            ambiguous     INTEGER NOT NULL,
            PRIMARY KEY (channel, message_id, ticker)
        );
        CREATE INDEX idx_mentions_ticker_date ON mentions(ticker, date);
    """,
    "dup_clusters": """
        CREATE TABLE dup_clusters (
            cluster_id   INTEGER PRIMARY KEY,
            size         INTEGER NOT NULL,
            n_channels   INTEGER NOT NULL,
            first_date   TEXT    NOT NULL,
            last_date    TEXT    NOT NULL,
            span_minutes REAL    NOT NULL,
            sample_text  TEXT
        );
    """,
    "dup_members": """
        CREATE TABLE dup_members (
            cluster_id INTEGER NOT NULL,
            channel    TEXT    NOT NULL,
            message_id INTEGER NOT NULL,
            date       TEXT    NOT NULL,
            PRIMARY KEY (channel, message_id)
        );
        CREATE INDEX idx_dup_members_cluster ON dup_members(cluster_id);
    """,
    "graph_edges": """
        CREATE TABLE graph_edges (
            source      TEXT NOT NULL,
            target      TEXT NOT NULL,
            kind        TEXT NOT NULL,   -- coposting (tak berarah, source < target) | forward (source -> target)
            raw_weight  REAL NOT NULL,
            norm_weight REAL NOT NULL,
            PRIMARY KEY (source, target, kind)
        );
    """,
    "bursts": """
        CREATE TABLE bursts (
            ticker        TEXT    NOT NULL,
            hour          TEXT    NOT NULL,   -- awal jam, ISO UTC
            count         INTEGER NOT NULL,
            n_channels    INTEGER NOT NULL,
            baseline_mean REAL    NOT NULL,
            baseline_std  REAL    NOT NULL,
            z             REAL    NOT NULL,
            PRIMARY KEY (ticker, hour)
        );
    """,
    "burst_volume": """
        CREATE TABLE burst_volume (
            ticker          TEXT NOT NULL,
            hour            TEXT NOT NULL,
            burst_date      TEXT NOT NULL,    -- tanggal lokal (WIB) jam burst
            h1_date         TEXT,             -- hari bursa pertama setelah burst_date
            h1_volume       REAL,
            avg_volume      REAL,             -- rata-rata N hari bursa sebelum burst_date
            baseline_days   INTEGER,
            volume_ratio_h1 REAL,
            PRIMARY KEY (ticker, hour)
        );
    """,
}


def connect(path):
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Database tidak ditemukan: {path} (jalankan collect.py dulu)")
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def reset_table(conn, name):
    conn.execute(f"DROP TABLE IF EXISTS {name}")
    conn.executescript(TABLES[name])


def table_exists(conn, name):
    row = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()
    return row is not None


def read_df(conn, sql, params=()):
    return pd.read_sql_query(sql, conn, params=params)


def to_utc(series):
    """Kolom teks ISO (dengan offset) -> datetime64 UTC."""
    return pd.to_datetime(series, utc=True, format="ISO8601")
