"""Akses data untuk dashboard. Cache di-invalidasi otomatis saat file DB/CSV berubah (mtime)."""
import sqlite3
from pathlib import Path

import networkx as nx
import pandas as pd
import streamlit as st

from analysis import db as adb
from analysis.config import load_config
from analysis.kinds import classify

ROOT = Path(__file__).resolve().parents[1]
WIB = pd.Timedelta(hours=7)


def config():
    return load_config()


def _mtime(path):
    path = Path(path)
    return path.stat().st_mtime_ns if path.exists() else 0


def db_path():
    return config()["paths"]["db"]


def db_exists():
    return Path(db_path()).exists()


@st.cache_data(show_spinner=False, max_entries=256)
def _query(path, sql, params, version):
    conn = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True, timeout=15)
    try:
        return pd.read_sql_query(sql, conn, params=params)
    finally:
        conn.close()


def q(sql, params=()):
    """SELECT read-only; hasil di-cache sampai messages.db berubah."""
    path = db_path()
    return _query(str(path), sql, tuple(params), _mtime(path))


def tables():
    if not db_exists():
        return set()
    return set(q("SELECT name FROM sqlite_master WHERE type='table'")["name"])


def has(*names):
    return set(names) <= tables()


def to_wib(values):
    """Teks ISO UTC -> waktu WIB tanpa zona (naive), untuk tampilan dan grafik."""
    ts = adb.to_utc(values) + WIB
    return ts.dt.tz_localize(None) if isinstance(ts, pd.Series) else ts.tz_localize(None)


# ---------- master data ----------
@st.cache_data(show_spinner=False)
def _tickers(path, version):
    if not Path(path).exists():
        return pd.DataFrame(columns=["ticker", "name", "ambiguous"])
    df = pd.read_csv(path, dtype=str, encoding="utf-8-sig").fillna("")
    df.columns = [c.strip().lower() for c in df.columns]
    df = df.rename(columns={c: "ticker" for c in ("kode", "code", "symbol") if c in df and "ticker" not in df})
    for col in ("name", "ambiguous"):  # kolom opsional
        if col not in df:
            df[col] = ""
    df["ticker"] = df["ticker"].str.strip().str.upper().str.removesuffix(".JK")
    df["ambiguous"] = df["ambiguous"].str.strip().str.lower().isin(["1", "true", "ya", "yes"])
    return df[["ticker", "name", "ambiguous"]]


def tickers():
    path = config()["paths"]["tickers_csv"]
    return _tickers(str(path), _mtime(path))


def ticker_names():
    return dict(zip(tickers()["ticker"], tickers()["name"]))


@st.cache_data(show_spinner=False)
def _prices(path, version):
    if not Path(path).exists():
        return pd.DataFrame(columns=["date", "ticker", "close", "volume"])
    df = pd.read_csv(path, parse_dates=["date"])
    df["ticker"] = df["ticker"].astype(str).str.upper()
    return df


def prices(ticker=None):
    path = config()["paths"]["prices_csv"]
    df = _prices(str(path), _mtime(path))
    return df[df["ticker"] == ticker].sort_values("date") if ticker else df


# ---------- ringkasan ----------
def overview():
    stats = q("""SELECT COUNT(*) AS messages, COUNT(DISTINCT channel) AS channels, MIN(date) AS first,
                        MAX(date) AS last, MAX(collected_at) AS collected,
                        SUM(fwd_from_channel_id IS NOT NULL) AS forwards FROM messages""").iloc[0].to_dict()
    t = tables()
    if "mentions" in t:
        m = q("SELECT COUNT(*) AS n, COUNT(DISTINCT ticker) AS tickers FROM mentions").iloc[0]
        stats.update(mentions=int(m["n"]), tickers=int(m["tickers"]))
    if "dup_clusters" in t:
        stats["dup_cross"] = int(q("SELECT COUNT(*) AS n FROM dup_clusters WHERE n_channels > 1")["n"][0])
    if "bursts" in t:
        b = q("SELECT COUNT(*) AS n, SUM(n_channels >= 3) AS n3 FROM bursts").iloc[0]
        stats.update(bursts=int(b["n"]), bursts3=int(b["n3"] or 0))
    return stats


def weekly_messages():
    df = q("SELECT date FROM messages")
    ts = to_wib(df["date"])
    return ts.dt.to_period("W-SUN").dt.start_time.value_counts().sort_index().rename_axis("minggu").reset_index(name="pesan")


def ticker_table():
    """Satu baris per ticker yang pernah disebut."""
    if not has("mentions"):
        return pd.DataFrame()
    df = q("""SELECT ticker, COUNT(*) AS mention, COUNT(DISTINCT channel) AS kanal,
                     MIN(date) AS pertama, MAX(date) AS terakhir, SUM(prefixed) AS cashtag
              FROM mentions GROUP BY ticker ORDER BY mention DESC""")
    if has("bursts"):
        b = q("""SELECT ticker, COUNT(*) AS burst, SUM(n_channels >= 2) AS burst_lintas FROM bursts GROUP BY ticker""")
        df = df.merge(b, on="ticker", how="left")
    df["nama"] = df["ticker"].map(ticker_names()).fillna("")
    return df.fillna({"burst": 0, "burst_lintas": 0})


def bursts(ticker=None, min_channels=1):
    if not has("bursts"):
        return pd.DataFrame()
    vol = has("burst_volume")
    sql = f"""SELECT b.ticker, b.hour, b.count, b.n_channels, b.baseline_mean, b.z
                     {", v.h1_date, v.h1_volume, v.avg_volume, v.volume_ratio_h1" if vol else ""}
              FROM bursts b {"LEFT JOIN burst_volume v USING (ticker, hour)" if vol else ""}
              WHERE b.n_channels >= ? {"AND b.ticker = ?" if ticker else ""}
              ORDER BY b.hour DESC"""
    df = q(sql, (min_channels, ticker) if ticker else (min_channels,))
    df["jam_wib"] = to_wib(df["hour"])
    return df


# ---------- per ticker ----------
def ticker_messages(ticker, start, end):
    """Pesan yang menyebut ticker; start/end = tanggal (WIB), inklusif."""
    lo = (pd.Timestamp(start) - WIB).isoformat()
    hi = (pd.Timestamp(end) + pd.Timedelta(days=1) - WIB).isoformat()
    df = q("""SELECT m.channel, m.message_id, m.date, m.n_occurrences, m.prefixed, g.views, g.forwards,
                     g.fwd_from_name, g.text
              FROM mentions m JOIN messages g ON g.channel = m.channel AND g.message_id = m.message_id
              WHERE m.ticker = ? AND m.date >= ? AND m.date < ? ORDER BY m.date""", (ticker, lo[:19], hi[:19]))
    df["waktu_wib"] = to_wib(df["date"])
    df["jenis"] = df["text"].map(classify)
    return df


def ticker_clusters(ticker):
    if not has("dup_members", "dup_clusters"):
        return pd.DataFrame(), pd.DataFrame()
    clusters = q("""SELECT DISTINCT c.* FROM dup_clusters c
                    JOIN dup_members d ON d.cluster_id = c.cluster_id
                    JOIN mentions m ON m.channel = d.channel AND m.message_id = d.message_id
                    WHERE m.ticker = ? ORDER BY c.n_channels DESC, c.size DESC""", (ticker,))
    if clusters.empty:
        return clusters, pd.DataFrame()
    ids = ",".join(str(int(i)) for i in clusters["cluster_id"])
    members = q(f"""SELECT d.cluster_id, d.channel, d.message_id, d.date, g.views
                    FROM dup_members d JOIN messages g ON g.channel = d.channel AND g.message_id = d.message_id
                    WHERE d.cluster_id IN ({ids}) ORDER BY d.cluster_id, d.date""")
    members["waktu_wib"] = to_wib(members["date"])
    return clusters, members


def cluster_messages(cluster_id):
    return q("""SELECT d.channel, d.message_id, d.date, g.views, g.text FROM dup_members d
                JOIN messages g ON g.channel = d.channel AND g.message_id = d.message_id
                WHERE d.cluster_id = ? ORDER BY d.date""", (int(cluster_id),))


def burst_messages(ticker, hour_iso):
    """Pesan dalam satu jam burst (hour_iso = awal jam UTC seperti di tabel bursts)."""
    start = adb.to_utc(pd.Series([hour_iso]))[0]
    end = start + pd.Timedelta(hours=1)
    df = q("""SELECT m.channel, m.message_id, m.date, g.views, g.text FROM mentions m
              JOIN messages g ON g.channel = m.channel AND g.message_id = m.message_id
              WHERE m.ticker = ? AND m.date >= ? AND m.date < ? ORDER BY m.date""",
           (ticker, start.isoformat()[:19], end.isoformat()[:19]))
    df["waktu_wib"] = to_wib(df["date"])
    df["jenis"] = df["text"].map(classify)
    return df


# ---------- kanal & jaringan ----------
def channel_table():
    df = q("""SELECT channel, COUNT(*) AS pesan, MIN(date) AS pertama, MAX(date) AS terakhir,
                     CAST(AVG(views) AS INTEGER) AS rata_views, SUM(fwd_from_channel_id IS NOT NULL) AS forward
              FROM messages GROUP BY channel ORDER BY pesan DESC""")
    if has("mentions"):
        m = q("SELECT channel, COUNT(*) AS mention, COUNT(DISTINCT ticker) AS ticker FROM mentions GROUP BY channel")
        df = df.merge(m, on="channel", how="left").fillna({"mention": 0, "ticker": 0})
    return df


def edges():
    if not has("graph_edges"):
        return pd.DataFrame(columns=["source", "target", "kind", "raw_weight", "norm_weight"])
    return q("SELECT * FROM graph_edges")


@st.cache_data(show_spinner=False)
def _layout(edge_records, nodes, version):
    g = nx.Graph()
    g.add_nodes_from(nodes)
    for s, t, w in edge_records:
        prev = g[s][t]["weight"] if g.has_edge(s, t) else 0.0
        g.add_edge(s, t, weight=prev + w)
    comms = nx.community.greedy_modularity_communities(g, weight="weight") if g.number_of_edges() else []
    community = {n: i for i, c in enumerate(comms) for n in c}
    pos = nx.spring_layout(g, weight="weight", seed=7, k=0.9)
    strength = dict(g.degree(weight="weight"))
    return {n: (float(pos[n][0]), float(pos[n][1]), community.get(n, -1), float(strength.get(n, 0))) for n in g.nodes}


def network(kinds=("coposting", "forward"), min_weight=0.0, include_external=False):
    """Node + edge untuk graf. Sumber forward di luar korpus (grup privat/premium) opsional."""
    ch = channel_table()
    corpus = set(ch["channel"])
    e = edges()
    e = e[e["kind"].isin(kinds) & (e["norm_weight"] >= min_weight)]
    if not include_external:
        e = e[e["source"].isin(corpus) & e["target"].isin(corpus)]
    nodes = sorted(corpus | set(e["source"]) | set(e["target"]))
    layout = _layout(tuple(zip(e["source"], e["target"], e["norm_weight"])), tuple(nodes), _mtime(db_path()))
    n = pd.DataFrame([{"node": k, "x": v[0], "y": v[1], "community": v[2], "strength": v[3]} for k, v in layout.items()])
    n = n.merge(ch[["channel", "pesan"]], left_on="node", right_on="channel", how="left")
    n["external"] = ~n["node"].isin(corpus)
    return n, e


def channel_messages(channel, limit=None):
    sql = "SELECT message_id, date, views, forwards, fwd_from_name, has_media, text FROM messages WHERE channel = ? ORDER BY date DESC"
    df = q(sql + (f" LIMIT {int(limit)}" if limit else ""), (channel,))
    df["waktu_wib"] = to_wib(df["date"])
    return df


def channel_tickers(channel, top=15):
    if not has("mentions"):
        return pd.DataFrame()
    return q("""SELECT ticker, COUNT(*) AS mention, SUM(prefixed) AS cashtag FROM mentions
                WHERE channel = ? GROUP BY ticker ORDER BY mention DESC LIMIT ?""", (channel, top))


def channel_kinds(channel):
    """Komposisi jenis pesan yang menyebut ticker di kanal ini."""
    if not has("mentions"):
        return pd.Series(dtype=int)
    df = q("""SELECT DISTINCT g.message_id, g.text FROM mentions m
              JOIN messages g ON g.channel = m.channel AND g.message_id = m.message_id WHERE m.channel = ?""", (channel,))
    return df["text"].map(classify).value_counts()
