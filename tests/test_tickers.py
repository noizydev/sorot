import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis.tickers import TickerExtractor, load_tickers  # noqa: E402

TICKERS = {"BBCA", "BBRI", "BELI", "BUMI", "GOTO", "CUAN", "ANTM"}
AMBIGUOUS = {"BELI", "BUMI", "CUAN", "JUAL"}  # JUAL bukan ticker: diabaikan
CONTEXT = ["tp", "target", "entry", "sl", "buy", "sell", "beli", "jual"]


@pytest.fixture
def ex():
    return TickerExtractor(TICKERS, AMBIGUOUS, CONTEXT, context_window=5)


@pytest.mark.parametrize(
    "text, expected",
    [
        ("BBCA dan BBRI kuat hari ini", ["BBCA", "BBRI"]),
        ("Saya mau BELI rumah tahun depan", []),            # BELI kata umum, tanpa konteks
        ("BELI saham apa ya hari ini", []),                 # 'beli' sebagai kata kerja
        ("$BELI lagi menarik", ["BELI"]),                   # cashtag
        ("#BELI breakout", ["BELI"]),                       # hashtag
        ("BELI entry 450 TP 500", ["BELI"]),                # kata konteks dalam 5 kata
        ("Swing BELI 1.250-1.300 tp1 1.400", ["BELI"]),     # harga berformat tetap 1 token; tp1 -> tp
        ("BELI a b c d e f TP 500", []),                    # konteks di kata ke-7: terlalu jauh
        ("TP 500 untuk BUMI", ["BUMI"]),                    # konteks sebelum ticker
        ("Bakrie grup BUMI masih anteng", []),
        ("beli BUMI sekarang", ["BUMI"]),                   # 'beli' sebagai konteks untuk ticker lain
        ("BELI BELI BELI", []),                             # ticker ambigu tidak jadi konteks untuk dirinya
        ("BELI BUMI", []),                                  # kode kapital bukan kata konteks
        ("BELI BUMI TP 200", ["BELI", "BUMI"]),
        ("IHSG turun, LQ45 merah", []),                     # bukan ticker / bukan 4 huruf
        ("BBCA2 dan XBBCA bukan, BBCA. iya", ["BBCA"]),     # batas kata
        ("bbca huruf kecil tidak, $bbri boleh", ["BBRI"]),
        ("GOTO/ANTM/BBRI", ["ANTM", "BBRI", "GOTO"]),
        ("CUAN mantap", []),
        ("CUAN SL 15000", ["CUAN"]),
        ("", []),
        (None, []),
    ],
)
def test_extract_codes(ex, text, expected):
    assert ex.extract_codes(text) == expected


def test_mention_metadata(ex):
    mentions = ex.extract("Pantau $BUMI dan BBCA, BUMI target 200")
    assert [(m.ticker, m.prefixed, m.ambiguous) for m in mentions] == [
        ("BUMI", True, True), ("BBCA", False, False), ("BUMI", False, True),
    ]
    assert mentions[0].start == 7


def test_lowercase_prefix_can_be_disabled():
    ex = TickerExtractor(TICKERS, AMBIGUOUS, CONTEXT, allow_lowercase_with_prefix=False)
    assert ex.extract_codes("$bbri dan BBCA") == ["BBCA"]


def test_load_tickers(tmp_path):
    path = tmp_path / "idx_tickers.csv"
    path.write_text("Ticker,name,ambiguous\nBBCA,Bank Central Asia,0\nbeli,Global Digital Niaga,1\n"
                    "GOTO.JK,GoTo,\nIHSG5,bukan,0\n", encoding="utf-8")
    tickers, flagged = load_tickers(path)
    assert tickers == {"BBCA", "BELI", "GOTO"}
    assert flagged == {"BELI"}


def test_load_tickers_requires_column(tmp_path):
    path = tmp_path / "bad.csv"
    path.write_text("nama\nBBCA\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_tickers(path)
