from __future__ import annotations

import json
import locale
import re
import subprocess
import hashlib
from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import Any, Callable

from .cutme_intro import intro_subtitle_events_from_plan
from .settings import CUTME_ROOT, INTERNAL_WORKSPACE_ROOT
from .subtitle_helpers import align_subtitle_text_with_asr, distribute_subtitle_text
from .utils import safe_text
from .workflow_service import safe_path_component

Runner = Callable[..., Any]
ProbeVideo = Callable[[Path], dict[str, Any]]
MeasureLoudness = Callable[[Path], dict[str, Any]]


class _TimingCollector:
    def __init__(self) -> None:
        self._started_at = perf_counter()
        self._items: dict[str, int] = {}

    def measure(self, key: str):
        collector = self

        class _Timer:
            def __enter__(self):
                self.started_at = perf_counter()
                return self

            def __exit__(self, exc_type, exc, tb):
                collector._items[key] = int(round((perf_counter() - self.started_at) * 1000))
                return False

        return _Timer()

    def set_zero(self, key: str) -> None:
        self._items.setdefault(key, 0)

    def finish(self) -> dict[str, int]:
        payload = dict(self._items)
        payload["total_ms"] = int(round((perf_counter() - self._started_at) * 1000))
        return payload


def run_final_video_pipeline(
    workflow: Any,
    *,
    project_id: int,
    account_label: str,
    product_media_mode: str = "video_preferred",
    product_order_strategy: str = "price_segment_shuffle",
    product_image_mode: str = "missing",
    stale_product_image_policy: str = "block",
    mode: str = "standard",
    top_uids: str = "",
    product_card_template_id: str = "",
    package_output_path: str | Path | None = None,
    output_path: str | Path | None = None,
    delivery_dir: str | Path | None = None,
    intro_video_path: str | Path | None = None,
    intro_video_text: str = "",
    intro_video_source_plan_path: str | Path | None = None,
    full_output_path: str | Path | None = None,
    pipeline_path: str | Path | None = None,
    acceptance_mode: str = "full",
    subtitle_alignment: str = "proportional",
    cutme_root: str | Path = CUTME_ROOT,
    runner: Runner | None = None,
    probe_video: ProbeVideo | None = None,
    measure_loudness: MeasureLoudness | None = None,
) -> dict[str, Any]:
    account = safe_text(account_label)
    if not account:
        raise ValueError("render-final-video 需要指定账号。")
    subtitle_mode = safe_text(subtitle_alignment) or "proportional"
    if subtitle_mode not in {"proportional", "asr"}:
        raise ValueError(f"unsupported subtitle_alignment: {subtitle_mode}")
    acceptance = safe_text(acceptance_mode) or "full"
    if acceptance not in {"none", "quick", "visual", "full"}:
        raise ValueError(f"unsupported acceptance_mode: {acceptance}")
    timings = _TimingCollector()

    render_root = INTERNAL_WORKSPACE_ROOT / f"project-{project_id}" / "render"
    render_root.mkdir(parents=True, exist_ok=True)
    clip_cache_dir = render_root / "final-video-cache"
    clip_cache_manifest_path = clip_cache_dir / "clip-cache-manifest.json"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    stem = f"render-package-{safe_path_component(account)}-final-video-{timestamp}"
    delivery_layout = _delivery_layout(delivery_dir, account=account, timestamp=timestamp)
    if delivery_layout:
        package_output_path = package_output_path or delivery_layout["package_path"]
        output_path = output_path or delivery_layout["product_mp4"]
        full_output_path = full_output_path or delivery_layout["full_mp4"]
    package_path = _absolute_path(package_output_path) if package_output_path else render_root / f"{stem}.json"
    target_mp4 = _absolute_path(output_path) if output_path else package_path.with_suffix(".mp4")

    product_images: dict[str, Any] | None = None
    with timings.measure("product_images_ms"):
        if product_image_mode != "skip":
            product_images = workflow.regenerate_product_card_images(
                project_id,
                account_label=account,
                mode=product_image_mode,
                product_uid="",
                product_card_template_id=product_card_template_id,
            )
        if product_images.get("ok") is False:
            return {
                "ok": False,
                "stage": "product_images",
                "product_images": product_images,
            }

    with timings.measure("render_package_ms"):
        package_result = workflow.prepare_product_recommendation_output(
            project_id,
            account_label=account,
            output_mode="final_mp4",
            product_media_mode=product_media_mode,
            product_order_strategy=product_order_strategy,
            stale_product_image_policy=stale_product_image_policy,
            mode=mode,
            top_uids=top_uids,
            product_card_template_id=product_card_template_id,
            package_output_path=str(package_path),
            subtitle_alignment=subtitle_mode,
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
    with timings.measure("build_job_ms"):
        build = command_runner(
            ["python", "-m", "cutme", "--package", str(package_path), "--build-render-job"],
            cwd=Path(cutme_root),
            timeout=600,
        )
    job_package_path = _parse_job_package_path(_command_stdout(build))

    with timings.measure("cutme_render_ms"):
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
                "--cache-dir",
                str(clip_cache_dir),
            ],
            cwd=Path(cutme_root),
            timeout=7200,
        )

    full_target_mp4: Path | None = None
    concat_result: Any | None = None
    intro_subtitle_ass_path: Path | None = None
    intro_subtitle_report: dict[str, Any] | None = None
    if intro_video_path:
        intro_mp4 = _absolute_path(intro_video_path)
        if not intro_mp4.is_file():
            raise FileNotFoundError(f"intro video does not exist: {intro_mp4}")
        full_target_mp4 = (
            _absolute_path(full_output_path)
            if full_output_path
            else target_mp4.with_name(f"{target_mp4.stem}-with-intro{target_mp4.suffix}")
        )
        intro_text = safe_text(intro_video_text).strip()
        intro_source_plan_path = _absolute_path(intro_video_source_plan_path) if intro_video_source_plan_path else None
        if intro_source_plan_path or intro_text:
            intro_subtitle_ass_path = (
                delivery_layout["process_dir"] / "intro-subtitles.ass"
                if delivery_layout
                else full_target_mp4.parent / "intro-subtitles.ass"
            )
            intro_subtitle_report = _write_intro_subtitles_ass(
                intro_subtitle_ass_path,
                intro_mp4,
                intro_text,
                intro_source_plan_path=intro_source_plan_path,
                subtitle_alignment=subtitle_mode,
                duration=_video_duration_seconds(intro_mp4, probe_video or _probe_video),
            )
        elif _looks_like_subtitled_intro_video(intro_mp4):
            intro_subtitle_report = {
                "required": True,
                "status": "ready",
                "source": "embedded_intro_mp4",
                "event_count": None,
                "ass_path": None,
                "source_plan_path": None,
            }
        else:
            raise ValueError(
                "intro subtitle blocked: intro video was provided, but neither source plan nor fallback text was provided."
            )
        with timings.measure("concat_intro_ms"):
            concat_result = _concat_intro_and_product_video(
                intro_mp4,
                target_mp4,
                full_target_mp4,
                intro_subtitle_ass_path=intro_subtitle_ass_path,
                cwd=Path(cutme_root),
                runner=command_runner,
            )
    else:
        timings.set_zero("concat_intro_ms")

    verification_target = full_target_mp4 or target_mp4
    with timings.measure("ffprobe_ms"):
        ffprobe_result = (probe_video or _probe_video)(verification_target)
    loudnorm_result = None
    if acceptance == "full":
        with timings.measure("loudnorm_ms"):
            loudnorm_result = (measure_loudness or _measure_loudness)(verification_target)
    else:
        timings.set_zero("loudnorm_ms")
    frames: list[dict[str, Any]] = []
    if acceptance in {"visual", "full"}:
        with timings.measure("acceptance_frames_ms"):
            frames = _extract_acceptance_frames(
                verification_target,
                package_path,
                cwd=Path(cutme_root),
                runner=command_runner,
                frame_dir=delivery_layout["frames_dir"] if delivery_layout else None,
                intro_offset=_video_duration_seconds(_absolute_path(intro_video_path), probe_video or _probe_video)
                if full_target_mp4 and intro_video_path
                else 0.0,
            )
    else:
        timings.set_zero("acceptance_frames_ms")
    package_payload = json.loads(package_path.read_text(encoding="utf-8-sig"))
    price_transition_report = _price_transition_report(package_payload, mode=mode, top_uids=top_uids)
    clip_cache_manifest = _find_clip_cache_manifest(clip_cache_manifest_path)

    result = {
        "ok": True,
        "project_id": project_id,
        "account": account,
        "product_media_mode": product_media_mode,
        "product_order_strategy": product_order_strategy,
        "subtitle_alignment": subtitle_mode,
        "product_image_mode": product_image_mode,
        "product_card_template_id": safe_text(product_card_template_id) or None,
        "package_path": str(package_path),
        "job_package_path": str(job_package_path),
        "output_mp4": str(target_mp4),
        "output_mp4_link": _markdown_link("打开完整 MP4", target_mp4),
        "full_output_mp4": str(full_target_mp4) if full_target_mp4 else None,
        "full_output_mp4_link": _markdown_link("打开完整 MP4", full_target_mp4) if full_target_mp4 else None,
        "intro_video_path": str(_absolute_path(intro_video_path)) if intro_video_path else None,
        "intro_subtitle_ass_path": str(intro_subtitle_ass_path) if intro_subtitle_ass_path else None,
        "intro_subtitle_source_plan_path": str(_absolute_path(intro_video_source_plan_path)) if intro_video_source_plan_path else None,
        "intro_subtitles": intro_subtitle_report,
        "acceptance_mode": acceptance,
        "product_images": product_images,
        "render_package": package_result,
        "cutme": {
            "build": _command_summary(build),
            "render": _command_summary(render),
            "concat_intro": _command_summary(concat_result) if concat_result is not None else None,
            "clip_cache_dir": str(clip_cache_dir),
            "clip_cache_manifest": str(clip_cache_manifest) if clip_cache_manifest else None,
            "clip_cache": _read_clip_cache_summary(clip_cache_manifest) if clip_cache_manifest else None,
        },
        "verification": {
            "ffprobe": ffprobe_result,
            "loudnorm": loudnorm_result,
            "full_ffprobe": ffprobe_result if full_target_mp4 else None,
        },
        "price_transition_report": price_transition_report,
        "frames": frames,
    }
    if delivery_layout:
        result["delivery"] = _delivery_result(delivery_layout)
    result["timings"] = timings.finish()
    run_manifest_path = _write_final_video_run_manifest(
        project_id=project_id,
        timestamp=timestamp,
        package=package_payload,
        result=result,
        target_mp4=target_mp4,
        full_target_mp4=full_target_mp4,
        intro_video_path=_absolute_path(intro_video_path) if intro_video_path else None,
        intro_video_source_plan_path=_absolute_path(intro_video_source_plan_path) if intro_video_source_plan_path else None,
    )
    result["run_manifest_path"] = str(run_manifest_path)
    result["run_manifest_link"] = _markdown_link("打开本次生成记录", run_manifest_path)
    if pipeline_path:
        _record_final_video_pipeline(
            pipeline_path=_absolute_path(pipeline_path),
            result=result,
            run_manifest_path=run_manifest_path,
            target_mp4=target_mp4,
            full_target_mp4=full_target_mp4,
        )
    return result


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


def _delivery_layout(
    delivery_dir: str | Path | None,
    *,
    account: str,
    timestamp: str,
) -> dict[str, Path] | None:
    if not delivery_dir:
        return None
    root = _absolute_path(delivery_dir)
    root.mkdir(parents=True, exist_ok=True)
    evidence_dir = root / "02_验收证据" / timestamp
    process_dir = root / "03_过程记录" / timestamp
    frames_dir = evidence_dir / "frames"
    return {
        "dir": root,
        "evidence_dir": evidence_dir,
        "process_dir": process_dir,
        "frames_dir": frames_dir,
        "product_mp4": root / f"商品推荐段-{timestamp}.mp4",
        "full_mp4": root / f"完整成片-{timestamp}.mp4",
        "package_path": process_dir / "render-package.json",
    }


def _delivery_result(layout: dict[str, Path]) -> dict[str, str]:
    return {
        "dir": str(layout["dir"]),
        "evidence_dir": str(layout["evidence_dir"]),
        "process_dir": str(layout["process_dir"]),
        "frames_dir": str(layout["frames_dir"]),
    }


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
    frame_dir: Path | None = None,
    intro_offset: float = 0.0,
) -> list[dict[str, Any]]:
    package = json.loads(package_path.read_text(encoding="utf-8"))
    frame_specs = _acceptance_frame_specs(package)
    frame_dir = frame_dir or target_mp4.parent / f"{target_mp4.stem}-frames"
    frame_dir.mkdir(parents=True, exist_ok=True)
    frames: list[dict[str, Any]] = []
    for spec in frame_specs:
        frame_path = frame_dir / f"{spec['label']}.png"
        runner(
            [
                "ffmpeg",
                "-y",
                "-ss",
                _format_seconds(float(spec["time"]) + intro_offset),
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
                "time": spec["time"] + intro_offset,
                "package_time": spec["time"],
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


def _concat_intro_and_product_video(
    intro_mp4: Path,
    product_mp4: Path,
    output_mp4: Path,
    *,
    intro_subtitle_ass_path: Path | None = None,
    cwd: Path,
    runner: Runner,
) -> Any:
    output_mp4.parent.mkdir(parents=True, exist_ok=True)
    intro_video_filter = "[0:v]scale=1920:1080:flags=lanczos,setsar=1,fps=30,format=yuv420p"
    if intro_subtitle_ass_path:
        intro_video_filter += f",subtitles='{_ffmpeg_filter_path(intro_subtitle_ass_path)}'"
    intro_video_filter += "[v0];"
    filter_complex = (
        intro_video_filter
        + "[1:v]scale=1920:1080:flags=lanczos,setsar=1,fps=30,format=yuv420p[v1];"
        "[0:a]aformat=sample_rates=48000:channel_layouts=stereo[a0];"
        "[1:a]aformat=sample_rates=48000:channel_layouts=stereo[a1];"
        "[v0][a0][v1][a1]concat=n=2:v=1:a=1[v][a];"
        "[a]loudnorm=I=-11:TP=-1:LRA=11,aresample=48000[aout]"
    )
    return runner(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-i",
            str(intro_mp4),
            "-i",
            str(product_mp4),
            "-filter_complex",
            filter_complex,
            "-map",
            "[v]",
            "-map",
            "[aout]",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-r",
            "30",
            "-c:a",
            "aac",
            "-ar",
            "48000",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            str(output_mp4),
        ],
        cwd=cwd,
        timeout=7200,
    )


def _write_intro_subtitles_ass(
    output_path: Path,
    intro_mp4: Path,
    text: str,
    *,
    intro_source_plan_path: Path | None = None,
    subtitle_alignment: str,
    duration: float,
) -> dict[str, Any]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    plan_events = _intro_subtitle_events_from_source_plan(intro_source_plan_path)
    if plan_events:
        events = plan_events
        source = "source_plan"
    elif safe_text(subtitle_alignment) == "asr":
        events = align_subtitle_text_with_asr(intro_mp4, text, 0.0)
        source = "asr"
    else:
        events = distribute_subtitle_text(text, 0.0, max(0.0, float(duration or 0.0)))
        source = "fallback_text"
    if not events:
        source_plan_note = " source plan produced zero events." if intro_source_plan_path else ""
        raise ValueError(
            "intro subtitle blocked: no intro subtitle events were generated."
            + source_plan_note
            + " Provide a timed source plan or --intro-video-text-file."
        )
    output_path.write_text(_build_intro_ass(events), encoding="utf-8")
    return {
        "required": True,
        "status": "ready",
        "source": source,
        "event_count": len(events),
        "ass_path": str(output_path),
        "source_plan_path": str(intro_source_plan_path) if intro_source_plan_path else None,
    }


def _intro_subtitle_events_from_source_plan(path: Path | None) -> list[tuple[float, float, str]]:
    if path is None:
        return []
    events: list[tuple[float, float, str]] = []
    for item in intro_subtitle_events_from_plan(path):
        try:
            start = float(item.get("start") or 0.0)
            end = float(item.get("end") or 0.0)
        except (AttributeError, TypeError, ValueError):
            continue
        text = safe_text(item.get("text") if isinstance(item, dict) else "")
        if text and end > start:
            events.append((start, end, text))
    return events


def _looks_like_subtitled_intro_video(path: Path) -> bool:
    stem = path.stem.casefold()
    return any(marker in stem for marker in ("subtitle", "subtitled", "字幕"))


def _build_intro_ass(events: list[tuple[float, float, str]]) -> str:
    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        "PlayResX: 1920",
        "PlayResY: 1080",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        "Style: Default,Microsoft YaHei,54,&H00FFFFFF,&H00FFFFFF,&H00222222,&H99000000,0,0,0,0,100,100,0,0,1,4,1,2,110,110,78,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    for start, end, text in events:
        if end <= start:
            continue
        lines.append(
            "Dialogue: 0,"
            f"{_ass_timestamp(start)},{_ass_timestamp(end)},Default,,0,0,0,,{_escape_ass_text(text)}"
        )
    return "\n".join(lines) + "\n"


def _ass_timestamp(seconds: float) -> str:
    centiseconds = max(0, int(round(float(seconds or 0.0) * 100)))
    hours, rem = divmod(centiseconds, 360000)
    minutes, rem = divmod(rem, 6000)
    secs, centis = divmod(rem, 100)
    return f"{hours}:{minutes:02}:{secs:02}.{centis:02}"


def _escape_ass_text(text: object) -> str:
    return safe_text(text).replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}").replace("\n", "\\N")


def _ffmpeg_filter_path(path: Path) -> str:
    value = str(path.resolve()).replace("\\", "/")
    return value.replace(":", "\\:").replace("'", "\\'")


def _video_duration_seconds(path: Path, probe_video: ProbeVideo) -> float:
    try:
        result = probe_video(path)
        return float(result.get("duration") or 0.0)
    except Exception:
        return 0.0


def _find_clip_cache_manifest(candidate: Path) -> Path | None:
    return candidate if candidate.is_file() else None


def _read_clip_cache_summary(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {"manifest_path": str(path), "readable": False}
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    return {
        "manifest_path": str(path),
        "readable": True,
        "segments_total": summary.get("segments_total", 0),
        "cache_hits": summary.get("cache_hits", 0),
        "rendered": summary.get("rendered", 0),
    }


def _write_final_video_run_manifest(
    *,
    project_id: int,
    timestamp: str,
    package: dict[str, Any],
    result: dict[str, Any],
    target_mp4: Path,
    full_target_mp4: Path | None,
    intro_video_path: Path | None,
    intro_video_source_plan_path: Path | None = None,
) -> Path:
    run_dir = INTERNAL_WORKSPACE_ROOT / f"project-{project_id}" / "runs"
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = run_dir / f"final-video-{timestamp}.run-manifest.json"
    payload = _final_video_run_manifest_payload(
        package=package,
        result=result,
        target_mp4=target_mp4,
        full_target_mp4=full_target_mp4,
        intro_video_path=intro_video_path,
        intro_video_source_plan_path=intro_video_source_plan_path,
    )
    manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return manifest_path


def _record_final_video_pipeline(
    *,
    pipeline_path: Path,
    result: dict[str, Any],
    run_manifest_path: Path,
    target_mp4: Path,
    full_target_mp4: Path | None,
) -> None:
    try:
        payload = json.loads(pipeline_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    phases = payload.get("phases") if isinstance(payload.get("phases"), dict) else {}
    assembly = phases.get("assembly") if isinstance(phases.get("assembly"), dict) else {}
    price_report = result.get("price_transition_report") if isinstance(result.get("price_transition_report"), dict) else {}
    now = datetime.now().isoformat(timespec="seconds")
    final_mp4_path = str(full_target_mp4 or target_mp4)
    assembly.update(
        {
            "status": "done",
            "account": result.get("account"),
            "product_card_template_id": result.get("product_card_template_id"),
            "product_media_mode": result.get("product_media_mode"),
            "mode": _safe_text_or_default(price_report.get("mode"), result.get("mode")),
            "top_uids": price_report.get("top_uids") or [],
            "product_order_strategy": result.get("product_order_strategy"),
            "final_mp4_path": final_mp4_path,
            "product_only_mp4_path": str(target_mp4),
            "run_manifest_path": str(run_manifest_path),
            "package_path": result.get("package_path"),
            "job_package_path": result.get("job_package_path"),
            "acceptance_frames": result.get("frames") or [],
            "verification": result.get("verification") or {},
            "price_transition_report": price_report,
            "clip_cache": result.get("cutme", {}).get("clip_cache"),
            "timings": result.get("timings") or {},
            "updated_at": now,
        }
    )
    phases["assembly"] = assembly
    payload["phases"] = phases

    paths = payload.get("paths") if isinstance(payload.get("paths"), dict) else {}
    paths.update(
        {
            "manifest": str(run_manifest_path),
            "final_mp4": final_mp4_path,
            "render_package": result.get("package_path"),
            "job_package": result.get("job_package_path"),
        }
    )
    payload["paths"] = paths
    payload["current_phase"] = "assembly"
    payload["next_action"] = (
        "完整 MP4 已生成；下一步进入发布准备：标题、封面文案、简介、投票、评论区材料。"
    )
    payload["updated_at"] = now
    pipeline_path.parent.mkdir(parents=True, exist_ok=True)
    pipeline_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _final_video_run_manifest_payload(
    *,
    package: dict[str, Any],
    result: dict[str, Any],
    target_mp4: Path,
    full_target_mp4: Path | None,
    intro_video_path: Path | None,
    intro_video_source_plan_path: Path | None = None,
) -> dict[str, Any]:
    package_path = Path(safe_text(result.get("package_path")))
    job_package_path = Path(safe_text(result.get("job_package_path")))
    clip_cache_manifest = safe_text(result.get("cutme", {}).get("clip_cache_manifest"))
    frame_paths = [Path(frame["path"]) for frame in result.get("frames") or [] if safe_text(frame.get("path"))]
    fingerprint_targets: list[tuple[str, Path | None]] = [
        ("render_package", package_path),
        ("cutme_job_package", job_package_path),
        ("intro_video", intro_video_path),
        ("intro_source_plan", intro_video_source_plan_path),
        ("product_mp4", target_mp4),
        ("full_mp4", full_target_mp4),
        ("clip_cache_manifest", Path(clip_cache_manifest) if clip_cache_manifest else None),
    ]
    fingerprint_targets.extend((f"acceptance_frame:{path.stem}", path) for path in frame_paths)

    return {
        "schemaVersion": "1.0.0",
        "kind": "bworkflow.final_video_run",
        "createdAt": datetime.now().isoformat(timespec="seconds"),
        "asset_model": {
            "asset_library": "reusable_copy_and_parameter_assets",
            "pipeline": "this_run_selection",
            "run_manifest": "generation_evidence",
            "note": (
                "Copy, intro, price-transition, parameter, voice, and product-card assets are reusable inputs; "
                "this manifest records one concrete generation run and may reference outputs that are later moved or deleted."
            ),
        },
        "project": {
            "id": result.get("project_id"),
            "account": result.get("account"),
        },
        "selection": {
            "product_media_mode": result.get("product_media_mode"),
            "product_order_strategy": result.get("product_order_strategy"),
            "product_image_mode": result.get("product_image_mode"),
            "product_card_template_id": result.get("product_card_template_id"),
            "mode": _safe_text_or_default(result.get("price_transition_report", {}).get("mode"), result.get("mode")),
            "top_uids": result.get("price_transition_report", {}).get("top_uids") or [],
            "acceptance_mode": result.get("acceptance_mode"),
        },
        "inputs": {
            "render_package_path": result.get("package_path"),
            "cutme_job_package_path": result.get("job_package_path"),
            "intro_video_path": str(intro_video_path) if intro_video_path else None,
            "intro_video_source_plan_path": str(intro_video_source_plan_path) if intro_video_source_plan_path else None,
            "intro_subtitles": result.get("intro_subtitles"),
        },
        "outputs": {
            "product_mp4": str(target_mp4),
            "full_mp4": str(full_target_mp4) if full_target_mp4 else None,
            "acceptance_frames": result.get("frames") or [],
        },
        "delivery": result.get("delivery") or None,
        "segments": _run_manifest_segments(package),
        "segment_fingerprints": _segment_fingerprints(package),
        "file_fingerprints": [_file_fingerprint(role, path) for role, path in fingerprint_targets],
        "reports": {
            "price_transition_report": result.get("price_transition_report"),
            "clip_cache": result.get("cutme", {}).get("clip_cache"),
            "verification": result.get("verification"),
            "timings": result.get("timings"),
        },
    }


def _safe_text_or_default(value: Any, default: Any) -> str:
    text = safe_text(value)
    return text or safe_text(default)


def _run_manifest_segments(package: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    products: list[dict[str, Any]] = []
    price_transitions: list[dict[str, Any]] = []
    for position, segment in enumerate(package.get("segments") or [], start=1):
        if not isinstance(segment, dict):
            continue
        segment_type = safe_text(segment.get("type"))
        if segment_type == "product_recommendation":
            products.append(
                {
                    "position": position,
                    "uid": _segment_product_uid(segment),
                    "title": safe_text(segment.get("title") or segment.get("productTitle")),
                    "voiceAsset": safe_text(segment.get("voiceAsset")),
                    "imageCardAsset": safe_text(segment.get("imageCardAsset")),
                    "videoAsset": safe_text(segment.get("videoAsset")),
                }
            )
        elif segment_type == "price_transition":
            price_transitions.append(
                {
                    "position": position,
                    "label": safe_text(segment.get("priceRangeLabel")),
                    "voiceAsset": safe_text(segment.get("voiceAsset")),
                    "text": safe_text(segment.get("transitionText")),
                }
            )
    return {"products": products, "price_transitions": price_transitions}


def _segment_product_uid(segment: dict[str, Any]) -> str:
    return safe_text(segment.get("productUid") or segment.get("uid") or segment.get("product_id"))


def _segment_fingerprints(package: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for position, segment in enumerate(package.get("segments") or [], start=1):
        if not isinstance(segment, dict):
            continue
        semantic = _semantic_segment_payload(segment)
        items.append(
            {
                "position": position,
                "type": safe_text(segment.get("type")),
                "uid": _segment_product_uid(segment),
                "sha256": _json_sha256(semantic),
            }
        )
    return items


def _semantic_segment_payload(segment: dict[str, Any]) -> dict[str, Any]:
    ignored = {"duration", "start", "end"}
    return {key: value for key, value in segment.items() if key not in ignored}


def _json_sha256(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _file_fingerprint(role: str, path: Path | None) -> dict[str, Any]:
    if path is None:
        return {"role": role, "path": None, "exists": False, "size": 0, "sha256": ""}
    exists = path.is_file()
    return {
        "role": role,
        "path": str(path),
        "exists": exists,
        "size": path.stat().st_size if exists else 0,
        "sha256": _path_sha256(path) if exists else "",
    }


def _path_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _price_transition_report(package: dict[str, Any], *, mode: str, top_uids: str) -> dict[str, Any]:
    top_uid_list = [item.strip() for item in top_uids.split(",") if item.strip()]
    items: list[dict[str, Any]] = []
    product_count_before = 0
    for position, segment in enumerate(package.get("segments") or [], start=1):
        if not isinstance(segment, dict):
            continue
        segment_type = safe_text(segment.get("type"))
        if segment_type == "product_recommendation":
            product_count_before += 1
            continue
        if segment_type != "price_transition":
            continue
        items.append(
            {
                "position": position,
                "label": safe_text(segment.get("priceRangeLabel")),
                "after_products": product_count_before,
                "after_top_products": min(product_count_before, len(top_uid_list)) if mode == "top" else 0,
                "text": safe_text(segment.get("transitionText")),
                "voiceAsset": safe_text(segment.get("voiceAsset")),
            }
        )
    return {
        "count": len(items),
        "mode": mode,
        "top_uids": top_uid_list,
        "summary": _price_transition_summary(items, mode=mode, top_uids=top_uid_list),
        "items": items,
    }


def _price_transition_summary(items: list[dict[str, Any]], *, mode: str, top_uids: list[str]) -> str:
    if not items:
        return "本次 RenderPackage 没有价格过渡段。"
    first = items[0]
    if mode == "top" and top_uids:
        return f"本次价格过渡共 {len(items)} 段；因启用置顶，第一段价格过渡在 {len(top_uids)} 个置顶商品之后出现。"
    return f"本次价格过渡共 {len(items)} 段；第一段在第 {first['position']} 个片段出现。"


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
