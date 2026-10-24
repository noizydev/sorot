import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from masking import mask_pii  # noqa: E402


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Admin hanya ini 085737186163", "Admin hanya ini [PHONE]"),
        ("dengan nohp 087888897847 yang lain", "dengan nohp [PHONE] yang lain"),
        ("WA: 0812-3456-7890.", "WA: [PHONE]."),
        ("hubungi 0813 1234 5678 ya", "hubungi [PHONE] ya"),
        ("No. WA +62831-1918-1386", "No. WA [PHONE]"),
        ("WA: (+62) 8585-1500-688", "WA: [PHONE]"),
        ("call +6281234567890 now", "call [PHONE] now"),
        ("telp 6281234567890", "telp [PHONE]"),
        ("kantor (021) 555-1234", "kantor [PHONE]"),
        ("kantor 0274 123456", "kantor [PHONE]"),
        ("chat http://wa.me/+6281234567890 sekarang", "chat [PHONE] sekarang"),
        ("https://api.whatsapp.com/send?phone=6281234567890", "[PHONE]"),
        ("email: admin@bigalpha.id", "email: [EMAIL]"),
        ("helpdesk@pilarmas-investindo.co.id untuk info", "[EMAIL] untuk info"),
    ],
)
def test_masks_contacts(raw, expected):
    assert mask_pii(raw) == expected


@pytest.mark.parametrize(
    "text",
    [
        "IHSG ditutup menguat +51,35 poin atau +0,95% ke level 5.439.",
        "cek n ricek vktr range target 372 380 388 SL < 346",
        "Support 4890, 4846. Resisten 6289 , 5453",
        "Volume : 41.99B Value :15,187.14B Freq. : 1,768,200",
        "TP 6280 6300 6350 SL 6200",
        "BBCA = 216.6M BRPT = 28M",
        "Disclaimer on Owner @rekomsahamritel",
        "SAPA MENTARI - 23 Oktober 2026, tanggal 2026-10-23",
        "Laba Rp 45,5 T, naik 20,7% YoY",
        "Call: 1500 688",
        "kode emiten 0812? tidak ada",
    ],
)
def test_keeps_market_numbers(text):
    assert mask_pii(text) == text


def test_none_and_empty():
    assert mask_pii(None) is None
    assert mask_pii("") == ""
