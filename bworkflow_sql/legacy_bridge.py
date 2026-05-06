from __future__ import annotations

import sys
import importlib
from pathlib import Path
from typing import Any

from .settings import B_WORKFLOW_SKILL_SCRIPTS, LEGACY_PROJECT_ROOT, PEIYINDAN_SKILL_SCRIPTS


def install_legacy_paths() -> None:
    paths = [
        LEGACY_PROJECT_ROOT,
        LEGACY_PROJECT_ROOT / "scripts",
        B_WORKFLOW_SKILL_SCRIPTS,
        PEIYINDAN_SKILL_SCRIPTS,
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


def legacy_script_path(*parts: str) -> Path:
    return LEGACY_PROJECT_ROOT.joinpath(*parts)
