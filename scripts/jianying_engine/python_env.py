from __future__ import annotations

import os
import sys
from pathlib import Path

ENGINE_ROOT = Path(__file__).resolve().parent
LOCAL_VENV = ENGINE_ROOT / ".venv"
LOCAL_VENV_PYTHON = LOCAL_VENV / "Scripts" / "python.exe"
LOCAL_SITE_PACKAGES = LOCAL_VENV / "Lib" / "site-packages"


def inject_local_site_packages() -> None:
    site_packages = str(LOCAL_SITE_PACKAGES)
    if LOCAL_SITE_PACKAGES.exists() and site_packages not in sys.path:
        sys.path.insert(0, site_packages)


def local_python_command() -> list[str]:
    override = os.environ.get("BWORKFLOW_JIANYING_PYTHON", "").strip()
    if override:
        return [override]
    if LOCAL_VENV_PYTHON.exists():
        return [str(LOCAL_VENV_PYTHON)]
    if sys.executable:
        return [sys.executable]
    return ["python"]


def preferred_python_commands() -> list[list[str]]:
    commands = [
        local_python_command(),
        ["python"],
        ["py", "-3"],
    ]
    unique_commands: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()
    for command in commands:
        key = tuple(command)
        if key in seen:
            continue
        seen.add(key)
        unique_commands.append(command)
    return unique_commands
