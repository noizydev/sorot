import os
from pathlib import Path

import yaml

BASE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = BASE_DIR / "config.yaml"


def load_config(path=None):
    """Baca config.yaml; path di bagian `paths` diubah jadi Path absolut relatif ke file config.

    Tanpa argumen, env SOROT_CONFIG (bila ada) menggantikan config.yaml bawaan.
    """
    path = Path(path or os.environ.get("SOROT_CONFIG") or DEFAULT_CONFIG)
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    base = path.resolve().parent
    cfg["paths"] = {k: (Path(v) if Path(v).is_absolute() else base / v) for k, v in cfg["paths"].items()}
    return cfg
