"""Transparency log append-only berbasis Merkle tree (algoritma RFC 6962 / Certificate Transparency).

- Leaf hash   = SHA256(0x00 || entri JSON kanonis)
- Node hash   = SHA256(0x01 || kiri || kanan)
- Tree head   = {size, root, timestamp}, ditambahkan setiap kali entri baru dipublikasikan.

Entri disimpan di log.jsonl (satu baris per entri, tidak pernah ditulis ulang). Tree head disimpan di
heads.jsonl yang ikut di-commit ke git: siapa pun yang pernah melihat sebuah root bisa memeriksa bahwa
log yang sekarang adalah perpanjangan dari log itu (consistency proof) dan bahwa sebuah label memang
tercatat di dalamnya (inclusion proof). Mengubah satu entri lama akan mengubah root dan gagal diverifikasi.
"""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


def canonical(obj):
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def leaf_hash(data: bytes) -> bytes:
    return hashlib.sha256(b"\x00" + data).digest()


def node_hash(left: bytes, right: bytes) -> bytes:
    return hashlib.sha256(b"\x01" + left + right).digest()


def _split(n):
    """Pangkat dua terbesar yang < n (n > 1)."""
    k = 1
    while k << 1 < n:
        k <<= 1
    return k


def root(leaves):
    """Merkle Tree Hash dari daftar leaf hash (RFC 6962 2.1)."""
    n = len(leaves)
    if n == 0:
        return hashlib.sha256(b"").digest()
    if n == 1:
        return leaves[0]
    k = _split(n)
    return node_hash(root(leaves[:k]), root(leaves[k:]))


def inclusion_proof(leaves, index):
    """Audit path untuk leaf ke-index pada pohon berisi `leaves` (RFC 6962 2.1.1)."""
    n = len(leaves)
    if not 0 <= index < n:
        raise IndexError(index)
    if n == 1:
        return []
    k = _split(n)
    if index < k:
        return inclusion_proof(leaves[:k], index) + [root(leaves[k:])]
    return inclusion_proof(leaves[k:], index - k) + [root(leaves[:k])]


def verify_inclusion(leaf, index, size, proof, expected_root):
    """Verifikasi audit path (algoritma RFC 9162 2.1.3.2)."""
    if index >= size:
        return False
    fn, sn, r = index, size - 1, leaf
    for p in proof:
        if sn == 0:
            return False
        if fn & 1 or fn == sn:
            r = node_hash(p, r)
            if not fn & 1:
                while fn and not fn & 1:
                    fn >>= 1
                    sn >>= 1
        else:
            r = node_hash(r, p)
        fn >>= 1
        sn >>= 1
    return sn == 0 and r == expected_root


def consistency_proof(leaves, m):
    """Bukti bahwa pohon ukuran m adalah prefiks pohon ukuran len(leaves) (RFC 6962 2.1.2)."""
    n = len(leaves)
    if not 0 < m <= n:
        raise ValueError("0 < m <= n")

    def subproof(m, leaves, complete):
        n = len(leaves)
        if m == n:
            return [] if complete else [root(leaves)]
        k = _split(n)
        if m <= k:
            return subproof(m, leaves[:k], complete) + [root(leaves[k:])]
        return subproof(m - k, leaves[k:], False) + [root(leaves[:k])]

    return [] if m == n else subproof(m, leaves, True)


def verify_consistency(m, n, old_root, new_root, proof):
    """Verifikasi consistency proof (algoritma RFC 9162 2.1.4.2)."""
    if m == n:
        return not proof and old_root == new_root
    if m == 0 or m > n or not proof:
        return False
    if m & (m - 1) == 0:  # m pangkat dua: root lama adalah simpul pertama
        proof = [old_root] + list(proof)
    fn, sn = m - 1, n - 1
    while fn & 1:
        fn >>= 1
        sn >>= 1
    fr = sr = proof[0]
    for c in proof[1:]:
        if sn == 0:
            return False
        if fn & 1 or fn == sn:
            fr = node_hash(c, fr)
            sr = node_hash(c, sr)
            if not fn & 1:
                while fn and not fn & 1:
                    fn >>= 1
                    sn >>= 1
        else:
            sr = node_hash(sr, c)
        fn >>= 1
        sn >>= 1
    return sn == 0 and fr == old_root and sr == new_root


class TransparencyLog:
    def __init__(self, log_path, heads_path):
        self.log_path, self.heads_path = Path(log_path), Path(heads_path)

    def entries(self):
        if not self.log_path.exists():
            return []
        with open(self.log_path, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    def heads(self):
        if not self.heads_path.exists():
            return []
        with open(self.heads_path, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    def leaves(self, entries=None):
        return [leaf_hash(canonical(e["entry"])) for e in (entries if entries is not None else self.entries())]

    def append(self, records):
        """Tambahkan entri lalu terbitkan tree head baru. Mengembalikan head."""
        existing = self.entries()
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.heads_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.log_path, "a", encoding="utf-8") as f:
            for i, rec in enumerate(records, start=len(existing)):
                line = {"index": i, "leaf_hash": leaf_hash(canonical(rec)).hex(), "entry": rec}
                f.write(json.dumps(line, ensure_ascii=False, sort_keys=True) + "\n")
        leaves = self.leaves()
        prev = self.heads()[-1] if self.heads() else None
        head = {"size": len(leaves), "root": root(leaves).hex(),
                "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "prev_size": prev["size"] if prev else 0}
        if prev and prev["size"]:
            head["consistency"] = [h.hex() for h in consistency_proof(leaves, prev["size"])]
        with open(self.heads_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(head, sort_keys=True) + "\n")
        return head

    def proof(self, index, size=None):
        leaves = self.leaves()
        size = size or len(leaves)
        return [h.hex() for h in inclusion_proof(leaves[:size], index)]

    def audit(self):
        """Periksa seluruh log terhadap semua tree head. Kembalikan (ok, daftar temuan)."""
        entries = self.entries()
        problems = []
        for i, e in enumerate(entries):
            if e.get("index") != i:
                problems.append(f"Entri baris {i + 1}: indeks {e.get('index')} tidak berurutan")
            if leaf_hash(canonical(e["entry"])).hex() != e.get("leaf_hash"):
                problems.append(f"Entri #{i}: isi tidak cocok dengan leaf hash yang tercatat (diubah?)")
        leaves = self.leaves(entries)
        heads = self.heads()
        for h in heads:
            if h["size"] > len(leaves):
                problems.append(f"Tree head ukuran {h['size']} lebih besar dari log ({len(leaves)} entri): entri hilang")
                continue
            if root(leaves[:h["size"]]).hex() != h["root"]:
                problems.append(f"Root untuk ukuran {h['size']} ({h['timestamp']}) tidak cocok: log telah diubah")
        for old, new in zip(heads, heads[1:]):
            if new.get("consistency") is not None and old["size"]:
                ok = verify_consistency(old["size"], new["size"], bytes.fromhex(old["root"]),
                                        bytes.fromhex(new["root"]), [bytes.fromhex(x) for x in new["consistency"]])
                if not ok:
                    problems.append(f"Consistency proof {old['size']} -> {new['size']} gagal")
        return not problems, problems
