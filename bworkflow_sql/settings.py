from __future__ import annotations

from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = APP_ROOT / "data"
DB_PATH = DATA_DIR / "bworkflow.db"

LEGACY_PROJECT_ROOT = Path(r"G:\workspace\PC-Bilibili-workflow")
B_WORKFLOW_SKILL_SCRIPTS = Path(r"C:\Users\zhaoer\.codex\skills\b-workflow\scripts")
PEIYINDAN_SKILL_SCRIPTS = Path(r"C:\Users\zhaoer\.codex\skills\get-peiyindan\scripts")

DEFAULT_MARKDOWN_ROOT = Path(r"G:\WriteSpace\B站-文案脚本\10_b站文案\3.商品文案")
DEFAULT_IMAGE_ROOT = Path(r"G:\2026项目-b站\素材-商品ppt图片")
DEFAULT_VIDEO_ROOT = Path(r"G:\2026项目-b站\素材-剪辑\roll-b素材")
DEFAULT_VOICE_ROOT = Path(r"G:\2026项目-b站\素材-配音")
DEFAULT_OUTPUT_ROOT = Path(r"G:\2026项目-b站\输出")


def ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
