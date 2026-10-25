"""Smoke test dashboard: setiap halaman dirender tanpa exception di atas data sintetis."""
import sys
import time
from pathlib import Path

import pytest
import yaml
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from test_pipeline import env  # noqa: E402,F401  (fixture: DB sintetis + hasil analisis)

PAGES = ["dashboard/pages/ringkasan.py", "dashboard/pages/saham.py", "dashboard/pages/kanal.py",
         "dashboard/pages/pipeline.py", "dashboard/pages/kelola.py"]


@pytest.fixture
def app(env, tmp_path, monkeypatch):
    _, cfg = env
    raw = dict(cfg)
    raw["paths"] = {k: str(v) for k, v in cfg["paths"].items()}
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    monkeypatch.setenv("SOROT_CONFIG", str(path))
    import streamlit as st
    st.cache_data.clear()
    return AppTest.from_file(str(ROOT / "app.py"), default_timeout=60)


def errors(at):
    return [e.value for e in at.exception]


@pytest.mark.parametrize("page", PAGES)
def test_page_renders(app, page):
    app.run()
    app.switch_page(page).run()
    assert errors(app) == []


def test_overview_numbers(app):
    app.run()
    metrics = {m.label: m.value for m in app.metric}
    assert metrics["Pesan"] == "23"
    assert metrics["Kanal"] == "3"
    assert metrics["Jam burst"] == "1"


def test_stock_page_for_burst_ticker(app):
    app.session_state["ticker"] = "BUMI"
    app.run()
    app.switch_page("dashboard/pages/saham.py").run()
    assert errors(app) == []
    metrics = {m.label: m.value for m in app.metric}
    assert metrics["Pesan"] == "6"
    assert metrics["Kanal"] == "3"
    assert metrics["Burst ≥2 kanal"] == "1"


def test_channel_profile(app):
    app.session_state["channel"] = "a"
    app.session_state["kanal_view_next"] = "Profil kanal"
    app.run()
    app.switch_page("dashboard/pages/kanal.py").run()
    assert errors(app) == []
    assert {m.label: m.value for m in app.metric}["Pesan"] == "18"


def test_job_runner_captures_output():
    from dashboard import jobs
    job = jobs.start("tes", [["-c", "print('halo dari job')"]])
    for _ in range(100):
        if not job.running:
            break
        time.sleep(0.1)
    assert job.returncode == 0
    assert "halo dari job" in job.tail()
    assert "Selesai." in job.tail()
    with pytest.raises(RuntimeError):
        jobs.start("a", [["-c", "import time; time.sleep(2)"]]) and jobs.start("b", [["-c", "pass"]])
    jobs.stop()
