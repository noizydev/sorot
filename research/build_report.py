"""Langkah 3 riset: gabungkan research.json + template.html -> reports/laporan_riset_sorot.html.

    python research/build_report.py
"""
import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
OUT = PROJECT / "reports" / "laporan_riset_sorot.html"
d = json.load(open(PROJECT / "data" / "analysis" / "research.json", encoding="utf-8"))

kinds = d["extra"]["mention_kinds_total"]
total = sum(kinds.values())
d["corpus"]["share_routine_news"] = (kinds["laporan rutin"] + kinds["berita"]) / total
d["corpus"]["share_promo"] = kinds["ajakan/rekomendasi"] / total
for row in d["extra"]["burst_summary"]:
    k = next(r for r in d["extra"]["burst_kinds"] if r["group"] == row["group"])
    n = sum(v for key, v in k.items() if key != "group")
    row["share_promo_dominant"] = k.get("ajakan/rekomendasi", 0) / n

conn = sqlite3.connect(f"{PROJECT}/data/messages.db")
corpus = {r[0] for r in conn.execute("SELECT DISTINCT channel FROM messages")}
d["fwdTotalPremium"] = sum(raw for src, raw in conn.execute(
    "SELECT source, raw_weight FROM graph_edges WHERE kind='forward'") if src not in corpus)

d["periodLabel"] = "1 Apr – 24 Okt 2026"
d["generated"] = "24 Okt 2026"

CASES = {
    "ENRG": ("hot", "klaim setelah naik",
             "Delapan dari 14 burst ENRG melibatkan dua kanal atau lebih, terbanyak di sampel. Jam burst 14 Juli "
             "pukul 10.00 WIB berisi pesan “done target”, “pesta profit bersama warga premium”, dan "
             "ajakan “daftar member priority” dari dua kanal, ditambah satu ulasan harga minyak. Volume hari itu "
             "2,8× rata-rata dan H+1 1,7×. Pesan-pesan ini merayakan kenaikan yang sudah terjadi, jadi lebih "
             "tepat dibaca sebagai pemasaran keanggotaan berbayar daripada ajakan beli terkoordinasi."),
    "CDIA": ("hot", "klaim setelah naik",
             "Hanya satu burst, tetapi volumenya tertinggi di antara burst yang didominasi ajakan: 6,8× pada hari "
             "burst dan 3,9× pada H+1. Tiga kanal menulis dalam 45 menit: “sudah di 1200 aja +40%”, "
             "“1 minggu +40%… yang mau join chat admin”, dan “pesta cuan jumbo… Member Premium”. "
             "Kesamaan pesan lintas kanal ini layak ditinjau manual, meski isinya merayakan kenaikan yang sudah terjadi."),
    "INET": ("acc", "mengikuti harga",
             "Rasio volume H+1 tertinggi di seluruh sampel: 9,9× setelah burst 18 Agustus pukul 12.00 WIB. Tiga dari "
             "empat pesan di jam itu adalah laporan penutupan sesi 1, salah satunya mencantumkan INET sebagai top gainer "
             "(+23,1%), lalu satu pesan “Daytrade dan Swingtrade $INET”. Perhatian kanal datang setelah harga "
             "melonjak. Burst reaktif seperti ini perlu disaring sebelum menyimpulkan adanya promosi."),
    "TPIA": ("acc", "dipicu berita",
             "Skor sinyal tertinggi, tetapi sebagian besar berasal dari berita: MSCI Index Review 12 Mei 2026 yang "
             "mengeluarkan TPIA bersama AMMN, BREN, DSSA, CUAN, dan AMRT dari indeks standar. Teks pengumuman yang hampir "
             "sama muncul di lima kanal (cluster near-duplicate terbesar). Volume H+1 2,9×, dan rasio tertinggi "
             "(7,6× pada 3 Juni) berdekatan dengan tanggal efektif perubahan indeks."),
    "BUMI": ("quiet", "promosi satu kanal",
             "61% mention BUMI berasal dari satu kanal, @bss_sahamindonesia, yang rutin memposting rekap “rekom sore "
             "tercapai target” berisi daftar cashtag. Delapan dari sembilan burst hanya melibatkan satu kanal, dan "
             "median volume H+1 0,8×, di bawah normal. Intensitas promosi yang tinggi tidak otomatis meninggalkan "
             "jejak di pasar."),
}
BULAN = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des"]
for c in d["cases"]:
    c["tone"], c["pattern"], c["reading"] = CASES[c["ticker"]]
    if c["burst_msgs"]:
        h = datetime.fromisoformat(c["burst_msgs"][0]["burst_hour"]) + timedelta(hours=7)
        c["burst_label"] = f"{h.day} {BULAN[h.month - 1]} {h.year}, {h:%H}.00 WIB"
        for m in c["burst_msgs"]:
            hh, mm = map(int, m["time"].split(":"))
            m["time"] = f"{(hh + 7) % 24:02d}.{mm:02d}"
    c.pop("prices_full", None)

d.pop("channels", None)
html = open(HERE / "template.html", encoding="utf-8").read()
data = json.dumps(d, ensure_ascii=False, separators=(",", ":"), default=str).replace("</", "<\\/")
html = html.replace("/*__DATA__*/null", data)
OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(html, encoding="utf-8")
print(f"{OUT}: {len(html) / 1024:.0f} KB, fwdPremium={d['fwdTotalPremium']}")
