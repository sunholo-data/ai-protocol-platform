"""Load the CLI's per-deployment config, falling back to the shipped example.

``config.yaml`` holds this deployment's real URLs and project ids; it is
gitignored and produced by ``make materialize-config``. ``config.example.yaml``
is what ships. Reading only the real file made every command module raise
``FileNotFoundError`` at IMPORT on a fresh checkout — which is exactly what CI
is — so the CLI test job could not even collect (2026-09-14). Falling back to
the example keeps import side-effect free; the placeholder guards in each
command still refuse to act on ``your-project-id-*`` at runtime.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

_CONFIG_DIR = Path(__file__).resolve().parent


def config_path() -> Path:
    real = _CONFIG_DIR / "config.yaml"
    return real if real.exists() else _CONFIG_DIR / "config.example.yaml"


def load_config() -> dict[str, Any]:
    try:
        return yaml.safe_load(config_path().read_text()) or {}
    except FileNotFoundError:
        return {}
