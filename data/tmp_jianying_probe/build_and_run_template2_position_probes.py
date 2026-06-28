from __future__ import annotations

import json
import subprocess
import sys
import wave
from pathlib import Path

from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from bworkflow_sql.template_config import get_template_slot

PROBE_DIR = REPO_ROOT / "data" / "tmp_jianying_probe"
ASSET_DIR = PROBE_DIR / "template2-position-assets"
ENGINE_DIR = REPO_ROOT / "scripts" / "jianying_engine"
GENERATOR = ENGINE_DIR / "generate_jianying_draft.py"
ENGINE_PYTHON = ENGINE_DIR / ".venv" / "Scripts" / "python.exe"
LEGACY_PYTHON = Path.home() / ".codex" / "skills" / "b-workflow" / ".venv" / "Scripts" / "python.exe"
VENV_PYTHON = ENGINE_PYTHON if ENGINE_PYTHON.exists() else LEGACY_PYTHON
DRAFT_ROOT = Path(r"E:\剪辑-剪映\草稿\JianyingPro Drafts")
DISPLAY_VIDEO = PROBE_DIR / "xiaowai2-slot-test.mp4"

JOBS = [
    {
        "template": "\u5c0f\u535a-\u6a21\u677f2",
        "account": "\u5c0f\u535a",
        "source_image": PROBE_DIR / "xiaobo2-html-render" / "xiaobo2-template2-970x480.png",
        "canvas_image": ASSET_DIR / "xiaobo2-template2-canvas-1920x1080.png",
        "manifest": PROBE_DIR / "xiaobo2-short-position-test.manifest.json",
        "draft_name": "\u5c0f\u535a-\u6a21\u677f2-\u5355\u6761\u4f4d\u7f6e\u6d4b\u8bd5",
    },
    {
        "template": "\u5c0f\u6b6a-\u6a21\u677f2",
        "account": "\u5c0f\u6b6a",
        "source_image": PROBE_DIR / "xiaowai2-html-render" / "xiaowai2-template2-970x480.png",
        "canvas_image": ASSET_DIR / "xiaowai2-template2-canvas-1920x1080.png",
        "manifest": PROBE_DIR / "xiaowai2-short-position-test.manifest.json",
        "draft_name": "\u5c0f\u6b6a-\u6a21\u677f2-\u5355\u6761\u4f4d\u7f6e\u6d4b\u8bd5",
    },
]


def ensure_silent_wav(path: Path, duration_sec: float = 4.0) -> None:
    sample_rate = 44100
    frame_count = int(sample_rate * duration_sec)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(b"\x00\x00" * frame_count)


def build_canvas(source: Path, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(source) as image:
        image = image.convert("RGB")
        resized = image.resize((1920, 960), Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", (1920, 1080), (8, 10, 14))
    canvas.paste(resized, (0, 0))
    canvas.save(output)


def write_manifest(job: dict[str, object], audio_path: Path) -> None:
    template = str(job["template"])
    account = str(job["account"])
    slot = get_template_slot(template)
    payload = {
        "version": 2,
        "source": "template2-position-probe",
        "project_id": 0,
        "project_name": f"{template} 单条位置测试",
        "category": "probe",
        "mode": "template2_position_probe",
        "account_label": account,
        "account_id": account,
        "entries": [
            {
                "type": "product",
                "order_index": 1,
                "section": "product",
                "section_order": 1,
                "product_uid": "POSITION-PROBE",
                "product_name": f"{template} 位置测试",
                "price_label": "",
                "source_label": "正文",
                "text": f"{template} 视频位置测试。",
                "audio_path": str(audio_path),
                "image_path": str(job["canvas_image"]),
                "video_path": "",
                "display_video_path": str(DISPLAY_VIDEO),
                "display_video_slot": slot,
                "account_id": account,
                "account_label": account,
                "text_hash": f"position-probe-{template}",
            }
        ],
        "display_video_slot": slot,
    }
    manifest = Path(job["manifest"])
    manifest.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def run_generator(job: dict[str, object]) -> int:
    python_exe = VENV_PYTHON if VENV_PYTHON.exists() else Path(sys.executable)
    cmd = [
        str(python_exe),
        str(GENERATOR),
        "--manifest",
        str(job["manifest"]),
        "--draft-root",
        str(DRAFT_ROOT),
        "--draft-name",
        str(job["draft_name"]),
        "--skip-subtitles",
        "--allow-replace",
    ]
    print(f"RUN {job['template']}: {' '.join(cmd)}", flush=True)
    completed = subprocess.run(cmd, cwd=str(REPO_ROOT))
    return completed.returncode


def main() -> int:
    if not DISPLAY_VIDEO.exists():
        raise FileNotFoundError(f"Missing display video: {DISPLAY_VIDEO}")
    audio_path = ASSET_DIR / "position-probe-4s.wav"
    ensure_silent_wav(audio_path)
    for job in JOBS:
        build_canvas(Path(job["source_image"]), Path(job["canvas_image"]))
        write_manifest(job, audio_path)
    return max((run_generator(job) for job in JOBS), default=0)


if __name__ == "__main__":
    raise SystemExit(main())
