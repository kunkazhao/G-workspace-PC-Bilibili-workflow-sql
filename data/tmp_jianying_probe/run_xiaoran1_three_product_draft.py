from __future__ import annotations

import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST = REPO_ROOT / "data" / "tmp_jianying_probe" / "xiaoran1-three-products.manifest.json"
GENERATOR = Path.home() / ".codex" / "skills" / "b-workflow" / "scripts" / "generate_jianying_draft.py"
DRAFT_ROOT = Path(r"E:\剪辑-剪映\草稿\JianyingPro Drafts")
BACKGROUND_IMAGE = Path(r"G:\2026项目-b站\素材-剪辑\1-背景图\背景1 (1).png")
DRAFT_NAME = "小燃1-3产品对齐测试-坐标修正"


def main() -> int:
    cmd = [
        sys.executable,
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
