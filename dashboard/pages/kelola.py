import io
import shutil
import zipfile

import pandas as pd
import streamlit as st
import yaml

from collect import normalize_username
from dashboard import data, ui

st.title("Kelola & ekspor")
ROOT = data.ROOT
cfg = data.config()

t_ch, t_tk, t_cfg, t_exp = st.tabs(["Kanal", "Ticker", "Parameter", "Ekspor"])


def backup(path):
    if path.exists():
        shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))


# ---------- kanal ----------
with t_ch:
    path = ROOT / "channels.txt"
    st.markdown("Daftar kanal untuk `collect.py`. Kanal nonaktif ditulis sebagai komentar (`# nama`) "
                "sehingga tidak diambil tetapi tetap tercatat.")
    rows = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            raw = line.strip()
            active = not raw.startswith("#")
            body = raw.lstrip("#").strip()
            try:
                name = normalize_username(body) if body else None
            except ValueError:
                name = None
            if name:
                rows.append({"username": name, "aktif": active})
    df = pd.DataFrame(rows or [{"username": "", "aktif": True}])
    stats = data.channel_table().set_index("channel") if data.db_exists() else pd.DataFrame()
    df["pesan"] = df["username"].str.lower().map(stats["pesan"]) if len(stats) else None
    df["terakhir"] = df["username"].str.lower().map(
        data.to_wib(stats["terakhir"]).dt.strftime("%Y-%m-%d")) if len(stats) else None
    edited = st.data_editor(
        df, num_rows="dynamic", use_container_width=True, hide_index=True, key="ed_channels",
        column_config={"username": st.column_config.TextColumn("Username", required=True,
                                                               help="Tanpa @; t.me/nama juga diterima"),
                       "aktif": st.column_config.CheckboxColumn("Aktif", default=True),
                       "pesan": st.column_config.NumberColumn("Pesan tersimpan", disabled=True),
                       "terakhir": st.column_config.TextColumn("Pesan terakhir", disabled=True)},
        disabled=["pesan", "terakhir"])
    if st.button("Simpan channels.txt", type="primary", key="save_ch"):
        clean, bad, seen = [], [], set()
        for r in edited.to_dict("records"):
            if not str(r.get("username") or "").strip():
                continue
            try:
                name = normalize_username(str(r["username"]))
            except ValueError as e:
                bad.append(f"{r['username']}: {e}")
                continue
            if name and name.lower() not in seen:
                seen.add(name.lower())
                clean.append(name if r.get("aktif", True) else f"# {name}")
        if bad:
            st.error("Tidak disimpan. Perbaiki baris berikut:\n\n" + "\n".join(f"- {b}" for b in bad))
        else:
            backup(path)
            header = "# Kanal publik saham IDX. Satu username per baris; baris diawali # diabaikan.\n"
            path.write_text(header + "\n".join(clean) + "\n", encoding="utf-8")
            st.success(f"Tersimpan {len(clean)} kanal ({sum(not c.startswith('#') for c in clean)} aktif). "
                       f"Cadangan: channels.txt.bak")

# ---------- ticker ----------
with t_tk:
    path = cfg["paths"]["tickers_csv"]
    st.markdown("Daftar kode saham untuk ekstraksi. Centang **Ambigu** untuk kode yang juga kata umum: kode itu "
                "hanya dihitung bila ditulis dengan `$`/`#` atau dekat kata konteks (TP, target, entry, dll.).")
    tk = data.tickers().copy()
    if data.has("mentions"):
        tk = tk.merge(data.ticker_table()[["ticker", "mention"]], on="ticker", how="left")
    search = st.text_input("Cari", placeholder="kode atau nama emiten", key="tk_search").strip().upper()
    shown = tk[tk["ticker"].str.contains(search) | tk["name"].str.upper().str.contains(search)] if search else tk
    st.caption(f"{len(shown)} dari {len(tk)} kode · {int(tk['ambiguous'].sum())} ditandai ambigu")
    edited = st.data_editor(
        shown, num_rows="dynamic", use_container_width=True, hide_index=True, key=f"ed_tickers_{search}",
        column_config={"ticker": st.column_config.TextColumn("Kode", required=True, max_chars=4),
                       "name": st.column_config.TextColumn("Nama emiten"),
                       "ambiguous": st.column_config.CheckboxColumn("Ambigu"),
                       "mention": st.column_config.NumberColumn("Mention", disabled=True)},
        disabled=["mention"])
    if st.button("Simpan idx_tickers.csv", type="primary", key="save_tk"):
        e = edited.copy()
        e["ticker"] = e["ticker"].fillna("").astype(str).str.strip().str.upper()
        e = e[e["ticker"] != ""]
        invalid = e[~e["ticker"].str.fullmatch(r"[A-Z]{4}")]["ticker"].tolist()
        if invalid:
            st.error(f"Kode harus 4 huruf: {', '.join(invalid)}")
        else:
            rest = tk[~tk["ticker"].isin(shown["ticker"])]  # baris di luar filter tidak berubah
            merged = pd.concat([rest, e]).drop_duplicates("ticker", keep="last").sort_values("ticker")
            out = merged[["ticker", "name"]].copy()
            out["ambiguous"] = merged["ambiguous"].fillna(False).map(lambda v: "1" if bool(v) else "")
            backup(path)
            out.to_csv(path, index=False, encoding="utf-8")
            st.success(f"Tersimpan {len(out)} kode. Jalankan ulang analisis `mentions` agar perubahan berlaku.")

# ---------- parameter ----------
with t_cfg:
    path = ROOT / "config.yaml"
    st.markdown("Edit `config.yaml` langsung (komentar tetap terjaga). Isi divalidasi sebelum disimpan; versi "
                "lama disalin ke `config.yaml.bak`.")
    text = st.text_area("config.yaml", path.read_text(encoding="utf-8"), height=520, key="cfg_text",
                        label_visibility="collapsed")
    REQUIRED = {"paths": ["db", "tickers_csv", "prices_csv"],
                "tickers": ["context_window", "context_words"],
                "dedup": ["shingle_size", "threshold", "num_perm", "min_chars"],
                "coposting": ["window_minutes", "normalization"],
                "bursts": ["window_days", "z_threshold", "min_count", "min_std"],
                "prices": ["baseline_days", "utc_offset_hours"]}

    def validate(src):
        try:
            parsed = yaml.safe_load(src)
        except yaml.YAMLError as e:
            return [f"YAML tidak valid: {e}"]
        if not isinstance(parsed, dict):
            return ["Isi harus berupa mapping YAML."]
        errs = [f"Kunci `{sec}.{k}` hilang" for sec, keys in REQUIRED.items() for k in keys
                if not isinstance(parsed.get(sec), dict) or k not in parsed[sec]]
        if not errs:
            d = parsed["dedup"]
            if not 0 < float(d["threshold"]) <= 1:
                errs.append("dedup.threshold harus di antara 0 dan 1")
            if parsed["coposting"]["normalization"] not in ("sqrt", "min"):
                errs.append("coposting.normalization harus `sqrt` atau `min`")
            for sec, key in [("bursts", "z_threshold"), ("bursts", "window_days"), ("prices", "baseline_days"),
                             ("coposting", "window_minutes"), ("tickers", "context_window")]:
                if not isinstance(parsed[sec][key], (int, float)) or parsed[sec][key] <= 0:
                    errs.append(f"{sec}.{key} harus angka positif")
        return errs

    a, b = st.columns([1, 5])
    if a.button("Simpan", type="primary", key="save_cfg"):
        errs = validate(text)
        if errs:
            st.error("Tidak disimpan:\n\n" + "\n".join(f"- {e}" for e in errs))
        else:
            backup(path)
            path.write_text(text, encoding="utf-8")
            st.success("config.yaml tersimpan. Jalankan ulang analisis agar parameter baru dipakai.")
    if b.button("Periksa saja", key="check_cfg"):
        errs = validate(text)
        st.error("\n".join(f"- {e}" for e in errs)) if errs else st.success("Konfigurasi valid.")

# ---------- ekspor ----------
with t_exp:
    if not data.db_exists():
        st.info("Belum ada data untuk diekspor.")
        st.stop()
    st.markdown("Unduh hasil analisis sebagai CSV. Teks pesan di database sudah disamarkan (telepon/email).")
    s = data.overview()
    first, last = data.to_wib([s["first"], s["last"]])
    c1, c2 = st.columns(2)
    period = c1.date_input("Periode (WIB)", value=(first.date(), last.date()), key="exp_period")
    start, end = period if isinstance(period, tuple) and len(period) == 2 else (first.date(), last.date())
    tick = c2.multiselect("Batasi ke ticker (opsional)", data.ticker_table()["ticker"].tolist()
                          if data.has("mentions") else [], key="exp_tickers")
    lo = (pd.Timestamp(start) - data.WIB).isoformat()[:19]
    hi = (pd.Timestamp(end) + pd.Timedelta(days=1) - data.WIB).isoformat()[:19]
    tfilter = f" AND m.ticker IN ({','.join('?' * len(tick))})" if tick else ""

    datasets = {}
    if data.has("mentions"):
        datasets["mentions"] = ("Mention ticker per pesan", lambda: data.q(
            f"""SELECT m.ticker, m.channel, m.message_id, m.date, m.n_occurrences, m.prefixed, m.ambiguous,
                       g.views, g.forwards, g.text FROM mentions m
                JOIN messages g ON g.channel = m.channel AND g.message_id = m.message_id
                WHERE m.date >= ? AND m.date < ?{tfilter} ORDER BY m.date""", (lo, hi, *tick)))
    if data.has("bursts"):
        def _bursts():
            b = data.bursts()
            b = b[(b["jam_wib"].dt.date >= start) & (b["jam_wib"].dt.date <= end)]
            return b[b["ticker"].isin(tick)] if tick else b
        datasets["bursts"] = ("Burst + rasio volume", _bursts)
    if data.has("dup_clusters"):
        datasets["dup_clusters"] = ("Cluster near-duplicate", lambda: data.q(
            "SELECT * FROM dup_clusters WHERE last_date >= ? AND first_date < ? ORDER BY n_channels DESC",
            (lo, hi)))
        datasets["dup_members"] = ("Anggota cluster", lambda: data.q(
            "SELECT * FROM dup_members WHERE date >= ? AND date < ?", (lo, hi)))
    if data.has("graph_edges"):
        datasets["graph_edges"] = ("Edge graf kanal (seluruh periode)", lambda: data.q("SELECT * FROM graph_edges"))
    datasets["messages"] = ("Semua pesan (tanpa filter ticker)", lambda: data.q(
        "SELECT * FROM messages WHERE date >= ? AND date < ? ORDER BY channel, message_id", (lo, hi)))

    chosen = st.pills("Dataset", list(datasets), default=[k for k in ("mentions", "bursts") if k in datasets],
                      selection_mode="multi", format_func=lambda k: datasets[k][0], key="exp_sets")
    if chosen:
        frames = {k: datasets[k][1]() for k in chosen}
        st.dataframe(pd.DataFrame({"dataset": [datasets[k][0] for k in frames],
                                   "baris": [len(v) for v in frames.values()]}), hide_index=True)
        if len(frames) == 1:
            k, df_one = next(iter(frames.items()))
            st.download_button(f"Unduh {k}.csv", df_one.to_csv(index=False).encode("utf-8-sig"), f"{k}_{start}_{end}.csv",
                               "text/csv", type="primary")
        else:
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                for k, v in frames.items():
                    zf.writestr(f"{k}.csv", v.to_csv(index=False).encode("utf-8-sig"))
            st.download_button(f"Unduh {len(frames)} CSV (.zip)", buf.getvalue(), f"sorot_{start}_{end}.zip",
                               "application/zip", type="primary")

    st.divider()
    st.markdown("**File lain**")
    f1, f2, f3 = st.columns(3)
    gexf = cfg["paths"]["output_dir"] / "coposting.gexf"
    if gexf.exists():
        f1.download_button("Graf (GEXF untuk Gephi)", gexf.read_bytes(), gexf.name, "application/xml")
    report = ROOT / "reports" / "laporan_riset_sorot.html"
    if report.exists():
        f2.download_button("Laporan riset (HTML)", report.read_bytes(), report.name, "text/html")
    cand = ROOT / "channels_candidates.csv"
    if cand.exists():
        f3.download_button("Kandidat kanal (CSV)", cand.read_bytes(), cand.name, "text/csv")
