"""Deteksi lonjakan mention per ticker per jam (z-score terhadap baseline rolling)."""
import logging

import numpy as np
import pandas as pd

from analysis import db

log = logging.getLogger(__name__)


def hourly_counts(mentions):
    """mentions: DataFrame ticker, date, channel -> count & n_channels per (ticker, hour)."""
    df = mentions.assign(hour=db.to_utc(mentions["date"]).dt.floor("h"))
    return df.groupby(["ticker", "hour"]).agg(count=("channel", "size"), n_channels=("channel", "nunique"))


def detect(hourly, start, end, window_days=7, z_threshold=3.0, min_history_hours=24,
           min_count=3, min_std=1.0, min_total_mentions=5):
    """Kembalikan DataFrame jam burst. Deret tiap ticker diisi nol dari `start` s.d. `end`
    (jam tanpa mention = 0); baseline = jam-jam sebelumnya, jam berjalan tidak ikut."""
    hours = pd.date_range(start.floor("h"), end.floor("h"), freq="h", tz="UTC")
    window = window_days * 24
    results = []
    totals = hourly["count"].groupby(level="ticker").sum()
    for ticker in totals[totals >= min_total_mentions].index:
        part = hourly.xs(ticker, level="ticker").reindex(hours, fill_value=0)
        x = part["count"].astype(float)
        past = x.shift(1).rolling(window, min_periods=min_history_hours)
        mean, std = past.mean(), past.std(ddof=0)
        z = (x - mean) / np.maximum(std.fillna(0), min_std)
        flagged = (z > z_threshold) & (x >= min_count)
        for hour in x.index[flagged.fillna(False)]:
            results.append({
                "ticker": ticker, "hour": hour, "count": int(x[hour]),
                "n_channels": int(part.at[hour, "n_channels"]),
                "baseline_mean": float(mean[hour]), "baseline_std": float(std[hour]), "z": float(z[hour]),
            })
    return pd.DataFrame(results, columns=["ticker", "hour", "count", "n_channels", "baseline_mean",
                                          "baseline_std", "z"])


def run(conn, cfg):
    bcfg = cfg["bursts"]
    mentions = db.read_df(conn, "SELECT ticker, date, channel FROM mentions")
    span = db.read_df(conn, "SELECT MIN(date) AS start, MAX(date) AS end FROM messages")
    db.reset_table(conn, "bursts")
    if mentions.empty:
        log.info("Burst: tidak ada mention")
        return pd.DataFrame()
    start, end = db.to_utc(span["start"])[0], db.to_utc(span["end"])[0]
    bursts = detect(hourly_counts(mentions), start, end, **bcfg)
    conn.executemany(
        "INSERT INTO bursts VALUES (?, ?, ?, ?, ?, ?, ?)",
        [(r.ticker, r.hour.isoformat(), r.count, r.n_channels, r.baseline_mean, r.baseline_std, r.z)
         for r in bursts.itertuples()],
    )
    conn.commit()
    log.info("Burst: %s jam burst pada %s ticker", len(bursts), bursts["ticker"].nunique() if len(bursts) else 0)
    return bursts
