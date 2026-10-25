import streamlit as st

from dashboard import charts, data, ui

st.title("Jaringan & kanal")
ui.require_db()

VIEWS = ["Graf jaringan", "Profil kanal", "Semua kanal"]
if "kanal_view_next" in st.session_state:
    st.session_state["kanal_view"] = st.session_state.pop("kanal_view_next")
view = st.segmented_control("Tampilan", VIEWS, default=VIEWS[0], key="kanal_view", label_visibility="collapsed") or VIEWS[0]

if view == "Graf jaringan":
    if not data.has("graph_edges"):
        ui.require_analysis("graph_edges")
    c1, c2, c3 = st.columns([2, 2, 1])
    kinds = c1.pills("Jenis edge", ["coposting", "forward"], default=["coposting", "forward"], selection_mode="multi",
                     format_func={"coposting": "Co-posting", "forward": "Forward"}.get, key="net_kinds")
    e_all = data.edges()
    max_w = float(e_all["norm_weight"].max()) if len(e_all) else 1.0
    min_w = c2.slider("Bobot minimum (ternormalisasi)", 0.0, round(min(max_w, 0.5), 3), 0.0, step=0.005,
                      key="net_min_w", help="Sembunyikan edge lemah supaya struktur terlihat")
    external = c3.toggle("Sumber luar", key="net_ext",
                         help="Tampilkan grup premium/privat yang pesannya diteruskan ke kanal (belah ketupat abu)")
    nodes, edges = data.network(tuple(kinds or []), min_w, external)
    st.plotly_chart(charts.network(nodes, edges), use_container_width=True)
    st.caption("Ukuran titik = jumlah pesan, warna = komunitas (greedy modularity), tebal garis = kuantil bobot. "
               "Co-posting: dua kanal menyebut ticker yang sama dalam jendela waktu; bobot dinormalisasi "
               "terhadap jumlah pesan kanal.")

    a, b = st.columns(2)
    with a:
        st.markdown("**Co-posting terkuat**")
        cp = e_all[e_all["kind"] == "coposting"].sort_values("norm_weight", ascending=False).head(15)
        st.dataframe(cp[["source", "target", "raw_weight", "norm_weight"]], hide_index=True, use_container_width=True,
                     column_config={"source": "Kanal A", "target": "Kanal B",
                                    "raw_weight": st.column_config.NumberColumn("Kejadian", format="%d"),
                                    "norm_weight": st.column_config.NumberColumn("Bobot", format="%.3f")})
    with b:
        st.markdown("**Forward terbanyak**")
        fw = e_all[e_all["kind"] == "forward"].sort_values("raw_weight", ascending=False).head(15)
        st.dataframe(fw[["source", "target", "raw_weight"]], hide_index=True, use_container_width=True,
                     column_config={"source": "Sumber", "target": "Kanal penerus",
                                    "raw_weight": st.column_config.NumberColumn("Pesan", format="%d")})

ch_table = data.channel_table()

if view == "Profil kanal":
    channels = list(ch_table["channel"])
    preset = st.session_state.pop("channel", None)
    if preset in channels:
        st.session_state["kanal_sel"] = preset
    channel = st.selectbox("Kanal", channels, key="kanal_sel",
                           format_func=lambda c: f"@{c} ({ui.fmt_int(ch_table.set_index('channel').at[c, 'pesan'])} pesan)")
    info = ch_table.set_index("channel").loc[channel]
    msgs = data.channel_messages(channel)
    st.markdown(f"[t.me/{channel}](https://t.me/s/{channel}) · pratinjau publik")
    k = st.columns(5)
    k[0].metric("Pesan", ui.fmt_int(info["pesan"]))
    k[1].metric("Rata-rata views", ui.fmt_int(info["rata_views"]))
    k[2].metric("Forward masuk", ui.fmt_int(info["forward"]))
    k[3].metric("Mention ticker", ui.fmt_int(info.get("mention")))
    k[4].metric("Ticker berbeda", ui.fmt_int(info.get("ticker")))

    st.plotly_chart(charts.channel_daily(msgs), use_container_width=True)
    left, right = st.columns(2)
    with left:
        st.markdown("**Jenis pesan yang menyebut ticker**")
        kinds_count = data.channel_kinds(channel)
        if kinds_count.empty:
            st.caption("Belum ada mention.")
        else:
            st.plotly_chart(charts.kinds_bar(kinds_count), use_container_width=True)
        st.markdown("**Ticker teratas**")
        ct = data.channel_tickers(channel)
        ev = st.dataframe(ct.assign(nama=ct["ticker"].map(data.ticker_names())), hide_index=True,
                          use_container_width=True, on_select="rerun", selection_mode="single-row", key="ch_tickers",
                          column_config={"ticker": "Ticker", "nama": "Emiten", "mention": "Mention", "cashtag": "$/#"})
        if ev.selection.rows:
            ui.open_ticker(ct.iloc[ev.selection.rows[0]]["ticker"])
    with right:
        e = data.edges()
        st.markdown("**Sumber forward**")
        src = e[(e["kind"] == "forward") & (e["target"] == channel)].sort_values("raw_weight", ascending=False)
        if src.empty:
            st.caption("Tidak ada pesan diteruskan dari kanal lain.")
        else:
            st.dataframe(src[["source", "raw_weight"]], hide_index=True, use_container_width=True,
                         column_config={"source": "Sumber", "raw_weight": st.column_config.NumberColumn("Pesan", format="%d")})
        st.markdown("**Mitra co-posting**")
        cp = e[(e["kind"] == "coposting") & ((e["source"] == channel) | (e["target"] == channel))].copy()
        cp["mitra"] = cp["source"].where(cp["target"] == channel, cp["target"])
        st.dataframe(cp.sort_values("norm_weight", ascending=False)[["mitra", "raw_weight", "norm_weight"]].head(15),
                     hide_index=True, use_container_width=True,
                     column_config={"mitra": "Kanal", "raw_weight": st.column_config.NumberColumn("Kejadian", format="%d"),
                                    "norm_weight": st.column_config.NumberColumn("Bobot", format="%.3f")})

    st.markdown("**Pesan terbaru**")
    st.dataframe(msgs.head(200)[["waktu_wib", "views", "forwards", "fwd_from_name", "text"]], hide_index=True,
                 use_container_width=True, height=380,
                 column_config={"waktu_wib": st.column_config.DatetimeColumn("Waktu (WIB)", format="YYYY-MM-DD HH:mm"),
                                "views": "Views", "forwards": "Diteruskan", "fwd_from_name": "Forward dari",
                                "text": st.column_config.TextColumn("Teks", width="large")})

if view == "Semua kanal":
    view = ch_table.assign(pertama=data.to_wib(ch_table["pertama"]), terakhir=data.to_wib(ch_table["terakhir"]))
    ev = st.dataframe(view, hide_index=True, use_container_width=True, on_select="rerun", selection_mode="single-row",
                      key="ch_list",
                      column_config={"channel": "Kanal", "pesan": "Pesan",
                                     "pertama": st.column_config.DatetimeColumn("Pesan pertama", format="YYYY-MM-DD"),
                                     "terakhir": st.column_config.DatetimeColumn("Pesan terakhir", format="YYYY-MM-DD"),
                                     "rata_views": "Rata-rata views", "forward": "Forward masuk",
                                     "mention": "Mention", "ticker": "Ticker"})
    st.caption("Pilih baris untuk membuka profil kanal.")
    if ev.selection.rows:
        ui.open_channel(view.iloc[ev.selection.rows[0]]["channel"])
