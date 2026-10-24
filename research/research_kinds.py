"""Langkah 2 riset: jenis pesan di jam burst dan volume H0 vs H+1, ditambahkan ke research.json.

    python research/research_kinds.py
"""
import json
import re
import sqlite3
import sys
from datetime import timedelta
from pathlib import Path

import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")
PROJECT = Path(__file__).resolve().parents[1]
RJSON = PROJECT / "data" / "analysis" / "research.json"
conn = sqlite3.connect(f"{PROJECT}/data/messages.db")
rd = lambda sql, p=(): pd.read_sql_query(sql, conn, params=p)  # noqa: E731
utc = lambda s: pd.to_datetime(s, utc=True, format="ISO8601")  # noqa: E731

REPORT = re.compile(r"market report|top gainer|top loser|sesi 1|sesi pertama|sesi kedua|session|day break|"
                    r"closing review|market highlight|market review|#ihsg|top foreign|top local|"
                    r"cashdeviden|stocksplit|exdate|recdate|rups", re.I)
NEWS = re.compile(r"kontan|bisnis\.com|cnbc|katadata|emitennews|investor\.id|idx channel|iqplus|"
                  r"https?://|msci|ftse|ojk|laporan keuangan|laba bersih", re.I)
PROMO = re.compile(r"\brekom|\btp\d?\b|target|entry|\bbuy\b|\bbeli\b|join|vip|premium|cuan|sinyal|"
                   r"tradingplan|swing|daytrade|belanja", re.I)


def kind(text):
    t = text or ""
    if REPORT.search(t):
        return "laporan rutin"
    if NEWS.search(t):
        return "berita"
    if PROMO.search(t):
        return "ajakan/rekomendasi"
    return "lainnya"


msgs = rd("SELECT channel, message_id, date, text FROM messages")
msgs["ts"] = utc(msgs["date"])
mentions = rd("SELECT channel, message_id, ticker, date FROM mentions")
mentions["ts"] = utc(mentions["date"])
mentions = mentions.merge(msgs[["channel", "message_id", "text"]], on=["channel", "message_id"])
mentions["kind"] = mentions["text"].map(kind)

bursts = rd("SELECT b.ticker, b.hour, b.count, b.n_channels, b.z, v.burst_date, v.volume_ratio_h1, v.avg_volume "
            "FROM bursts b LEFT JOIN burst_volume v USING(ticker, hour)")
bursts["ts"] = utc(bursts["hour"])
prices = pd.read_csv(f"{PROJECT}/prices.csv", parse_dates=["date"])
px_idx = {(t, d.date()): v for t, d, v in zip(prices["ticker"], prices["date"], prices["volume"])}

rows = []
for b in bursts.itertuples():
    inh = mentions[(mentions["ticker"] == b.ticker) & (mentions["ts"] >= b.ts) &
                   (mentions["ts"] < b.ts + timedelta(hours=1))]
    kinds = inh["kind"].value_counts()
    dominant = kinds.idxmax() if len(kinds) else "lainnya"
    d0 = pd.Timestamp(b.burst_date).date() if b.burst_date else None
    v0 = px_idx.get((b.ticker, d0))
    ratio_h0 = v0 / b.avg_volume if v0 is not None and b.avg_volume else None
    # jam burst dalam WIB
    wib = (b.ts + timedelta(hours=7))
    session = ("pra-pasar" if wib.hour < 9 else "jam bursa" if wib.hour < 16 else "setelah tutup")
    if wib.weekday() >= 5:
        session = "akhir pekan"
    rows.append({"ticker": b.ticker, "hour": b.hour, "channels": b.n_channels, "dominant": dominant,
                 "share_routine_news": float(kinds.get("laporan rutin", 0) + kinds.get("berita", 0)) / max(len(inh), 1),
                 "ratio_h0": ratio_h0, "ratio_h1": b.volume_ratio_h1, "session": session})
bt = pd.DataFrame(rows)
bt["group"] = pd.cut(bt["channels"], [0, 1, 2, 99], labels=["1 kanal", "2 kanal", "≥3 kanal"])

out = {}
out["burst_kinds"] = (bt.groupby(["group", "dominant"], observed=True).size().unstack(fill_value=0)
                      .reset_index().to_dict(orient="records"))
summary = []
for g, part in bt.groupby("group", observed=True):
    in_session = part[part["session"] == "jam bursa"]
    summary.append({
        "group": str(g), "n": int(len(part)),
        "median_h0": float(part["ratio_h0"].median()) if part["ratio_h0"].notna().any() else None,
        "median_h1": float(part["ratio_h1"].median()) if part["ratio_h1"].notna().any() else None,
        "share_routine_news_majority": float((part["share_routine_news"] >= .5).mean()),
        "share_in_session": float(len(in_session) / len(part)),
        "median_h0_in_session": float(in_session["ratio_h0"].median()) if in_session["ratio_h0"].notna().any() else None,
    })
out["burst_summary"] = summary
# burst lintas kanal yang didominasi ajakan/rekomendasi (kandidat promosi)
promo = bt[(bt["channels"] >= 2) & (bt["dominant"] == "ajakan/rekomendasi")].sort_values("channels", ascending=False)
out["promo_bursts"] = promo.to_dict(orient="records")

# komposisi semua mention per kanal (untuk tabel kanal)
comp = mentions.groupby(["channel", "kind"]).size().unstack(fill_value=0)
comp["total"] = comp.sum(axis=1)
out["channel_kinds"] = comp.sort_values("total", ascending=False).head(15).reset_index().to_dict(orient="records")
out["mention_kinds_total"] = mentions["kind"].value_counts().to_dict()

d = json.load(open(RJSON, encoding="utf-8"))
d["extra"] = out
json.dump(d, open(RJSON, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
print(json.dumps(out["burst_summary"], indent=1, ensure_ascii=False))
print(pd.DataFrame(out["burst_kinds"]).to_string())
print("mention kinds:", out["mention_kinds_total"])
print("promo bursts:")
for r in out["promo_bursts"]:
    print(" ", r)
print(pd.DataFrame(out["channel_kinds"]).to_string())
