"""Klasifikasi kasar jenis pesan dengan kata kunci (dipakai laporan riset dan dashboard)."""
import re

ROUTINE = "laporan rutin"
NEWS = "berita"
PROMO = "ajakan/rekomendasi"
OTHER = "lainnya"
KINDS = [ROUTINE, NEWS, PROMO, OTHER]

REPORT_RE = re.compile(r"market report|top gainer|top loser|sesi 1|sesi pertama|sesi kedua|session|day break|"
                       r"closing review|market highlight|market review|#ihsg|top foreign|top local|"
                       r"cashdeviden|stocksplit|exdate|recdate|rups", re.I)
NEWS_RE = re.compile(r"kontan|bisnis\.com|cnbc|katadata|emitennews|investor\.id|idx channel|iqplus|"
                     r"https?://|msci|ftse|ojk|laporan keuangan|laba bersih", re.I)
PROMO_RE = re.compile(r"\brekom|\btp\d?\b|target|entry|\bbuy\b|\bbeli\b|join|vip|premium|cuan|sinyal|"
                      r"tradingplan|swing|daytrade|belanja", re.I)


def classify(text):
    """Urutan prioritas: laporan rutin > berita > ajakan/rekomendasi > lainnya."""
    t = text or ""
    if REPORT_RE.search(t):
        return ROUTINE
    if NEWS_RE.search(t):
        return NEWS
    if PROMO_RE.search(t):
        return PROMO
    return OTHER
