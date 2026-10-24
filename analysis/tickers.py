"""Ekstraksi kode saham IDX dari teks pesan."""
import csv
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from analysis import db

log = logging.getLogger(__name__)

# Token kata: opsional awalan $/#, lalu huruf/angka; angka seperti 1.250 atau 1.250-1.300 tetap satu token
# supaya jarak konteks tidak membengkak karena tanda baca harga.
TOKEN_RE = re.compile(r"[$#]?[A-Za-z0-9]+(?:[.,:\-][0-9]+)*")
TICKER_TOKEN_RE = re.compile(r"^([$#]?)([A-Za-z]{4})$")
LEADING_ALPHA_RE = re.compile(r"[a-z]+")


@dataclass(frozen=True)
class Mention:
    ticker: str
    start: int        # posisi karakter di teks
    prefixed: bool    # diawali $ atau #
    ambiguous: bool   # kode yang juga kata umum


def load_tickers(path):
    """Baca idx_tickers.csv -> (set kode, set kode yang ditandai ambiguous di kolom opsional)."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"{path} tidak ditemukan (wajib: kolom `ticker`, satu kode saham per baris)")
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fields = {name.lower().strip(): name for name in reader.fieldnames or []}
        code_col = next((fields[k] for k in ("ticker", "kode", "code", "symbol") if k in fields), None)
        if code_col is None:
            raise ValueError(f"{path} harus punya kolom `ticker`")
        amb_col = fields.get("ambiguous")
        tickers, flagged = set(), set()
        for row in reader:
            code = (row[code_col] or "").strip().upper().removesuffix(".JK")
            if not re.fullmatch(r"[A-Z]{4}", code):
                continue
            tickers.add(code)
            if amb_col and str(row.get(amb_col, "")).strip().lower() in ("1", "true", "yes", "ya"):
                flagged.add(code)
    return tickers, flagged


class TickerExtractor:
    def __init__(self, tickers, ambiguous, context_words, context_window=5, allow_lowercase_with_prefix=True):
        self.tickers = {t.upper() for t in tickers}
        self.ambiguous = {t.upper() for t in ambiguous} & self.tickers
        self.context_words = {w.lower() for w in context_words}
        self.window = context_window
        self.allow_lowercase_with_prefix = allow_lowercase_with_prefix

    @classmethod
    def from_config(cls, cfg):
        tickers, flagged = load_tickers(cfg["paths"]["tickers_csv"])
        tcfg = cfg["tickers"]
        # str(): YAML membaca TRUE/NULL tanpa kutip sebagai bool/None
        ambiguous = flagged | {str(w).upper() for w in tcfg.get("common_words", []) if w is not None}
        return cls(tickers, ambiguous, [str(w) for w in tcfg["context_words"]], tcfg["context_window"],
                   tcfg.get("allow_lowercase_with_prefix", True))

    def _is_context(self, token):
        bare = token.lstrip("$#")
        if bare.isupper() and bare in self.tickers:
            return False  # "BELI BUMI": kode saham kapital tidak dihitung sebagai kata konteks
        m = LEADING_ALPHA_RE.match(bare.lower())
        return bool(m) and m.group(0) in self.context_words

    def extract(self, text):
        """Semua kemunculan ticker yang lolos aturan, urut posisi."""
        if not text:
            return []
        tokens = [(m.group(0), m.start()) for m in TOKEN_RE.finditer(text)]
        mentions = []
        for i, (tok, start) in enumerate(tokens):
            m = TICKER_TOKEN_RE.match(tok)
            if not m:
                continue
            prefix, code = m.groups()
            if not code.isupper() and not (prefix and self.allow_lowercase_with_prefix):
                continue
            code = code.upper()
            if code not in self.tickers:
                continue
            ambiguous = code in self.ambiguous
            if ambiguous and not prefix:
                lo, hi = max(0, i - self.window), min(len(tokens), i + self.window + 1)
                if not any(self._is_context(tokens[j][0]) for j in range(lo, hi) if j != i):
                    continue
            mentions.append(Mention(code, start, bool(prefix), ambiguous))
        return mentions

    def extract_codes(self, text):
        return sorted({m.ticker for m in self.extract(text)})


def run(conn, cfg):
    """Bangun ulang tabel mentions dari seluruh tabel messages."""
    extractor = TickerExtractor.from_config(cfg)
    log.info("Ticker: %s kode, %s ditandai ambigu", len(extractor.tickers), len(extractor.ambiguous))
    db.reset_table(conn, "mentions")
    rows = []
    n_messages = 0
    for msg in conn.execute("SELECT channel, message_id, date, text FROM messages WHERE text IS NOT NULL AND text != ''"):
        n_messages += 1
        per_ticker = {}
        for m in extractor.extract(msg["text"]):
            n, prefixed, amb = per_ticker.get(m.ticker, (0, False, m.ambiguous))
            per_ticker[m.ticker] = (n + 1, prefixed or m.prefixed, amb)
        for ticker, (n, prefixed, amb) in per_ticker.items():
            rows.append((msg["channel"], msg["message_id"], ticker, msg["date"], n, int(prefixed), int(amb)))
    conn.executemany("INSERT INTO mentions VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
    conn.commit()
    n_msgs_with = len({(r[0], r[1]) for r in rows})
    log.info("Mentions: %s baris dari %s/%s pesan, %s ticker unik",
             len(rows), n_msgs_with, n_messages, len({r[2] for r in rows}))
    return len(rows)
