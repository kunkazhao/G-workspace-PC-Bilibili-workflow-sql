from __future__ import annotations

import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST = REPO_ROOT / "data" / "tmp_jianying_probe" / "xiaowai2-position-test.manifest.json"
ENGINE_DIR = REPO_ROOT / "scripts" / "jianying_engine"
GENERATOR = ENGINE_DIR / "generate_jianying_draft.py"
ENGINE_PYTHON = ENGINE_DIR / ".venv" / "Scripts" / "python.exe"
LEGACY_PYTHON = Path.home() / ".codex" / "skills" / "b-workflow" / ".venv" / "Scripts" / "python.exe"
DRAFT_ROOT = Path(r"E:\剪辑-剪映\草稿\JianyingPro Drafts")
BACKGROUND_IMAGE = Path(r"G:\2026项目-b站\素材-剪辑\1-背景图\背景1 (1).png")
DRAFT_NAME = "小歪2-图片位置测试-X843-Y34"


def main() -> int:
    cmd = [
        str(ENGINE_PYTHON if ENGINE_PYTHON.exists() else LEGACY_PYTHON if LEGACY_PYTHON.exists() else sys.executable),
        str(GENERATOR),
        "--manifest",
        str(MANIFEST),
        "--draft-root",
        str(DRAFT_ROOT),
        "--draft-name",
        DRAFT_NAME,
        "--background-image",
        str(BACKGROUND_IMAGE),
        "--skip-subtitles",
        "--allow-replace",
    ]
    completed = subprocess.run(cmd, cwd=str(REPO_ROOT))
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
