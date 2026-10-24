import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis.dedup import find_clusters, normalize_text, shingles  # noqa: E402

PROMO = ("Saham BUMI siap terbang! Akumulasi bandar sudah terlihat jelas, entry di area 120-125 "
         "target 150 dalam seminggu. Join VIP sekarang sebelum terlambat, slot terbatas.")


def test_normalize_text():
    raw = "🚀🚀 BUMI siap TERBANG!!! Cek https://t.me/xyz dan www.contoh.com  sekarang_juga"
    assert normalize_text(raw) == "bumi siap terbang cek dan sekarang juga"
    assert normalize_text("Target 1.250", strip_digits=True) == "target"
    assert normalize_text(None) == ""


def test_shingles():
    assert shingles("abcdef", 5) == {"abcde", "bcdef"}
    assert shingles("abc", 5) == {"abc"}
    assert shingles("", 5) == set()


def test_near_duplicates_grouped_and_distinct_text_separate():
    items = [
        (("a", 1), PROMO),
        (("b", 7), "🔥 " + PROMO.upper() + " 🔥"),                    # beda kapital/emoji: tetap sama
        (("c", 3), PROMO.replace("seminggu", "minggu ini") + " https://t.me/vip"),  # sedikit diubah
        (("d", 9), "Market Report EOD: IHSG ditutup menguat 0,95% ke level 5.439 dipimpin sektor energi "
                   "dan perbankan, asing mencatat net sell di pasar reguler."),
        (("e", 2), "Selamat pagi"),                                    # terlalu pendek: diabaikan
    ]
    clusters = find_clusters(items, shingle_size=5, threshold=0.8, num_perm=128, seed=1, min_chars=40)
    assert len(clusters) == 1
    assert sorted(clusters[0]) == [("a", 1), ("b", 7), ("c", 3)]


def test_below_threshold_not_grouped():
    other = ("Saham ANTM mulai akumulasi, area beli 1.500 dengan target 1.700. Pantau terus volume "
             "dan jangan lupa pasang stop loss di bawah 1.450.")
    clusters = find_clusters([(1, PROMO), (2, other)], threshold=0.8, min_chars=40)
    assert clusters == []


def test_strip_digits_merges_variants_with_different_prices():
    a = "Rekomendasi hari ini: GOTO beli di 70 target 85, stop loss 64. Disiplin money management ya."
    b = "Rekomendasi hari ini: GOTO beli di 112 target 139, stop loss 101. Disiplin money management ya."
    assert find_clusters([(1, a), (2, b)], threshold=0.8, min_chars=40) == []
    assert len(find_clusters([(1, a), (2, b)], threshold=0.8, min_chars=40, strip_digits=True)) == 1
