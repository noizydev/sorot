from datetime import date, datetime

import streamlit as st

from collect import load_channels
from dashboard import data, jobs, ui

st.title("Jalankan pipeline")
ROOT = data.ROOT
cfg = data.config()

# ---------- status ----------
env_ok = (ROOT / ".env").exists()
session_name = "sorot"
try:
    from dotenv import dotenv_values
    session_name = dotenv_values(ROOT / ".env").get("TG_SESSION") or "sorot"
except Exception:
    pass
session_ok = (ROOT / f"{session_name}.session").exists()
s = data.overview() if data.db_exists() else {}

c = st.columns(4)
c[0].metric("Kredensial (.env)", "ada" if env_ok else "belum ada")
c[1].metric("Session Telegram", "sudah login" if session_ok else "belum login")
c[2].metric("Pesan tersimpan", ui.fmt_int(s.get("messages")))
c[3].metric("Pengambilan terakhir", data.to_wib([s["collected"]])[0].strftime("%d %b %H.%M") if s.get("collected") else "–",
            help="Waktu WIB pesan terakhir disimpan")

if not (env_ok and session_ok):
    st.warning(
        "Login Telegram (OTP) harus dilakukan sekali dari terminal karena butuh kode yang dikirim ke HP:\n\n"
        "1. Salin `.env.example` ke `.env` dan isi `TG_API_ID` / `TG_API_HASH`.\n"
        "2. Jalankan `.venv/Scripts/python collect.py --channel wigunainvestment --limit 5` lalu masukkan nomor HP "
        "dan kode OTP.\n\nSetelah file session ada, tombol pengumpulan di bawah bisa dipakai.")


# ---------- job yang sedang/terakhir berjalan ----------
@st.fragment(run_every=2)
def job_panel():
    job = jobs.current() or jobs.last()
    if job is None:
        st.caption("Belum ada proses yang dijalankan dari dashboard pada sesi server ini.")
        return
    elapsed = (job.finished or datetime.now().timestamp()) - job.started
    if job.running:
        state, label = "running", f"Menjalankan **{job.name}**"
    elif job.returncode == 0:
        state, label = "complete", f"**{job.name}** selesai"
    else:
        state, label = "error", f"**{job.name}** berhenti (kode {job.returncode})"
    with st.status(label, state=state, expanded=True):
        st.caption(f"Langkah {job.current + 1}/{len(job.steps)} · {elapsed:,.0f} detik · log: "
                   f"{job.log_path.relative_to(data.ROOT).as_posix()}")
        st.code(job.tail(120) or "(belum ada keluaran)", language=None)
        if job.running and st.button("Hentikan proses", icon=":material/stop_circle:", key="stop_job"):
            jobs.stop()
    if not job.running and st.session_state.get("_last_refreshed") != job.started:
        st.session_state["_last_refreshed"] = job.started
        st.cache_data.clear()  # data baru: buang cache query
        st.rerun(scope="app")


busy = jobs.current() is not None


def run(name, steps):
    try:
        jobs.start(name, steps)
        st.toast(f"{name} dimulai")
        st.rerun()
    except RuntimeError as e:
        st.error(str(e))


st.subheader("Proses")
job_panel()

# ---------- aksi ----------
t1, t2, t3, t4 = st.tabs(["1 · Kumpulkan pesan", "2 · Perbarui harga", "3 · Analisis", "4 · Laporan riset"])

with t1:
    st.markdown("Mengambil pesan baru dari kanal publik di `channels.txt` (incremental, hanya membaca).")

    class _A:  # load_channels butuh objek mirip argparse
        channel = None
        channels = str(ROOT / "channels.txt")

    try:
        all_channels = load_channels(_A)
    except FileNotFoundError:
        all_channels = []
    with st.form("collect_form", border=False):
        a, b, c3 = st.columns([1, 1, 2])
        since = a.date_input("Sejak (untuk kanal baru)", value=date(2026, 4, 1))
        limit = b.number_input("Batas pesan per kanal", min_value=0, value=0, step=100, help="0 = tanpa batas")
        subset = c3.multiselect("Hanya kanal tertentu (kosong = semua)", all_channels)
        go = st.form_submit_button("Kumpulkan pesan", type="primary", disabled=busy or not (env_ok and session_ok),
                                   icon=":material/download:")
    if go:
        argv = ["collect.py", "--since", since.isoformat()]
        if limit:
            argv += ["--limit", str(int(limit))]
        for ch in subset:
            argv += ["--channel", ch]
        run("kumpulkan", [argv])

with t2:
    st.markdown("Mengunduh harga harian semua ticker di `idx_tickers.csv` dari Yahoo Finance dan menimpa `prices.csv`.")
    n_tickers = len(data.tickers())
    st.caption(f"{n_tickers} ticker · perkiraan {n_tickers * 1.3 / 60:.0f} menit")
    with st.form("prices_form", border=False):
        frm = st.date_input("Mulai", value=date(2026, 1, 1),
                            help="Beri jarak ≥ 20 hari bursa sebelum pesan pertama untuk baseline volume")
        go = st.form_submit_button("Perbarui harga", type="primary", disabled=busy, icon=":material/show_chart:")
    if go:
        run("harga", [["fetch_prices.py", "--from", frm.isoformat()]])
    with st.expander("Bangun ulang daftar ticker dari pesan"):
        st.markdown("Memeriksa setiap token 4 huruf kapital yang sering muncul ke Yahoo Finance, lalu menulis "
                    "ulang `idx_tickers.csv` dan `prices.csv`. Tanda ambigu yang sudah ada dipertahankan. "
                    "Butuh sekitar 15 menit.")
        if st.button("Validasi ticker", disabled=busy, key="validate"):
            run("validasi_ticker", [["research/validate_tickers.py"]])

with t3:
    steps_all = ["mentions", "dedup", "graph", "bursts", "prices"]
    st.markdown("Membangun ulang tabel analisis di `messages.db` dengan parameter dari `config.yaml`.")
    with st.form("analyze_form", border=False):
        steps = st.pills("Langkah", steps_all, default=steps_all, selection_mode="multi")
        go = st.form_submit_button("Jalankan analisis", type="primary", disabled=busy or not data.db_exists(),
                                   icon=":material/analytics:")
    if go:
        ordered = [s for s in steps_all if s in (steps or [])]
        if not ordered:
            st.error("Pilih minimal satu langkah.")
        else:
            run("analisis", [["analyze.py", "--steps", ",".join(ordered)]])

with t4:
    st.markdown("Menghasilkan `reports/laporan_riset_sorot.html` (skor sinyal, uji volume, jaringan, studi kasus). "
                "Butuh hasil analisis lengkap.")
    if st.button("Bangun laporan riset", type="primary", disabled=busy or not data.has("bursts", "graph_edges"),
                 icon=":material/description:"):
        run("laporan_riset", [["research/research_data.py"], ["research/research_kinds.py"],
                              ["research/build_report.py"]])
    report = ROOT / "reports" / "laporan_riset_sorot.html"
    if report.exists():
        st.caption(f"Laporan terakhir: {datetime.fromtimestamp(report.stat().st_mtime):%d %b %Y %H.%M}")
        st.download_button("Unduh laporan HTML", report.read_bytes(), report.name, "text/html")

with st.expander("Log pengumpulan (logs/collect.log)"):
    log = ROOT / "logs" / "collect.log"
    st.code("\n".join(log.read_text(encoding="utf-8", errors="replace").splitlines()[-80:]) if log.exists()
            else "(belum ada)", language=None)
