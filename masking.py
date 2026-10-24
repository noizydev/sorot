"""Penyamaran nomor telepon dan email di teks pesan sebelum disimpan ke SQLite.

Pola nomor sengaja ketat (wajib prefiks +62 / 62 / 0 dan pengelompokan digit
yang wajar) supaya harga saham, level IHSG, dan volume tidak ikut tersamarkan.
"""
import re

EMAIL_TOKEN = "[EMAIL]"
PHONE_TOKEN = "[PHONE]"

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")

# Tautan WhatsApp yang memuat nomor: wa.me/62812..., api.whatsapp.com/send?phone=62812...
WA_LINK_RE = re.compile(
    r"(?:https?://)?(?:www\.)?(?:wa\.me/|api\.whatsapp\.com/send/?\?phone=)\+?\d{8,15}",
    re.IGNORECASE,
)

_SEP = r"[ .\-]?"

PHONE_RES = [
    # Seluler internasional: +62 812-3456-7890, (+62) 878 8889 7847, +6281234567890
    re.compile(rf"(?<![\w+])(?:\(\+?62\)|\+62){_SEP}8\d{{1,3}}{_SEP}\d{{3,4}}{_SEP}\d{{3,5}}(?!\d)"),
    # Seluler dengan 62 tanpa plus: hanya jika digitnya menyambung (deret harga dipisah spasi tidak kena)
    re.compile(r"(?<![\w+])628\d{8,11}(?!\d)"),
    # Seluler lokal: 0812-3456-7890, 087888897847, 0813 1234 5678
    re.compile(rf"(?<![\w+])08\d{{1,3}}{_SEP}\d{{3,4}}{_SEP}\d{{3,5}}(?!\d)"),
    # Telepon rumah/kantor: (021) 555-1234, 021-5551234, 0274 123456, +62 21 5551234
    re.compile(
        rf"(?<![\w+])(?:\(0[2-7]\d{{1,2}}\)|0[2-7]\d{{1,2}}|\+62{_SEP}[2-7]\d{{1,2}})"
        rf"{_SEP}\d{{3,4}}{_SEP}\d{{3,4}}(?!\d)"
    ),
]


def mask_pii(text):
    """Kembalikan teks dengan email dan nomor telepon diganti token. None tetap None."""
    if not text:
        return text
    text = EMAIL_RE.sub(EMAIL_TOKEN, text)
    text = WA_LINK_RE.sub(PHONE_TOKEN, text)
    for pattern in PHONE_RES:
        text = pattern.sub(PHONE_TOKEN, text)
    return text
