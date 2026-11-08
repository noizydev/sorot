"""Tes modul kepemilikan: parser baris KSEI, penyimpanan bitemporal, entity resolution,
kepemilikan terintegrasi + free float efektif, deteksi ambang 1%, label risiko, transparency log."""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis import entities as ent  # noqa: E402
from analysis import freefloat, holdings, ksei, risk, threshold  # noqa: E402
from analysis.config import load_config  # noqa: E402
from analysis.translog import (  # noqa: E402
    TransparencyLog,
    canonical,
    leaf_hash,
    root,
    verify_inclusion,
)

CLASS = {"I": "Individual", "C": "Corporate", "B": "Bank"}


def rows(valid_date, data):
    """data: list (ticker, issuer, investor, kelas, saham, pct)."""
    return pd.DataFrame([{
        "valid_date": valid_date, "ticker": t, "issuer_name": iss, "investor_name": inv,
        "investor_class": CLASS[k], "local_foreign": "L", "nationality": "", "domicile": "INDONESIA",
        "scripless": sh, "scrip": 0, "total_shares": sh, "pct": pct} for t, iss, inv, k, sh, pct in data])


def meta(name, valid_date, published):
    return {"file": name, "sha256": name * 4, "valid_date": valid_date, "published_at": published}


# ---------- parser ----------
def test_normalize_row():
    row = ["30-Apr-2026", "AGII", "SAMATOR INDO GAS Tbk", "PT SAMATOR", "Corporate", "L", "", "INDONESIA",
           "709.549.020", "371.144.000", "1.080.693.020", "35,24"]
    r = ksei.normalize_row(row)
    assert r["valid_date"] == "2026-04-30" and r["ticker"] == "AGII"
    assert r["total_shares"] == 1_080_693_020 and r["scrip"] == 371_144_000 and r["pct"] == 35.24
    assert ksei.normalize_row(["DATE", "SHARE_CODE"] + [""] * 10) is None
    assert ksei.normalize_row(["#DIV/0!"] + [""] * 11) is None
    assert ksei.parse_pct("45") == 45.0 and ksei.parse_pct("x") is None


# ---------- bitemporal ----------
def test_bitemporal_corrections(tmp_path):
    conn = holdings.connect(tmp_path / "o.db")
    v1 = rows("2026-03-31", [("AAAA", "AAAA Tbk", "BUDI", "I", 150, 1.5)])
    v2 = rows("2026-03-31", [("AAAA", "AAAA Tbk", "BUDI", "I", 120, 1.2), ("AAAA", "AAAA Tbk", "PT X", "C", 300, 3.0)])
    holdings.add_document(conn, meta("v1", "2026-03-31", "2026-04-02T15:20:00"), v1)
    holdings.add_document(conn, meta("v2", "2026-03-31", "2026-04-10T01:28:00"), v2)
    now = holdings.snapshot(conn, "2026-03-31")
    assert sorted(now["investor_name"]) == ["BUDI", "PT X"] and now.loc[now.investor_name == "BUDI", "pct"].iat[0] == 1.2
    before = holdings.snapshot(conn, "2026-03-31", known_at="2026-04-05T00:00:00")
    assert list(before["investor_name"]) == ["BUDI"] and before["pct"].iat[0] == 1.5
    assert holdings.snapshot(conn, "2026-03-31", known_at="2026-04-01T00:00:00").empty  # belum terbit
    old = conn.execute("SELECT tx_to FROM holdings WHERE doc_id = 1").fetchone()[0]
    assert old == "2026-04-10T01:28:00"
    diff = holdings.diff_versions(conn, "2026-03-31")
    assert set(diff["perubahan"]) == {"diubah", "ditambah"}
    import sqlite3
    plain = sqlite3.connect(tmp_path / "o.db")  # dashboard memakai koneksi tanpa row_factory
    assert len(holdings.diff_versions(plain, "2026-03-31")) == len(diff)
    plain.close()


# ---------- entity resolution ----------
def test_entity_resolution_and_issuer_link():
    h = rows("2026-04-30", [
        ("AAAA", "AAAA Tbk", "PT SAMATOR", "C", 1, 2), ("BBBB", "BBBB Tbk", "SAMATOR, PT", "C", 1, 2),
        ("CCCC", "CCCC Tbk", "PT. SAMATOR", "C", 1, 2),
        ("AAAA", "AAAA Tbk", "REKSA DANA ALPHA 1", "C", 1, 2), ("AAAA", "AAAA Tbk", "REKSA DANA ALPHA 2", "C", 1, 2),
        ("AAAA", "AAAA Tbk", "IR. T. PERMADI RACHMAT", "I", 1, 2), ("BBBB", "BBBB Tbk", "T PERMADI RACHMAT", "I", 1, 2),
        ("AAAA", "AAAA Tbk", "BUDI SANTOSO", "I", 1, 2), ("BBBB", "BBBB Tbk", "BUDI SANTOSA", "I", 1, 2),
        ("AAAA", "AAAA Tbk", "SARATOGA INVESTAMA SEDAYA TBK PT", "C", 1, 2),
        ("AAAA", "AAAA Tbk", "DBS BANK LTD S/A CLIENTS", "B", 1, 2),
    ])
    names, ents = ent.resolve(h, {"SRTG": "SARATOGA INVESTAMA SEDAYA Tbk", "AAAA": "AAAA Tbk"})
    eid = dict(zip(names["investor_name"], names["entity_id"]))
    assert eid["PT SAMATOR"] == eid["SAMATOR, PT"] == eid["PT. SAMATOR"]
    assert eid["REKSA DANA ALPHA 1"] != eid["REKSA DANA ALPHA 2"]
    assert eid["IR. T. PERMADI RACHMAT"] == eid["T PERMADI RACHMAT"]
    assert eid["BUDI SANTOSO"] != eid["BUDI SANTOSA"]  # individu: ambang ketat, beda satu huruf tidak digabung
    e = ents.set_index("entity_id")
    assert e.at[eid["SARATOGA INVESTAMA SEDAYA TBK PT"], "ticker"] == "SRTG"
    assert bool(e.at[eid["DBS BANK LTD S/A CLIENTS"], "nominee"])


def test_aliases_force_merge():
    h = rows("2026-04-30", [("AAAA", "AAAA Tbk", "PT ALFA PRIMA", "C", 1, 2), ("AAAA", "AAAA Tbk", "PT OMEGA JAYA", "C", 1, 2)])
    names, _ = ent.resolve(h, {}, aliases=[("PT OMEGA JAYA", "PT ALFA PRIMA")])
    assert names["entity_id"].nunique() == 1


# ---------- kepemilikan terintegrasi & free float ----------
def test_integrated_ownership_matrix():
    # X memegang 50% Y, Y memegang 40% Z -> X terintegrasi di Z = 20%
    w_cc = np.array([[0, 0.5, 0], [0, 0, 0.4], [0, 0, 0]])
    w_uc = np.array([[0.6, 0, 0.1]])  # A: 60% X, 10% Z langsung
    t_cc, t_uc = freefloat.integrated(w_cc, w_uc)
    assert t_cc[0, 2] == pytest.approx(0.2)
    assert t_uc[0, 2] == pytest.approx(0.1 + 0.6 * 0.5 * 0.4)


def _ff_fixture():
    h = rows("2026-04-30", [
        # XXXX dikendalikan PT INDUK 60%
        ("XXXX", "XXXX MAKMUR Tbk", "PT INDUK", "C", 600, 60.0),
        # YYYY: tampak tersebar (tidak ada >=5%), tetapi PT INDUK 3% + XXXX (anak PT INDUK) 3% = 6%
        ("YYYY", "YYYY Tbk", "PT INDUK", "C", 30, 3.0),
        ("YYYY", "YYYY Tbk", "PT XXXX MAKMUR TBK", "C", 30, 3.0),
        ("YYYY", "YYYY Tbk", "BUDI", "I", 20, 2.0),
        ("YYYY", "YYYY Tbk", "CITIBANK S/A CLIENTS", "B", 40, 4.0),
    ])
    names, ents = ent.resolve(h, ent.issuers_from_holdings(h))
    return h, names, ents


def test_effective_free_float_detects_split_group():
    h, names, ents = _ff_fixture()
    summary, groups, owners = freefloat.compute(h, names, ents, control_min=0.25, concentrated_pct=5.0)
    y = summary.set_index("ticker").loc["YYYY"]
    assert y["ff_proxy"] == pytest.approx(100.0)
    assert y["ff_effective"] == pytest.approx(94.0)
    g = groups[(groups.ticker == "YYYY") & groups.split]
    assert len(g) == 1 and g["agg_pct"].iat[0] == pytest.approx(6.0) and "PT INDUK" in g["reason"].iat[0]
    o = owners[owners.ticker == "YYYY"].set_index("owner")
    assert o.at["PT INDUK", "integrated_pct"] == pytest.approx(3.0 + 0.6 * 3.0)  # langsung + lewat XXXX
    assert "CITIBANK S/A CLIENTS" not in o.index  # nominee bukan pemilik akhir
    assert summary.set_index("ticker").at["XXXX", "controller"] == "PT INDUK"


# ---------- ambang 1% ----------
def _snaps():
    base = [("AAAA", "AAAA Tbk", "PT BESAR", "C", 6000, 60.0)]
    s0 = rows("2026-02-27", base + [("AAAA", "AAAA Tbk", "PT KECIL", "C", 120, 1.2), ("AAAA", "AAAA Tbk", "PT LAIN", "C", 115, 1.15),
                                    ("BBBB", "BBBB Tbk", "PT BESAR", "C", 5000, 50.0), ("BBBB", "BBBB Tbk", "DEWI", "I", 110, 1.1)])
    s1 = rows("2026-03-31", base + [("AAAA", "AAAA Tbk", "PT BARU", "C", 105, 1.05),
                                    # BBBB right issue: saham beredar naik 40% -> DEWI tinggal 0,79%
                                    ("BBBB", "BBBB Tbk", "PT BESAR", "C", 7000, 50.0)])
    s2 = rows("2026-04-30", base + [("AAAA", "AAAA Tbk", "PT KECIL", "C", 120, 1.2),
                                    ("BBBB", "BBBB Tbk", "PT BESAR", "C", 7000, 50.0)])
    allr = pd.concat([s0, s1, s2])
    names, ents = ent.resolve(allr, {})
    e = ents.set_index("entity_id")

    def attach(s):
        s = s.merge(names[["investor_name", "entity_id"]], on="investor_name")
        s["canonical_name"] = s["entity_id"].map(e["canonical_name"])
        s["kind"] = s["entity_id"].map(e["kind"])
        s["nominee"] = s["entity_id"].map(e["nominee"])
        return s

    return {d: attach(s) for d, s in [("2026-02-27", s0), ("2026-03-31", s1), ("2026-04-30", s2)]}


def test_threshold_detection_patterns_and_dilution():
    ev = threshold.detect(_snaps(), 1.0, 0.5, 0.02)
    by = {(r.ticker, r.name, r.t0): r for r in ev.itertuples()}
    kecil = by[("AAAA", "PT KECIL", "2026-02-27")]
    assert kecil.status == "tepat_di_bawah_ambang"
    assert set(kecil.patterns.split(",")) == {"muncul_kembali", "serentak", "pemegang_baru_dekat_ambang"}
    assert kecil.score == 4 and "muncul lagi" in kecil.reason
    dewi = by[("BBBB", "DEWI", "2026-02-27")]
    assert dewi.status == "terjelaskan_dilusi" and dewi.score == 0 and "dilusi" in dewi.reason


# ---------- label risiko ----------
def test_risk_reasons_and_label():
    cfg = load_config()
    ff = pd.Series({"ff_effective": 5.0, "ff_proxy": 40.0, "issuer_name": "AAAA Tbk"})
    split = pd.DataFrame({"reason": ["PT INDUK menguasai 35% lewat 4 nama"]})
    ev = threshold.detect(_snaps(), 1.0, 0.5, 0.02)
    ev = ev[(ev.ticker == "AAAA") & (ev.status == "tepat_di_bawah_ambang")]
    promo = {"bursts3": [("2026-05-12T23:00:00+00:00", 4)], "promo": [("2026-05-12T23:00:00+00:00", 3)],
             "spikes": [("2026-05-12T23:00:00+00:00", 2.9, "2026-05-18")], "dup3": 1}
    score, label, reasons = risk.score_ticker("AAAA", ff, split, ev, promo, cfg)
    codes = [r["code"] for r in reasons]
    assert codes == ["ff_below_min", "ff_gap", "threshold_event", "threshold_pattern", "burst_cross", "burst_promo",
                     "volume_spike", "dup_cross"]
    assert score == 100 and label == "Tinggi"
    assert all(r["text"] for r in reasons)
    score0, label0, reasons0 = risk.score_ticker("ZZZZ", pd.Series({"ff_effective": 60.0, "ff_proxy": 60.0}),
                                                 pd.DataFrame({"reason": []}), pd.DataFrame(), None, cfg)
    assert (score0, label0, reasons0[0]["code"]) == (0, "Rendah", "none")


# ---------- transparency log ----------
def test_transparency_log_detects_tampering(tmp_path):
    tlog = TransparencyLog(tmp_path / "log.jsonl", tmp_path / "heads.jsonl")
    h1 = tlog.append([{"ticker": "AAAA", "score": 10}, {"ticker": "BBBB", "score": 60}])
    h2 = tlog.append([{"ticker": "AAAA", "score": 15}])
    assert (h1["size"], h2["size"]) == (2, 3) and tlog.audit() == (True, [])
    leaves = tlog.leaves()
    proof = [bytes.fromhex(x) for x in tlog.proof(1)]
    assert verify_inclusion(leaves[1], 1, 3, proof, bytes.fromhex(h2["root"]))
    assert root(leaves[:2]).hex() == h1["root"]

    lines = (tmp_path / "log.jsonl").read_text(encoding="utf-8").splitlines()
    rec = json.loads(lines[1])
    rec["entry"]["score"] = 5  # ubah diam-diam
    rec["leaf_hash"] = leaf_hash(canonical(rec["entry"])).hex()  # bahkan bila leaf hash ikut dipalsukan
    lines[1] = json.dumps(rec)
    (tmp_path / "log.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    ok, problems = tlog.audit()
    assert not ok and any("tidak cocok" in p for p in problems)


def test_split_overlapping_names():
    tail = ["Corporate", "L", "", "INDONESIA", "1.000", "0", "1.000", "1,00"]
    rows = [["29-May-2026", "CARS", "INDUSTRI DAN PERDAGANGAN BINTRACO DHARMA TbPkT. BALI BINTANG", ""] + tail,
            ["29-May-2026", "CARS", "INDUSTRI DAN PERDAGANGAN BINTRACO DHARMA TbYkASA MULTI", ""] + tail,
            ["29-May-2026", "ULTJ", "ULTRAJAYA MILK TPbRkAWIRA PT", ""] + tail,
            ["29-May-2026", "ULTJ", "ULTRAJAYA MILK TSbAkMUDERA", ""] + tail]
    out = ksei.split_overlapping_names([list(r) for r in rows])
    assert [(r[2], r[3]) for r in out] == [
        ("INDUSTRI DAN PERDAGANGAN BINTRACO DHARMA Tbk", "PT. BALI BINTANG"),
        ("INDUSTRI DAN PERDAGANGAN BINTRACO DHARMA Tbk", "YASA MULTI"),
        ("ULTRAJAYA MILK Tbk", "PRAWIRA PT"), ("ULTRAJAYA MILK Tbk", "SAMUDERA")]


@pytest.mark.parametrize("issuer, investor, expected", [
    ("ASURANSI MAXIMUS GRAHA PERSADA T", "bPkT DIAN GRAHA CIPTA", ("ASURANSI MAXIMUS GRAHA PERSADA Tbk", "PT DIAN GRAHA CIPTA")),
    ("ARGHA KARYA PRIMA INDUSTRY Tb", "kMAYBANK SECURITIES PTE. LTD.", ("ARGHA KARYA PRIMA INDUSTRY Tbk", "MAYBANK SECURITIES PTE. LTD.")),
    ("ASTRINDO NUSANTARA INFRASTRUKTUR", "BTabskkara Timur Kencana, PT", ("ASTRINDO NUSANTARA INFRASTRUKTUR Tbk", "Baskara Timur Kencana, PT")),
    ("BANK RAKYAT INDONESIA (PERSERO) Tbk", "kDANA PENSIUN", ("BANK RAKYAT INDONESIA (PERSERO) Tbk", "kDANA PENSIUN")),
    ("BANK SANGAT PANJANG SEKALI", "TOTO BUDIMAN", ("BANK SANGAT PANJANG SEKALI", "TOTO BUDIMAN")),  # tidak tercemar
])
def test_fix_tbk_overflow(issuer, investor, expected):
    assert ksei.fix_tbk_overflow(issuer, investor) == expected


def test_continuity_merges_truncated_names_and_blank_class():
    h = pd.concat([
        rows("2026-04-30", [("ANDI", "ANDI Tbk", "REKSA DANA SUCORINVEST SHARIA EQUITY FUND", "C", 1_490_000, 1.49),
                            ("ANDI", "ANDI Tbk", "DONAL MANIK", "I", 1_110_000, 1.11)]),
        rows("2026-05-29", [("ANDI", "ANDI Tbk", "REKSA DANA SUCORINVEST SHARIA EQUI", "C", 1_490_000, 1.49),
                            ("ANDI", "ANDI Tbk", "DONAL MANIK", "I", 1_110_000, 1.11)]).assign(investor_class=["Mutual Funds", ""],
                                                                                              nationality=["", "INDONESIAN"]),
    ], ignore_index=True)
    names, ents = ent.resolve(h, {})
    eid = dict(zip(names["investor_name"], names["entity_id"]))
    assert eid["REKSA DANA SUCORINVEST SHARIA EQUITY FUND"] == eid["REKSA DANA SUCORINVEST SHARIA EQUI"]
    assert ents.set_index("entity_id").at[eid["DONAL MANIK"], "kind"] == "individu"


def test_fix_issuer_overflow_reconstructs_interleaved_names():
    canon = "INDUSTRI DAN PERDAGANGAN BINTRACO DHARMA Tbk"
    assert ksei.fix_issuer_overflow("INDUSTRI DAN PERDAGANGAN BINTRAC", "OY ADSHAA MRMUALT TI bINkVESTAMA", canon) == \
        (canon, "YASA MULTI INVESTAMA")
    # sel emiten lengkap atau investor tidak tercemar: tidak diubah
    assert ksei.fix_issuer_overflow(canon, "PT X", canon) == (canon, "PT X")
    assert ksei.fix_issuer_overflow("INDUSTRI DAN PERDAGANGAN BINTRAC", "BUDI", canon) == ("INDUSTRI DAN PERDAGANGAN BINTRAC", "BUDI")


def test_custodian_account_linked_to_beneficial_owner():
    h = rows("2026-04-30", [("JAWA", "JAYA AGRA WATTIE Tbk", "SARANA AGRO INVESTAMA, PT", "C", 900, 90.0),
                            ("JAWA", "JAYA AGRA WATTIE Tbk", "UOB KAY HIAN PTE LTD A/C UNITED OVERSEAS BANK A/C PT SARANA AGRO INVESTAMA", "C", 25, 2.5),
                            ("JAWA", "JAYA AGRA WATTIE Tbk", "DBS BANK LTD SG-PB CLIENTS", "C", 30, 3.0)])
    names, ents = ent.resolve(h, {})
    eid = dict(zip(names["investor_name"], names["entity_id"]))
    sarana = eid["SARANA AGRO INVESTAMA, PT"]
    assert eid["UOB KAY HIAN PTE LTD A/C UNITED OVERSEAS BANK A/C PT SARANA AGRO INVESTAMA"] == sarana
    e = ents.set_index("entity_id")
    assert e.at[sarana, "canonical_name"] == "SARANA AGRO INVESTAMA, PT" and not e.at[sarana, "nominee"]
    assert bool(e.at[eid["DBS BANK LTD SG-PB CLIENTS"], "nominee"])


def test_whole_stake_transfer_is_not_threshold_avoidance():
    s0 = rows("2026-03-31", [("DEFI", "DEFI Tbk", "PT KRESNA PRIMA INVEST", "C", 138, 1.38), ("DEFI", "DEFI Tbk", "PT BESAR", "C", 6000, 60.0)])
    s1 = rows("2026-04-30", [("DEFI", "DEFI Tbk", "PT KARUNIA PERDANA ISTENIA", "C", 138, 1.38), ("DEFI", "DEFI Tbk", "PT BESAR", "C", 6000, 60.0)])
    allr = pd.concat([s0, s1], ignore_index=True)
    names, ents = ent.resolve(allr, {})
    e = ents.set_index("entity_id")

    def attach(s):
        s = s.merge(names[["investor_name", "entity_id"]], on="investor_name")
        return s.assign(canonical_name=s["entity_id"].map(e["canonical_name"]), kind=s["entity_id"].map(e["kind"]),
                        nominee=s["entity_id"].map(e["nominee"]))

    ev = threshold.detect({"2026-03-31": attach(s0), "2026-04-30": attach(s1)})
    r = ev.iloc[0]
    assert r["status"] == "pindah_utuh" and r["score"] == 0 and "PT KARUNIA PERDANA ISTENIA" in r["reason"]


def test_series_funds_kept_apart_and_truncated_corporate_merged():
    h = rows("2026-04-30", [("MABA", "MABA Tbk", "REKSA DANA NARADA SAHAM INDONESIA", "C", 489, 4.89),
                            ("MABA", "MABA Tbk", "REKSA DANA NARADA SAHAM INDONESIA II", "C", 416, 4.16),
                            ("AMOR", "AMOR Tbk", "PT ASHMORE ASSET MANAGEMENT INDONESIA TBK", "C", 107, 1.07),
                            ("BBBB", "BBBB Tbk", "PTA SHMORE ASSET MANAGEMENT INDO", "C", 123, 1.23)])
    h.loc[3, "valid_date"] = "2026-03-31"
    names, _ = ent.resolve(h, {})
    eid = dict(zip(names["investor_name"], names["entity_id"]))
    assert eid["REKSA DANA NARADA SAHAM INDONESIA"] != eid["REKSA DANA NARADA SAHAM INDONESIA II"]
    assert eid["PT ASHMORE ASSET MANAGEMENT INDONESIA TBK"] == eid["PTA SHMORE ASSET MANAGEMENT INDO"]


def test_foreign_custodian_banks_are_omnibus():
    h = rows("2026-04-30", [("CUAN", "CUAN Tbk", "BANK OF SINGAPORE LIMITED", "B", 104, 1.04)]).assign(
        investor_class="Private Bank", local_foreign="F")
    _, ents = ent.resolve(h, {})
    assert bool(ents["nominee"].iat[0])


def test_strategic_foreign_bank_is_not_omnibus():
    h = rows("2026-04-30", [("AGRS", "BANK IBK INDONESIA Tbk", "INDUSTRIAL BANK OF KOREA", "B", 9149, 91.49),
                            ("CUAN", "CUAN Tbk", "DBS BANK LTD.", "B", 145, 1.45)]).assign(investor_class=["Private Bank", "IB"],
                                                                                             local_foreign="F")
    _, ents = ent.resolve(h, {})
    nom = dict(zip(ents["canonical_name"], ents["nominee"]))
    assert not nom["INDUSTRIAL BANK OF KOREA"] and nom["DBS BANK LTD."]


def test_generic_truncated_name_does_not_chain_entities():
    h = pd.concat([
        rows("2026-03-31", [("BCIP", "BCIP Tbk", "PERUSAHAAN PERSEROAN (PERSERO) PT. ASABRI", "C", 126, 1.26),
                            ("BBRI", "BBRI Tbk", "PERUSAHAAN PERSEROAN (PERSERO) PT DANANTARA ASSET MANAGEMENT", "C", 5266, 52.66)]),
        rows("2026-04-30", [("BCIP", "BCIP Tbk", "PERUSAHAAN PERSEROAN (PERSERO) PT.", "C", 126, 1.26),
                            ("BBRI", "BBRI Tbk", "PERUSAHAAN PERSEROAN (PERSERO) PT.", "C", 5266, 52.66)]),
    ], ignore_index=True)
    names, _ = ent.resolve(h, {})
    eid = dict(zip(names["investor_name"], names["entity_id"]))
    assert eid["PERUSAHAAN PERSEROAN (PERSERO) PT. ASABRI"] != eid["PERUSAHAAN PERSEROAN (PERSERO) PT DANANTARA ASSET MANAGEMENT"]


def test_overmerge_guards_funds_custodians_parent_subsidiary():
    h = rows("2026-04-30", [
        ("AAAA", "AAAA Tbk", "FIDELITY INVESTMENT TRUST: FIDELITY SERIES EMERGING MARKETS OPPORTUNITIES FUND", "C", 1, 1.1),
        ("AAAA", "AAAA Tbk", "FIDELITY INVESTMENT TRUST: FIDELITY SERIES INTERNATIONAL SMALL CAP FUND", "C", 2, 1.2),
        ("BBBB", "BBBB Tbk", "NOMURA SINGAPORE LIMITED", "C", 3, 1.3),
        ("BBBB", "BBBB Tbk", "NOMURA SINGAPORE LIMITED S/A NIPPON STEEL CORPORATION", "C", 4, 1.4),
        ("BBBB", "BBBB Tbk", "NIPPON STEEL CORPORATION", "C", 5, 6.0),
        ("CCCC", "CCCC Tbk", "HITACHI CONSTRUCTION MACHINERY CO.,LTD", "C", 6, 1.6),
        ("CCCC", "CCCC Tbk", "Hitachi Construction Machinery Asia And Pasific Pte Ltd", "C", 7, 1.7),
    ])
    names, _ = ent.resolve(h, {})
    eid = dict(zip(names["investor_name"], names["entity_id"]))
    assert eid["FIDELITY INVESTMENT TRUST: FIDELITY SERIES EMERGING MARKETS OPPORTUNITIES FUND"] != \
        eid["FIDELITY INVESTMENT TRUST: FIDELITY SERIES INTERNATIONAL SMALL CAP FUND"]
    assert eid["NOMURA SINGAPORE LIMITED S/A NIPPON STEEL CORPORATION"] == eid["NIPPON STEEL CORPORATION"]
    assert eid["NOMURA SINGAPORE LIMITED"] != eid["NIPPON STEEL CORPORATION"]
    assert eid["HITACHI CONSTRUCTION MACHINERY CO.,LTD"] != eid["Hitachi Construction Machinery Asia And Pasific Pte Ltd"]
