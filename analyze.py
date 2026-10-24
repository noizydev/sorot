"""Jalankan pipeline analisis di atas messages.db; hasil ditulis ke tabel-tabel baru di DB yang sama.

Contoh:
    python analyze.py                       # semua langkah
    python analyze.py --steps dedup,graph   # sebagian
"""
import argparse
import logging
import sys

from analysis import bursts, db, dedup, graph, prices, tickers
from analysis.config import load_config

STEPS = {
    "mentions": tickers.run,
    "dedup": dedup.run,
    "graph": graph.run,
    "bursts": bursts.run,
    "prices": prices.run,
}
# langkah -> tabel yang harus sudah ada
REQUIRES = {"graph": "mentions", "bursts": "mentions", "prices": "bursts"}


def parse_args():
    p = argparse.ArgumentParser(description="Analisis promosi saham terkoordinasi dari messages.db")
    p.add_argument("--config", default=None, help="Path config.yaml (default: config.yaml di folder proyek)")
    p.add_argument("--steps", default=",".join(STEPS),
                   help=f"Langkah dipisah koma, urut dijalankan (default: {','.join(STEPS)})")
    args = p.parse_args()
    args.steps = [s.strip() for s in args.steps.split(",") if s.strip()]
    unknown = [s for s in args.steps if s not in STEPS]
    if unknown:
        p.error(f"langkah tidak dikenal: {', '.join(unknown)}")
    return args


def main():
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.StreamHandler(sys.stdout)])
    log = logging.getLogger("analyze")
    cfg = load_config(args.config)
    conn = db.connect(cfg["paths"]["db"])
    try:
        for step in args.steps:
            needed = REQUIRES.get(step)
            if needed and not db.table_exists(conn, needed):
                sys.exit(f"Langkah {step} butuh tabel {needed}; jalankan langkah itu dulu.")
            if step == "prices" and not cfg["paths"]["prices_csv"].exists():
                log.warning("Lewati prices: %s belum ada", cfg["paths"]["prices_csv"])
                continue
            log.info("== %s ==", step)
            try:
                STEPS[step](conn, cfg)
            except (FileNotFoundError, ValueError) as e:
                sys.exit(f"Langkah {step} gagal: {e}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
