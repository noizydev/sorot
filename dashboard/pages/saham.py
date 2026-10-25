import sqlite3
from datetime import date

import pandas as pd
import streamlit as st

from analysis.graph import coposting_counts
from dashboard import charts, data, ui
from report import build_report

st.title("Jelajah saham")
ui.require_db()
ui.require_analysis("mentions")

tt = data.ticker_table()
if tt.empty:
    st.info("Belum ada mention ticker. Periksa idx_tickers.csv lalu jalankan analisis.")
    st.stop()

labels = {r["ticker"]: ui.ticker_label(r) for r in tt.to_dict("records")}
options = list(labels)
preset = st.session_state.pop("ticker", None) or st.query_params.get("ticker")
if preset in labels:
    st.session_state["saham_ticker"] = preset

c1, c2 = st.columns([2, 3])
ticker = c1.selectbox("Ticker", options, format_func=labels.get, key="saham_ticker",
                      help="Diurutkan dari yang paling sering disebut. Ketik untuk mencari.")
st.query_params["ticker"] = ticker

row = tt.set_index("ticker").loc[ticker]
first, last = data.to_wib([row["pertama"], row["terakhir"]])
lo, hi = first.date(), max(last.date(), date.today())
period = c2.date_input("Periode (WIB)", value=(first.date(), last.date()), min_value=lo, max_value=hi,
                       key=f"period_{ticker}")
start, end = (period if isinstance(period, tuple) and len(period) == 2 else (first.date(), last.date()))

msgs = data.ticker_messages(ticker, start, end)
bursts = data.bursts(ticker)
if len(bursts):
    bursts = bursts[(bursts["jam_wib"].dt.date >= start) & (bursts["jam_wib"].dt.date <= end)]
px = data.prices(ticker)
px = px[(px["date"].dt.date >= start) & (px["date"].dt.date <= end)]

st.markdown(f"### {ticker} <span style='font-weight:400;font-size:1rem;opacity:.7'>{row['nama']}</span>",
            unsafe_allow_html=True)
k = st.columns(5)
k[0].metric("Pesan", ui.fmt_int(len(msgs)))
k[1].metric("Kanal", ui.fmt_int(msgs["channel"].nunique()))
k[2].metric("Jam burst", ui.fmt_int(len(bursts)), help="Termasuk burst satu kanal")
k[3].metric("Burst ≥2 kanal", ui.fmt_int((bursts["n_channels"] >= 2).sum() if len(bursts) else 0))
share = (msgs["jenis"] == "ajakan/rekomendasi").mean() if len(msgs) else 0
k[4].metric("Porsi ajakan", f"{share:.0%}", help="Pesan berisi ajakan/rekomendasi (klasifikasi kata kunci)")

if px.empty:
    st.caption("Data harga belum ada untuk ticker ini. Jalankan **Perbarui harga** di halaman pipeline.")
st.plotly_chart(charts.ticker_timeline(msgs, px, bursts, data.config()["prices"]["baseline_days"]),
                use_container_width=True)

t_msg, t_burst, t_dup, t_pair, t_export = st.tabs(["Pesan", "Burst", "Near-duplicate", "Co-posting", "Ringkasan & unduh"])

with t_msg:
    f1, f2, f3 = st.columns([2, 2, 1])
    ch_sel = f1.multiselect("Kanal", sorted(msgs["channel"].unique()), key=f"msg_ch_{ticker}")
    kind_sel = f2.multiselect("Jenis pesan", ["laporan rutin", "berita", "ajakan/rekomendasi", "lainnya"],
                              key=f"msg_kind_{ticker}")
    only_burst = f3.toggle("Hanya jam burst", key=f"msg_burst_{ticker}")
    view = msgs
    if ch_sel:
        view = view[view["channel"].isin(ch_sel)]
    if kind_sel:
        view = view[view["jenis"].isin(kind_sel)]
    if only_burst and len(bursts):
        hours = set(bursts["jam_wib"])
        view = view[view["waktu_wib"].dt.floor("h").isin(hours)]
    st.dataframe(
        view.sort_values("waktu_wib", ascending=False)[["waktu_wib", "channel", "views", "jenis", "prefixed", "text"]],
        hide_index=True, use_container_width=True, height=460,
        column_config={"waktu_wib": st.column_config.DatetimeColumn("Waktu (WIB)", format="YYYY-MM-DD HH:mm"),
                       "channel": "Kanal", "views": st.column_config.NumberColumn("Views", format="%d"),
                       "jenis": "Jenis", "prefixed": st.column_config.CheckboxColumn("$/#"),
                       "text": st.column_config.TextColumn("Teks", width="large")})
    st.caption(f"{ui.fmt_int(len(view))} pesan. Nomor telepon dan email sudah disamarkan saat pengumpulan.")

with t_burst:
    if bursts.empty:
        st.info("Tidak ada burst pada periode ini.")
    else:
        bview = bursts.assign(waktu=bursts["jam_wib"].dt.strftime("%Y-%m-%d %H.00"))
        cols = ["waktu", "count", "n_channels", "baseline_mean", "z"] + \
            [c for c in ("h1_date", "volume_ratio_h1") if c in bview]
        ev = st.dataframe(bview[cols], hide_index=True, use_container_width=True, on_select="rerun",
                          selection_mode="single-row", key=f"burst_tbl_{ticker}",
                          column_config={"waktu": "Jam (WIB)", "count": "Pesan", "n_channels": "Kanal",
                                         "baseline_mean": st.column_config.NumberColumn("Baseline/jam", format="%.2f"),
                                         "z": st.column_config.NumberColumn("z", format="%.1f"),
                                         "h1_date": "H+1", "volume_ratio_h1": st.column_config.NumberColumn(
                                             "Volume H+1", format="%.2f×")})
        if ev.selection.rows:
            b = bview.iloc[ev.selection.rows[0]]
            st.markdown(f"**Pesan di jam {b['waktu']} WIB**")
            bm = data.burst_messages(ticker, b["hour"])
            for r in bm.itertuples():
                with st.container(border=True):
                    st.markdown(f"`{r.waktu_wib:%H.%M}` **@{r.channel}** · {ui.fmt_int(r.views)} views · _{r.jenis}_")
                    st.text(r.text)
        else:
            st.caption("Pilih satu burst untuk melihat pesan-pesannya.")

with t_dup:
    clusters, members = data.ticker_clusters(ticker)
    if clusters.empty:
        st.info("Tidak ada cluster near-duplicate yang memuat ticker ini.")
    else:
        cross = st.toggle("Hanya lintas kanal", value=True, key=f"dup_cross_{ticker}")
        cl = clusters[clusters["n_channels"] > 1] if cross else clusters
        st.caption(f"{len(cl)} cluster")
        for c in cl.head(30).itertuples():
            first_wib = data.to_wib([c.first_date])[0]
            with st.expander(f"#{c.cluster_id} · {c.size} pesan · {c.n_channels} kanal · rentang "
                             f"{c.span_minutes:,.0f} menit · mulai {first_wib:%d %b %H.%M}"):
                st.text((c.sample_text or "")[:600])
                mem = members[members["cluster_id"] == c.cluster_id]
                st.dataframe(mem[["waktu_wib", "channel", "views"]], hide_index=True, use_container_width=True,
                             column_config={"waktu_wib": st.column_config.DatetimeColumn("Waktu (WIB)",
                                                                                         format="YYYY-MM-DD HH:mm"),
                                            "channel": "Kanal", "views": "Views"})

with t_pair:
    window = data.config()["coposting"]["window_minutes"]
    ts = (msgs["waktu_wib"] - data.WIB).astype("int64") // 10**9
    pairs = coposting_counts(zip([ticker] * len(msgs), ts, msgs["channel"]), window)
    if not pairs:
        st.info(f"Tidak ada dua kanal yang menyebut {ticker} dalam jendela {window} menit.")
    else:
        pdf = pd.DataFrame([(a, b, n) for (a, b), n in pairs.most_common()], columns=["Kanal A", "Kanal B", "Kejadian"])
        st.dataframe(pdf, hide_index=True, use_container_width=True)
        st.caption(f"Jumlah pasangan pesan dari dua kanal berbeda yang menyebut {ticker} dalam {window} menit.")

with t_export:
    conn = sqlite3.connect(data.db_path())
    try:
        summary, rows = build_report(conn, data.config(), ticker, start, end)
    finally:
        conn.close()
    st.code(summary, language=None)
    stem = f"{ticker}_{start}_{end}"
    d1, d2 = st.columns(2)
    d1.download_button("Unduh CSV per pesan", rows.to_csv(index=False).encode("utf-8-sig"),
                       f"{stem}.csv", "text/csv", use_container_width=True)
    d2.download_button("Unduh ringkasan (.txt)", summary.encode("utf-8"), f"{stem}.txt", "text/plain",
                       use_container_width=True)
    st.caption("Catatan: report.py membaca tanggal sebagai UTC, sehingga batas hari bisa bergeser 7 jam dari tampilan WIB.")
