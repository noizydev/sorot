"""Free float efektif dengan kepemilikan terintegrasi (integrated ownership).

Notasi: W[i, j] = porsi saham perusahaan tercatat j yang dipegang langsung oleh i (data >= 1% KSEI).
Kepemilikan terintegrasi antar perusahaan tercatat: T = W_cc (I - W_cc)^-1, yaitu jumlah porsi lewat
semua jalur (langsung, lewat satu perantara, dua perantara, ...). Untuk pemegang di luar perusahaan
tercatat (individu, PT tertutup, dana): T_u = W_uc (I - W_cc)^-1.

Free float proksi   = 100 - total pemegang langsung >= 5%.
Free float efektif  = 100 - total kelompok yang menguasai >= 5%, di mana satu kelompok =
                       semua nama yang ter-resolve ke entitas yang sama, ditambah perusahaan tercatat
                       yang dikendalikan pengendali akhir yang sama. Akun nominee/kustodian dianggap publik.
"""
import json

import numpy as np
import pandas as pd

from analysis import entities as ent
from analysis import holdings as hold


def _matrices(snap):
    """snap: baris kepemilikan + entity_id, entity_ticker. Kembalikan indeks dan matriks W."""
    companies = sorted(snap["ticker"].unique())
    cidx = {t: k for k, t in enumerate(companies)}
    # pemegang yang merupakan perusahaan tercatat dengan data pemegang sahamnya sendiri -> node perusahaan
    snap = snap.assign(node=np.where(snap["entity_ticker"].isin(cidx), "C:" + snap["entity_ticker"].fillna(""),
                                     "U:" + snap["entity_id"].fillna(snap["investor_name"])))
    agg = snap.groupby(["node", "ticker"])["pct"].sum().div(100).clip(upper=1.0).reset_index()
    unlisted = sorted(n for n in agg["node"].unique() if n.startswith("U:"))
    uidx = {n: k for k, n in enumerate(unlisted)}
    n = len(companies)
    w_cc = np.zeros((n, n))
    w_uc = np.zeros((len(unlisted), n))
    for node, ticker, frac in agg.itertuples(index=False):
        j = cidx[ticker]
        if node.startswith("C:"):
            i = cidx[node[2:]]
            if i != j:  # saham treasuri / kepemilikan diri sendiri tidak dirambatkan
                w_cc[i, j] += frac
        else:
            w_uc[uidx[node], j] += frac
    return snap, companies, cidx, unlisted, uidx, w_cc, w_uc


def integrated(w_cc, w_uc):
    """Kembalikan (T_cc, T_uc). (I - W) dijamin bisa dibalik selama tiap kolom < 100%."""
    n = w_cc.shape[0]
    try:
        inv = np.linalg.solve(np.eye(n) - w_cc, np.eye(n))
    except np.linalg.LinAlgError:
        inv = np.linalg.pinv(np.eye(n) - w_cc)
    return w_cc @ inv, w_uc @ inv


def compute(snap, names_df, entities_df, control_min=0.25, concentrated_pct=5.0):
    """snap: satu snapshot (holdings). Kembalikan (summary, groups, owners) sebagai DataFrame."""
    s = snap.merge(names_df[["investor_name", "entity_id"]], on="investor_name", how="left")
    e = entities_df.set_index("entity_id")
    s["canonical_name"] = s["entity_id"].map(e["canonical_name"])
    s["kind"] = s["entity_id"].map(e["kind"])
    s["nominee"] = s["entity_id"].map(e["nominee"]).fillna(False).astype(bool)
    s["entity_ticker"] = s["entity_id"].map(e["ticker"])

    s, companies, cidx, unlisted, uidx, w_cc, w_uc = _matrices(s)
    t_cc, t_uc = integrated(w_cc, w_uc)
    has_holders = set(s["ticker"])

    # pemilik akhir: entitas di luar perusahaan tercatat + perusahaan tercatat tanpa data pemegang
    root_names = {}
    for node in unlisted:
        eid = node[2:]
        root_names[node] = e["canonical_name"].get(eid, eid)
    nominee_nodes = {"U:" + eid for eid, nom in e["nominee"].items() if nom}

    def ultimate(j):
        """Daftar (node, porsi terintegrasi) pemilik akhir perusahaan j, terurut menurun."""
        col = t_uc[:, j]
        out = [(unlisted[k], col[k]) for k in np.nonzero(col > 1e-6)[0] if unlisted[k] not in nominee_nodes]
        for k in np.nonzero(t_cc[:, j] > 1e-6)[0]:
            if companies[k] not in has_holders or not w_cc[:, k].any() and not w_uc[:, k].any():
                out.append(("C:" + companies[k], t_cc[k, j]))
        return sorted(out, key=lambda x: -x[1])

    controller = {}
    for t, j in cidx.items():
        u = ultimate(j)
        if u and u[0][1] >= control_min:
            controller[t] = u[0]

    def node_name(node):
        return root_names.get(node, node[2:])

    summaries, groups, owners = [], [], []
    outstanding = hold.outstanding_shares(snap)
    for ticker, g in s.groupby("ticker"):
        j = cidx[ticker]
        direct_ge5 = g.loc[g["pct"] >= concentrated_pct, "pct"].sum()
        # kelompok: entitas yang sama, atau perusahaan tercatat yang dikendalikan pengendali yang sama
        def group_of(row):
            if row.nominee:
                return ("NOMINEE:" + row.investor_name, row.investor_name + " (nominee)")
            if row.node.startswith("C:"):
                k = row.node[2:]
                if k in controller:
                    cnode = controller[k][0]
                    return (cnode, node_name(cnode))
                return (row.node, row.canonical_name or row.investor_name)
            # pemegang yang sendiri merupakan pengendali perusahaan lain tetap satu kelompok dengannya
            return (row.node, row.canonical_name or row.investor_name)

        gg = g.assign(_g=[group_of(r) for r in g.itertuples()])
        gg["group_id"] = gg["_g"].map(lambda x: x[0])
        gg["group_name"] = gg["_g"].map(lambda x: x[1])
        grp = gg.groupby(["group_id", "group_name"]).agg(
            agg_pct=("pct", "sum"), n_rows=("pct", "size"),
            members=("investor_name", lambda x: list(x)), member_pcts=("pct", lambda x: list(x)),
            max_single=("pct", "max")).reset_index()
        grp["concentrated"] = grp["agg_pct"] >= concentrated_pct
        grp["split"] = grp["concentrated"] & (grp["max_single"] < concentrated_pct)  # tersembunyi oleh pemecahan
        locked = grp.loc[grp["concentrated"], "agg_pct"].sum()
        ff_proxy = max(0.0, 100 - direct_ge5)
        ff_eff = max(0.0, 100 - locked)
        ctrl = controller.get(ticker)
        summaries.append({
            "ticker": ticker, "issuer_name": g["issuer_name"].iat[0], "outstanding": outstanding.get(ticker),
            "n_holders": len(g), "pct_ge1_total": round(g["pct"].sum(), 4),
            "ff_proxy": round(ff_proxy, 4), "ff_effective": round(ff_eff, 4), "gap": round(ff_proxy - ff_eff, 4),
            "controller": node_name(ctrl[0]) if ctrl else None,
            "controller_stake": round(float(ctrl[1]) * 100, 4) if ctrl else None,
            "n_split_groups": int(grp["split"].sum()),
        })
        for r in grp.itertuples():
            reason = None
            if r.split:
                parts = ", ".join(f"{m} {p:.2f}%" for m, p in zip(r.members, r.member_pcts))
                reason = (f"{r.group_name} menguasai {r.agg_pct:.2f}% lewat {r.n_rows} nama/perusahaan yang masing-masing"
                          f" di bawah {concentrated_pct:g}%: {parts}")
            groups.append({"ticker": ticker, "group_id": r.group_id, "group_name": r.group_name,
                           "agg_pct": round(r.agg_pct, 4), "n_rows": r.n_rows, "concentrated": bool(r.concentrated),
                           "split": bool(r.split), "members": json.dumps(list(zip(r.members, r.member_pcts))),
                           "reason": reason})
        for node, frac in ultimate(j)[:10]:
            owners.append({"ticker": ticker, "owner": node_name(node), "node": node,
                           "integrated_pct": round(float(frac) * 100, 4),
                           "direct_pct": round(float(g.loc[g["node"] == node, "pct"].sum()), 4)})
    return pd.DataFrame(summaries), pd.DataFrame(groups), pd.DataFrame(owners)


def run(conn, cfg):
    """Resolve entitas atas semua snapshot lalu hitung free float untuk setiap valid_date (versi terbaru)."""
    o = cfg["ownership"]
    allrows = pd.read_sql_query("SELECT * FROM holdings WHERE tx_to IS NULL", conn)
    if allrows.empty:
        raise ValueError("Belum ada data kepemilikan. Jalankan `python ownership.py import` dulu.")
    issuers = ent.issuers_from_holdings(allrows)
    names, entities = ent.resolve(allrows, issuers, o["match_threshold_corporate"], o["match_threshold_individual"],
                                  o["issuer_match_threshold"], ent.load_aliases(cfg["paths"].get("entity_aliases_csv")))
    ent.write(conn, names, entities)

    conn.executescript("""
        DROP TABLE IF EXISTS ff_summary; DROP TABLE IF EXISTS ff_groups; DROP TABLE IF EXISTS ff_owners;
    """)
    out = []
    for vd in hold.valid_dates(conn):
        snap = hold.snapshot(conn, vd)
        summary, groups, owners = compute(snap, names, entities, o["control_min"], o["concentrated_pct"])
        for df in (summary, groups, owners):
            df.insert(0, "valid_date", vd)
        summary.to_sql("ff_summary", conn, if_exists="append", index=False)
        groups.to_sql("ff_groups", conn, if_exists="append", index=False)
        owners.to_sql("ff_owners", conn, if_exists="append", index=False)
        out.append(summary)
    conn.commit()
    return pd.concat(out), entities
