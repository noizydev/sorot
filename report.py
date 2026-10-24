"""Ringkasan aktivitas satu ticker dalam rentang tanggal: teks + CSV per pesan.

Contoh:
    python report.py --ticker BUMI --from 2026-04-01 --to 2026-06-30

Butuh hasil analyze.py (minimal langkah mentions). Tanggal dibaca sebagai UTC, inklusif.
"""
import argparse
import csv
import re
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from analysis import db
from analysis.config import load_config
from analysis.graph import coposting_counts

CSV_COLUMNS = ["date", "channel", "message_id", "views", "forwards", "n_occurrences", "prefixed",
               "in_burst_hour", "dup_cluster_id", "dup_cluster_channels", "text"]


def parse_args():
    p = argparse.ArgumentParser(description="Laporan aktivitas satu ticker")
    p.add_argument("--ticker", required=True, help="Kode saham 4 huruf, mis. BUMI")
    p.add_argument("--from", dest="date_from", required=True, help="Tanggal awal YYYY-MM-DD (UTC, inklusif)")
    p.add_argument("--to", dest="date_to", required=True, help="Tanggal akhir YYYY-MM-DD (UTC, inklusif)")
    p.add_argument("--config", default=None, help="Path config.yaml")
    p.add_argument("--out-dir", default=None, help="Folder keluaran (default: paths.reports_dir)")
    args = p.parse_args()
    args.ticker = args.ticker.strip().upper().lstrip("$#")
    if not re.fullmatch(r"[A-Z]{4}", args.ticker):
        p.error("--ticker harus 4 huruf")
    try:
        args.date_from = date.fromisoformat(args.date_from)
        args.date_to = date.fromisoformat(args.date_to)
    except ValueError:
        p.error("--from/--to harus berformat YYYY-MM-DD")
    if args.date_to < args.date_from:
        p.error("--to lebih awal dari --from")
    return args


def _short(text, n):
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 3] + "..."


def build_report(conn, cfg, ticker, date_from, date_to):
    """Kembalikan (teks ringkasan, DataFrame baris CSV)."""
    if not db.table_exists(conn, "mentions"):
        raise RuntimeError("Tabel mentions belum ada; jalankan `python analyze.py` dulu.")
    lo, hi = date_from.isoformat(), (date_to + timedelta(days=1)).isoformat()
    rcfg = cfg["report"]

    rows = db.read_df(conn, """
        SELECT m.date, m.channel, m.message_id, g.views, g.forwards, m.n_occurrences, m.prefixed, g.text
        FROM mentions m JOIN messages g ON g.channel = m.channel AND g.message_id = m.message_id
        WHERE m.ticker = ? AND m.date >= ? AND m.date < ?
        ORDER BY m.date
    """, (ticker, lo, hi))
    rows["ts"] = db.to_utc(rows["date"])

    bursts = pd.DataFrame()
    if db.table_exists(conn, "bursts"):
        vol = db.table_exists(conn, "burst_volume")
        bursts = db.read_df(conn, f"""
            SELECT b.hour, b.count, b.n_channels, b.z
                   {", v.h1_date, v.volume_ratio_h1" if vol else ""}
            FROM bursts b {"LEFT JOIN burst_volume v ON v.ticker = b.ticker AND v.hour = b.hour" if vol else ""}
            WHERE b.ticker = ? AND b.hour >= ? AND b.hour < ?
            ORDER BY b.hour
        """, (ticker, lo, hi))
    burst_hours = set(db.to_utc(bursts["hour"])) if len(bursts) else set()
    rows["in_burst_hour"] = rows["ts"].dt.floor("h").isin(burst_hours).astype(int)

    clusters = pd.DataFrame()
    rows["dup_cluster_id"] = None
    rows["dup_cluster_channels"] = None
    if db.table_exists(conn, "dup_members") and len(rows):
        members = db.read_df(conn, """
            SELECT dm.channel, dm.message_id, c.cluster_id, c.size, c.n_channels, c.first_date,
                   c.span_minutes, c.sample_text
            FROM dup_members dm
            JOIN mentions m ON m.channel = dm.channel AND m.message_id = dm.message_id
            JOIN dup_clusters c ON c.cluster_id = dm.cluster_id
            WHERE m.ticker = ? AND m.date >= ? AND m.date < ?
        """, (ticker, lo, hi))
        if len(members):
            key = members.set_index(["channel", "message_id"])
            idx = list(zip(rows["channel"], rows["message_id"]))
            rows["dup_cluster_id"] = [key["cluster_id"].get(k) for k in idx]
            rows["dup_cluster_channels"] = [key["n_channels"].get(k) for k in idx]
            clusters = (members.drop_duplicates("cluster_id")
                        .sort_values(["n_channels", "size"], ascending=False))

    pairs = coposting_counts(zip([ticker] * len(rows), rows["ts"].astype("int64") // 10**9, rows["channel"]),
                             cfg["coposting"]["window_minutes"])

    top_n, n_chars = rcfg["top_n"], rcfg["sample_chars"]
    lines = [f"Laporan {ticker}  {date_from} s/d {date_to} (UTC)", "=" * 60]
    if rows.empty:
        lines.append("Tidak ada mention dalam rentang ini.")
    else:
        lines.append(f"Mention : {len(rows)} pesan dari {rows['channel'].nunique()} kanal "
                     f"({rows['ts'].min():%Y-%m-%d %H:%M} - {rows['ts'].max():%Y-%m-%d %H:%M})")
        lines.append(f"Views   : median {rows['views'].median():,.0f}, total {rows['views'].sum():,.0f}")
        per_day = rows.groupby(rows["ts"].dt.date).size()
        lines.append(f"Harian  : rata-rata {per_day.mean():.1f}, puncak {per_day.max()} pada {per_day.idxmax()}")
        lines.append("")
        lines.append("Kanal teratas:")
        for ch, n in rows["channel"].value_counts().head(top_n).items():
            lines.append(f"  @{ch:<28} {n}")

    lines.append("")
    lines.append(f"Burst (z > {cfg['bursts']['z_threshold']}): {len(bursts)} jam")
    for b in bursts.itertuples():
        vol_txt = ""
        ratio = getattr(b, "volume_ratio_h1", None)
        if ratio is not None and pd.notna(ratio):
            vol_txt = f" | volume H+1 ({b.h1_date}) = {ratio:.2f}x rata-rata {cfg['prices']['baseline_days']} hari"
        elif "volume_ratio_h1" in bursts.columns:
            vol_txt = " | volume H+1 belum tersedia"
        lines.append(f"  {pd.Timestamp(b.hour):%Y-%m-%d %H:%M} UTC  mention={b.count} kanal={b.n_channels} "
                     f"z={b.z:.1f}{vol_txt}")

    lines.append("")
    lines.append(f"Cluster near-duplicate yang memuat {ticker}: {len(clusters)}")
    for c in clusters.head(top_n).itertuples():
        lines.append(f"  #{c.cluster_id}: {c.size} pesan, {c.n_channels} kanal, rentang {c.span_minutes:.0f} menit")
        lines.append(f"      \"{_short(c.sample_text, n_chars)}\"")

    lines.append("")
    lines.append(f"Pasangan kanal co-posting (jendela {cfg['coposting']['window_minutes']} menit): {len(pairs)}")
    for (a, b), n in pairs.most_common(top_n):
        lines.append(f"  @{a} - @{b}: {n}")

    return "\n".join(lines) + "\n", rows[CSV_COLUMNS]


def main():
    args = parse_args()
    sys.stdout.reconfigure(errors="replace")
    cfg = load_config(args.config)
    conn = db.connect(cfg["paths"]["db"])
    try:
        summary, rows = build_report(conn, cfg, args.ticker, args.date_from, args.date_to)
    except RuntimeError as e:
        sys.exit(str(e))
    finally:
        conn.close()

    out_dir = Path(args.out_dir) if args.out_dir else cfg["paths"]["reports_dir"]
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = out_dir / f"{args.ticker}_{args.date_from}_{args.date_to}"
    stem.with_suffix(".txt").write_text(summary, encoding="utf-8")
    rows.to_csv(stem.with_suffix(".csv"), index=False, encoding="utf-8-sig", quoting=csv.QUOTE_NONNUMERIC)
    print(summary)
    print(f"Tersimpan: {stem.with_suffix('.txt')} dan {stem.with_suffix('.csv')}")


if __name__ == "__main__":
    main()
