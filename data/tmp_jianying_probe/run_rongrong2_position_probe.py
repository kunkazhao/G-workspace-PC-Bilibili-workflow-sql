"""荣荣-模板2 probe：渲染 HTML → 拼画布 → 写 manifest → 跑生成器。"""
import json
import os
import subprocess
import sys
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

REPO_ROOT = Path(r"G:\workspace\PC-Bilibili-workflow-sql")
sys.path.insert(0, str(REPO_ROOT))

from bworkflow_sql.template_config import get_template_slot

PROBE_DIR = REPO_ROOT / "data" / "tmp_jianying_probe"
ASSET_DIR = PROBE_DIR / "rongrong2-position-assets"
ASSET_DIR.mkdir(parents=True, exist_ok=True)

HTML_PATH = r"G:\workspace\bilibili-newTools-next-master\templates\image-templates\rongrong2.html"
HTML_PNG = PROBE_DIR / "rongrong2-html-render" / "rongrong2-template2-970x480.png"
HTML_PNG.parent.mkdir(parents=True, exist_ok=True)
CANVAS_PNG = ASSET_DIR / "rongrong2-template2-canvas-1920x1080.png"
MANIFEST = PROBE_DIR / "rongrong2-short-position-test.manifest.json"
DRAFT_NAME = "荣荣-模板2-单条位置测试"

ENGINE_DIR = REPO_ROOT / "scripts" / "jianying_engine"
ENGINE_PYTHON = ENGINE_DIR / ".venv" / "Scripts" / "python.exe"
LEGACY_PYTHON = Path.home() / ".codex" / "skills" / "b-workflow" / ".venv" / "Scripts" / "python.exe"
VENV_PYTHON = ENGINE_PYTHON if ENGINE_PYTHON.exists() else LEGACY_PYTHON
GENERATOR = ENGINE_DIR / "generate_jianying_draft.py"
DRAFT_ROOT = Path(r"E:\剪辑-剪映\草稿\JianyingPro Drafts")
DISPLAY_VIDEO = PROBE_DIR / "xiaowai2-slot-test.mp4"
AUDIO_PATH = ASSET_DIR / "position-probe-4s.wav"


def render_html_to_png() -> None:
    """用 playwright 把 rongrong2.html 渲染成 970x480 PNG。"""
    url = "file:///" + HTML_PATH.replace("\\", "/")
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 970, "height": 480}, device_scale_factor=1)
        page.goto(url)
        page.wait_for_load_state("networkidle")
        # 取 .tpl-root 元素截屏（精确 970x480）
        root = page.locator(".tpl-root").first
        root.screenshot(path=str(HTML_PNG))
        browser.close()
    print(f"rendered: {HTML_PNG} ({HTML_PNG.stat().st_size} bytes)")


def build_canvas() -> None:
    """把 970x480 PNG 拼到 1920x1080 画布的顶部 960。"""
    with Image.open(HTML_PNG) as image:
        image = image.convert("RGB")
        resized = image.resize((1920, 960), Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", (1920, 1080), (8, 10, 14))
    canvas.paste(resized, (0, 0))
    canvas.save(CANVAS_PNG)
    print(f"canvas: {CANVAS_PNG} ({CANVAS_PNG.stat().st_size} bytes)")


def ensure_silent_wav() -> None:
    import wave
    sample_rate = 44100
    duration_sec = 4.0
    frame_count = int(sample_rate * duration_sec)
    with wave.open(str(AUDIO_PATH), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(b"\x00\x00" * frame_count)
    print(f"audio: {AUDIO_PATH}")


def write_manifest() -> None:
    slot = get_template_slot("荣荣-模板2")
    print(f"slot: {slot}")
    payload = {
        "version": 2,
        "source": "rongrong2-position-probe",
        "project_id": 0,
        "project_name": "荣荣-模板2 单条位置测试",
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
                "text": "荣荣-模板2 视频位置测试。",
                "audio_path": str(AUDIO_PATH),
                "image_path": str(CANVAS_PNG),
                "video_path": "",
                "display_video_path": str(DISPLAY_VIDEO),
                "display_video_slot": slot,
                "account_id": "荣荣",
                "account_label": "荣荣",
                "text_hash": "position-probe-荣荣-模板2",
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
    render_html_to_png()
    build_canvas()
    ensure_silent_wav()
    write_manifest()
    rc = run_generator()
    print(f"\nreturncode: {rc}")
    sys.exit(rc)
