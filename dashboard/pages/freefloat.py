import json

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from dashboard import charts, data, ui

st.title("Free float efektif")
st.caption("Sumber: daftar pemegang saham di atas 1% (KSEI) yang dipublikasikan BEI. Free float proksi = 100% dikurangi "
           "pemegang langsung ≥5%. Free float efektif juga mengurangi kelompok yang sama (nama berbeda untuk entitas "
           "yang sama, atau perusahaan tercatat yang dikendalikan pengendali yang sama) bila totalnya ≥5%.")

if not data.ohas("ff_summary"):
    st.info("Belum ada hasil free float. Jalankan **Kepemilikan & risiko** di halaman Jalankan pipeline "
            "(atau `python ownership.py all`).")
    st.page_link("dashboard/pages/pipeline.py", label="Ke Jalankan pipeline", icon=":material/play_circle:")
    st.stop()

dates = data.ownership_dates()
cfg = data.config()["ownership"]
if "ff_view_next" in st.session_state:
    st.session_state["ff_view"] = st.session_state.pop("ff_view_next")
view = st.segmented_control("Tampilan", ["Peringkat", "Per emiten", "Entitas"], default="Peringkat",
                            key="ff_view", label_visibility="collapsed") or "Peringkat"
vd = st.selectbox("Posisi per", dates[::-1], format_func=lambda d: pd.Timestamp(d).strftime("%d %b %Y"), key="ff_vd")
summary = data.ff_summary(vd)

if view == "Peringkat":
    k = st.columns(4)
    k[0].metric("Emiten", ui.fmt_int(len(summary)))
    k[1].metric(f"Efektif < {cfg['ff_min_pct']:g}%", ui.fmt_int((summary["ff_effective"] < cfg["ff_min_pct"]).sum()))
    k[2].metric("Selisih ≥ 5 poin", ui.fmt_int((summary["gap"] >= 5).sum()),
                help="Free float tampak lebih besar dari efektifnya karena kepemilikan kelompok yang terpecah")
    k[3].metric("Median efektif", f"{summary['ff_effective'].median():.1f}%")
    sort = st.radio("Urutkan", ["Selisih terbesar", "Efektif terendah"], horizontal=True, key="ff_sort")
    s = summary.sort_values("gap" if sort == "Selisih terbesar" else "ff_effective", ascending=sort != "Selisih terbesar")
    ev = st.dataframe(
        s[["ticker", "issuer_name", "ff_proxy", "ff_effective", "gap", "pct_ge1_total", "controller", "controller_stake",
           "n_split_groups"]], hide_index=True, use_container_width=True, height=520, on_select="rerun",
        selection_mode="single-row", key="ff_rank",
        column_config={"ticker": "Ticker", "issuer_name": "Emiten",
                       "ff_proxy": st.column_config.NumberColumn("FF proksi", format="%.2f%%"),
                       "ff_effective": st.column_config.ProgressColumn("FF efektif", format="%.2f%%", min_value=0, max_value=100),
                       "gap": st.column_config.NumberColumn("Selisih", format="%.2f"),
                       "pct_ge1_total": st.column_config.NumberColumn("Total ≥1%", format="%.2f%%"),
                       "controller": "Pengendali akhir",
                       "controller_stake": st.column_config.NumberColumn("Porsi terintegrasi", format="%.2f%%"),
                       "n_split_groups": st.column_config.NumberColumn("Kelompok terpecah", format="%d")})
    st.caption("Pilih baris untuk melihat rincian emiten.")
    if ev.selection.rows:
        st.session_state["ff_ticker"] = s.iloc[ev.selection.rows[0]]["ticker"]
        st.session_state["ff_view_next"] = "Per emiten"
        st.rerun()

if view == "Per emiten":
    tickers = summary["ticker"].tolist()
    preset = st.session_state.pop("ff_open", None)
    if preset in tickers:
        st.session_state["ff_ticker"] = preset
    ticker = st.selectbox("Emiten", tickers, key="ff_ticker",
                          format_func=lambda t: f"{t} · {summary.set_index('ticker').at[t, 'issuer_name']}")
    row = summary.set_index("ticker").loc[ticker]
    k = st.columns(5)
    k[0].metric("FF proksi", f"{row['ff_proxy']:.2f}%")
    k[1].metric("FF efektif", f"{row['ff_effective']:.2f}%", delta=f"{-row['gap']:.2f} poin" if row["gap"] else None)
    k[2].metric("Total pemegang ≥1%", f"{row['pct_ge1_total']:.2f}%", help="Sisa di bawah 1% tidak terlihat di data KSEI")
    k[3].metric("Porsi pengendali (terintegrasi)",
                f"{row['controller_stake']:.2f}%" if pd.notna(row["controller_stake"]) else "–")
    k[4].metric("Pemegang ≥1%", ui.fmt_int(row["n_holders"]))
    no_ctrl = f"tidak ada pemegang dengan porsi terintegrasi ≥ {cfg['control_min'] * 100:g}%"
    st.markdown(f"**Pengendali akhir:** {row['controller'] or no_ctrl}")

    groups = data.ff_groups(vd, ticker)
    for r in groups[groups["split"] == 1].itertuples():
        st.warning(r.reason, icon=":material/link:")

    left, right = st.columns([3, 2])
    with left:
        st.markdown("**Komposisi kepemilikan**")
        fig = go.Figure()
        for r in groups.itertuples():
            color = charts.RED if r.split else charts.AMBER if r.concentrated else charts.TEAL
            fig.add_trace(go.Bar(y=[""], x=[r.agg_pct], orientation="h", name=r.group_name, marker_color=color,
                                 hovertemplate=f"{r.group_name}: {r.agg_pct:.2f}%<extra></extra>", showlegend=False))
        fig.add_trace(go.Bar(y=[""], x=[max(0, 100 - groups["agg_pct"].sum())], orientation="h", name="< 1% (publik)",
                             marker_color="rgba(140,150,160,0.35)", hovertemplate="Di bawah 1%%: %{x:.2f}%<extra></extra>",
                             showlegend=False))
        fig.update_layout(barmode="stack", height=130, margin=dict(l=8, r=8, t=8, b=8), hovermode="closest")
        fig.update_xaxes(range=[0, 100], ticksuffix="%")
        st.plotly_chart(fig, use_container_width=True)
        st.caption("Merah: kelompok ≥5% yang tersembunyi karena dipecah. Oranye: pemegang/kelompok ≥5%. Teal: kelompok "
                   "<5% (dihitung free float). Abu: kepemilikan di bawah 1%.")
        st.markdown("**Kelompok pemegang**")
        gview = groups.assign(anggota=groups["members"].map(lambda m: ", ".join(f"{n} ({p:.2f}%)" for n, p in json.loads(m))))
        st.dataframe(gview[["group_name", "agg_pct", "n_rows", "concentrated", "split", "anggota"]], hide_index=True,
                     use_container_width=True,
                     column_config={"group_name": "Kelompok", "agg_pct": st.column_config.NumberColumn("Total", format="%.2f%%"),
                                    "n_rows": "Nama", "concentrated": st.column_config.CheckboxColumn("≥5%"),
                                    "split": st.column_config.CheckboxColumn("Terpecah"),
                                    "anggota": st.column_config.TextColumn("Anggota", width="large")})
    with right:
        st.markdown("**Pemilik akhir (kepemilikan terintegrasi)**")
        owners = data.ff_owners(vd, ticker)
        st.dataframe(owners[["owner", "integrated_pct", "direct_pct"]], hide_index=True, use_container_width=True,
                     column_config={"owner": "Pemilik", "integrated_pct": st.column_config.NumberColumn("Terintegrasi", format="%.2f%%"),
                                    "direct_pct": st.column_config.NumberColumn("Langsung", format="%.2f%%")})
        st.caption("Terintegrasi = langsung + lewat semua rantai perusahaan tercatat (W(I−W)⁻¹).")
        hist = data.ff_summary()
        hist = hist[hist["ticker"] == ticker]
        if len(hist) > 1:
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=hist["valid_date"], y=hist["ff_proxy"], name="Proksi", mode="lines+markers",
                                     line=dict(color=charts.GREY)))
            fig.add_trace(go.Scatter(x=hist["valid_date"], y=hist["ff_effective"], name="Efektif", mode="lines+markers",
                                     line=dict(color=charts.TEAL)))
            fig.add_hline(y=cfg["ff_min_pct"], line_dash="dot", line_color=charts.RED)
            fig.update_layout(height=260, margin=dict(l=8, r=8, t=28, b=8), legend=dict(orientation="h", y=1.1))
            fig.update_yaxes(ticksuffix="%")
            st.plotly_chart(fig, use_container_width=True)

    st.markdown("**Daftar pemegang ≥1% (KSEI)**")
    known = st.toggle("Lihat versi yang diketahui pada tanggal tertentu", key="ff_known_toggle",
                      help="Data per tanggal yang sama bisa dikoreksi BEI. Pilih waktu untuk melihat versi yang berlaku saat itu.")
    known_at = None
    if known:
        docs = data.documents()
        versions = docs[docs["valid_date"] == vd]["published_at"].tolist()
        known_at = st.select_slider("Diketahui per (waktu terbit BEI)", versions, value=versions[-1], key=f"ff_known_{vd}")
    h = data.holdings_at(vd, ticker, known_at)
    st.dataframe(h[["investor_name", "canonical_name", "investor_class", "local_foreign", "domicile", "total_shares", "pct",
                    "tx_from", "tx_to"]], hide_index=True, use_container_width=True,
                 column_config={"investor_name": "Nama di KSEI", "canonical_name": "Entitas", "investor_class": "Klasifikasi",
                                "local_foreign": "L/A", "domicile": "Domisili",
                                "total_shares": st.column_config.NumberColumn("Saham", format="%d"),
                                "pct": st.column_config.NumberColumn("%", format="%.2f"),
                                "tx_from": "Berlaku sejak", "tx_to": "Digantikan"})

if view == "Entitas":
    ents = data.entities_table()
    st.markdown("Hasil entity resolution: nama-nama di KSEI yang digabung menjadi satu entitas, dan entitas korporat "
                "yang dikenali sebagai emiten tercatat (dasar rantai kepemilikan).")
    k = st.columns(3)
    k[0].metric("Entitas", ui.fmt_int(len(ents)))
    k[1].metric("Digabung dari >1 nama", ui.fmt_int((ents["n_names"] > 1).sum()))
    k[2].metric("Terhubung ke emiten", ui.fmt_int(ents["ticker"].notna().sum()))
    q = st.text_input("Cari nama", key="ent_q", placeholder="mis. SAMATOR, THOHIR, DANANTARA").strip().upper()
    only = st.toggle("Hanya yang digabung atau terhubung ke emiten", value=not q, key="ent_only")
    e = ents
    if q:
        e = e[e["names"].str.upper().str.contains(q, regex=False)]
    if only:
        e = e[(e["n_names"] > 1) | e["ticker"].notna()]
    ev = st.dataframe(e[["canonical_name", "kind", "n_names", "ticker", "nominee", "names"]], hide_index=True,
                      use_container_width=True, height=420, on_select="rerun", selection_mode="single-row", key="ent_tbl",
                      column_config={"canonical_name": "Entitas", "kind": "Jenis", "n_names": "Nama",
                                     "ticker": "Emiten sendiri", "nominee": st.column_config.CheckboxColumn("Nominee"),
                                     "names": st.column_config.TextColumn("Variasi nama", width="large")})
    if ev.selection.rows:
        r = e.iloc[ev.selection.rows[0]]
        st.markdown(f"**Portofolio {r['canonical_name']}** (versi terbaru setiap tanggal)")
        st.dataframe(data.entity_portfolio(r["entity_id"]), hide_index=True, use_container_width=True,
                     column_config={"pct": st.column_config.NumberColumn("%", format="%.2f"),
                                    "total_shares": st.column_config.NumberColumn("Saham", format="%d")})
    st.caption("Penggabungan yang keliru bisa diperbaiki lewat entity_aliases.csv (kolom alias,canonical) lalu jalankan ulang.")
