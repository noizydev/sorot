"""Graf co-posting antarkanal: ticker sama dalam jendela waktu + relasi forward."""
import json
import logging
import math
from collections import Counter
from pathlib import Path

import networkx as nx

from analysis import db

log = logging.getLogger(__name__)


def coposting_counts(mentions, window_minutes):
    """mentions: iterable (ticker, ts_seconds, channel). Hitung pasangan pesan dari kanal berbeda
    yang menyebut ticker sama dengan selisih <= window. Kunci (a, b) dengan a < b."""
    window = window_minutes * 60
    by_ticker = {}
    for ticker, ts, channel in mentions:
        by_ticker.setdefault(ticker, []).append((ts, channel))
    counts = Counter()
    for events in by_ticker.values():
        events.sort()
        for i, (ts_i, ch_i) in enumerate(events):
            for ts_j, ch_j in events[i + 1:]:
                if ts_j - ts_i > window:
                    break
                if ch_i != ch_j:
                    counts[tuple(sorted((ch_i, ch_j)))] += 1
    return counts


def normalize(raw, n_a, n_b, method):
    if method == "min":
        denom = min(n_a, n_b)
    elif method == "sqrt":
        denom = math.sqrt(n_a * n_b)
    else:
        raise ValueError(f"normalization tidak dikenal: {method}")
    return raw / denom if denom else 0.0


def channel_id_map(raw_dir):
    """channel_id numerik -> username, dibaca dari baris pertama tiap raw JSONL."""
    mapping = {}
    for path in Path(raw_dir).glob("*.jsonl"):
        with open(path, encoding="utf-8") as f:
            line = f.readline()
        if not line:
            continue
        peer = json.loads(line).get("message", {}).get("peer_id") or {}
        if peer.get("channel_id"):
            mapping[int(peer["channel_id"])] = path.stem
    return mapping


def run(conn, cfg):
    gcfg = cfg["coposting"]
    method = gcfg["normalization"]
    n_msgs = {r["channel"]: r["n"] for r in conn.execute("SELECT channel, COUNT(*) AS n FROM messages GROUP BY channel")}

    mentions = db.read_df(conn, "SELECT ticker, date, channel FROM mentions")
    ts = db.to_utc(mentions["date"]).astype("int64") // 10**9
    copost = coposting_counts(zip(mentions["ticker"], ts, mentions["channel"]), gcfg["window_minutes"])

    edges = []
    for (a, b), raw in copost.items():
        edges.append((a, b, "coposting", raw, normalize(raw, n_msgs.get(a, 0), n_msgs.get(b, 0), method)))

    id_map = channel_id_map(cfg["paths"]["raw_dir"])
    fwd = Counter()
    for r in conn.execute("SELECT channel, fwd_from_channel_id, fwd_from_name FROM messages "
                          "WHERE fwd_from_channel_id IS NOT NULL"):
        source = id_map.get(r["fwd_from_channel_id"]) or r["fwd_from_name"] or f"id:{r['fwd_from_channel_id']}"
        if source != r["channel"]:
            fwd[(source, r["channel"])] += 1
    for (src, dst), raw in fwd.items():
        edges.append((src, dst, "forward", raw, raw / n_msgs[dst] if n_msgs.get(dst) else 0.0))

    db.reset_table(conn, "graph_edges")
    conn.executemany("INSERT INTO graph_edges VALUES (?, ?, ?, ?, ?)", edges)
    conn.commit()

    g = build_graph(edges, n_msgs, gcfg["forward_weight"])
    out_dir = Path(cfg["paths"]["output_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    gexf = out_dir / "coposting.gexf"
    nx.write_gexf(g, gexf)
    log.info("Graf: %s node, %s edge (%s co-posting, %s forward) -> %s",
             g.number_of_nodes(), g.number_of_edges(), len(copost), len(fwd), gexf)
    return g


def build_graph(edges, n_msgs, forward_weight=1.0):
    """Graf tak berarah gabungan; atribut `weight` = coposting_norm + forward_weight * forward_norm."""
    g = nx.Graph()
    for ch, n in n_msgs.items():
        g.add_node(ch, n_messages=n)
    for src, dst, kind, raw, norm in edges:
        if not g.has_edge(src, dst):
            g.add_edge(src, dst, coposting_raw=0.0, coposting_norm=0.0, forward_raw=0.0, forward_norm=0.0)
        e = g[src][dst]
        e[f"{kind}_raw"] += raw
        e[f"{kind}_norm"] += norm
    for _, _, e in g.edges(data=True):
        e["weight"] = e["coposting_norm"] + forward_weight * e["forward_norm"]
    return g
