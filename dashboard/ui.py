"""Helper tampilan bersama."""
import pandas as pd
import streamlit as st

from dashboard import data


def fmt_int(v):
    try:
        return f"{int(v):,}".replace(",", ".")
    except (TypeError, ValueError):
        return "–"


def fmt_ratio(v):
    return f"{v:.2f}×" if v is not None and pd.notna(v) else "–"


def fmt_wib(ts, with_time=True):
    if ts is None or pd.isna(ts):
        return "–"
    return ts.strftime("%d %b %Y %H.%M WIB" if with_time else "%d %b %Y")


def require_db():
    """Hentikan halaman dengan pesan jelas bila data belum ada."""
    if not data.db_exists():
        st.warning("Belum ada data. Buka **Jalankan pipeline** untuk mengumpulkan pesan, atau jalankan "
                   "`python collect.py` dari terminal.")
        st.page_link("dashboard/pages/pipeline.py", label="Ke Jalankan pipeline", icon=":material/play_circle:")
        st.stop()


def require_analysis(*tables):
    missing = [t for t in tables if not data.has(t)]
    if missing:
        st.info(f"Tabel analisis belum ada ({', '.join(missing)}). Jalankan langkah analisis di "
                "**Jalankan pipeline** terlebih dulu.")
        st.page_link("dashboard/pages/pipeline.py", label="Ke Jalankan pipeline", icon=":material/play_circle:")
        st.stop()


def ticker_label(row):
    name = f" · {row['nama']}" if row.get("nama") else ""
    return f"{row['ticker']}{name} ({fmt_int(row['mention'])})"


def open_ticker(ticker):
    st.session_state["ticker"] = ticker
    st.switch_page("dashboard/pages/saham.py")


def open_channel(channel):
    st.session_state["channel"] = channel
    st.session_state["kanal_view_next"] = "Profil kanal"
    st.switch_page("dashboard/pages/kanal.py")
