"""Bangun idx_tickers.csv dan prices.csv dari kode yang benar-benar muncul di pesan.

Kandidat = token 4 huruf kapital (opsional $/#) yang muncul di >= --min-messages pesan.
Kandidat valid bila Yahoo Finance mengembalikan data harian <KODE>.JK dari bursa Jakarta.
Tanda `ambiguous` yang sudah ada di idx_tickers.csv dipertahankan.

    python research/validate_tickers.py --from 2026-01-01
"""
import argparse
import csv
import json
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
from fetch_prices import CHART_URL, COLUMNS  # noqa: E402

TOKEN_RE = re.compile(r"(?<![A-Za-z0-9])[$#]?([A-Z]{4})(?![A-Za-z0-9])")


def candidates(db_path, min_messages):
    conn = sqlite3.connect(db_path)
    counts = Counter()
    for (text,) in conn.execute("SELECT text FROM messages WHERE text IS NOT NULL"):
        counts.update(set(TOKEN_RE.findall(text)))
    conn.close()
    return sorted(t for t, n in counts.items() if n >= min_messages)


def fetch_chart(code, p1, p2):
    req = urllib.request.Request(CHART_URL.format(symbol=f"{code}.JK", p1=p1, p2=p2),
                                 headers={"User-Agent": "Mozilla/5.0"})
    err = "gagal setelah 3 percobaan"
    for _ in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r), None
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None, None
            if e.code == 429:  # dibatasi: tunggu lalu coba lagi
                time.sleep(30)
                continue
            return None, f"HTTP {e.code}"
        except Exception as e:  # koneksi putus sesaat
            err = str(e)
            time.sleep(5)
    return None, err


def main():
    ap = argparse.ArgumentParser(description="Validasi kode saham dari pesan ke Yahoo Finance")
    ap.add_argument("--from", dest="date_from", default="2026-01-01")
    ap.add_argument("--to", dest="date_to", default=date.today().isoformat())
    ap.add_argument("--min-messages", type=int, default=5, help="Minimal jumlah pesan yang memuat token")
    ap.add_argument("--delay", type=float, default=0.35)
    args = ap.parse_args()
    date_from, date_to = date.fromisoformat(args.date_from), date.fromisoformat(args.date_to)
    p1 = int(datetime.combine(date_from, dtime.min, timezone.utc).timestamp())
    p2 = int(datetime.combine(date_to + timedelta(days=1), dtime.min, timezone.utc).timestamp())

    tickers_csv = PROJECT / "idx_tickers.csv"
    flags = {}
    if tickers_csv.exists():
        with open(tickers_csv, encoding="utf-8-sig", newline="") as f:
            flags = {r["ticker"]: r.get("ambiguous", "") for r in csv.DictReader(f)}

    cands = candidates(PROJECT / "data" / "messages.db", args.min_messages)
    print(f"{len(cands)} kandidat token")
    valid, prices, errors = [], [], []
    for i, code in enumerate(cands):
        data, err = fetch_chart(code, p1, p2)
        if err:
            errors.append(code)
        res = ((data or {}).get("chart") or {}).get("result")
        if res:
            res = res[0]
            meta = res["meta"]
            if meta.get("exchangeName") in ("JKT", "JKSE") or meta.get("exchangeTimezoneName") == "Asia/Jakarta":
                offset = timedelta(seconds=meta.get("gmtoffset", 7 * 3600))
                q = res["indicators"]["quote"][0]
                rows = []
                for j, ts in enumerate(res.get("timestamp") or []):
                    if q["close"][j] is None or q["volume"][j] is None:
                        continue
                    d = (datetime.fromtimestamp(ts, timezone.utc) + offset).date()
                    if date_from <= d <= date_to:
                        rows.append([d.isoformat(), code, q["open"][j], q["high"][j], q["low"][j], q["close"][j],
                                     int(q["volume"][j])])
                if rows:
                    prices.extend(rows)
                    valid.append((code, meta.get("longName") or meta.get("shortName") or ""))
        if i % 100 == 0:
            print(f"{i}/{len(cands)} valid={len(valid)} gagal={len(errors)}", flush=True)
        time.sleep(args.delay)

    with open(tickers_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ticker", "name", "ambiguous"])
        for code, name in sorted(valid):
            w.writerow([code, name, flags.get(code, "")])
    prices.sort(key=lambda r: (r[1], r[0]))
    with open(PROJECT / "prices.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(COLUMNS)
        w.writerows(prices)
    print(f"Selesai: {len(valid)} kode valid dari {len(cands)}, {len(prices)} baris harga")
    if errors:
        print(f"Gagal diperiksa (coba ulang nanti): {', '.join(errors)}")


if __name__ == "__main__":
    main()
