"""Unduh data EOD (OHLCV harian) dari Yahoo Finance untuk ticker di idx_tickers.csv -> prices.csv.

Contoh:
    python fetch_prices.py                          # sejak 2026-01-01 sampai hari ini
    python fetch_prices.py --from 2026-01-01 --ticker BREN --ticker BUMI

Memakai endpoint chart publik Yahoo (tidak resmi, tanpa API key); kode diberi sufiks .JK.
"""
import argparse
import csv
import json
import sys
import time
import urllib.request
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path

from analysis.config import load_config
from analysis.tickers import load_tickers

CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?period1={p1}&period2={p2}&interval=1d"
COLUMNS = ["date", "ticker", "open", "high", "low", "close", "volume"]


def parse_args():
    p = argparse.ArgumentParser(description="Unduh harga harian saham IDX dari Yahoo Finance")
    p.add_argument("--from", dest="date_from", default="2026-01-01",
                   help="Tanggal awal YYYY-MM-DD (beri jarak >= 20 hari bursa sebelum pesan pertama)")
    p.add_argument("--to", dest="date_to", default=date.today().isoformat(), help="Tanggal akhir YYYY-MM-DD")
    p.add_argument("--ticker", action="append", help="Kode tertentu saja (default: semua di idx_tickers.csv)")
    p.add_argument("--config", default=None, help="Path config.yaml")
    p.add_argument("--out", default=None, help="File keluaran (default: paths.prices_csv)")
    p.add_argument("--delay", type=float, default=1.0, help="Jeda antar-ticker (detik)")
    return p.parse_args()


def fetch(symbol, date_from, date_to):
    p1 = int(datetime.combine(date_from, dtime.min, timezone.utc).timestamp())
    p2 = int(datetime.combine(date_to + timedelta(days=1), dtime.min, timezone.utc).timestamp())
    req = urllib.request.Request(CHART_URL.format(symbol=symbol, p1=p1, p2=p2), headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.load(r)
    result = (data.get("chart") or {}).get("result")
    if not result:
        raise ValueError((data.get("chart") or {}).get("error") or "respons kosong")
    result = result[0]
    # Timestamp harian = jam buka bursa; tanggal dihitung di zona waktu bursa (WIB).
    offset = timedelta(seconds=result["meta"].get("gmtoffset", 7 * 3600))
    quote = result["indicators"]["quote"][0]
    rows = []
    for i, ts in enumerate(result.get("timestamp") or []):
        if quote["close"][i] is None or quote["volume"][i] is None:
            continue  # hari tanpa transaksi / data belum lengkap
        d = (datetime.fromtimestamp(ts, timezone.utc) + offset).date()
        if date_from <= d <= date_to:
            rows.append([d.isoformat(), symbol.removesuffix(".JK"), quote["open"][i], quote["high"][i],
                         quote["low"][i], quote["close"][i], int(quote["volume"][i])])
    return rows


def main():
    args = parse_args()
    try:
        date_from, date_to = date.fromisoformat(args.date_from), date.fromisoformat(args.date_to)
    except ValueError:
        sys.exit("--from/--to harus berformat YYYY-MM-DD")
    cfg = load_config(args.config)
    tickers = sorted({t.upper() for t in args.ticker}) if args.ticker else sorted(load_tickers(cfg["paths"]["tickers_csv"])[0])
    out = Path(args.out) if args.out else cfg["paths"]["prices_csv"]

    all_rows, failed = [], []
    for i, ticker in enumerate(tickers):
        try:
            rows = fetch(f"{ticker}.JK", date_from, date_to)
            all_rows.extend(rows)
            span = f"{rows[0][0]} s/d {rows[-1][0]}" if rows else "tidak ada data"
            print(f"{ticker}: {len(rows)} hari ({span})")
        except Exception as e:  # satu ticker gagal tidak menghentikan yang lain
            failed.append(ticker)
            print(f"{ticker}: GAGAL ({e})")
        if i < len(tickers) - 1:
            time.sleep(args.delay)

    all_rows.sort(key=lambda r: (r[1], r[0]))
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(COLUMNS)
        w.writerows(all_rows)
    print(f"Tersimpan {len(all_rows)} baris -> {out}")
    if failed:
        sys.exit(f"Gagal: {', '.join(failed)}")


if __name__ == "__main__":
    main()
