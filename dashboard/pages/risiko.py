import json

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from analysis.translog import canonical, leaf_hash, verify_inclusion
from dashboard import charts, data, ui

st.title("Label risiko")
st.caption("Skor 0–100 dari empat komponen: free float efektif, penghindaran ambang 1%, dan promosi terkoordinasi di "
           "Telegram. Setiap poin punya alasan tertulis. Setiap label dicatat di transparency log berbasis Merkle tree, "
           "sehingga perubahan diam-diam akan terdeteksi saat audit.")

labels = data.risk_labels()
if labels.empty:
    st.info("Belum ada label risiko. Jalankan **Kepemilikan & risiko** di halaman Jalankan pipeline "
            "(atau `python ownership.py all`).")
    st.page_link("dashboard/pages/pipeline.py", label="Ke Jalankan pipeline", icon=":material/play_circle:")
    st.stop()

COLOR = {"Tinggi": charts.RED, "Sedang": charts.AMBER, "Rendah": charts.TEAL}
tlog = data.transparency_log()
if "risk_open" in st.session_state:
    st.session_state["risk_ticker"] = st.session_state.pop("risk_open")
    st.session_state["risk_view"] = "Per emiten"
view = st.segmented_control("Tampilan", ["Peringkat", "Per emiten", "Audit log"], default="Peringkat", key="risk_view",
                            label_visibility="collapsed") or "Peringkat"

if view == "Peringkat":
    counts = labels["label"].value_counts()
    k = st.columns(4)
    for col, lab in zip(k, ["Tinggi", "Sedang", "Rendah"]):
        col.metric(lab, ui.fmt_int(counts.get(lab, 0)))
    k[3].metric("Dihitung (WIB)", pd.Timestamp(labels["created_at"].iat[0]).tz_convert("Asia/Jakarta").strftime("%d %b %H.%M"))
    lab_sel = st.pills("Label", ["Tinggi", "Sedang", "Rendah"], default=["Tinggi", "Sedang"], selection_mode="multi",
                       key="risk_lab")
    v = labels[labels["label"].isin(lab_sel)] if lab_sel else labels
    v = v.assign(alasan_utama=v["reasons"].map(lambda r: max(json.loads(r), key=lambda x: x["points"])["text"]))
    sel = st.dataframe(v[["ticker", "issuer_name", "score", "label", "ff_effective", "alasan_utama"]], hide_index=True,
                       use_container_width=True, height=520, on_select="rerun", selection_mode="single-row", key="risk_tbl",
                       column_config={"ticker": "Ticker", "issuer_name": "Emiten",
                                      "score": st.column_config.ProgressColumn("Skor", min_value=0, max_value=100, format="%d"),
                                      "label": "Label", "ff_effective": st.column_config.NumberColumn("FF efektif", format="%.2f%%"),
                                      "alasan_utama": st.column_config.TextColumn("Alasan utama", width="large")})
    st.caption("Pilih baris untuk melihat semua alasan dan bukti log-nya.")
    if sel.selection.rows:
        st.session_state["risk_open"] = v.iloc[sel.selection.rows[0]]["ticker"]
        st.rerun()

if view == "Per emiten":
    tickers = labels["ticker"].tolist()
    ticker = st.selectbox("Emiten", tickers, key="risk_ticker",
                          format_func=lambda t: f"{t} · {labels.set_index('ticker').at[t, 'issuer_name'] or ''}")
    row = labels.set_index("ticker").loc[ticker]
    reasons = json.loads(row["reasons"])
    c1, c2 = st.columns([1, 2])
    with c1:
        st.markdown(f"<div style='font-size:3rem;font-weight:700;color:{COLOR[row['label']]}'>{int(row['score'])}</div>"
                    f"<div style='font-size:1.2rem'>Risiko <b>{row['label']}</b></div>", unsafe_allow_html=True)
        if pd.notna(row["ff_effective"]):
            st.metric("Free float efektif", f"{row['ff_effective']:.2f}%", help=f"Proksi: {row['ff_proxy']:.2f}%")
        b1, b2 = st.columns(2)
        if b1.button("Kepemilikan", icon=":material/account_tree:", use_container_width=True):
            ui.open_freefloat(ticker)
        if b2.button("Telegram", icon=":material/forum:", use_container_width=True):
            ui.open_ticker(ticker)
    with c2:
        st.markdown("**Mengapa skor ini**")
        for r in reasons:
            with st.container(border=True):
                st.markdown(f"**+{r['points']}** · `{r['code']}`  \n{r['text']}")
    if len(reasons) > 1 or reasons[0]["points"]:
        fig = go.Figure(go.Bar(x=[r["points"] for r in reasons], y=[r["code"] for r in reasons], orientation="h",
                               marker_color=COLOR[row["label"]]))
        fig.update_layout(height=60 + 28 * len(reasons), margin=dict(l=8, r=8, t=8, b=8))
        st.plotly_chart(fig, use_container_width=True)

    st.markdown("**Bukti di transparency log**")
    entries = tlog.entries()
    idx = int(row["log_index"])
    if idx >= len(entries):
        st.error("Entri log untuk label ini tidak ditemukan. Log mungkin terhapus atau tidak lengkap.")
    else:
        entry = entries[idx]
        heads = tlog.heads()
        head = heads[-1]
        lh = leaf_hash(canonical(entry["entry"]))
        proof = [bytes.fromhex(x) for x in tlog.proof(idx, head["size"])]
        ok = verify_inclusion(lh, idx, head["size"], proof, bytes.fromhex(head["root"]))
        same = entry["entry"]["score"] == int(row["score"]) and entry["entry"]["label"] == row["label"]
        (st.success if ok and same else st.error)(
            f"Entri #{idx} {'tercatat' if ok else 'TIDAK cocok'} di pohon berukuran {head['size']} dengan root "
            f"`{head['root'][:16]}…`" + ("" if same else " — skor di database berbeda dari log!"))
        with st.expander("Rincian kriptografis (bisa diverifikasi ulang secara independen)"):
            st.markdown(f"- Leaf hash: `{lh.hex()}`\n- Indeks: {idx}, ukuran pohon: {head['size']}\n"
                        f"- Root: `{head['root']}` ({head['timestamp']})")
            st.code("\n".join(p.hex() for p in proof) or "(pohon satu daun)", language=None)
            st.json(entry["entry"], expanded=False)

if view == "Audit log":
    ok, problems = tlog.audit()
    entries, heads = tlog.entries(), tlog.heads()
    (st.success if ok else st.error)(
        f"{'Log utuh' if ok else 'Log TIDAK utuh'}: {len(entries)} entri, {len(heads)} tree head diperiksa ulang "
        "dari awal (leaf hash, root setiap head, dan consistency proof antar head).")
    for p in problems:
        st.error(p)
    st.markdown("Tree head disimpan di `transparency/heads.jsonl` yang ikut di-commit ke git. Root yang sudah pernah "
                "dipublikasikan menjadi jangkar: log yang diubah tidak akan menghasilkan root yang sama, dan log yang hanya "
                "ditambah harus lolos consistency proof terhadap head sebelumnya.")
    if heads:
        hv = pd.DataFrame(heads)
        hv["consistency"] = hv.get("consistency", pd.Series([None] * len(hv))).map(
            lambda c: f"{len(c)} hash" if isinstance(c, list) else "–")
        st.dataframe(hv[["timestamp", "size", "prev_size", "root", "consistency"]], hide_index=True, use_container_width=True,
                     column_config={"timestamp": "Waktu", "size": "Ukuran", "prev_size": "Ukuran sebelumnya",
                                    "root": st.column_config.TextColumn("Root", width="large"), "consistency": "Bukti konsistensi"})
    st.code("python ownership.py verify", language="bash")
