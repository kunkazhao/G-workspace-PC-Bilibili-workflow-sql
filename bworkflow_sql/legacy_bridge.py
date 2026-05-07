from __future__ import annotations

import sys
import importlib
from pathlib import Path
from typing import Any

from .settings import LEGACY_PROJECT_ROOT


def install_legacy_paths() -> None:
    paths = [
        LEGACY_PROJECT_ROOT,
        LEGACY_PROJECT_ROOT / "scripts",
    ]
    for path in paths:
        text = str(path)
        if path.exists() and text not in sys.path:
            sys.path.insert(0, text)


def try_import(module_name: str) -> Any | None:
    install_legacy_paths()
    try:
        return importlib.import_module(module_name)
    except Exception:
        return None

