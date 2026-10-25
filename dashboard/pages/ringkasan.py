import streamlit as st

from dashboard import charts, data, ui

st.title("Ringkasan")
ui.require_db()

s = data.overview()
first, last = data.to_wib([s["first"], s["last"]])
st.caption(f"Pesan {ui.fmt_wib(first, False)} – {ui.fmt_wib(last)} · pengambilan terakhir "
           f"{ui.fmt_wib(data.to_wib([s['collected']])[0])}")

cols = st.columns(5)
cols[0].metric("Pesan", ui.fmt_int(s["messages"]), help="Semua pesan tersimpan di messages.db")
cols[1].metric("Kanal", ui.fmt_int(s["channels"]))
cols[2].metric("Mention ticker", ui.fmt_int(s.get("mentions")), help=f"{ui.fmt_int(s.get('tickers'))} ticker berbeda")
cols[3].metric("Cluster lintas kanal", ui.fmt_int(s.get("dup_cross")), help="Pesan near-duplicate di ≥2 kanal")
cols[4].metric("Jam burst", ui.fmt_int(s.get("bursts")), help=f"{ui.fmt_int(s.get('bursts3'))} melibatkan ≥3 kanal")

left, right = st.columns([3, 2])
with left:
    st.subheader("Volume pesan per minggu")
    st.plotly_chart(charts.weekly_messages(data.weekly_messages()), use_container_width=True)
with right:
    st.subheader("Ticker paling sering disebut")
    tt = data.ticker_table()
    if tt.empty:
        st.info("Jalankan langkah analisis `mentions` untuk melihat ticker.")
    else:
        st.plotly_chart(charts.top_tickers(tt), use_container_width=True)

st.subheader("Burst lintas kanal terbaru")
if not data.has("bursts"):
    st.info("Belum ada tabel burst. Jalankan analisis di halaman **Jalankan pipeline**.")
else:
    min_ch = st.segmented_control("Minimal kanal", [2, 3, 4], default=2, key="ov_min_ch")
    b = data.bursts(min_channels=min_ch or 2)
    view = b.assign(
        waktu=b["jam_wib"].dt.strftime("%Y-%m-%d %H.00"),
        nama=b["ticker"].map(data.ticker_names()),
        vol_h1=b["volume_ratio_h1"] if "volume_ratio_h1" in b else None,
    )[["waktu", "ticker", "nama", "count", "n_channels", "z", "vol_h1"]]
    event = st.dataframe(
        view, hide_index=True, use_container_width=True, on_select="rerun", selection_mode="single-row",
        column_config={
            "waktu": "Jam (WIB)", "ticker": "Ticker", "nama": "Emiten", "count": "Pesan",
            "n_channels": "Kanal", "z": st.column_config.NumberColumn("z-score", format="%.1f"),
            "vol_h1": st.column_config.NumberColumn("Volume H+1", format="%.2f×",
                                                     help="Volume hari bursa berikutnya / rata-rata 20 hari"),
        })
    st.caption("Pilih satu baris untuk membuka saham tersebut.")
    if event.selection.rows:
        ui.open_ticker(view.iloc[event.selection.rows[0]]["ticker"])
