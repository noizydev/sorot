"""Menjalankan skrip proyek sebagai proses latar belakang dari dashboard.

Hanya satu job berjalan pada satu waktu. Status disimpan di level modul, jadi bertahan
selama server Streamlit hidup; log ditulis ke logs/dashboard_<job>.log.
"""
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "logs"
PY = sys.executable


@dataclass
class Job:
    name: str
    steps: list                       # list of argv lists
    log_path: Path
    started: float = field(default_factory=time.time)
    finished: float | None = None
    returncode: int | None = None
    current: int = 0
    proc: subprocess.Popen | None = None
    stop_requested: bool = False

    @property
    def running(self):
        return self.finished is None

    def tail(self, lines=200):
        if not self.log_path.exists():
            return ""
        text = self.log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(text[-lines:])


_lock = threading.Lock()
_jobs: dict[str, Job] = {}
_last: list[Job] = []


def current():
    """Job yang sedang berjalan, atau None."""
    with _lock:
        return next((j for j in _jobs.values() if j.running), None)


def last():
    return _last[-1] if _last else None


def _run(job):
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
    with open(job.log_path, "w", encoding="utf-8") as log:
        for i, argv in enumerate(job.steps):
            job.current = i
            log.write(f"$ {' '.join(Path(a).name if a == PY else a for a in argv)}\n")
            log.flush()
            try:
                job.proc = subprocess.Popen(argv, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                                            stdin=subprocess.DEVNULL, env=env)
                job.returncode = job.proc.wait()
            except OSError as e:
                log.write(f"Gagal menjalankan: {e}\n")
                job.returncode = -1
            if job.returncode != 0 or job.stop_requested:
                log.write("\nDihentikan.\n" if job.stop_requested else f"\nLangkah gagal (kode {job.returncode}).\n")
                break
        else:
            log.write("\nSelesai.\n")
    job.finished = time.time()


def start(name, steps):
    """Mulai job baru. steps: list argumen skrip relatif ke root, mis. [["collect.py", "--limit", "50"]]."""
    with _lock:
        if any(j.running for j in _jobs.values()):
            raise RuntimeError("Masih ada proses yang berjalan.")
        LOG_DIR.mkdir(exist_ok=True)
        job = Job(name=name, steps=[[PY, *s] for s in steps], log_path=LOG_DIR / f"dashboard_{name}.log")
        _jobs[name] = job
        _last.append(job)
    threading.Thread(target=_run, args=(job,), daemon=True).start()
    return job


def stop():
    job = current()
    if job and job.proc and job.proc.poll() is None:
        job.stop_requested = True
        job.proc.terminate()
