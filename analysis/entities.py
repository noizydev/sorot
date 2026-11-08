"""Entity resolution nama pemegang saham.

Nama yang sama ditulis berbeda antar emiten/bulan ("PT SAMATOR", "SAMATOR, PT", "PT. Samator Tbk").
Langkah: normalisasi -> cocok persis -> cocok kabur (RapidFuzz) dalam blok -> alias manual.
Entitas korporat lalu dihubungkan ke emiten tercatat bila namanya sama dengan nama emiten, sehingga
rantai kepemilikan antar perusahaan tercatat bisa dihitung.
"""
import csv
import hashlib
import re
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path

import pandas as pd
from rapidfuzz import fuzz

LEGAL = {"PT", "TBK", "PERSERO", "LTD", "LIMITED", "PTE", "INC", "CORP", "CORPORATION", "CO", "LLC", "PLC",
         "SA", "AG", "NV", "BV", "GMBH", "BHD", "SDN", "THE", "TERBUKA", "PERUSAHAAN", "PERSEROAN"}
TITLES = {"IR", "DR", "DRS", "DRA", "H", "HJ", "HAJI", "HAJJAH", "PROF", "SH", "SE", "MM", "MBA", "ST", "SKOM",
          "SIP", "MSC", "PHD", "BSC", "MSI", "SPD", "AK", "CPA", "MR", "MRS", "MS", "TN", "NY", "SDRI", "BPK", "IBU"}
BENEFICIARY_RE = re.compile(r"\b(?:A/C|S/A|QQ|FOR THE ACCOUNT OF|ON BEHALF OF)\s+(?!CLIENTS?\b)([^/]+?)\s*$", re.I)
NOMINEE_RE = re.compile(r"\b(S/A|A/C|CLIENTS?|NOMINEES?|OMNIBUS|CUSTOD(Y|IAN)|QQ|REGISTRAR|ON BEHALF|"
                        r"FOR THE ACCOUNT|SEGREGATED|RE[- ]?CLIENTS)\b", re.I)


def normalize(name, individual=False):
    s = (name or "").upper().replace("&", " DAN ")
    s = re.sub(r"\(PERSERO\)", " ", s)
    s = re.sub(r"[^A-Z0-9 ]", " ", s)
    drop = LEGAL | (TITLES if individual else set())
    tokens = [t for t in s.split() if t not in drop]
    return " ".join(tokens)


FUND_TOKENS = {"FUND", "FUNDS", "TRUST", "REKSA", "PORTFOLIO", "ETF", "SERIES", "INDEX", "SICAV", "UCITS"}
LEGAL_END = re.compile(r"\b(LTD|LIMITED|TBK|PT|INC|CORP|CORPORATION|CO|LLC|PLC|AG|SA|NV|BV|GMBH|BHD|PERSERO)\.?\)?$", re.I)


def is_fund(norm):
    return bool(FUND_TOKENS & set(norm.split()))


def looks_truncated(raw):
    """Nama yang terpotong lebar kolom biasanya panjang dan tidak diakhiri bentuk badan hukum."""
    raw = " ".join((raw or "").split())
    return len(raw) >= 25 and not LEGAL_END.search(raw.rstrip(" .,"))


ROMAN = {"I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII"}
# kelas KSEI untuk bank/kustodian/sekuritas (kodenya berbeda antarbulan: "IB", "CP", "GIB", ...)
OMNIBUS_CLASSES = {"private bank", "custodian bank", "trustee bank", "securities company", "bank", "ib", "cp", "gib"}
OMNIBUS_MAX_PCT = 5.0  # pemilik strategis (mis. bank induk asing 91%) tidak dianggap akun nasabah


def distinct_series(a, b):
    """'REKSA DANA X' vs 'REKSA DANA X II' / '... 2': seri berbeda, jangan digabung."""
    ta, tb = set(a.split()), set(b.split())
    diff = ta ^ tb
    return any(t.isdigit() or t in ROMAN for t in diff) or         (any(ch.isdigit() for ch in a + b) and re.sub(r"\D", "", a) != re.sub(r"\D", "", b))


def is_omnibus(investor_class, local_foreign, max_pct=0.0):
    """Bank/kustodian/sekuritas asing dengan porsi kecil umumnya memegang atas nama banyak nasabah (omnibus)."""
    return ((local_foreign or "").strip().upper() == "F" and (investor_class or "").strip().lower() in OMNIBUS_CLASSES
            and max_pct < OMNIBUS_MAX_PCT)


def is_nominee(name):
    return bool(NOMINEE_RE.search(name or ""))


def beneficiary(name):
    """'UOB KAY HIAN PTE LTD A/C UNITED OVERSEAS BANK A/C PT SARANA AGRO' -> 'PT SARANA AGRO' (pemilik manfaat)."""
    m = BENEFICIARY_RE.search(name or "")
    return m.group(1).strip() if m else None


def is_individual(investor_class, nationality=""):
    """Klasifikasi KSEI kadang kosong; kewarganegaraan hanya diisi untuk investor individu."""
    c = (investor_class or "").strip().lower()
    if c:
        return c == "individual"
    return bool((nationality or "").strip())


class _UF:
    def __init__(self):
        self.p = {}

    def find(self, x):
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[max(ra, rb)] = min(ra, rb)


def load_aliases(path):
    """CSV alias,canonical -> pasangan nama yang dipaksa satu entitas."""
    path = Path(path) if path else None
    if not path or not path.exists():
        return []
    with open(path, encoding="utf-8-sig", newline="") as f:
        return [(r["alias"], r["canonical"]) for r in csv.DictReader(f) if r.get("alias") and r.get("canonical")]


def resolve(holdings, issuers, corp_threshold=93, indiv_threshold=97, issuer_threshold=95, aliases=()):
    """holdings: DataFrame dengan investor_name, investor_class. issuers: dict ticker -> nama emiten.

    Kembalikan (names, entities):
      names    : investor_name, kind, norm, entity_id
      entities : entity_id, canonical_name, kind, nominee, n_names, names (contoh), ticker (emiten yang sama)
    """
    h = holdings.assign(nationality=holdings["nationality"] if "nationality" in holdings else "")
    # satu nama bisa muncul dengan kelas kosong di satu bulan: pakai kelas yang terisi bila ada
    h = h.assign(_filled=h["investor_class"].fillna("").str.strip() != "").sort_values("_filled", ascending=False)
    h = h.assign(local_foreign=h["local_foreign"] if "local_foreign" in h else "")
    df = h[["investor_name", "investor_class", "nationality", "local_foreign"]].drop_duplicates("investor_name").reset_index(drop=True)
    max_pct = h.groupby("investor_name")["pct"].max() if "pct" in h else {}
    omnibus = {n for n, c, lf in zip(df["investor_name"], df["investor_class"], df["local_foreign"])
               if is_omnibus(c, lf, float(max_pct.get(n, 0)))}
    df["individual"] = [is_individual(c, n) for c, n in zip(df["investor_class"], df["nationality"])]
    df["kind"] = df["individual"].map({True: "individu", False: "korporat"})
    df["norm"] = [normalize(n, ind) for n, ind in zip(df["investor_name"], df["individual"])]
    df.loc[df["norm"] == "", "norm"] = df["investor_name"].str.upper()

    uf = _UF()
    key = lambda kind, norm: f"{kind}|{norm}"  # noqa: E731
    for k, n in zip(df["kind"], df["norm"]):
        uf.find(key(k, n))

    # cocok kabur: blok per jenis + token pertama, supaya perbandingan tetap kecil
    blocks = defaultdict(list)
    for k, n in set(zip(df["kind"], df["norm"])):
        blocks[(k, n.split()[0] if n else "")].append(n)
    nominee_norms = {n for raw, n in zip(df["investor_name"], df["norm"]) if is_nominee(raw)}
    for (kind, _), names in blocks.items():
        threshold = indiv_threshold if kind == "individu" else corp_threshold
        names.sort()
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                if a in nominee_norms or b in nominee_norms:
                    continue  # akun nominee hanya dihubungkan lewat pemilik manfaatnya
                if (is_fund(a) or is_fund(b)) and fuzz.token_sort_ratio(a, b) < 98:
                    continue  # dana berbeda dari manajer yang sama sering hanya beda satu-dua kata
                if abs(len(a) - len(b)) > max(4, 0.25 * max(len(a), len(b))):
                    continue
                if distinct_series(a, b):
                    continue  # "REKSA DANA X" vs "REKSA DANA X II" / "X 2" adalah entitas berbeda
                if fuzz.token_sort_ratio(a, b) >= threshold:
                    uf.union(key(kind, a), key(kind, b))

    by_raw = dict(zip(df["investor_name"], zip(df["kind"], df["norm"])))
    despaced_unions(df, uf, key, corp_threshold)
    continuity_unions(holdings, by_raw, uf, key)
    # akun nominee/kustodian atas nama pemilik manfaat yang juga tercatat langsung -> entitas yang sama
    norm_to_key = {n: key(k, n) for k, n in zip(df["kind"], df["norm"])}
    linked = set()
    for raw, (kind, norm) in by_raw.items():
        b = beneficiary(raw)
        if b:
            for ind in (False, True):
                bn = normalize(b, individual=ind)
                if bn in norm_to_key and norm_to_key[bn] != key(kind, norm):
                    uf.union(key(kind, norm), norm_to_key[bn])
                    linked.add(raw)
                    break
    for alias, canonical in aliases:
        if alias in by_raw and canonical in by_raw:
            uf.union(key(*by_raw[alias]), key(*by_raw[canonical]))

    df["root"] = [uf.find(key(k, n)) for k, n in zip(df["kind"], df["norm"])]
    df["entity_id"] = df["root"].map(lambda r: "E" + hashlib.sha1(r.encode()).hexdigest()[:10])

    # entitas korporat -> emiten tercatat dengan nama yang sama
    issuer_norm = {normalize(n): t for t, n in issuers.items()}
    counts = Counter(holdings["investor_name"])
    ents = []
    for eid, g in df.groupby("entity_id"):
        raw = sorted(g["investor_name"], key=lambda n: (-counts[n], n))
        kind = g["kind"].iat[0]
        ticker = None
        if kind == "korporat":
            for n in g["norm"]:
                if n in issuer_norm:
                    ticker = issuer_norm[n]
                    break
            if ticker is None:
                best = max(((fuzz.token_sort_ratio(n, inorm), t) for n in g["norm"] for inorm, t in issuer_norm.items()
                            if inorm[:1] == n[:1]), default=(0, None))
                if best[0] >= issuer_threshold:
                    ticker = best[1]
        # entitas dianggap nominee (publik) hanya bila semua namanya akun nominee tanpa pemilik manfaat yang dikenal
        direct = [n for n in raw if not is_nominee(n) and n not in omnibus]
        canonical = direct[0] if direct else raw[0]
        kind = next((k for k, n in zip(g["kind"], g["investor_name"]) if n == canonical), kind)
        ents.append({"entity_id": eid, "canonical_name": canonical, "kind": kind,
                     "nominee": not direct and not any(n in linked for n in raw), "n_names": len(raw),
                     "names": " | ".join(raw[:6]), "ticker": ticker})
    entities = pd.DataFrame(ents)
    return df[["investor_name", "kind", "norm", "entity_id"]], entities


def despaced_unions(df, uf, key, threshold=93, min_len=15):
    """Nama korporat yang terpotong atau spasinya bergeser ('PTA SHMORE ASSET MANAGEMENT INDO' vs
    'ASHMORE ASSET MANAGEMENT INDONESIA'): bandingkan tanpa spasi, setelah membuang 'PT' di depan."""
    raw_of = defaultdict(list)
    for raw, n in zip(df["investor_name"], df["norm"]):
        raw_of[n].append(raw)
    corp = {n for k, n in zip(df["kind"], df["norm"]) if k == "korporat"
            and not is_fund(n) and not any(is_nominee(r) for r in raw_of[n])}
    squash = {}
    for n in corp:
        q = n.replace(" ", "")
        q = q[2:] if q.startswith("PT") and len(q) > min_len + 2 else q
        squash[n] = q
    blocks = defaultdict(list)
    for n, q in squash.items():
        blocks[q[:6]].append(n)
    merged = 0
    for names in blocks.values():
        for a, b in combinations(sorted(names), 2):
            qa, qb = squash[a], squash[b]
            short, long_ = sorted((qa, qb), key=len)
            if distinct_series(a, b):
                continue
            short_norm = a if squash[a] == short else b
            truncated = any(looks_truncated(r) for r in raw_of[short_norm])
            if (truncated and len(short) >= min_len and long_.startswith(short)) or fuzz.ratio(qa, qb) >= max(threshold, 95):
                uf.union(key("korporat", a), key("korporat", b))
                merged += 1
    return merged


def continuity_unions(holdings, by_raw, uf, key, min_ratio=85):
    """Nama berbeda dengan jumlah saham PERSIS sama di emiten yang sama pada tanggal berbeda, dan namanya
    mirip (atau salah satunya potongan nama lainnya), dianggap entitas yang sama. Ini menangkap nama yang
    terpotong/tercemar di satu bulan dan penulisan ulang nama oleh KSEI."""
    if not {"ticker", "total_shares", "valid_date"} <= set(holdings.columns):
        return 0
    h = holdings[["ticker", "total_shares", "valid_date", "investor_name"]].drop_duplicates()
    merged = 0
    for _, g in h.groupby(["ticker", "total_shares"]):
        if g["investor_name"].nunique() < 2:
            continue
        dates = g.groupby("investor_name")["valid_date"].apply(set).to_dict()
        for a, b in combinations(sorted(dates), 2):
            if dates[a] & dates[b]:
                continue  # muncul bersamaan = dua akun berbeda
            na, nb = by_raw[a][1], by_raw[b][1]
            if is_nominee(a) != is_nominee(b):
                continue  # pindah ke/dari akun kustodian: ditautkan lewat pemilik manfaat, bukan kemiripan nama
            if is_nominee(a) and normalize(beneficiary(a) or "") != normalize(beneficiary(b) or ""):
                continue  # dua akun kustodian untuk klien berbeda
            if distinct_series(na, nb) or not normalize(a) or not normalize(b):
                continue  # nama generik ("PERUSAHAAN PERSEROAN (PERSERO) PT.") bukan bukti identitas
            ra, rb = " ".join(a.upper().split()), " ".join(b.upper().split())
            prefix = any(len(short) >= 8 and long_.startswith(short)
                         for short, long_ in (sorted((na, nb), key=len), sorted((ra, rb), key=len)))
            if prefix or fuzz.ratio(na, nb) >= min_ratio:
                uf.union(key(*by_raw[a]), key(*by_raw[b]))
                merged += 1
    return merged


def write(conn, names, entities):
    conn.executescript("""
        DROP TABLE IF EXISTS entity_names; DROP TABLE IF EXISTS entities;
        CREATE TABLE entities (entity_id TEXT PRIMARY KEY, canonical_name TEXT, kind TEXT, nominee INTEGER,
                               n_names INTEGER, names TEXT, ticker TEXT);
        CREATE TABLE entity_names (investor_name TEXT PRIMARY KEY, kind TEXT, norm TEXT, entity_id TEXT);
    """)
    conn.executemany("INSERT INTO entities VALUES (?, ?, ?, ?, ?, ?, ?)",
                     entities[["entity_id", "canonical_name", "kind", "nominee", "n_names", "names", "ticker"]]
                     .astype(object).where(entities.notna(), None).itertuples(index=False, name=None))
    conn.executemany("INSERT INTO entity_names VALUES (?, ?, ?, ?)", names.itertuples(index=False, name=None))
    conn.commit()


def issuers_from_holdings(holdings):
    """ticker -> nama emiten (dari kolom ISSUER_NAME data KSEI)."""
    return holdings.drop_duplicates("ticker").set_index("ticker")["issuer_name"].to_dict()


def attach(holdings, conn):
    """Tambahkan entity_id, kind, nominee, ticker entitas ke baris kepemilikan."""
    names = pd.read_sql_query("SELECT investor_name, entity_id FROM entity_names", conn)
    ents = pd.read_sql_query("SELECT entity_id, canonical_name, kind, nominee, ticker AS entity_ticker FROM entities", conn)
    return holdings.merge(names, on="investor_name", how="left").merge(ents, on="entity_id", how="left")
