"""Deteksi penghindaran ambang 1%: pemegang yang "menghilang" dari daftar KSEI tepat di bawah ambang.

Untuk setiap pasangan snapshot berurutan (t0 -> t1) dan setiap emiten, entitas yang tercatat di t0
tetapi tidak di t1 adalah *keluar*. Keluar dianggap mencurigakan bila porsinya di t0 sudah dekat
ambang (< ambang + band), lalu diperkuat oleh pola:
  - muncul kembali di snapshot berikutnya (keluar-masuk di sekitar ambang)
  - beberapa pemegang keluar serentak dari emiten yang sama
  - pemegang baru muncul dengan porsi kecil di dekat ambang (indikasi pemecahan akun)
Keluar yang bisa dijelaskan oleh dilusi (saham beredar naik sehingga porsi lama turun di bawah
ambang tanpa penjualan) ditandai `terjelaskan_dilusi` dan tidak diberi skor.
"""
import pandas as pd

from analysis import holdings as hold


def per_entity(snap):
    g = snap.groupby(["ticker", "entity_id"]).agg(
        name=("canonical_name", "first"), pct=("pct", "sum"), shares=("total_shares", "sum"),
        kind=("kind", "first"), nominee=("nominee", "first"))
    return g


def detect(snapshots, threshold_pct=1.0, near_band_pp=0.5, dilution_tolerance=0.02):
    """snapshots: dict valid_date -> snapshot berisi entity_id, canonical_name, kind, nominee.
    Kembalikan DataFrame kejadian keluar (termasuk yang terjelaskan), satu baris per entitas per transisi."""
    dates = sorted(snapshots)
    ents = {d: per_entity(snapshots[d]) for d in dates}
    outs = {d: hold.outstanding_shares(snapshots[d]) for d in dates}
    rows = []
    for i, (t0, t1) in enumerate(zip(dates, dates[1:])):
        e0, e1 = ents[t0], ents[t1]
        later = [ents[d] for d in dates[i + 2:]]
        tickers1 = set(e1.index.get_level_values("ticker"))
        for ticker, g0 in e0.groupby(level="ticker"):
            if ticker not in tickers1:
                continue  # emiten tidak ada di snapshot berikutnya (delisting/suspensi), bukan kejadian per pemegang
            ids1 = set(e1.loc[ticker].index) if ticker in tickers1 else set()
            ids0 = set(g0.index.get_level_values("entity_id"))
            exits = [eid for eid in ids0 if eid not in ids1]
            entrants = e1.loc[ticker].loc[[eid for eid in ids1 if eid not in ids0]] if ids1 - ids0 else e1.iloc[0:0]
            near_entrants = entrants[entrants["pct"] < threshold_pct + near_band_pp]
            o0, o1 = outs[t0].get(ticker), outs[t1].get(ticker)
            near_exits = []
            for eid in exits:
                r = g0.loc[(ticker, eid)]
                if r["nominee"]:
                    continue
                pct0 = float(r["pct"])
                near = pct0 < threshold_pct + near_band_pp
                diluted_pct = (r["shares"] / o1 * 100) if o0 and o1 and o1 > o0 * (1 + dilution_tolerance) else None
                explained = diluted_pct is not None and diluted_pct < threshold_pct
                reappeared = next((d for d, e in zip(dates[i + 2:], later) if (ticker, eid) in e.index), None)
                # seluruh saham pindah ke nama lain (pengalihan utuh / ganti nama / pindah kustodian)
                moved = entrants.index[(entrants["shares"] - r["shares"]).abs() <= max(1, 0.001 * r["shares"])]
                moved_to = entrants.loc[moved[0], "name"] if len(moved) else None
                rows.append({"ticker": ticker, "t0": t0, "t1": t1, "entity_id": eid, "name": r["name"],
                             "kind": r["kind"], "pct0": round(pct0, 4), "shares0": int(r["shares"]),
                             "near": near, "explained_dilution": explained, "moved_to": moved_to,
                             "diluted_pct": round(diluted_pct, 4) if diluted_pct is not None else None,
                             "reappeared": reappeared})
                if near and not explained and not moved_to:
                    near_exits.append(len(rows) - 1)
            for k in near_exits:
                rows[k]["coordinated_n"] = len(near_exits)
                rows[k]["entrants_n"] = len(near_entrants)
                rows[k]["entrants_pct"] = round(float(near_entrants["pct"].sum()), 4)
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    for c in ("coordinated_n", "entrants_n", "entrants_pct"):
        df[c] = df.get(c, pd.Series(dtype=float)).fillna(0)
    df["status"] = "keluar"
    df.loc[df["near"] & ~df["explained_dilution"], "status"] = "tepat_di_bawah_ambang"
    df.loc[df["explained_dilution"], "status"] = "terjelaskan_dilusi"
    df.loc[df["moved_to"].notna(), "status"] = "pindah_utuh"
    df["patterns"] = [_patterns(r) for r in df.itertuples()]
    df["score"] = [(_score(r) if r.status == "tepat_di_bawah_ambang" else 0) for r in df.itertuples()]
    df["reason"] = [_reason(r, threshold_pct) for r in df.itertuples()]
    return df.sort_values(["score", "ticker"], ascending=[False, True]).reset_index(drop=True)


def _patterns(r):
    p = []
    if r.status != "tepat_di_bawah_ambang":
        return ""
    if r.reappeared:
        p.append("muncul_kembali")
    if r.coordinated_n >= 2:
        p.append("serentak")
    if r.entrants_n >= 1:
        p.append("pemegang_baru_dekat_ambang")
    return ",".join(p)


def _score(r):
    return 1 + sum(1 for _ in filter(None, (r.patterns or "").split(",")))


def _fmt_date(d):
    return pd.Timestamp(d).strftime("%d %b %Y")


def _reason(r, threshold_pct):
    base = (f"{r.name} memegang {r.pct0:.2f}% {r.ticker} per {_fmt_date(r.t0)} lalu tidak lagi tercatat per "
            f"{_fmt_date(r.t1)}")
    if r.status == "pindah_utuh":
        shares = f"{r.shares0:,}".replace(",", ".")
        return base + (f"; seluruh {shares} sahamnya kini tercatat atas nama {r.moved_to} "
                       "(pengalihan utuh, ganti nama, atau pindah kustodian).")
    if r.status == "terjelaskan_dilusi":
        return base + (f"; saham beredar bertambah sehingga kepemilikan yang sama tinggal {r.diluted_pct:.2f}% "
                       f"(di bawah {threshold_pct:g}%), jadi kemungkinan karena dilusi.")
    if r.status != "tepat_di_bawah_ambang":
        return base + "."
    extra = []
    if r.reappeared:
        extra.append(f"muncul lagi per {_fmt_date(r.reappeared)}")
    if r.coordinated_n >= 2:
        extra.append(f"{int(r.coordinated_n)} pemegang dekat ambang keluar bersamaan")
    if r.entrants_n >= 1:
        extra.append(f"{int(r.entrants_n)} pemegang baru masuk dengan total {r.entrants_pct:.2f}%")
    return base + f"; porsinya hanya {r.pct0 - threshold_pct:.2f} poin di atas ambang {threshold_pct:g}%" + \
        (f" ({'; '.join(extra)})." if extra else ".")


def hovering(snapshots, threshold_pct=1.0, band_pp=0.1, min_dates=2):
    """Pemegang yang berulang kali tercatat di [ambang, ambang + band) - "parkir" tepat di atas ambang."""
    frames = []
    for d, snap in snapshots.items():
        e = per_entity(snap).reset_index()
        e["valid_date"] = d
        frames.append(e[(e["pct"] >= threshold_pct) & (e["pct"] < threshold_pct + band_pp) & ~e["nominee"].astype(bool)])
    if not frames:
        return pd.DataFrame()
    allf = pd.concat(frames)
    g = allf.groupby(["ticker", "entity_id", "name"]).agg(n_dates=("valid_date", "nunique"),
                                                           pcts=("pct", lambda x: ", ".join(f"{v:.2f}" for v in x)))
    return g[g["n_dates"] >= min_dates].reset_index()


def run(conn, cfg):
    from analysis import entities as ent
    t = cfg["threshold"]
    snaps = {d: ent.attach(hold.snapshot(conn, d), conn) for d in hold.valid_dates(conn)}
    events = detect(snaps, t["threshold_pct"], t["near_band_pp"], t["dilution_tolerance"])
    hov = hovering(snaps, t["threshold_pct"])
    conn.execute("DROP TABLE IF EXISTS threshold_events")
    conn.execute("DROP TABLE IF EXISTS threshold_hovering")
    if len(events):
        events.to_sql("threshold_events", conn, index=False)
    if len(hov):
        hov.to_sql("threshold_hovering", conn, index=False)
    conn.commit()
    return events, hov
