from __future__ import annotations

import os
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[1]
CANONICAL_APP_ROOT = Path(r"G:\workspace\PC-Bilibili-workflow-sql")


def _default_data_dir() -> Path:
    override = os.environ.get("BWORKFLOW_SQL_DATA_DIR", "").strip()
    if override:
        return Path(override)
    if APP_ROOT != CANONICAL_APP_ROOT and CANONICAL_APP_ROOT.exists():
        return CANONICAL_APP_ROOT / "data"
    return APP_ROOT / "data"


DATA_DIR = _default_data_dir()
DB_PATH = DATA_DIR / "bworkflow.db"

LEGACY_PROJECT_ROOT = Path(r"G:\workspace\PC-Bilibili-workflow")
LEGACY_B_WORKFLOW_SKILL_SCRIPTS = Path(r"C:\Users\zhaoer\.codex\skills_archived\b-workflow-20260625\scripts")


def _default_jianying_engine_dir() -> Path:
    override = os.environ.get("BWORKFLOW_JIANYING_ENGINE_DIR", "").strip()
    if override:
        return Path(override)
    canonical_engine = CANONICAL_APP_ROOT / "scripts" / "jianying_engine"
    if APP_ROOT != CANONICAL_APP_ROOT and canonical_engine.exists():
        return canonical_engine
    return APP_ROOT / "scripts" / "jianying_engine"


JIANYING_ENGINE_DIR = _default_jianying_engine_dir()
DEFAULT_INDEXTTS_DIR = Path(r"G:\Tools\IndexTTS2.0")
DEFAULT_TTS_API_BASE_URL = "http://127.0.0.1:7861"
DEFAULT_MASTER_API_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_MASTER_SERVICE_ROOT = Path(r"G:\workspace\bilibili-newTools-next-master")

DEFAULT_MARKDOWN_ROOT = Path(r"G:\WriteSpace\B站-文案脚本\10_b站文案\3.商品文案")
DEFAULT_IMAGE_ROOT = Path(r"G:\2026项目-b站\素材-商品ppt图片")
DEFAULT_VIDEO_ROOT = Path(r"G:\2026项目-b站\素材-剪辑\roll-b素材")
DEFAULT_VOICE_ROOT = Path(r"G:\2026项目-b站\素材-配音")
DEFAULT_STANDALONE_VOICE_ROOT = Path(r"G:\2026项目-b站")
DEFAULT_OUTPUT_ROOT = DATA_DIR / "workspace"
DEFAULT_SPOKEN_MD_ROOT = Path(r"G:\WriteSpace\B站-文案脚本\10_b站文案\1.口播文案")
DEFAULT_JIANYING_DRAFT_ROOT = Path(r"E:\剪辑-剪映\草稿\JianyingPro Drafts")
INTERNAL_WORKSPACE_ROOT = DATA_DIR / "workspace"

CUTME_ROOT = Path(r"G:\workspace\赵二-工具-CutMe")
CUTME_OUTPUT_ROOT = CUTME_ROOT / "cutme" / "output"


def ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    INTERNAL_WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)
