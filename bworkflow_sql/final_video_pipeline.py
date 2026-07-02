from __future__ import annotations

import json
import locale
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .settings import CUTME_ROOT, INTERNAL_WORKSPACE_ROOT
from .utils import safe_text
from .workflow_service import safe_path_component

Runner = Callable[..., Any]
ProbeVideo = Callable[[Path], dict[str, Any]]
MeasureLoudness = Callable[[Path], dict[str, Any]]


def run_final_video_pipeline(
    workflow: Any,
    *,
    project_id: int,
    account_label: str,
    product_media_mode: str = "video_preferred",
    product_image_mode: str = "missing",
    stale_product_image_policy: str = "block",
    mode: str = "standard",
    top_uids: str = "",
    package_output_path: str | Path | None = None,
    output_path: str | Path | None = None,
    cutme_root: str | Path = CUTME_ROOT,
    runner: Runner | None = None,
    probe_video: ProbeVideo | None = None,
    measure_loudness: MeasureLoudness | None = None,
) -> dict[str, Any]:
    account = safe_text(account_label)
    if not account:
        raise ValueError("render-final-video 需要指定账号。")

    render_root = INTERNAL_WORKSPACE_ROOT / f"project-{project_id}" / "render"
    render_root.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    stem = f"render-package-{safe_path_component(account)}-final-video-{timestamp}"
    package_path = _absolute_path(package_output_path) if package_output_path else render_root / f"{stem}.json"
    target_mp4 = _absolute_path(output_path) if output_path else package_path.with_suffix(".mp4")

    product_images: dict[str, Any] | None = None
    if product_image_mode != "skip":
        product_images = workflow.regenerate_product_card_images(
            project_id,
            account_label=account,
            mode=product_image_mode,
            product_uid="",
        )
        if product_images.get("ok") is False:
            return {
                "ok": False,
                "stage": "product_images",
                "product_images": product_images,
            }

    package_result = workflow.prepare_product_recommendation_output(
        project_id,
        account_label=account,
        output_mode="final_mp4",
        product_media_mode=product_media_mode,
        stale_product_image_policy=stale_product_image_policy,
        mode=mode,
        top_uids=top_uids,
        package_output_path=str(package_path),
    )
    if package_result.get("ok") is not True:
        return {
            "ok": False,
            "stage": "render_package",
            "product_images": product_images,
            "render_package": package_result,
        }

    package_path = _absolute_path(package_result["package_path"])
    target_mp4 = _absolute_path(output_path) if output_path else _absolute_path(package_result.get("next", {}).get("target_mp4") or target_mp4)
    target_mp4.parent.mkdir(parents=True, exist_ok=True)

    command_runner = runner or _run_command
    build = command_runner(
        ["python", "-m", "cutme", "--package", str(package_path), "--build-render-job"],
        cwd=Path(cutme_root),
        timeout=600,
    )
    job_package_path = _parse_job_package_path(_command_stdout(build))

    render = command_runner(
        [
            "python",
            "-m",
            "cutme",
            "--package",
            str(job_package_path),
            "--render-fast-final",
            "--output",
            str(target_mp4),
        ],
        cwd=Path(cutme_root),
        timeout=7200,
    )

    ffprobe_result = (probe_video or _probe_video)(target_mp4)
    loudnorm_result = (measure_loudness or _measure_loudness)(target_mp4)
    frames = _extract_acceptance_frames(
        target_mp4,
        package_path,
        cwd=Path(cutme_root),
        runner=command_runner,
    )

    return {
        "ok": True,
        "project_id": project_id,
        "account": account,
        "product_media_mode": product_media_mode,
        "product_image_mode": product_image_mode,
        "package_path": str(package_path),
        "job_package_path": str(job_package_path),
        "output_mp4": str(target_mp4),
        "output_mp4_link": _markdown_link("打开完整 MP4", target_mp4),
        "product_images": product_images,
        "render_package": package_result,
        "cutme": {
            "build": _command_summary(build),
            "render": _command_summary(render),
        },
        "verification": {
            "ffprobe": ffprobe_result,
            "loudnorm": loudnorm_result,
        },
        "frames": frames,
    }


def _run_command(command: list[str], *, cwd: Path, timeout: int) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        command,
        cwd=str(cwd),
        capture_output=True,
        timeout=timeout,
    )
    stdout = _decode_process_bytes(completed.stdout)
    stderr = _decode_process_bytes(completed.stderr)
    if completed.returncode != 0:
        raise RuntimeError(
            "command failed: "
            + " ".join(command)
            + f"\nstdout:\n{stdout}\nstderr:\n{stderr}"
        )
    return subprocess.CompletedProcess(
        completed.args,
        completed.returncode,
        stdout=stdout,
        stderr=stderr,
    )


def _absolute_path(path_text: str | Path) -> Path:
    return Path(path_text).expanduser().resolve()


def _decode_process_bytes(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    for encoding in ("utf-8", locale.getpreferredencoding(False), "gbk"):
        try:
            return value.decode(encoding)
        except UnicodeDecodeError:
            continue
    return value.decode("utf-8", errors="replace")


def _parse_job_package_path(stdout: str) -> Path:
    match = re.search(r"RenderPackage:\s*(.+)", stdout)
    if not match:
        raise ValueError("CutMe build-render-job 没有输出 RenderPackage 路径。")
    return Path(match.group(1).strip())


def _probe_video(path: Path) -> dict[str, Any]:
    completed = _run_command(
        [
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ],
        cwd=path.parent,
        timeout=120,
    )
    payload = json.loads(completed.stdout)
    video = next((s for s in payload.get("streams", []) if s.get("codec_type") == "video"), {})
    audio = next((s for s in payload.get("streams", []) if s.get("codec_type") == "audio"), {})
    return {
        "duration": float(payload.get("format", {}).get("duration") or 0),
        "size": int(payload.get("format", {}).get("size") or 0),
        "video": _video_stream_summary(video),
        "audio": _audio_stream_summary(audio),
    }


def _measure_loudness(path: Path) -> dict[str, Any]:
    completed = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-i",
            str(path),
            "-af",
            "loudnorm=I=-11:TP=-1:LRA=11:print_format=json",
            "-f",
            "null",
            "-",
        ],
        cwd=str(path.parent),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=7200,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"loudnorm failed:\n{completed.stderr}")
    match = re.search(r"\{[\s\S]*?\}", completed.stderr)
    if not match:
        raise ValueError("ffmpeg loudnorm 没有输出 JSON。")
    return json.loads(match.group(0))


def _extract_acceptance_frames(
    target_mp4: Path,
    package_path: Path,
    *,
    cwd: Path,
    runner: Runner,
) -> list[dict[str, Any]]:
    package = json.loads(package_path.read_text(encoding="utf-8"))
    frame_specs = _acceptance_frame_specs(package)
    frame_dir = target_mp4.parent / f"{target_mp4.stem}-frames"
    frame_dir.mkdir(parents=True, exist_ok=True)
    frames: list[dict[str, Any]] = []
    for spec in frame_specs:
        frame_path = frame_dir / f"{spec['label']}.png"
        runner(
            [
                "ffmpeg",
                "-y",
                "-ss",
                _format_seconds(float(spec["time"])),
                "-i",
                str(target_mp4),
                "-frames:v",
                "1",
                str(frame_path),
            ],
            cwd=cwd,
            timeout=180,
        )
        frames.append(
            {
                "label": spec["label"],
                "time": spec["time"],
                "path": str(frame_path),
                "link": _markdown_link(spec["label"], frame_path),
            }
        )
    return frames


def _acceptance_frame_specs(package: dict[str, Any]) -> list[dict[str, Any]]:
    segments = package.get("segments") if isinstance(package, dict) else []
    if not isinstance(segments, list):
        return []

    ranges: list[dict[str, Any]] = []
    cursor = 0.0
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        duration = float(segment.get("duration") or 0)
        ranges.append({"start": cursor, "duration": duration, "segment": segment})
        cursor += max(duration, 0.0)
    total = max(cursor, 0.0)

    specs: list[dict[str, Any]] = []
    price = _first_range(ranges, lambda s: safe_text(s.get("type")) == "price_transition")
    if price:
        specs.append({"label": "price-transition", "time": _midpoint(price)})
    product_video = _first_range(
        ranges,
        lambda s: safe_text(s.get("type")) == "product_recommendation" and bool(s.get("videoAsset")),
    )
    if product_video:
        specs.append({"label": "product-video", "time": _midpoint(product_video)})
    later_product = _first_range(
        ranges,
        lambda s: safe_text(s.get("type")) == "product_recommendation" and s.get("_range_start", 0) >= total / 2,
    )
    if not later_product:
        product_ranges = [
            item for item in ranges if safe_text(item["segment"].get("type")) == "product_recommendation"
        ]
        later_product = product_ranges[-1] if product_ranges else None
    if later_product:
        specs.append({"label": "later-product", "time": _midpoint(later_product)})

    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for spec in specs:
        if spec["label"] in seen:
            continue
        seen.add(spec["label"])
        deduped.append(spec)
    return deduped


def _first_range(ranges: list[dict[str, Any]], predicate: Callable[[dict[str, Any]], bool]) -> dict[str, Any] | None:
    for item in ranges:
        segment = dict(item["segment"])
        segment["_range_start"] = item["start"]
        if predicate(segment):
            return item
    return None


def _midpoint(item: dict[str, Any]) -> float:
    return float(item["start"]) + max(float(item.get("duration") or 0), 0.0) / 2


def _format_seconds(value: float) -> str:
    return f"{max(value, 0.0):.3f}"


def _video_stream_summary(stream: dict[str, Any]) -> str:
    if not stream:
        return ""
    width = stream.get("width") or "?"
    height = stream.get("height") or "?"
    fps = safe_text(stream.get("avg_frame_rate"))
    return f"{safe_text(stream.get('codec_name'))} {width}x{height} {fps}"


def _audio_stream_summary(stream: dict[str, Any]) -> str:
    if not stream:
        return ""
    sample_rate = safe_text(stream.get("sample_rate"))
    return f"{safe_text(stream.get('codec_name'))} {sample_rate}Hz"


def _command_stdout(result: Any) -> str:
    if isinstance(result, dict):
        return safe_text(result.get("stdout"))
    return safe_text(getattr(result, "stdout", ""))


def _command_summary(result: Any) -> dict[str, Any]:
    if isinstance(result, dict):
        return {
            "returncode": result.get("returncode", 0),
            "stdout": safe_text(result.get("stdout")),
            "stderr": safe_text(result.get("stderr")),
        }
    return {
        "returncode": getattr(result, "returncode", 0),
        "stdout": safe_text(getattr(result, "stdout", "")),
        "stderr": safe_text(getattr(result, "stderr", "")),
    }


def _markdown_link(label: str, path: Path) -> str:
    return f"[{label}]({path.as_posix()})"
