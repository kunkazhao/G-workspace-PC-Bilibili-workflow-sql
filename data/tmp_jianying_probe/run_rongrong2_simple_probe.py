"""荣荣-模板2 简易探测：渲染 HTML 底图 + 静音音频 → 生成剪映草稿验证位置。"""
import json
import os
import subprocess
import sys
import wave
from pathlib import Path

REPO_ROOT = Path(r"G:\workspace\PC-Bilibili-workflow-sql")
sys.path.insert(0, str(REPO_ROOT))

from bworkflow_sql.template_config import get_template_slot
from PIL import Image
from playwright.sync_api import sync_playwright

PROBE_DIR = REPO_ROOT / "data" / "tmp_jianying_probe"
ASSET_DIR = PROBE_DIR / "rongrong2-position-assets"
ASSET_DIR.mkdir(parents=True, exist_ok=True)

HTML_PATH = r"G:\workspace\bilibili-newTools-next-master\templates\image-templates\rongrong2.html"
HTML_PNG = PROBE_DIR / "rongrong2-html-render" / "rongrong2-template2-970x480.png"
HTML_PNG.parent.mkdir(parents=True, exist_ok=True)
CANVAS_PNG = ASSET_DIR / "rongrong2-simple-canvas-1920x1080.png"
MANIFEST = PROBE_DIR / "rongrong2-simple-position-test.manifest.json"
DRAFT_NAME = "荣荣-模板2-简易位置测试"
AUDIO_PATH = ASSET_DIR / "position-probe-4s.wav"
DISPLAY_VIDEO = PROBE_DIR / "xiaowai2-slot-test.mp4"

ENGINE_DIR = REPO_ROOT / "scripts" / "jianying_engine"
ENGINE_PYTHON = ENGINE_DIR / ".venv" / "Scripts" / "python.exe"
LEGACY_PYTHON = Path.home() / ".codex" / "skills" / "b-workflow" / ".venv" / "Scripts" / "python.exe"
VENV_PYTHON = ENGINE_PYTHON if ENGINE_PYTHON.exists() else LEGACY_PYTHON
GENERATOR = ENGINE_DIR / "generate_jianying_draft.py"
DRAFT_ROOT = Path(r"E:\剪辑-剪映\草稿\JianyingPro Drafts")


def render_html() -> None:
    url = "file:///" + HTML_PATH.replace("\\", "/")
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 970, "height": 480}, device_scale_factor=1)
        page.goto(url)
        page.wait_for_load_state("networkidle")
        root = page.locator(".tpl-root").first
        root.screenshot(path=str(HTML_PNG))
        browser.close()
    print(f"rendered: {HTML_PNG} ({HTML_PNG.stat().st_size} bytes)")


def make_canvas() -> None:
    with Image.open(HTML_PNG) as img:
        img = img.convert("RGB")
        resized = img.resize((1920, 960), Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", (1920, 1080), (8, 10, 14))
    canvas.paste(resized, (0, 0))
    canvas.save(CANVAS_PNG)
    print(f"canvas: {CANVAS_PNG}")


def make_silent_wav() -> None:
    sample_rate = 44100
    frames = int(sample_rate * 4.0)
    with wave.open(str(AUDIO_PATH), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(b"\x00\x00" * frames)
    print(f"audio: {AUDIO_PATH}")


def write_manifest() -> None:
    slot = get_template_slot("荣荣-模板2")
    print(f"slot: {slot}")
    payload = {
        "version": 2,
        "source": "rongrong2-simple-probe",
        "project_id": 0,
        "project_name": "荣荣-模板2 简易位置测试",
        "category": "probe",
        "mode": "rongrong2_position_probe",
        "account_label": "荣荣",
        "account_id": "荣荣",
        "entries": [
            {
                "type": "product",
                "order_index": 1,
                "section": "product",
                "section_order": 1,
                "product_uid": "POSITION-PROBE",
                "product_name": "荣荣-模板2 位置测试",
                "price_label": "",
                "source_label": "正文",
                "text": "荣荣模板二视频位置测试。",
                "audio_path": str(AUDIO_PATH),
                "image_path": str(CANVAS_PNG),
                "video_path": "",
                "display_video_path": str(DISPLAY_VIDEO),
                "display_video_slot": slot,
                "account_id": "荣荣",
                "account_label": "荣荣",
                "text_hash": "simple-probe-rongrong2",
            }
        ],
        "display_video_slot": slot,
    }
    MANIFEST.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"manifest: {MANIFEST}")


def run_generator() -> int:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    cmd = [
        str(VENV_PYTHON),
        str(GENERATOR),
        "--manifest", str(MANIFEST),
        "--draft-root", str(DRAFT_ROOT),
        "--draft-name", DRAFT_NAME,
        "--skip-subtitles",
        "--allow-replace",
    ]
    print(f"RUN: {' '.join(cmd)}")
    return subprocess.run(cmd, cwd=str(REPO_ROOT), env=env).returncode


if __name__ == "__main__":
    render_html()
    make_canvas()
    make_silent_wav()
    write_manifest()
    rc = run_generator()
    print(f"\nreturncode: {rc}")
    sys.exit(rc)
