from __future__ import annotations

import json
import os
import subprocess
import sys
import wave
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from bworkflow_sql.template_config import get_template_slot

PROBE_DIR = REPO_ROOT / "data" / "tmp_jianying_probe"
ASSET_DIR = PROBE_DIR / "xiaobo3-position-assets"
HTML_RENDER_DIR = PROBE_DIR / "xiaobo3-html-render"
HTML_RENDER_DIR.mkdir(parents=True, exist_ok=True)
ASSET_DIR.mkdir(parents=True, exist_ok=True)

HTML_PATH = Path(r"G:\workspace\bilibili-newTools-next-master\templates\image-templates\xiaobo3.html")
HTML_PNG = HTML_RENDER_DIR / "xiaobo3-template3-970x480.png"
CANVAS_PNG = ASSET_DIR / "xiaobo3-template3-canvas-1920x1080.png"
MANIFEST = PROBE_DIR / "xiaobo3-simple-position-test.manifest.json"
AUDIO_PATH = ASSET_DIR / "position-probe-4s.wav"
DISPLAY_VIDEO = PROBE_DIR / "xiaowai2-slot-test.mp4"

ENGINE_DIR = REPO_ROOT / "scripts" / "jianying_engine"
GENERATOR = ENGINE_DIR / "generate_jianying_draft.py"
ENGINE_PYTHON = ENGINE_DIR / ".venv" / "Scripts" / "python.exe"
LEGACY_PYTHON = Path.home() / ".codex" / "skills" / "b-workflow" / ".venv" / "Scripts" / "python.exe"
VENV_PYTHON = ENGINE_PYTHON if ENGINE_PYTHON.exists() else LEGACY_PYTHON
DRAFT_ROOT = Path(r"E:\剪辑-剪映\草稿\JianyingPro Drafts")
DRAFT_NAME = "\u5c0f\u535a-\u6a21\u677f3-\u5355\u6761\u4f4d\u7f6e\u6d4b\u8bd5"
TEMPLATE_NAME = "\u5c0f\u535a-\u6a21\u677f3"
ACCOUNT = "\u5c0f\u535a"


def render_html() -> None:
    if not HTML_PATH.exists():
        raise FileNotFoundError(f"Missing HTML template: {HTML_PATH}")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 970, "height": 480}, device_scale_factor=1)
        page.goto(HTML_PATH.as_uri())
        page.wait_for_load_state("networkidle")
        page.locator(".tpl-img-card").first.screenshot(path=str(HTML_PNG))
        browser.close()
    print(f"rendered_html={HTML_PNG}")


def make_canvas() -> None:
    with Image.open(HTML_PNG) as image:
        image = image.convert("RGB")
        resized = image.resize((1920, 960), Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", (1920, 1080), (8, 10, 14))
    canvas.paste(resized, (0, 0))
    canvas.save(CANVAS_PNG)
    print(f"canvas={CANVAS_PNG}")


def make_silent_wav() -> None:
    sample_rate = 44100
    frames = int(sample_rate * 4.0)
    with wave.open(str(AUDIO_PATH), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(b"\x00\x00" * frames)
    print(f"audio={AUDIO_PATH}")


def write_manifest() -> None:
    if not DISPLAY_VIDEO.exists():
        raise FileNotFoundError(f"Missing display video: {DISPLAY_VIDEO}")
    slot = get_template_slot(TEMPLATE_NAME)
    payload = {
        "version": 2,
        "source": "xiaobo3-simple-position-probe",
        "project_id": 0,
        "project_name": f"{TEMPLATE_NAME} simple position probe",
        "category": "probe",
        "mode": "xiaobo3_position_probe",
        "account_label": ACCOUNT,
        "account_id": ACCOUNT,
        "entries": [
            {
                "type": "product",
                "order_index": 1,
                "section": "product",
                "section_order": 1,
                "product_uid": "POSITION-PROBE",
                "product_name": f"{TEMPLATE_NAME} position probe",
                "price_label": "",
                "source_label": "body",
                "text": f"{TEMPLATE_NAME} video position probe.",
                "audio_path": str(AUDIO_PATH),
                "image_path": str(CANVAS_PNG),
                "video_path": "",
                "display_video_path": str(DISPLAY_VIDEO),
                "display_video_slot": slot,
                "account_id": ACCOUNT,
                "account_label": ACCOUNT,
                "text_hash": "simple-probe-xiaobo3",
            }
        ],
        "display_video_slot": slot,
    }
    MANIFEST.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"manifest={MANIFEST}")
    print(f"slot={slot}")


def run_generator() -> int:
    python_exe = VENV_PYTHON if VENV_PYTHON.exists() else Path(sys.executable)
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    cmd = [
        str(python_exe),
        str(GENERATOR),
        "--manifest",
        str(MANIFEST),
        "--draft-root",
        str(DRAFT_ROOT),
        "--draft-name",
        DRAFT_NAME,
        "--skip-subtitles",
        "--allow-replace",
    ]
    print("run=" + " ".join(cmd))
    return subprocess.run(cmd, cwd=str(REPO_ROOT), env=env).returncode


def main() -> int:
    render_html()
    make_canvas()
    make_silent_wav()
    write_manifest()
    return run_generator()


if __name__ == "__main__":
    raise SystemExit(main())
