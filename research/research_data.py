"""Langkah 1 riset: olah hasil analyze.py menjadi data/analysis/research.json.

    python research/research_data.py
    python research/research_data.py --cases ENRG,CDIA --hours ENRG=2026-07-14T03
"""
import argparse
import json
import sqlite3
import sys
from datetime import timedelta
from pathlib import Path

import networkx as nx
import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
from analysis.graph import coposting_counts  # noqa: E402

# Studi kasus di laporan dan jam burst yang dikutip pesannya (UTC, awal jam)
DEFAULT_CASES = "ENRG,CDIA,INET,TPIA,BUMI"
DEFAULT_HOURS = "ENRG=2026-07-14T03,CDIA=2026-04-14T02,INET=2026-08-18T05,TPIA=2026-05-12T23,BUMI=2026-09-07T10"

ap = argparse.ArgumentParser(description="Bangun data riset dari hasil analyze.py")
ap.add_argument("--cases", default=DEFAULT_CASES, help="Ticker studi kasus, dipisah koma")
ap.add_argument("--hours", default=DEFAULT_HOURS, help="TICKER=YYYY-MM-DDTHH jam burst yang dikutip")
ap.add_argument("--out", default=str(PROJECT / "data" / "analysis" / "research.json"))
args = ap.parse_args()
OUT = Path(args.out)
OUT.parent.mkdir(parents=True, exist_ok=True)
sys.stdout.reconfigure(encoding="utf-8")

conn = sqlite3.connect(f"{PROJECT}/data/messages.db")
rd = lambda sql, p=(): pd.read_sql_query(sql, conn, params=p)  # noqa: E731
utc = lambda s: pd.to_datetime(s, utc=True, format="ISO8601")  # noqa: E731

msgs = rd("SELECT channel, message_id, date, views, forwards, fwd_from_channel_id FROM messages")
msgs["ts"] = utc(msgs["date"])
mentions = rd("SELECT * FROM mentions")
mentions["ts"] = utc(mentions["date"])
clusters = rd("SELECT * FROM dup_clusters")
members = rd("SELECT * FROM dup_members")
edges = rd("SELECT * FROM graph_edges")
bursts = rd("SELECT b.*, v.h1_date, v.volume_ratio_h1, v.avg_volume, v.h1_volume FROM bursts b "
            "LEFT JOIN burst_volume v USING(ticker, hour)")
bursts["ts"] = utc(bursts["hour"])
prices = pd.read_csv(f"{PROJECT}/prices.csv", parse_dates=["date"])
names = pd.read_csv(f"{PROJECT}/idx_tickers.csv").set_index("ticker")["name"].to_dict()

out = {}

# ---------- Ringkasan korpus ----------
per_channel = msgs.groupby("channel").agg(n=("message_id", "size"), views=("views", "median"),
                                          first=("ts", "min"), last=("ts", "max"))
ment_per_channel = mentions.groupby("channel").size()
out["corpus"] = {
    "messages": int(len(msgs)), "channels": int(msgs["channel"].nunique()),
    "start": msgs["ts"].min().strftime("%Y-%m-%d"), "end": msgs["ts"].max().strftime("%Y-%m-%d"),
    "messages_with_mentions": int(mentions[["channel", "message_id"]].drop_duplicates().shape[0]),
    "mention_rows": int(len(mentions)), "tickers_mentioned": int(mentions["ticker"].nunique()),
    "tickers_in_list": int(len(names)),
    "forwards": int(msgs["fwd_from_channel_id"].notna().sum()),
    "dup_clusters": int(len(clusters)), "dup_clusters_cross": int((clusters["n_channels"] > 1).sum()),
    "dup_msgs_cross": int(clusters.loc[clusters["n_channels"] > 1, "size"].sum()),
    "bursts": int(len(bursts)), "bursts_multi": int((bursts["n_channels"] >= 2).sum()),
    "bursts_multi3": int((bursts["n_channels"] >= 3).sum()),
    "edges_copost": int((edges["kind"] == "coposting").sum()), "edges_fwd": int((edges["kind"] == "forward").sum()),
}
out["channels"] = [
    {"channel": ch, "messages": int(r.n), "median_views": float(r.views or 0),
     "mentions": int(ment_per_channel.get(ch, 0))}
    for ch, r in per_channel.sort_values("n", ascending=False).iterrows()
]
weekly = msgs.set_index("ts").resample("W-MON", label="left", closed="left").size()
out["weekly_messages"] = [[d.strftime("%Y-%m-%d"), int(n)] for d, n in weekly.items()]
top_tickers = mentions.groupby("ticker").agg(n=("message_id", "size"), ch=("channel", "nunique"))
out["top_tickers"] = [{"ticker": t, "name": names.get(t, ""), "mentions": int(r.n), "channels": int(r.ch)}
                      for t, r in top_tickers.sort_values("n", ascending=False).head(20).iterrows()]

# ---------- Uji volume: burst vs semua hari (kontrol) ----------
def vol_ratio_table(px, baseline=20):
    px = px.sort_values("date").reset_index(drop=True)
    avg = px["volume"].shift(1).rolling(baseline, min_periods=baseline).mean()  # 20 hari sebelum D0
    # rasio volume H+1 untuk 'burst' pada hari D0 = volume[D0+1] / avg 20 hari sebelum D0
    return pd.DataFrame({"date": px["date"], "ratio_next": px["volume"].shift(-1) / avg})

ctrl = []
ratio_tables = {}
for t, px in prices.groupby("ticker"):
    tab = vol_ratio_table(px)
    ratio_tables[t] = tab
    ctrl.append(tab.assign(ticker=t))
ctrl = pd.concat(ctrl).dropna()
ctrl = ctrl[np.isfinite(ctrl["ratio_next"])]
bv = bursts.dropna(subset=["volume_ratio_h1"])
groups = {
    "Semua hari (kontrol)": ctrl["ratio_next"],
    "Burst 1 kanal": bv.loc[bv["n_channels"] == 1, "volume_ratio_h1"],
    "Burst 2 kanal": bv.loc[bv["n_channels"] == 2, "volume_ratio_h1"],
    "Burst ≥3 kanal": bv.loc[bv["n_channels"] >= 3, "volume_ratio_h1"],
}
out["volume_test"] = [
    {"group": g, "n": int(len(s)), "median": float(s.median()) if len(s) else None,
     "p75": float(s.quantile(.75)) if len(s) else None,
     "share_2x": float((s >= 2).mean()) if len(s) else None}
    for g, s in groups.items()
]
ctrl_only = ctrl[ctrl["ticker"].isin(bv["ticker"].unique())]["ratio_next"]
out["volume_test_ctrl_same_tickers"] = {"n": int(len(ctrl_only)), "median": float(ctrl_only.median()),
                                        "share_2x": float((ctrl_only >= 2).mean())}

# ---------- Skor koordinasi per ticker ----------
cross = clusters[clusters["n_channels"] >= 2]
cm = members.merge(cross[["cluster_id", "n_channels", "span_minutes", "size"]], on="cluster_id")
cm = cm.merge(mentions[["channel", "message_id", "ticker"]], on=["channel", "message_id"])
dup_by_ticker = cm.groupby("ticker").agg(dup_clusters=("cluster_id", "nunique"),
                                         dup_max_channels=("n_channels", "max"))
b_multi = bursts[bursts["n_channels"] >= 2]
burst_by_ticker = b_multi.groupby("ticker").agg(bursts_multi=("hour", "size"),
                                                burst_max_channels=("n_channels", "max"),
                                                burst_max_ratio=("volume_ratio_h1", "max"))
score = top_tickers.join(dup_by_ticker, how="left").join(burst_by_ticker, how="left").fillna(0)
score["score"] = (score["bursts_multi"] * 2 + score["dup_clusters"] + score["burst_max_channels"]
                  + score["dup_max_channels"])
score = score.sort_values(["score", "n"], ascending=False)
out["score_table"] = [
    {"ticker": t, "name": names.get(t, ""), "mentions": int(r.n), "channels": int(r.ch),
     "bursts_multi": int(r.bursts_multi), "burst_max_channels": int(r.burst_max_channels),
     "burst_max_ratio": float(r.burst_max_ratio) if r.burst_max_ratio else None,
     "dup_clusters": int(r.dup_clusters), "dup_max_channels": int(r.dup_max_channels), "score": float(r.score)}
    for t, r in score.head(15).iterrows()
]

# ---------- Graf kanal ----------
n_msgs = per_channel["n"].to_dict()
g = nx.Graph()
for r in edges.itertuples():
    if r.source not in n_msgs or r.target not in n_msgs:
        continue  # sumber forward di luar korpus
    w = g[r.source][r.target]["weight"] + r.norm_weight if g.has_edge(r.source, r.target) else r.norm_weight
    g.add_edge(r.source, r.target, weight=w)
for ch in n_msgs:
    if ch not in g:
        g.add_node(ch)
comms = list(nx.community.greedy_modularity_communities(g, weight="weight")) if g.number_of_edges() else []
comm_of = {ch: i for i, c in enumerate(comms) for ch in c}
pos = nx.spring_layout(g, weight="weight", seed=7, k=0.9)
strength = dict(g.degree(weight="weight"))
out["network"] = {
    "nodes": [{"id": ch, "x": float(pos[ch][0]), "y": float(pos[ch][1]), "community": comm_of.get(ch, -1),
               "messages": int(n_msgs.get(ch, 0)), "strength": float(strength.get(ch, 0))} for ch in g.nodes],
    "edges": [{"s": a, "t": b, "w": float(d["weight"])} for a, b, d in g.edges(data=True)],
    "communities": [sorted(c, key=lambda ch: -strength.get(ch, 0)) for c in comms],
}
top_edges = edges[edges["kind"] == "coposting"].sort_values("norm_weight", ascending=False).head(12)
out["top_copost_edges"] = [{"a": r.source, "b": r.target, "raw": int(r.raw_weight), "norm": float(r.norm_weight)}
                           for r in top_edges.itertuples()]
fwd = edges[edges["kind"] == "forward"].sort_values("raw_weight", ascending=False).head(10)
out["top_forward_edges"] = [{"source": r.source, "target": r.target, "raw": int(r.raw_weight)}
                            for r in fwd.itertuples()]


# ---------- Studi kasus ----------
HOURS = dict(kv.split("=") for kv in args.hours.split(",") if kv)


def case(ticker):
    m = mentions[mentions["ticker"] == ticker].merge(msgs[["channel", "message_id", "views"]],
                                                     on=["channel", "message_id"])
    daily = m.set_index("ts").resample("D").agg({"message_id": "size", "channel": "nunique"})
    px = prices[prices["ticker"] == ticker].sort_values("date")
    b = bursts[bursts["ticker"] == ticker].sort_values("ts")
    tm = cm[cm["ticker"] == ticker].drop_duplicates("cluster_id").merge(
        clusters[["cluster_id", "first_date", "last_date", "sample_text"]], on="cluster_id")
    tm = tm.sort_values(["n_channels", "size_x" if "size_x" in tm else "size"], ascending=False)
    pairs = coposting_counts(zip(m["ticker"], m["ts"].astype("int64") // 10**9, m["channel"]), 30)
    # pesan dalam jam burst multi-kanal terbesar
    burst_msgs = []
    bm = b[b["n_channels"] >= 2].sort_values(["n_channels", "z"], ascending=False)
    if ticker in HOURS:
        bm = b[b["hour"].str.startswith(HOURS[ticker])]
    if len(bm):
        top = bm.iloc[0]
        in_hour = m[(m["ts"] >= top["ts"]) & (m["ts"] < top["ts"] + timedelta(hours=1))].sort_values("ts")
        texts = rd(f"SELECT channel, message_id, text FROM messages WHERE (channel, message_id) IN "
                   f"(VALUES {','.join(['(?,?)'] * len(in_hour))})",
                   [v for r in in_hour.itertuples() for v in (r.channel, int(r.message_id))]) if len(in_hour) else []
        tx = {(r.channel, r.message_id): r.text for r in texts.itertuples()} if len(in_hour) else {}
        burst_msgs = [{"time": r.ts.strftime("%H:%M"), "channel": r.channel, "views": int(r.views or 0),
                       "text": " ".join((tx.get((r.channel, r.message_id)) or "").split())[:200], "burst_hour": top["hour"]}
                      for r in in_hour.itertuples()]
    return {
        "ticker": ticker, "name": names.get(ticker, ""),
        "mentions": int(len(m)), "channels": int(m["channel"].nunique()),
        "median_views": float(m["views"].median()) if len(m) else 0,
        "top_channels": [[c, int(n)] for c, n in m["channel"].value_counts().head(8).items()],
        "daily": [[d.strftime("%Y-%m-%d"), int(r.message_id), int(r.channel)] for d, r in daily.iterrows()],
        "prices": [[d.strftime("%Y-%m-%d"), float(c), int(v)] for d, c, v in zip(px["date"], px["close"], px["volume"])],
        "bursts": [{"hour": r.ts.strftime("%Y-%m-%d %H:%M"), "count": int(r.count), "channels": int(r.n_channels),
                    "z": float(r.z), "h1": r.h1_date,
                    "ratio": float(r.volume_ratio_h1) if pd.notna(r.volume_ratio_h1) else None}
                   for r in b.itertuples()],
        "dup_clusters": [{"id": int(r.cluster_id), "channels": int(r.n_channels),
                          "size": int(getattr(r, "size_x", getattr(r, "size", 0))),
                          "span": float(r.span_minutes), "first": r.first_date[:16].replace("T", " "),
                          "text": " ".join((r.sample_text or "").split())[:280]} for r in tm.head(4).itertuples()],
        "pairs": [[a, b2, int(n)] for (a, b2), n in pairs.most_common(6)],
        "burst_msgs": burst_msgs,
    }


picked = [t.strip().upper() for t in args.cases.split(",") if t.strip()]
out["cases"] = [case(t) for t in picked]
json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
print("picked:", picked)
print(json.dumps({k: out[k] for k in ("corpus", "volume_test", "volume_test_ctrl_same_tickers")}, indent=1, ensure_ascii=False))
print("score top15:")
for r in out["score_table"]:
    print(r)
print("communities:", [c[:6] for c in out["network"]["communities"]])
print("top copost:", out["top_copost_edges"][:6])
print("top fwd:", out["top_forward_edges"][:6])
