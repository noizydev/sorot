"""Label risiko per emiten yang bisa diaudit.

Skor = jumlah poin dari komponen di config.yaml `risk.points`; setiap poin disertai kalimat alasan
dalam bahasa sederhana dan bukti yang bisa diperiksa. Label: Rendah / Sedang / Tinggi.
Setiap label dipublikasikan ke transparency log (analysis.translog) bersama sidik jari inputnya.
"""
import hashlib
import json
import sqlite3
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from analysis.kinds import PROMO, classify

ROOT = Path(__file__).resolve().parents[1]


def _fmt(d):
    return pd.Timestamp(d).strftime("%d %b %Y")


def ownership_signals(conn):
    """Metrik kepemilikan untuk valid_date terakhir."""
    vd = conn.execute("SELECT MAX(valid_date) FROM ff_summary").fetchone()[0]
    summary = pd.read_sql_query("SELECT * FROM ff_summary WHERE valid_date = ?", conn, params=(vd,)).set_index("ticker")
    groups = pd.read_sql_query("SELECT ticker, group_name, agg_pct, reason FROM ff_groups WHERE valid_date = ? AND split = 1",
                               conn, params=(vd,))
    try:
        events = pd.read_sql_query("SELECT * FROM threshold_events WHERE status = 'tepat_di_bawah_ambang'", conn)
    except Exception:
        events = pd.DataFrame(columns=["ticker"])
    known_at = conn.execute("SELECT MAX(published_at) FROM documents WHERE valid_date = ?", (vd,)).fetchone()[0]
    docs = [r[0] for r in conn.execute("SELECT sha256 FROM documents ORDER BY published_at")]
    return vd, known_at, docs, summary, groups, events


def promotion_signals(messages_db, volume_spike_ratio=2.0):
    """Per ticker: burst >=3 kanal, burst lintas kanal berisi ajakan, lonjakan volume, cluster lintas kanal."""
    if not messages_db or not Path(messages_db).exists():
        return {}, None
    conn = sqlite3.connect(messages_db)
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "bursts" not in tables:
            return {}, None
        until = conn.execute("SELECT MAX(date) FROM messages").fetchone()[0]
        vol = "burst_volume" in tables
        b = pd.read_sql_query(f"""SELECT b.ticker, b.hour, b.count, b.n_channels, b.z
                                  {", v.volume_ratio_h1, v.h1_date" if vol else ""}
                                  FROM bursts b {"LEFT JOIN burst_volume v USING (ticker, hour)" if vol else ""}
                                  WHERE b.n_channels >= 2""", conn)
        sig = {}
        for r in b.itertuples():
            s = sig.setdefault(r.ticker, {"bursts3": [], "promo": [], "spikes": [], "dup3": 0})
            if r.n_channels >= 3:
                s["bursts3"].append((r.hour, int(r.n_channels)))
            start = pd.Timestamp(r.hour)
            texts = [t for (t,) in conn.execute(
                "SELECT g.text FROM mentions m JOIN messages g ON g.channel = m.channel AND g.message_id = m.message_id "
                "WHERE m.ticker = ? AND m.date >= ? AND m.date < ?",
                (r.ticker, start.isoformat()[:19], (start + timedelta(hours=1)).isoformat()[:19]))]
            kinds = pd.Series([classify(t) for t in texts]).value_counts()
            if len(kinds) and kinds.idxmax() == PROMO:
                s["promo"].append((r.hour, int(r.n_channels)))
            ratio = getattr(r, "volume_ratio_h1", None)
            if ratio is not None and pd.notna(ratio) and ratio >= volume_spike_ratio:
                s["spikes"].append((r.hour, float(ratio), r.h1_date))
        if "dup_clusters" in tables:
            for t, n in conn.execute("""SELECT m.ticker, COUNT(DISTINCT c.cluster_id) FROM dup_clusters c
                                        JOIN dup_members d ON d.cluster_id = c.cluster_id
                                        JOIN mentions m ON m.channel = d.channel AND m.message_id = d.message_id
                                        WHERE c.n_channels >= 3 GROUP BY m.ticker"""):
                sig.setdefault(t, {"bursts3": [], "promo": [], "spikes": [], "dup3": 0})["dup3"] = int(n)
        return sig, until
    finally:
        conn.close()


def _wib(hour_iso):
    return (pd.Timestamp(hour_iso) + pd.Timedelta(hours=7)).strftime("%d %b %Y %H.00 WIB")


def score_ticker(ticker, ff, split_groups, events, promo, cfg):
    o, r = cfg["ownership"], cfg["risk"]
    pts = r["points"]
    reasons = []

    def add(code, points, text):
        if points:
            reasons.append({"code": code, "points": int(points), "text": text})

    if ff is not None:
        eff, proxy = float(ff["ff_effective"]), float(ff["ff_proxy"])
        if eff < o["ff_min_pct"]:
            add("ff_below_min", pts["ff_below_min"],
                f"Free float efektif {eff:.2f}%, di bawah batas minimum {o['ff_min_pct']:g}%: hanya sedikit saham yang "
                f"benar-benar beredar di publik, sehingga harga mudah digerakkan.")
        elif eff < o["ff_warn_pct"]:
            add("ff_below_warn", pts["ff_below_warn"],
                f"Free float efektif {eff:.2f}%, masih di bawah {o['ff_warn_pct']:g}%.")
        if proxy - eff >= r["ff_gap_pp"]:
            detail = "; ".join(g for g in split_groups["reason"].head(3) if g)
            add("ff_gap", pts["ff_gap"],
                f"Free float tampak {proxy:.2f}% tetapi efektifnya {eff:.2f}%: {proxy - eff:.2f} poin saham yang terlihat "
                f"tersebar ternyata dikuasai kelompok yang sama. {detail}")
    if len(events):
        n = min(len(events), 3)
        names = ", ".join(f"{e.name} ({e.pct0:.2f}%, {_fmt(e.t0)} → {_fmt(e.t1)})" for e in events.head(3).itertuples())
        add("threshold_event", pts["threshold_event"] * n,
            f"{len(events)} pemegang hilang dari daftar KSEI tepat di bawah ambang 1% tanpa bisa dijelaskan dilusi: {names}.")
        patterns = sorted({p for ps in events["patterns"].fillna("") for p in ps.split(",") if p})
        if patterns:
            label = {"muncul_kembali": "muncul kembali", "serentak": "keluar serentak",
                     "pemegang_baru_dekat_ambang": "diganti pemegang baru dekat ambang"}
            add("threshold_pattern", pts["threshold_pattern"],
                f"Pola yang memperkuat dugaan: {', '.join(label.get(p, p) for p in patterns)}.")
    if promo:
        if promo["bursts3"]:
            n = min(len(promo["bursts3"]), 3)
            first = ", ".join(_wib(h) for h, _ in promo["bursts3"][:3])
            add("burst_cross", pts["burst_cross"] * n,
                f"{len(promo['bursts3'])} kali disebut serentak oleh ≥3 kanal Telegram dalam satu jam ({first}).")
        if promo["promo"]:
            h, n = promo["promo"][0]
            add("burst_promo", pts["burst_promo"],
                f"Lonjakan pembahasan lintas kanal yang didominasi ajakan/rekomendasi beli ({len(promo['promo'])} kali, "
                f"mis. {_wib(h)} di {n} kanal).")
        if promo["spikes"]:
            h, ratio, d1 = max(promo["spikes"], key=lambda x: x[1])
            add("volume_spike", pts["volume_spike"],
                f"Setelah lonjakan pembahasan lintas kanal, volume perdagangan hari berikutnya mencapai {ratio:.1f}× "
                f"rata-rata 20 hari (tertinggi: {_fmt(d1)}).")
        if promo["dup3"]:
            add("dup_cross", pts["dup_cross"],
                f"{promo['dup3']} pesan yang hampir identik beredar di ≥3 kanal sekaligus.")

    score = min(100, sum(x["points"] for x in reasons))
    cut = r["cutoffs"]
    label = "Tinggi" if score >= cut["tinggi"] else "Sedang" if score >= cut["sedang"] else "Rendah"
    if not reasons:
        reasons.append({"code": "none", "points": 0, "text": "Tidak ada sinyal risiko yang terdeteksi dari data yang tersedia."})
    return score, label, reasons


def _git_commit():
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                              timeout=10).stdout.strip() or None
    except Exception:
        return None


def compute(conn, cfg):
    vd, known_at, docs, summary, groups, events = ownership_signals(conn)
    promo, until = promotion_signals(cfg["paths"].get("db"), cfg["risk"]["volume_spike_ratio"])
    cfg_hash = hashlib.sha256((ROOT / "config.yaml").read_bytes()).hexdigest() if (ROOT / "config.yaml").exists() else None
    inputs = {"ownership_valid_date": vd, "ownership_known_at": known_at,
              "ownership_documents": docs, "messages_until": until,
              "config_sha256": cfg_hash, "code_commit": _git_commit()}
    created = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows, records = [], []
    for ticker in sorted(set(summary.index) | set(promo)):
        ff = summary.loc[ticker] if ticker in summary.index else None
        score, label, reasons = score_ticker(ticker, ff, groups[groups["ticker"] == ticker],
                                             events[events["ticker"] == ticker] if len(events) else events,
                                             promo.get(ticker), cfg)
        metrics = {}
        if ff is not None:
            metrics = {k: (None if pd.isna(ff[k]) else float(ff[k])) for k in ("ff_proxy", "ff_effective", "gap", "pct_ge1_total")}
        rec = {"type": "sorot.risk_label", "version": 1, "ticker": ticker,
               "issuer_name": None if ff is None else ff["issuer_name"], "score": score, "label": label,
               "reasons": reasons, "metrics": metrics, "inputs": inputs, "created_at": created}
        records.append(rec)
        rows.append({"ticker": ticker, "issuer_name": rec["issuer_name"], "score": score, "label": label,
                     "reasons": json.dumps(reasons, ensure_ascii=False), "created_at": created,
                     "ff_effective": metrics.get("ff_effective"), "ff_proxy": metrics.get("ff_proxy")})
    return pd.DataFrame(rows), records


def publish(conn, df, records, tlog):
    """Tulis tabel risk_labels dan tambahkan semua label ke transparency log. Mengembalikan tree head."""
    start = len(tlog.entries())
    head = tlog.append(records)
    df = df.assign(log_index=range(start, start + len(df)), tree_size=head["size"], tree_root=head["root"])
    df.to_sql("risk_labels", conn, if_exists="replace", index=False)
    conn.commit()
    return head
