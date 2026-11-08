"""Dashboard SOROT. Jalankan dengan:

    .venv/Scripts/streamlit run app.py
"""
import streamlit as st

st.set_page_config(page_title="SOROT", page_icon="📡", layout="wide")

PAGES = {
    "Analisis": [
        st.Page("dashboard/pages/ringkasan.py", title="Ringkasan", icon=":material/dashboard:", default=True),
        st.Page("dashboard/pages/saham.py", title="Jelajah saham", icon=":material/candlestick_chart:"),
        st.Page("dashboard/pages/kanal.py", title="Jaringan & kanal", icon=":material/hub:"),
    ],
    "Kepemilikan & risiko": [
        st.Page("dashboard/pages/risiko.py", title="Label risiko", icon=":material/verified_user:"),
        st.Page("dashboard/pages/freefloat.py", title="Free float efektif", icon=":material/account_tree:"),
        st.Page("dashboard/pages/ambang.py", title="Ambang 1%", icon=":material/stacked_line_chart:"),
    ],
    "Operasional": [
        st.Page("dashboard/pages/pipeline.py", title="Jalankan pipeline", icon=":material/play_circle:"),
        st.Page("dashboard/pages/kelola.py", title="Kelola & ekspor", icon=":material/tune:"),
    ],
}

with st.sidebar:
    st.markdown("**SOROT**  \nDeteksi promosi saham terkoordinasi di kanal Telegram publik dan risiko kepemilikan "
                "dari data KSEI.")
    st.caption("Skor dan label adalah indikator statistik dari data publik untuk riset dan pengawasan, bukan tuduhan "
               "manipulasi dan bukan rekomendasi jual/beli saham.")

st.navigation(PAGES).run()
