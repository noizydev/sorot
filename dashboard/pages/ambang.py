import sqlite3

import plotly.graph_objects as go
import streamlit as st

from analysis import holdings as hold
from dashboard import charts, data, ui

st.title("Penghindaran ambang 1%")
st.caption("Setiap snapshot KSEI disimpan bitemporal: tanggal posisi (valid time) dan waktu terbit/koreksi BEI "
           "(transaction time). Pemegang yang hilang dari daftar tepat di bawah 1% tanpa bisa dijelaskan dilusi ditandai, "
           "lalu diperkuat bila muncul kembali, keluar serentak, atau diganti pemegang baru dekat ambang.")

if not data.ohas("holdings"):
    st.info("Belum ada data kepemilikan. Jalankan **Kepemilikan & risiko** di halaman Jalankan pipeline.")
    st.page_link("dashboard/pages/pipeline.py", label="Ke Jalankan pipeline", icon=":material/play_circle:")
    st.stop()

thr = data.config()["threshold"]
if "amb_view_next" in st.session_state:
    st.session_state["amb_view"] = st.session_state.pop("amb_view_next")
view = st.segmented_control("Tampilan", ["Kejadian", "Riwayat emiten", "Koreksi data (bitemporal)"],
                            default="Kejadian", key="amb_view", label_visibility="collapsed") or "Kejadian"
ev = data.threshold_events()

if view == "Kejadian":
    if ev.empty:
        ui.require_analysis("threshold_events")
    near = ev[ev["status"] == "tepat_di_bawah_ambang"]
    k = st.columns(4)
    k[0].metric("Pemegang keluar dari daftar", ui.fmt_int(len(ev)))
    k[1].metric("Tepat di bawah ambang", ui.fmt_int(len(near)), help=f"Porsi sebelumnya < {thr['threshold_pct'] + thr['near_band_pp']:g}%")
    k[2].metric("Dengan pola penguat", ui.fmt_int((near["score"] >= 2).sum()))
    k[3].metric("Terjelaskan dilusi", ui.fmt_int((ev["status"] == "terjelaskan_dilusi").sum()))
    f1, f2 = st.columns([2, 3])
    status = f1.multiselect("Status", ["tepat_di_bawah_ambang", "terjelaskan_dilusi", "keluar"],
                            default=["tepat_di_bawah_ambang"], key="amb_status")
    q = f2.text_input("Cari ticker/nama", key="amb_q").strip().upper()
    v = ev[ev["status"].isin(status)] if status else ev
    if q:
        v = v[v["ticker"].str.contains(q, regex=False) | v["name"].str.upper().str.contains(q, regex=False)]
    v = v.sort_values(["score", "pct0"], ascending=[False, True])
    sel = st.dataframe(v[["ticker", "name", "kind", "t0", "t1", "pct0", "score", "patterns", "reason"]], hide_index=True,
                       width="stretch", height=480, on_select="rerun", selection_mode="single-row", key="amb_tbl",
                       column_config={"ticker": "Ticker", "name": "Pemegang", "kind": "Jenis", "t0": "Terakhir tercatat",
                                      "t1": "Hilang per", "pct0": st.column_config.NumberColumn("Porsi terakhir", format="%.2f%%"),
                                      "score": st.column_config.NumberColumn("Skor", format="%d"), "patterns": "Pola",
                                      "reason": st.column_config.TextColumn("Penjelasan", width="large")})
    st.caption("Pilih baris untuk membuka riwayat emiten tersebut.")
    if sel.selection.rows:
        st.session_state["amb_ticker_next"] = v.iloc[sel.selection.rows[0]]["ticker"]
        st.session_state["amb_view_next"] = "Riwayat emiten"
        st.rerun()
    hov = data.threshold_hovering()
    if len(hov):
        with st.expander(f"{len(hov)} pemegang berulang kali 'parkir' tepat di atas 1% (1,00-1,10%)"):
            st.dataframe(hov, hide_index=True, width="stretch",
                         column_config={"n_dates": "Jumlah tanggal", "pcts": "Porsi per tanggal"})

if view == "Riwayat emiten":
    tickers = sorted(data.oq("SELECT DISTINCT ticker FROM holdings")["ticker"])
    if "amb_ticker_next" in st.session_state:
        st.session_state["amb_ticker"] = st.session_state.pop("amb_ticker_next")
    ticker = st.selectbox("Emiten", tickers, key="amb_ticker")
    hist = data.ticker_holding_history(ticker)
    fig = go.Figure()
    for name, g in hist.groupby("name"):
        fig.add_trace(go.Scatter(x=g["valid_date"], y=g["pct"], name=name, mode="lines+markers",
                                 hovertemplate=f"{name}: %{{y:.2f}}%<extra></extra>"))
    fig.add_hline(y=thr["threshold_pct"], line_dash="dot", line_color=charts.RED,
                  annotation_text=f"ambang {thr['threshold_pct']:g}%")
    fig.update_yaxes(type="log", title="porsi (%) skala log", ticksuffix="%")
    fig.update_layout(height=460, margin=dict(l=8, r=8, t=28, b=8), legend=dict(orientation="h", y=-0.15))
    st.plotly_chart(fig, width="stretch")
    st.caption("Garis yang berhenti sebelum tanggal terakhir berarti pemegang itu tidak lagi tercatat (di bawah 1% atau menjual).")
    te = ev[ev["ticker"] == ticker] if len(ev) else ev
    if len(te):
        st.dataframe(te[["name", "t0", "t1", "pct0", "status", "patterns", "reason"]], hide_index=True,
                     width="stretch",
                     column_config={"pct0": st.column_config.NumberColumn("Porsi terakhir", format="%.2f%%"),
                                    "reason": st.column_config.TextColumn("Penjelasan", width="large")})
    pivot = hist.pivot_table(index="name", columns="valid_date", values="pct", aggfunc="sum")
    st.dataframe(pivot.sort_values(pivot.columns[-1], ascending=False, na_position="last").style.format("{:.2f}", na_rep="-"),
                 width="stretch")

if view == "Koreksi data (bitemporal)":
    docs = data.documents()
    st.markdown("**Dokumen sumber**: setiap baris adalah satu versi yang diterbitkan BEI. Data per tanggal yang sama bisa "
                "dikoreksi beberapa kali; versi lama tetap tersimpan dan hanya diberi waktu berakhir.")
    st.dataframe(docs[["valid_date", "published_at", "announcement_no", "corrects", "rows", "file", "note"]],
                 hide_index=True, width="stretch",
                 column_config={"valid_date": "Posisi per", "published_at": "Terbit", "announcement_no": "Nomor pengumuman",
                                "corrects": "Mengoreksi", "rows": "Baris", "file": "File", "note": "Catatan"})
    multi = docs["valid_date"].value_counts()
    multi = sorted(multi[multi > 1].index)
    if not multi:
        st.caption("Belum ada tanggal posisi yang dikoreksi.")
    else:
        vd = st.selectbox("Lihat perubahan antar versi untuk posisi per", multi, key="amb_diff_vd")
        conn = sqlite3.connect(data.ownership_path())
        try:
            diff = hold.diff_versions(conn, vd)
        finally:
            conn.close()
        st.caption(f"{len(diff)} baris berubah antar versi.")
        st.dataframe(diff, hide_index=True, width="stretch", height=420,
                     column_config={"total_shares_lama": st.column_config.NumberColumn("Saham (lama)", format="%d"),
                                    "total_shares_baru": st.column_config.NumberColumn("Saham (baru)", format="%d"),
                                    "pct_lama": st.column_config.NumberColumn("% lama", format="%.2f"),
                                    "pct_baru": st.column_config.NumberColumn("% baru", format="%.2f")})
