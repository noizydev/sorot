"""Deteksi pesan near-duplicate lintas kanal dengan MinHash-LSH."""
import logging
import re

from datasketch import MinHash, MinHashLSH

from analysis import db

log = logging.getLogger(__name__)

URL_RE = re.compile(r"(?:https?://|www\.|t\.me/)\S+", re.IGNORECASE)
NON_WORD_RE = re.compile(r"[^\w\s]|_")
DIGIT_RE = re.compile(r"\d+")
SPACE_RE = re.compile(r"\s+")


def normalize_text(text, strip_urls=True, strip_digits=False):
    """Huruf kecil, tanpa URL/tanda baca/emoji, spasi dirapikan."""
    if not text:
        return ""
    s = text.lower()
    if strip_urls:
        s = URL_RE.sub(" ", s)
    s = NON_WORD_RE.sub(" ", s)
    if strip_digits:
        s = DIGIT_RE.sub(" ", s)
    return SPACE_RE.sub(" ", s).strip()


def shingles(text, k):
    if len(text) <= k:
        return {text} if text else set()
    return {text[i:i + k] for i in range(len(text) - k + 1)}


class _UnionFind:
    def __init__(self):
        self.parent = {}

    def find(self, x):
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def find_clusters(items, shingle_size=5, threshold=0.8, num_perm=128, seed=1,
                  min_chars=40, strip_urls=True, strip_digits=False):
    """items: iterable (key, text). Kembalikan list cluster (list key, ukuran >= 2).

    Kandidat dari LSH diverifikasi ulang dengan estimasi Jaccard MinHash >= threshold
    sebelum digabung, untuk mengurangi false positive LSH.
    """
    template = MinHash(num_perm=num_perm, seed=seed)
    lsh = MinHashLSH(threshold=threshold, num_perm=num_perm)
    hashes = {}
    uf = _UnionFind()
    for key, text in items:
        norm = normalize_text(text, strip_urls, strip_digits)
        if len(norm) < min_chars:
            continue
        # pakai ulang permutasi template: membuat permutasi baru per pesan jauh lebih lambat
        m = MinHash(num_perm=num_perm, seed=seed, permutations=template.permutations, scheme=template.scheme)
        m.update_batch([s.encode("utf-8") for s in shingles(norm, shingle_size)])
        for other in lsh.query(m):
            if hashes[other].jaccard(m) >= threshold:
                uf.union(other, key)
        lsh.insert(key, m)
        hashes[key] = m

    groups = {}
    for key in hashes:
        groups.setdefault(uf.find(key), []).append(key)
    return [g for g in groups.values() if len(g) >= 2]


def run(conn, cfg):
    dcfg = cfg["dedup"]
    msgs = db.read_df(conn, "SELECT channel, message_id, date, text FROM messages WHERE text IS NOT NULL AND text != ''")
    msgs["key"] = list(zip(msgs["channel"], msgs["message_id"]))
    clusters = find_clusters(
        zip(msgs["key"], msgs["text"]),
        shingle_size=dcfg["shingle_size"], threshold=dcfg["threshold"], num_perm=dcfg["num_perm"],
        seed=dcfg["seed"], min_chars=dcfg["min_chars"], strip_urls=dcfg["strip_urls"],
        strip_digits=dcfg["strip_digits"],
    )

    by_key = msgs.set_index("key")
    by_key["ts"] = db.to_utc(by_key["date"])
    summaries = []
    for keys in clusters:
        part = by_key.loc[keys].sort_values("ts")
        summaries.append((part, part["channel"].nunique()))
    # cluster lintas kanal paling banyak dulu, lalu ukuran
    summaries.sort(key=lambda x: (-x[1], -len(x[0]), x[0]["ts"].iloc[0]))

    db.reset_table(conn, "dup_clusters")
    db.reset_table(conn, "dup_members")
    for cid, (part, n_channels) in enumerate(summaries, start=1):
        first, last = part["ts"].iloc[0], part["ts"].iloc[-1]
        conn.execute(
            "INSERT INTO dup_clusters VALUES (?, ?, ?, ?, ?, ?, ?)",
            (cid, len(part), n_channels, first.isoformat(), last.isoformat(),
             (last - first).total_seconds() / 60, part["text"].iloc[0][:500]),
        )
        conn.executemany(
            "INSERT INTO dup_members VALUES (?, ?, ?, ?)",
            [(cid, ch, int(mid), d) for (ch, mid), d in zip(part.index, part["date"])],
        )
    conn.commit()
    multi = sum(1 for _, n in summaries if n > 1)
    log.info("Near-duplicate: %s cluster (%s lintas kanal) dari %s pesan", len(summaries), multi, len(msgs))
    return len(summaries)
