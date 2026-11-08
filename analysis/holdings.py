"""Penyimpanan kepemilikan saham secara bitemporal.

Dua sumbu waktu:
- valid time  (`valid_date`): tanggal posisi kepemilikan menurut KSEI (mis. per 31 Mar 2026).
- transaction time (`tx_from`, `tx_to`): kapan versi data itu diketahui publik (waktu terbit
  pengumuman BEI) sampai digantikan koreksi berikutnya. `tx_to` NULL = versi yang berlaku sekarang.

Baris tidak pernah diubah isinya atau dihapus; koreksi menambah versi baru dan hanya menutup
`tx_to` versi lama. Dengan begitu pertanyaan "apa yang diketahui publik pada tanggal X tentang
posisi per tanggal Y" selalu bisa dijawab.
"""
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from analysis import ksei

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    doc_id          INTEGER PRIMARY KEY,
    file            TEXT NOT NULL,
    sha256          TEXT NOT NULL UNIQUE,
    valid_date      TEXT NOT NULL,
    published_at    TEXT NOT NULL,
    announcement_no TEXT,
    corrects        TEXT,
    rows            INTEGER NOT NULL,
    imported_at     TEXT NOT NULL,
    note            TEXT       -- mis. 'diperbaiki: ...' bila PDF sumber rusak
);
CREATE TABLE IF NOT EXISTS holdings (
    doc_id         INTEGER NOT NULL REFERENCES documents(doc_id),
    row_no         INTEGER NOT NULL,
    valid_date     TEXT NOT NULL,
    ticker         TEXT NOT NULL,
    issuer_name    TEXT,
    investor_name  TEXT NOT NULL,
    investor_class TEXT,
    local_foreign  TEXT,
    nationality    TEXT,
    domicile       TEXT,
    scripless      INTEGER,
    scrip          INTEGER,
    total_shares   INTEGER NOT NULL,
    pct            REAL NOT NULL,
    tx_from        TEXT NOT NULL,
    tx_to          TEXT,
    PRIMARY KEY (doc_id, row_no)
);
CREATE INDEX IF NOT EXISTS idx_holdings_valid ON holdings(valid_date, ticker);
CREATE INDEX IF NOT EXISTS idx_holdings_tx ON holdings(tx_to);
"""


def connect(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def import_documents(conn, pdf_dir, parsed_dir):
    """Impor semua lampiran data di pdf_dir yang belum ada (berdasarkan sha256). Kembalikan jumlah dokumen baru."""
    known = {r["sha256"] for r in conn.execute("SELECT sha256 FROM documents")}
    added = 0
    docs = [ksei.load_document(pdf, parsed_dir) for pdf in sorted(Path(pdf_dir).glob("*_lamp1_data.pdf"))]
    # nama emiten lengkap dari semua dokumen, untuk membersihkan sel yang terpotong/bertumpuk
    canonical = ksei.canonical_issuers([rows for _, rows in docs if len(rows)])
    for meta, rows in docs:
        if meta["sha256"] in known:
            continue
        if rows.empty or not meta.get("published_at"):
            log.warning("Lewati %s: tidak ada baris atau waktu terbit", meta["file"])
            continue
        rows, fixed = ksei.clean_overflow(rows, canonical)
        if fixed:
            log.info("%s: %s nama investor dipulihkan dari tumpukan nama emiten", meta["file"], fixed)
        add_document(conn, meta, rows)
        added += 1
        log.info("Impor %s: per %s, terbit %s, %s baris", meta["file"], meta["valid_date"], meta["published_at"], len(rows))
    return added


HOLDING_COLS = ["doc_id", "row_no", "valid_date", "ticker", "issuer_name", "investor_name", "investor_class",
                "local_foreign", "nationality", "domicile", "scripless", "scrip", "total_shares", "pct", "tx_from", "tx_to"]


def add_document(conn, meta, rows):
    """Tambahkan satu versi data (meta: file, sha256, valid_date, published_at, ...; rows: kolom ksei.OUT_COLUMNS)."""
    cur = conn.execute(
        "INSERT INTO documents (file, sha256, valid_date, published_at, announcement_no, corrects, rows, imported_at, note)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (meta["file"], meta["sha256"], meta["valid_date"], meta["published_at"], meta.get("announcement_no"),
         meta.get("corrects"), len(rows), datetime.now(timezone.utc).isoformat(timespec="seconds"), meta.get("note")))
    rows = rows.assign(doc_id=cur.lastrowid, row_no=range(len(rows)), tx_from=meta["published_at"], tx_to=None)
    conn.executemany(f"INSERT INTO holdings ({','.join(HOLDING_COLS)}) VALUES ({','.join('?' * len(HOLDING_COLS))})",
                     rows[HOLDING_COLS].itertuples(index=False, name=None))
    _close_superseded(conn)
    conn.commit()
    return cur.lastrowid


def _close_superseded(conn):
    """Untuk tiap valid_date, versi ke-k berlaku sampai versi ke-(k+1) terbit. Hanya mengisi tx_to yang
    masih kosong, sehingga riwayat yang sudah tertutup tidak pernah ditulis ulang."""
    docs = conn.execute("SELECT doc_id, valid_date, published_at FROM documents ORDER BY valid_date, published_at").fetchall()
    by_valid = {}
    for d in docs:
        by_valid.setdefault(d["valid_date"], []).append(d)
    for versions in by_valid.values():
        for old, new in zip(versions, versions[1:]):
            conn.execute("UPDATE holdings SET tx_to = ? WHERE doc_id = ? AND tx_to IS NULL",
                         (new["published_at"], old["doc_id"]))


def documents(conn):
    return pd.read_sql_query("SELECT * FROM documents ORDER BY valid_date, published_at", conn)


def valid_dates(conn):
    return [r[0] for r in conn.execute("SELECT DISTINCT valid_date FROM holdings ORDER BY valid_date")]


def snapshot(conn, valid_date, known_at=None, ticker=None):
    """Posisi per `valid_date` seperti yang diketahui pada `known_at` (ISO; None = versi terbaru)."""
    where = ["valid_date = ?"]
    params = [valid_date]
    if known_at is None:
        where.append("tx_to IS NULL")
    else:
        where += ["tx_from <= ?", "(tx_to IS NULL OR tx_to > ?)"]
        params += [known_at, known_at]
    if ticker:
        where.append("ticker = ?")
        params.append(ticker)
    return pd.read_sql_query(f"SELECT * FROM holdings WHERE {' AND '.join(where)} ORDER BY ticker, pct DESC",
                             conn, params=params)


def history(conn, ticker):
    """Semua versi semua tanggal untuk satu ticker (untuk tampilan bitemporal)."""
    return pd.read_sql_query("SELECT * FROM holdings WHERE ticker = ? ORDER BY valid_date, tx_from, pct DESC",
                             conn, params=(ticker,))


def diff_versions(conn, valid_date):
    """Perubahan antar versi untuk satu valid_date: baris yang muncul, hilang, atau berubah jumlahnya."""
    docs = pd.read_sql_query("SELECT doc_id, published_at FROM documents WHERE valid_date = ? ORDER BY published_at",
                             conn, params=(valid_date,)).to_dict("records")
    out = []
    for old, new in zip(docs, docs[1:]):
        a = pd.read_sql_query("SELECT ticker, investor_name, total_shares, pct FROM holdings WHERE doc_id = ?",
                              conn, params=(old["doc_id"],))
        b = pd.read_sql_query("SELECT ticker, investor_name, total_shares, pct FROM holdings WHERE doc_id = ?",
                              conn, params=(new["doc_id"],))
        m = a.merge(b, on=["ticker", "investor_name"], how="outer", suffixes=("_lama", "_baru"), indicator=True)
        m["perubahan"] = m["_merge"].astype(str).map({"left_only": "dihapus", "right_only": "ditambah", "both": "sama"})
        m.loc[(m["_merge"] == "both") & (m["total_shares_lama"] != m["total_shares_baru"]), "perubahan"] = "diubah"
        m = m[m["perubahan"] != "sama"].drop(columns="_merge")
        m.insert(0, "versi_baru", new["published_at"])
        m.insert(0, "versi_lama", old["published_at"])
        out.append(m)
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


def outstanding_shares(snap):
    """Perkiraan saham beredar per ticker: dari pemegang terbesar (persentase paling presisi)."""
    top = snap.sort_values("pct", ascending=False).drop_duplicates("ticker")
    return (top.set_index("ticker")["total_shares"] / (top.set_index("ticker")["pct"] / 100)).round()
