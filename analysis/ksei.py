"""Parser PDF "Pemegang Saham di atas 1% (KSEI)" yang dipublikasikan BEI.

Satu dokumen = satu lampiran data (lamp1) + surat pengantar (opsional) yang memuat waktu terbit,
nomor pengumuman, dan nomor pengumuman yang dikoreksi. Hasil parse di-cache sebagai CSV per sha256.
"""
import hashlib
import logging
import re
from datetime import datetime
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)

# Naikkan bila logika parse berubah: cache lama otomatis tidak dipakai lagi.
PARSER_VERSION = 4

COLUMNS = ["DATE", "SHARE_CODE", "ISSUER_NAME", "INVESTOR_NAME", "INVESTOR_CLASSIFICATION", "LOCAL_FOREIGN",
           "NATIONALITY", "DOMICILE", "HOLDINGS_SCRIPLESS", "HOLDINGS_SCRIP", "TOTAL_HOLDING_SHARES", "PERCENTAGE"]
OUT_COLUMNS = ["valid_date", "ticker", "issuer_name", "investor_name", "investor_class", "local_foreign",
               "nationality", "domicile", "scripless", "scrip", "total_shares", "pct"]

COVER_TIME_RE = re.compile(r"Tanggal dan Waktu\s+(\d{2}-\d{2}-\d{4} \d{2}:\d{2})")
COVER_NO_RE = re.compile(r"No\.\s*(Peng-[A-Z]+-\d+/[A-Z.]+/\d{2}-\d{4})")
COVER_REF_RE = re.compile(r"Merujuk pada Pengumuman Nomor\s+(Peng-[A-Z]+-\d+/[A-Z.]+/\d{2}-\d{4})")
COVER_ASOF_RE = re.compile(r"per\s+(\d{8})")


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_int(s):
    """'1.080.693.020' -> 1080693020 (titik = pemisah ribuan)."""
    s = (s or "").strip().replace(".", "").replace(" ", "")
    return int(s) if s.isdigit() else None


def parse_pct(s):
    """'35,24' -> 35.24 (koma = desimal)."""
    s = (s or "").strip().replace(" ", "")
    if not s or not re.fullmatch(r"\d+(,\d+)?", s):
        return None
    return float(s.replace(",", "."))


def fix_tbk_overflow(issuer, investor):
    """Akhiran "Tbk" nama emiten yang panjang kadang tercetak masuk ke sel investor dan bercampur dengan
    huruf awalnya ("…GRAHA PERSADA T" + "bPkT DIAN" = "…T bk" + "PT DIAN"). Huruf akhiran yang hilang dari
    sel emiten dibuang dari awal sel investor (berurutan, dalam jendela pendek), lalu emiten dilengkapi."""
    up = issuer.rstrip().upper()
    if not issuer or up.endswith("TBK") or up.endswith("TBK."):
        return issuer, investor
    k = next((n for n in (2, 1) if issuer.rstrip().endswith("Tbk"[:n])), 0)
    tail = "Tbk"[k:]
    window = len(tail) * 3 + 1
    head, rest = investor[:window], investor[window:]
    out, j = [], 0
    for ch in head:
        if j < len(tail) and ch == tail[j]:
            j += 1
        else:
            out.append(ch)
    if j < len(tail):  # akhiran tidak ditemukan: sel investor tidak tercemar, biarkan apa adanya
        return issuer, investor
    sep = "" if k else " "
    return issuer.rstrip() + sep + tail, " ".join(("".join(out) + rest).split())


def fix_issuer_overflow(issuer, investor, canonical):
    """Versi umum fix_tbk_overflow: sel emiten terpotong di tengah ("…BINTRAC") dan sisa nama lengkapnya
    ("O DHARMA Tbk", diketahui dari bulan lain) bercampur dengan nama investor. Huruf sisa (tanpa spasi)
    disaring berurutan dari awal sel investor; bila tidak semuanya ditemukan, baris dibiarkan."""
    issuer = issuer.rstrip()
    if not canonical or canonical == issuer or not canonical.startswith(issuer):
        return issuer, investor
    full_tail = canonical[len(issuer):].lstrip()
    # spasi ikut dicocokkan dulu (memberi pemisahan kata yang benar); bila gagal, coba tanpa spasi
    for tail in (full_tail, full_tail.replace(" ", "")):
        window = len(tail) * 3 + 2
        head, rest = investor[:window], investor[window:]
        out, j = [], 0
        for ch in head:
            if j < len(tail) and ch == tail[j]:
                j += 1
            else:
                out.append(ch)
        if j == len(tail):
            return canonical, " ".join(("".join(out) + rest).split())
    return issuer, investor


def canonical_issuers(frames):
    """ticker -> nama emiten lengkap (terpanjang yang berakhiran Tbk) dari kumpulan dokumen."""
    names = pd.concat([f[["ticker", "issuer_name"]] for f in frames]).drop_duplicates()
    full = names[names["issuer_name"].str.upper().str.rstrip(". ").str.endswith("TBK")]
    return full.assign(n=full["issuer_name"].str.len()).sort_values("n").drop_duplicates("ticker", keep="last") \
        .set_index("ticker")["issuer_name"].to_dict()


def clean_overflow(rows, canonical):
    """Terapkan fix_issuer_overflow ke semua baris yang sel emitennya terpotong."""
    rows = rows.copy()
    fixed = 0
    for i, (t, iss, inv) in enumerate(zip(rows["ticker"], rows["issuer_name"], rows["investor_name"])):
        new_iss, new_inv = fix_issuer_overflow(iss, inv, canonical.get(t))
        if new_inv != inv or new_iss != iss:
            rows.iat[i, rows.columns.get_loc("issuer_name")] = new_iss
            rows.iat[i, rows.columns.get_loc("investor_name")] = new_inv
            fixed += 1
    return rows, fixed


def parse_date(s):
    return datetime.strptime(s.strip(), "%d-%b-%Y").date()


def normalize_row(row):
    """Satu baris tabel (12 sel) -> dict OUT_COLUMNS, atau None untuk header/baris rusak."""
    if not row or len(row) != len(COLUMNS):
        return None
    cells = [" ".join((c or "").split()) for c in row]
    if cells[0] == "DATE" or cells[0].startswith("#"):
        return None
    try:
        valid = parse_date(cells[0])
    except ValueError:
        return None
    total, pct = parse_int(cells[10]), parse_pct(cells[11])
    if not cells[1] or not cells[3] or total is None or pct is None:
        return None
    cells[2], cells[3] = fix_tbk_overflow(cells[2], cells[3])
    return {
        "valid_date": valid.isoformat(), "ticker": cells[1].upper(), "issuer_name": cells[2],
        "investor_name": cells[3], "investor_class": cells[4], "local_foreign": cells[5],
        "nationality": cells[6], "domicile": cells[7], "scripless": parse_int(cells[8]) or 0,
        "scrip": parse_int(cells[9]) or 0, "total_shares": total, "pct": pct,
    }


NUMERIC_RE = re.compile(r"^[\d.,]+$")
DATE_RE = re.compile(r"^\d{2}-[A-Za-z]{3}-\d{4}$")
DATE_SEARCH_RE = re.compile(r"\d{2}-[A-Za-z]{3}-\d{4}")
N_TEXT = 8  # DATE..DOMICILE rata kiri; 4 kolom terakhir angka rata kanan


def _segments(words, gap=1.0):
    """Gabungkan kata dalam satu baris menjadi sel bila jaraknya < gap pt (spasi antarkata ±0,6 pt, antarkolom >1,5 pt)."""
    segs = []
    for w in sorted(words, key=lambda w: w["x0"]):
        if segs and w["x0"] - segs[-1]["x1"] < gap:
            segs[-1]["text"] += " " + w["text"]
            segs[-1]["x1"] = w["x1"]
        else:
            segs.append({"text": w["text"], "x0": w["x0"], "x1": w["x1"]})
    return segs


def rows_by_columns(page):
    """Baca tabel tanpa garis: posisi kolom dipelajari dari baris lengkap (12 sel) di halaman yang sama."""
    words = page.extract_words(x_tolerance=1.5)
    lines = {}
    for w in words:
        lines.setdefault(round(w["top"]), []).append(w)
    seg_lines = [_segments(lines[t]) for t in sorted(lines)]
    full = [s for s in seg_lines if len(s) == len(COLUMNS) and DATE_RE.match(s[0]["text"])]
    if not full:
        return []
    import statistics
    starts = [statistics.median(s[i]["x0"] for s in full) for i in range(N_TEXT)]
    ends = [statistics.median(s[i]["x1"] for s in full) for i in range(N_TEXT, len(COLUMNS))]
    out = []
    for segs in seg_lines:
        if not segs or segs[0]["text"] == "DATE":
            continue
        cells = [""] * len(COLUMNS)
        for sg in segs:
            if NUMERIC_RE.match(sg["text"]) and sg["x0"] > starts[-1]:
                k = N_TEXT + min(range(len(ends)), key=lambda i: abs(ends[i] - sg["x1"]))
            else:
                k = max([i for i in range(N_TEXT) if starts[i] <= sg["x0"] + 1.0] or [0])
            cells[k] = (cells[k] + " " + sg["text"]).strip()
        date = DATE_SEARCH_RE.search(cells[0])
        if date:  # sel tanggal kadang tertimpa teks legenda halaman ("Kode A #")
            cells[0] = date.group(0)
            out.append(cells)
        elif out and any(cells[2:N_TEXT]) and not any(cells[N_TEXT:]):
            # baris lanjutan dari sel teks yang terbungkus; teks di kolom tanggal/angka (legenda halaman) diabaikan
            out[-1] = [(a + " " + b).strip() if b and 2 <= i < N_TEXT else a
                       for i, (a, b) in enumerate(zip(out[-1], cells))]
    return split_overlapping_names(out)


def split_overlapping_names(rows):
    """Nama emiten yang terlalu panjang bisa tercetak menimpa kolom nama investor, sehingga huruf keduanya
    bercampur ("…DHARMA TbPkT. BALI" = "…DHARMA Tbk" + "PT. BALI"). Bagian emiten sama di semua baris
    emiten itu, jadi awalan bersama = nama emiten sebelum tumpang tindih; sisa "Tbk" lalu disaring."""
    import os
    by_ticker = {}
    for i, r in enumerate(rows):
        if r[2] and not r[3]:
            by_ticker.setdefault(r[1], []).append(i)
    for ticker, idx in by_ticker.items():
        if len(idx) < 2:
            continue
        prefix = os.path.commonprefix([rows[i][2] for i in idx])
        cut = prefix.upper().rfind("TBK")
        if cut >= 0:
            issuer, tail = prefix[:cut + 3], ""
        else:
            k = next((n for n in (2, 1) if prefix.endswith("Tbk"[:n])), 0)
            if not k:
                continue
            issuer, tail = prefix, "Tbk"[k:]
        for i in idx:
            rest, inv, j = rows[i][2][len(issuer):], [], 0
            for ch in rest:
                if j < len(tail) and ch == tail[j]:
                    j += 1
                else:
                    inv.append(ch)
            rows[i][2] = (issuer + tail).strip()
            rows[i][3] = "".join(inv).strip()
    return rows


def repair_pdf(path):
    """Bangun ulang xref PDF yang terpotong/rusak dengan qpdf (pikepdf). Kembalikan path salinan."""
    import tempfile

    import pikepdf
    out = Path(tempfile.gettempdir()) / f"sorot_repaired_{Path(path).stem}.pdf"
    with pikepdf.open(path) as pdf:
        pdf.save(out)
    return out


def parse_data_pdf(path):
    """Kembalikan (DataFrame baris, catatan). Catatan berisi 'diperbaiki' bila PDF harus direparasi dulu."""
    import pdfplumber
    from pdfplumber.utils.exceptions import PdfminerException
    note = None
    try:
        with pdfplumber.open(path):
            pass
    except PdfminerException as e:
        log.warning("%s rusak (%s); mencoba perbaikan qpdf", Path(path).name, e)
        path, note = repair_pdf(path), f"diperbaiki: {e}"
    rows, skipped, text_pages = [], 0, 0
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            tables = page.extract_tables()
            if not tables:  # sebagian bulan diterbitkan tanpa garis tabel
                tables = [rows_by_columns(page)]
                text_pages += bool(tables[0])
            for table in tables:
                for raw in table:
                    r = normalize_row(raw)
                    if r:
                        rows.append(r)
                    elif raw and raw[0] not in ("DATE",) and any(raw):
                        skipped += 1
    if text_pages:
        log.info("%s: %s halaman tanpa garis tabel dibaca per posisi kolom", Path(path).name, text_pages)
    log.info("%s: %s baris, %s dilewati", Path(path).name, len(rows), skipped)
    return pd.DataFrame(rows, columns=OUT_COLUMNS), note


def parse_cover(path):
    """Metadata dari surat pengantar: waktu terbit, nomor, nomor yang dikoreksi, tanggal data."""
    import pdfplumber
    with pdfplumber.open(path) as pdf:
        text = " ".join(" ".join((p.extract_text() or "").split()) for p in pdf.pages)
    published = COVER_TIME_RE.search(text)
    number = COVER_NO_RE.search(text)
    ref = COVER_REF_RE.search(text)
    asof = COVER_ASOF_RE.search(text)
    return {
        "published_at": datetime.strptime(published.group(1), "%d-%m-%Y %H:%M").isoformat() if published else None,
        "announcement_no": number.group(1) if number else None,
        "corrects": ref.group(1) if ref else None,
        "cover_asof": datetime.strptime(asof.group(1), "%Y%m%d").date().isoformat() if asof else None,
    }


def load_document(data_pdf, parsed_dir):
    """Parse (atau ambil dari cache) satu lampiran data beserta surat pengantarnya."""
    data_pdf = Path(data_pdf)
    digest = sha256(data_pdf)
    parsed_dir = Path(parsed_dir)
    parsed_dir.mkdir(parents=True, exist_ok=True)
    cache = parsed_dir / f"{digest[:16]}_v{PARSER_VERSION}.csv"
    note_file = cache.with_suffix(".note")
    rows = None
    if cache.exists():
        rows = pd.read_csv(cache, dtype={"ticker": str, "nationality": str}, keep_default_na=False)
        note = note_file.read_text(encoding="utf-8") if note_file.exists() else None
    if rows is None or rows.empty:  # cache kosong dari parser lama: baca ulang
        rows, note = parse_data_pdf(data_pdf)
        rows.to_csv(cache, index=False)
        if note:
            note_file.write_text(note, encoding="utf-8")
    cover = data_pdf.with_name(data_pdf.name.replace("_lamp1_data", "_pengantar"))
    meta = parse_cover(cover) if cover.exists() and cover != data_pdf else {}
    if not meta.get("published_at"):
        # tanpa surat pengantar: pakai tanggal di nama file (YYYYMMDD_...) sebagai waktu terbit
        m = re.match(r"(\d{8})", data_pdf.name)
        meta["published_at"] = datetime.strptime(m.group(1), "%Y%m%d").isoformat() if m else None
    meta.update(file=data_pdf.name, sha256=digest, rows=len(rows), note=note,
                valid_date=rows["valid_date"].mode().iat[0] if len(rows) else None)
    return meta, rows
