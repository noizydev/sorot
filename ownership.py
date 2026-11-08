"""Kepemilikan saham: impor PDF KSEI (bitemporal), entity resolution + free float efektif,
deteksi penghindaran ambang 1%, dan label risiko yang dicatat di transparency log.

    python ownership.py import       # parse PDF baru di data/shareholders/pdf/
    python ownership.py freefloat    # entity resolution + kepemilikan terintegrasi + free float
    python ownership.py threshold    # pemegang yang hilang tepat di bawah 1%
    python ownership.py risk         # hitung label risiko lalu publikasikan ke transparency log
    python ownership.py all          # semua langkah di atas
    python ownership.py verify       # audit transparency log
"""
import argparse
import logging
import sys

from analysis import freefloat, holdings, risk, threshold
from analysis.config import load_config
from analysis.translog import TransparencyLog

STEPS = ["import", "freefloat", "threshold", "risk"]


def main():
    p = argparse.ArgumentParser(description="Analisis kepemilikan saham dan label risiko SOROT")
    p.add_argument("command", choices=STEPS + ["all", "verify"])
    p.add_argument("--config", default=None)
    p.add_argument("--no-publish", action="store_true", help="risk: hitung tanpa menulis ke transparency log")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.StreamHandler(sys.stdout)])
    log = logging.getLogger("ownership")
    cfg = load_config(args.config)
    paths = cfg["paths"]
    tlog = TransparencyLog(paths["transparency_log"], paths["transparency_heads"])

    if args.command == "verify":
        ok, problems = tlog.audit()
        heads = tlog.heads()
        log.info("Log: %s entri, %s tree head", len(tlog.entries()), len(heads))
        if heads:
            log.info("Root terakhir (ukuran %s): %s", heads[-1]["size"], heads[-1]["root"])
        for pr in problems:
            log.error(pr)
        log.info("Audit %s", "LULUS: log utuh dan konsisten dengan semua tree head" if ok else "GAGAL")
        sys.exit(0 if ok else 1)

    conn = holdings.connect(paths["ownership_db"])
    try:
        steps = STEPS if args.command == "all" else [args.command]
        for step in steps:
            log.info("== %s ==", step)
            if step == "import":
                n = holdings.import_documents(conn, paths["shareholder_pdf_dir"], paths["shareholder_parsed_dir"])
                docs = holdings.documents(conn)
                log.info("%s dokumen baru; total %s dokumen untuk %s tanggal data", n, len(docs),
                         docs["valid_date"].nunique())
            elif step == "freefloat":
                summary, entities = freefloat.run(conn, cfg)
                merged = int((entities["n_names"] > 1).sum())
                linked = int(entities["ticker"].notna().sum())
                last = summary[summary["valid_date"] == summary["valid_date"].max()]
                log.info("Entitas: %s (%s dari >1 nama, %s terhubung ke emiten tercatat)", len(entities), merged, linked)
                log.info("Free float per %s: %s emiten, %s dengan efektif < %s%%, %s dengan selisih >= 5 poin",
                         last["valid_date"].iat[0], len(last), int((last["ff_effective"] < cfg["ownership"]["ff_min_pct"]).sum()),
                         cfg["ownership"]["ff_min_pct"], int((last["gap"] >= 5).sum()))
            elif step == "threshold":
                events, hov = threshold.run(conn, cfg)
                if len(events):
                    log.info("Keluar dari daftar: %s; tepat di bawah ambang: %s; terjelaskan dilusi: %s",
                             len(events), int((events["status"] == "tepat_di_bawah_ambang").sum()),
                             int((events["status"] == "terjelaskan_dilusi").sum()))
                log.info("Pemegang 'parkir' tepat di atas ambang: %s", len(hov))
            elif step == "risk":
                df, records = risk.compute(conn, cfg)
                counts = df["label"].value_counts().to_dict()
                log.info("Label: %s", counts)
                if args.no_publish:
                    log.info("--no-publish: tidak ditulis ke transparency log")
                else:
                    head = risk.publish(conn, df, records, tlog)
                    log.info("Dipublikasikan ke transparency log: ukuran %s, root %s", head["size"], head["root"])
    except (ValueError, FileNotFoundError) as e:
        sys.exit(f"Gagal: {e}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
