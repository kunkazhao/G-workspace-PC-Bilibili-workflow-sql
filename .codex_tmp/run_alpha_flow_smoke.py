from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


PC_ROOT = Path(r"G:\workspace\PC-Bilibili-workflow-sql")
TOOLS_ROOT = Path(r"G:\workspace\tools-quzimu")
FFMPEG = TOOLS_ROOT / "node_modules" / "ffmpeg-static" / "ffmpeg.exe"
FFPROBE = TOOLS_ROOT / "node_modules" / "ffprobe-static" / "bin" / "win32" / "x64" / "ffprobe.exe"
JY_SCRIPT = Path(r"C:\Users\zhaoer\.codex\skills\b-workflow\scripts\generate_jianying_draft.py")
DRAFT_ROOT = Path(r"E:\剪辑-剪映\草稿\JianyingPro Drafts")
VIDEO_DIR = Path(r"G:\2026项目-b站\素材-剪辑\roll-b素材\数码-耳夹蓝牙耳机")
IMAGE_DIR = Path(r"G:\2026项目-b站\素材-商品ppt图片\数码-耳夹蓝牙耳机\小博\模板1")
OUT_WIDTH = 1280
OUT_HEIGHT = 720
RADIUS = 60
DEFAULT_CROP_PERCENT = 12
CLIP_SECONDS = 4.0


SAMPLES = [
    {
        "uid": "EJLY061",
        "name": "音贝奇ClipAir",
        "price": "139元",
        "video": VIDEO_DIR / "139元-EJLY061-音贝奇ClipAir.mov",
        "image": IMAGE_DIR / "139-EJLY061-音贝奇ClipAir.png",
    },
    {
        "uid": "EJLY059",
        "name": "熙彼儿EC200",
        "price": "148元",
        "video": VIDEO_DIR / "148元-EJLY059-熙彼儿EC200.mov",
        "image": IMAGE_DIR / "148-EJLY059-熙彼儿EC200.png",
    },
    {
        "uid": "EJJE051",
        "name": "iKF Air Clip",
        "price": "149元",
        "video": VIDEO_DIR / "149元-EJJE051-iKF Air Clip.mov",
        "image": IMAGE_DIR / "149-EJJE051-iKF Air Clip.png",
    },
]


def run(command: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=str(cwd) if cwd else None,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="ignore",
        check=True,
    )


def even_floor(value: float) -> int:
    floored = int(value)
    return floored if floored % 2 == 0 else floored - 1


def probe_video(path: Path) -> dict[str, Any]:
    result = run(
        [
            str(FFPROBE),
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,r_frame_rate:format=duration",
            "-of",
            "json",
            str(path),
        ]
    )
    payload = json.loads(result.stdout)
    stream = payload["streams"][0]
    return {
        "width": int(stream["width"]),
        "height": int(stream["height"]),
        "duration": float(payload.get("format", {}).get("duration") or 0),
    }


def build_crop_filter(width: int, height: int) -> str:
    crop_pixels = max(0, int(height * (DEFAULT_CROP_PERCENT / 100)))
    safe_crop_pixels = min(crop_pixels, max(0, height - 2))
    source_width = width
    source_height = max(2, height - safe_crop_pixels)
    offset_x = 0
    offset_y = 0
    source_aspect = source_width / source_height
    target_aspect = OUT_WIDTH / OUT_HEIGHT

    if source_aspect > target_aspect:
        next_width = max(2, even_floor(source_height * target_aspect))
        offset_x = max(0, even_floor((source_width - next_width) / 2))
        source_width = next_width
    elif source_aspect < target_aspect:
        next_height = max(2, even_floor(source_width / target_aspect))
        offset_y = max(0, even_floor((source_height - next_height) / 2))
        source_height = next_height

    return f"crop={source_width}:{source_height}:{offset_x}:{offset_y},scale={OUT_WIDTH}:{OUT_HEIGHT}:flags=fast_bilinear,setsar=1,format=rgb24"


def rounded_mask_expression(width: int, height: int, radius: int) -> str:
    safe_radius = max(1, min(radius, min(width, height) // 2))
    return "+".join(
        [
            f"lt(X,{safe_radius})*lt(Y,{safe_radius})*gt(pow(X-{safe_radius},2)+pow(Y-{safe_radius},2),pow({safe_radius},2))",
            f"gt(X,{width - safe_radius})*lt(Y,{safe_radius})*gt(pow(X-({width - safe_radius}),2)+pow(Y-{safe_radius},2),pow({safe_radius},2))",
            f"lt(X,{safe_radius})*gt(Y,{height - safe_radius})*gt(pow(X-{safe_radius},2)+pow(Y-({height - safe_radius}),2),pow({safe_radius},2))",
            f"gt(X,{width - safe_radius})*gt(Y,{height - safe_radius})*gt(pow(X-({width - safe_radius}),2)+pow(Y-({height - safe_radius}),2),pow({safe_radius},2))",
        ]
    )


def create_mask(mask_path: Path) -> None:
    alpha = rounded_mask_expression(OUT_WIDTH, OUT_HEIGHT, RADIUS)
    run(
        [
            str(FFMPEG),
            "-y",
            "-hide_banner",
            "-f",
            "lavfi",
            "-i",
            f"color=black:s={OUT_WIDTH}x{OUT_HEIGHT}:r=1:d=1",
            "-frames:v",
            "1",
            "-vf",
            f"format=gray,geq=lum='if({alpha},0,255)'",
            "-update",
            "1",
            str(mask_path),
        ]
    )


def export_rounded_mov(source: Path, output: Path, mask_path: Path) -> dict[str, Any]:
    meta = probe_video(source)
    base_filter = build_crop_filter(meta["width"], meta["height"])
    filter_complex = (
        f"[0:v]{base_filter}[video];"
        f"[1:v]format=gray,split=2[mask_rgb][mask_alpha];"
        f"color=black:s={OUT_WIDTH}x{OUT_HEIGHT}:r=30,format=rgb24[transparent_black];"
        f"[transparent_black][video][mask_rgb]maskedmerge[clean_rgb];"
        f"[clean_rgb][mask_alpha]alphamerge=shortest=1,format=yuva444p10le[outv]"
    )
    run(
        [
            str(FFMPEG),
            "-y",
            "-hide_banner",
            "-i",
            str(source),
            "-loop",
            "1",
            "-i",
            str(mask_path),
            "-filter_complex",
            filter_complex,
            "-map",
            "[outv]",
            "-an",
            "-c:v",
            "prores_ks",
            "-profile:v",
            "4444",
            "-alpha_bits",
            "16",
            "-pix_fmt",
            "yuva444p10le",
            "-vendor",
            "apl0",
            "-r",
            "30",
            "-t",
            f"{CLIP_SECONDS:.3f}",
            str(output),
        ]
    )
    return meta


def make_audio(output: Path, frequency: int) -> None:
    run(
        [
            str(FFMPEG),
            "-y",
            "-hide_banner",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={frequency}:sample_rate=44100",
            "-t",
            f"{CLIP_SECONDS:.3f}",
            "-c:a",
            "pcm_s16le",
            str(output),
        ]
    )


def sample_rgba(video_path: Path, x: int, y: int, scratch: Path) -> list[int]:
    raw_path = scratch / f"rgba_{video_path.stem}_{x}_{y}.raw"
    run(
        [
            str(FFMPEG),
            "-y",
            "-hide_banner",
            "-i",
            str(video_path),
            "-vf",
            f"crop=1:1:{x}:{y}",
            "-frames:v",
            "1",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgba",
            str(raw_path),
        ]
    )
    return list(raw_path.read_bytes()[:4])


def inspect_mov(video_path: Path, scratch: Path) -> dict[str, Any]:
    probe = run(
        [
            str(FFPROBE),
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_name,pix_fmt,width,height:format=duration,size",
            "-of",
            "json",
            str(video_path),
        ]
    )
    payload = json.loads(probe.stdout)
    stream = payload["streams"][0]
    corners = {
        "top_left": sample_rgba(video_path, 0, 0, scratch),
        "top_right": sample_rgba(video_path, OUT_WIDTH - 1, 0, scratch),
        "bottom_left": sample_rgba(video_path, 0, OUT_HEIGHT - 1, scratch),
        "bottom_right": sample_rgba(video_path, OUT_WIDTH - 1, OUT_HEIGHT - 1, scratch),
        "inside": sample_rgba(video_path, RADIUS + 10, RADIUS + 10, scratch),
    }
    return {
        "codec": stream.get("codec_name"),
        "pix_fmt": stream.get("pix_fmt"),
        "width": stream.get("width"),
        "height": stream.get("height"),
        "duration": float(payload.get("format", {}).get("duration") or 0),
        "size": int(payload.get("format", {}).get("size") or 0),
        "rgba_samples": corners,
    }


def build_manifest(entries: list[dict[str, Any]], manifest_path: Path) -> None:
    manifest = {
        "version": 2,
        "source": "codex-alpha-flow-smoke",
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "entries": entries,
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def run_draft_generator(manifest_path: Path, draft_name: str) -> dict[str, Any]:
    env = os.environ.copy()
    path_parts = [
        str(FFMPEG.parent),
        str(FFPROBE.parent),
        env.get("PATH", ""),
    ]
    env["PATH"] = os.pathsep.join(path_parts)
    env["PYTHONIOENCODING"] = "utf-8"
    result = run(
        [
            sys.executable,
            str(JY_SCRIPT),
            "--manifest",
            str(manifest_path),
            "--draft-root",
            str(DRAFT_ROOT),
            "--draft-name",
            draft_name,
            "--allow-replace",
            "--skip-subtitles",
        ],
        cwd=PC_ROOT,
        env=env,
    )
    return json.loads(result.stdout)


def assert_ready() -> None:
    required = [FFMPEG, FFPROBE, JY_SCRIPT]
    required.extend(sample["video"] for sample in SAMPLES)
    required.extend(sample["image"] for sample in SAMPLES)
    missing = [str(path) for path in required if not Path(path).exists()]
    if missing:
        raise FileNotFoundError("Missing required files: " + "; ".join(missing))


def main() -> None:
    assert_ready()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    output_dir = PC_ROOT / "data" / "tmp_jianying_alpha_flow" / stamp
    video_out_dir = output_dir / "rounded_mov"
    audio_out_dir = output_dir / "audio"
    scratch_dir = output_dir / "scratch"
    for directory in (video_out_dir, audio_out_dir, scratch_dir):
        directory.mkdir(parents=True, exist_ok=True)

    mask_path = scratch_dir / f"rounded-mask-{OUT_WIDTH}x{OUT_HEIGHT}-r{RADIUS}.png"
    create_mask(mask_path)

    manifest_entries: list[dict[str, Any]] = []
    processed: list[dict[str, Any]] = []
    for index, sample in enumerate(SAMPLES, start=1):
        out_mov = video_out_dir / f"{index:02d}-{sample['uid']}-{sample['name']}-rounded-alpha.mov"
        audio_path = audio_out_dir / f"{index:02d}-{sample['uid']}-voice.wav"
        source_meta = export_rounded_mov(sample["video"], out_mov, mask_path)
        make_audio(audio_path, 420 + index * 90)
        mov_inspection = inspect_mov(out_mov, scratch_dir)
        processed.append(
            {
                "uid": sample["uid"],
                "name": sample["name"],
                "price": sample["price"],
                "source_video": str(sample["video"]),
                "output_video": str(out_mov),
                "image": str(sample["image"]),
                "source_meta": source_meta,
                "output_inspection": mov_inspection,
            }
        )
        manifest_entries.append(
            {
                "type": "product",
                "order_index": index,
                "section": "product",
                "product_uid": sample["uid"],
                "product_name": sample["name"],
                "price_label": sample["price"],
                "text": f"{sample['name']} 圆角透明链路测试。",
                "audio_path": str(audio_path),
                "image_path": str(sample["image"]),
                "display_video_path": str(out_mov),
                "display_video_slot": {
                    "x": 850,
                    "y": 95,
                    "width": 980,
                    "height": 620,
                },
            }
        )

    manifest_path = output_dir / "alpha_flow_manifest.json"
    build_manifest(manifest_entries, manifest_path)
    draft_name = f"透明圆角链路测试-{stamp}"
    draft_summary = run_draft_generator(manifest_path, draft_name)

    summary = {
        "status": "success",
        "output_dir": str(output_dir),
        "manifest_path": str(manifest_path),
        "draft_name": draft_name,
        "draft_dir": draft_summary.get("draft_dir"),
        "draft_summary": draft_summary,
        "processed": processed,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
