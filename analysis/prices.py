"""Gabungkan burst dengan data EOD: rasio volume H+1 terhadap rata-rata N hari bursa."""
import logging
from datetime import timedelta
from pathlib import Path

import pandas as pd

from analysis import db

log = logging.getLogger(__name__)


def load_prices(path):
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"{path} tidak ditemukan (kolom wajib: date, ticker, volume)")
    df = pd.read_csv(path)
    df.columns = [c.strip().lower() for c in df.columns]
    missing = {"date", "ticker", "volume"} - set(df.columns)
    if missing:
        raise ValueError(f"{path} kekurangan kolom: {', '.join(sorted(missing))}")
    df["ticker"] = df["ticker"].astype(str).str.strip().str.upper().str.removesuffix(".JK")
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df.dropna(subset=["volume"]).sort_values(["ticker", "date"])


def volume_ratios(bursts, prices, baseline_days=20, utc_offset_hours=7):
    """bursts: DataFrame ticker, hour (datetime UTC). H+1 = hari bursa pertama setelah tanggal
    burst (zona lokal); baseline = `baseline_days` hari bursa tepat sebelum tanggal burst.
    Rasio kosong jika data H+1 belum ada atau riwayat kurang dari baseline_days."""
    by_ticker = {t: g.reset_index(drop=True) for t, g in prices.groupby("ticker")}
    offset = timedelta(hours=utc_offset_hours)
    rows = []
    for b in bursts.itertuples():
        burst_date = (b.hour + offset).date()
        row = {"ticker": b.ticker, "hour": b.hour, "burst_date": burst_date, "h1_date": None,
               "h1_volume": None, "avg_volume": None, "baseline_days": None, "volume_ratio_h1": None}
        px = by_ticker.get(b.ticker)
        if px is not None:
            before = px[px["date"] < burst_date].tail(baseline_days)
            after = px[px["date"] > burst_date]
            if not after.empty:
                row["h1_date"] = after["date"].iloc[0]
                row["h1_volume"] = float(after["volume"].iloc[0])
            if len(before) == baseline_days:
                row["avg_volume"] = float(before["volume"].mean())
                row["baseline_days"] = baseline_days
            if row["h1_volume"] is not None and row["avg_volume"]:
                row["volume_ratio_h1"] = row["h1_volume"] / row["avg_volume"]
        rows.append(row)
    return pd.DataFrame(rows, columns=["ticker", "hour", "burst_date", "h1_date", "h1_volume",
                                       "avg_volume", "baseline_days", "volume_ratio_h1"])


def run(conn, cfg):
    pcfg = cfg["prices"]
    prices = load_prices(cfg["paths"]["prices_csv"])
    bursts = db.read_df(conn, "SELECT ticker, hour FROM bursts")
    bursts["hour"] = db.to_utc(bursts["hour"])
    result = volume_ratios(bursts, prices, pcfg["baseline_days"], pcfg["utc_offset_hours"])
    db.reset_table(conn, "burst_volume")

    def iso(d):
        return d.isoformat() if hasattr(d, "isoformat") else None

    conn.executemany(
        "INSERT INTO burst_volume VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [(r.ticker, r.hour.isoformat(), iso(r.burst_date), iso(r.h1_date), r.h1_volume, r.avg_volume,
          r.baseline_days, r.volume_ratio_h1) for r in result.itertuples()],
    )
    conn.commit()
    log.info("Volume: %s/%s burst punya rasio H+1", int(result["volume_ratio_h1"].notna().sum()), len(result))
    return result
