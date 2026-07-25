from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .asset_paths import project_category_folder
from .intro_timeline import align_intro_plan_scenes_with_asr
from .settings import CUTME_ROOT, DEFAULT_INTRO_ASSET_ROOT, INTERNAL_WORKSPACE_ROOT
from .render_gate import acquire_production_render_slot, build_render_owner
from .subtitle_helpers import normalize_subtitle_alignment_text, distribute_subtitle_text
from .tts_helpers import normalize_audio_loudness
from .utils import now_iso, safe_text


ALLOWED_INTRO_TEMPLATE_IDS = {"pain_avoidance_priority_v1"}
BLOCKED_INTRO_TEMPLATE_IDS = {"recovered_markdown_intro_v1"}
INTRO_VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv"}
INTRO_MATERIAL_MANIFEST_NAME = "intro-materials.json"


@dataclass(frozen=True)
class PreparedCutMeIntro:
    intro_plan_path: Path
    config_path: Path
    selected_assets: dict[str, Any]
    preflight: dict[str, Any]
    aligned_with_asr: bool


def default_intro_plan_workspace(project_id: int) -> Path:
    return INTERNAL_WORKSPACE_ROOT / f"project-{int(project_id)}" / "intro"


def default_prepared_intro_plan_path(
    *,
    project_id: int,
    script_block_id: int,
    account_label: str,
) -> Path:
    account = _safe_path_part(account_label) or "account"
    return default_intro_plan_workspace(project_id) / f"intro-plan-{script_block_id}-{account}.json"


def default_cutme_config_path(
    *,
    project_id: int,
    script_block_id: int,
    account_label: str,
) -> Path:
    account = _safe_path_part(account_label) or "account"
    return default_intro_plan_workspace(project_id) / f"cutme-config-{script_block_id}-{account}.json"


def find_intro_plan_for_text(project_id: int, intro_text: str) -> Path | None:
    expected = normalize_subtitle_alignment_text(intro_text)
    if not expected:
        return None
    workspace = default_intro_plan_workspace(project_id)
    if not workspace.is_dir():
        return None
    paths = [
        *sorted(workspace.glob("source-intro-plan*.json"), key=lambda item: item.stat().st_mtime, reverse=True),
        *sorted(
            (path for path in workspace.glob("*.json") if not path.name.startswith("source-intro-plan")),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        ),
    ]
    for path in paths:
        try:
            plan = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(plan, dict):
            continue
        full_script = safe_text(plan.get("full_script"))
        if full_script and normalize_subtitle_alignment_text(full_script) == expected:
            return path
    return None


def prepare_intro_plan_for_cutme(
    *,
    source_plan_path: str | Path,
    audio_path: str | Path,
    project: dict[str, Any],
    account_label: str,
    expected_intro_text: str,
    output_plan_path: str | Path,
    asset_root: str | Path = DEFAULT_INTRO_ASSET_ROOT,
    seed: str | None = None,
) -> dict[str, Any]:
    plan_path = Path(source_plan_path)
    if not plan_path.is_file():
        raise FileNotFoundError(f"intro_plan 文件不存在：{plan_path}")

    plan = json.loads(plan_path.read_text(encoding="utf-8-sig"))
    if not isinstance(plan, dict):
        raise ValueError("intro_plan 必须是 JSON 对象")

    _validate_plan_matches_intro_text(plan, expected_intro_text)
    preflight = preflight_intro_plan_for_cutme(
        source_plan_path=plan_path,
        project=project,
        asset_root=asset_root,
    )
    if not preflight.get("ok"):
        raise ValueError(str(preflight.get("message") or "intro preflight failed"))
    plan = _ensure_selected_assets(
        plan,
        project=project,
        account_label=account_label,
        asset_root=asset_root,
        seed=seed,
    )

    aligned_with_asr = False
    if not _has_complete_scene_timing(plan) or _needs_visual_event_alignment(plan):
        plan = align_intro_plan_scenes_with_asr(plan, audio_path)
        aligned_with_asr = True

    plan["pc_workflow"] = {
        "prepared_at": now_iso(),
        "source_plan_path": str(plan_path),
        "asset_root": str(asset_root),
        "category_folder": project_category_folder(project),
        "account_label": account_label,
        "seed": seed or "",
        "aligned_with_asr": aligned_with_asr,
    }

    output_path = Path(output_plan_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_intro_render_report(
        output_path.with_suffix(".report.json"),
        prepared_intro_plan_path=output_path,
        plan=plan,
        preflight=preflight,
        renderer="hyperframes",
    )
    return plan


def prepare_cutme_config(
    *,
    config_path: str | Path,
    intro_plan_path: str | Path,
    audio_path: str | Path,
    intro_text: str,
    title: str,
    asset_folder: str = "",
    subtitle: str = "",
    template: str = "general",
    accent_color: str = "#00D4FF",
    seed: str = "",
) -> dict[str, Any]:
    normalize_audio_loudness(Path(audio_path))
    duration = get_cutme_audio_duration(audio_path)
    intro_subtitles = _intro_subtitle_events_from_plan(intro_plan_path)
    config = {
        "text": intro_text,
        "audio_path": str(audio_path),
        "audio_duration": duration,
        "title": title,
        "subtitle": subtitle,
        "params_points": [],
        "asset_folder": asset_folder,
        "template": template,
        "accent_color": accent_color,
        "seed": seed,
        "intro_plan_path": str(intro_plan_path),
        "subtitles": intro_subtitles,
        "output": {
            "subtitles": {
                "enabled": False,
                "source": "intro_plan_scenes",
                "scope": "final_video_only",
            }
        },
    }
    target = Path(config_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    return config


def intro_subtitle_events_from_plan(intro_plan_path: str | Path) -> list[dict[str, Any]]:
    path = Path(intro_plan_path)
    try:
        plan = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(plan, dict):
        return []

    events: list[dict[str, Any]] = []
    for scene in plan.get("scenes") or []:
        if not isinstance(scene, dict):
            continue
        text = safe_text(scene.get("text"))
        timing = scene.get("timing")
        if not text or not isinstance(timing, dict):
            continue
        try:
            start = max(0.0, float(timing.get("start") or 0.0))
            duration = float(timing.get("duration") or 0.0)
        except (TypeError, ValueError):
            continue
        if duration <= 0:
            continue
        for chunk_start, chunk_end, chunk_text in distribute_subtitle_text(text, start, duration):
            events.append(
                {
                    "start": round(chunk_start, 3),
                    "end": round(chunk_end, 3),
                    "text": chunk_text,
                }
            )
    return events


_intro_subtitle_events_from_plan = intro_subtitle_events_from_plan


def prepare_cutme_intro(
    *,
    source_plan_path: str | Path,
    audio_path: str | Path,
    project: dict[str, Any],
    account_label: str,
    script_block_id: int,
    intro_text: str,
    title: str,
    asset_folder: str = "",
    asset_root: str | Path = DEFAULT_INTRO_ASSET_ROOT,
) -> PreparedCutMeIntro:
    project_id = int(project["id"])
    intro_plan_path = default_prepared_intro_plan_path(
        project_id=project_id,
        script_block_id=script_block_id,
        account_label=account_label,
    )
    config_path = default_cutme_config_path(
        project_id=project_id,
        script_block_id=script_block_id,
        account_label=account_label,
    )
    seed = build_intro_visual_seed(
        project=project,
        account_label=account_label,
        script_block_id=script_block_id,
    )
    plan = prepare_intro_plan_for_cutme(
        source_plan_path=source_plan_path,
        audio_path=audio_path,
        project=project,
        account_label=account_label,
        expected_intro_text=intro_text,
        output_plan_path=intro_plan_path,
        asset_root=asset_root,
        seed=seed,
    )
    prepare_cutme_config(
        config_path=config_path,
        intro_plan_path=intro_plan_path,
        audio_path=audio_path,
        intro_text=intro_text,
        title=title,
        asset_folder=asset_folder,
        seed=seed,
    )
    return PreparedCutMeIntro(
        intro_plan_path=intro_plan_path,
        config_path=config_path,
        selected_assets=dict(plan.get("selected_assets") or {}),
        preflight=dict(plan.get("preflight") or {}),
        aligned_with_asr=bool(plan.get("pc_workflow", {}).get("aligned_with_asr")),
    )


def run_cutme_render(
    config_path: str | Path,
    output_path: str | Path,
    *,
    renderer: str = "remotion",
    render_owner: dict[str, Any] | None = None,
) -> Path:
    config = Path(config_path)
    output = Path(output_path).expanduser()
    output = output.resolve() if output.is_absolute() else (Path.cwd() / output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    cutme_root_text = str(CUTME_ROOT)
    env["PYTHONPATH"] = (
        cutme_root_text
        if not env.get("PYTHONPATH")
        else cutme_root_text + os.pathsep + env["PYTHONPATH"]
    )
    env["PYTHONIOENCODING"] = "utf-8"

    owner = render_owner or build_render_owner(phase="intro_video")
    with acquire_production_render_slot(owner, lock_root=INTERNAL_WORKSPACE_ROOT):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "cutme",
                str(config),
                "--renderer",
                renderer,
                "--output",
                str(output),
                "--clean",
            ],
            cwd=str(CUTME_ROOT),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    if result.returncode != 0:
        details = "\n".join(
            item for item in [result.stdout.strip(), result.stderr.strip()] if item
        )
        raise RuntimeError(details or f"CutMe 渲染失败，退出码 {result.returncode}")
    if not output.is_file():
        details = "\n".join(
            item for item in [result.stdout.strip(), result.stderr.strip()] if item
        )
        raise RuntimeError(f"CutMe 未生成输出文件：{output}\n{details}".strip())
    return output


def preflight_intro_plan_for_cutme(
    *,
    source_plan_path: str | Path,
    project: dict[str, Any],
    asset_root: str | Path = DEFAULT_INTRO_ASSET_ROOT,
    pipeline_path: str | Path | None = None,
) -> dict[str, Any]:
    plan_path = Path(source_plan_path)
    if not plan_path.is_file():
        raise FileNotFoundError(f"intro_plan 文件不存在：{plan_path}")

    plan = json.loads(plan_path.read_text(encoding="utf-8-sig"))
    if not isinstance(plan, dict):
        raise ValueError("intro_plan 必须是 JSON 对象")

    category = project_category_folder(project)
    root = Path(asset_root)
    category_dir = root / category
    common_dir = root / _intro_common_folder_name(plan)
    required = _visual_cue_counts(plan)
    required_product_count = int(required.get("product_demo") or 0)
    required_triple_count = int(required.get("triple_cta") or 0)
    product_videos, material_manifest_path = _intro_product_demo_files(category_dir)
    triple_videos = _matching_triple_cta_files(common_dir, plan)
    template_id = safe_text(plan.get("template_id") or plan.get("templateId"))

    if template_id in BLOCKED_INTRO_TEMPLATE_IDS or (
        template_id and template_id not in ALLOWED_INTRO_TEMPLATE_IDS
    ):
        return _record_intro_preflight_pipeline(
            pipeline_path,
            {
            "ok": False,
            "status": "blocked_wrong_intro_template",
            "message": f"引言源计划模板不正确：{template_id}。请重新用标准引言模板生成 source-intro-plan。",
            "source_intro_plan_path": str(plan_path),
            "template_id": template_id,
            "expected_template_ids": sorted(ALLOWED_INTRO_TEMPLATE_IDS),
            "issues": [
                {
                    "type": "wrong_intro_template",
                    "template_id": template_id,
                    "expected": sorted(ALLOWED_INTRO_TEMPLATE_IDS),
                }
            ],
            "next": {
                "action": "regenerate_intro_plan",
                "command": "python -m bworkflow_sql intro-plan <project_id> --slots <slots.json> --label 引言1 --sync",
            },
            },
        )

    selected = plan.get("selected_assets") if isinstance(plan.get("selected_assets"), dict) else {}
    issues = _selected_intro_asset_issues(
        selected=selected,
        category_dir=category_dir,
        common_dir=common_dir,
        required_product_count=required_product_count,
    )
    if issues:
        return _record_intro_preflight_pipeline(
            pipeline_path,
            _intro_preflight_result(
            ok=False,
            status="blocked_invalid_intro_demo_source",
            message="引言素材来源不符合标准素材池规则，请移除临时素材后重新预检。",
            plan_path=plan_path,
            template_id=template_id,
            root=root,
            category=category,
            category_dir=category_dir,
            common_dir=common_dir,
            material_manifest_path=material_manifest_path,
            required_product_count=required_product_count,
            product_videos=product_videos,
            required_triple_count=required_triple_count,
            triple_videos=triple_videos,
            issues=issues,
            selected=selected,
            next_hint={"action": "remove_invalid_intro_demo_sources"},
            ),
        )

    if required_product_count and len(product_videos) < required_product_count:
        missing = required_product_count - len(product_videos)
        return _record_intro_preflight_pipeline(
            pipeline_path,
            _intro_preflight_result(
            ok=False,
            status="blocked_missing_intro_demo",
            message=f"缺 {missing} 段{category}通用产品展示素材",
            plan_path=plan_path,
            template_id=template_id,
            root=root,
            category=category,
            category_dir=category_dir,
            common_dir=common_dir,
            material_manifest_path=material_manifest_path,
            required_product_count=required_product_count,
            product_videos=product_videos,
            required_triple_count=required_triple_count,
            triple_videos=triple_videos,
            issues=[
                {
                    "type": "missing_intro_product_demo",
                    "required": required_product_count,
                    "available": len(product_videos),
                    "missing": missing,
                    "folder": str(category_dir),
                }
            ],
            selected=selected,
            next_hint={
                "action": "add_intro_product_demo_clips",
                "folder": str(category_dir),
                "needed_count": missing,
            },
            ),
        )

    if required_triple_count and not triple_videos:
        return _record_intro_preflight_pipeline(
            pipeline_path,
            _intro_preflight_result(
            ok=False,
            status="blocked_missing_triple_cta",
            message=f"缺 引导三连 通用视频素材：{common_dir}",
            plan_path=plan_path,
            template_id=template_id,
            root=root,
            category=category,
            category_dir=category_dir,
            common_dir=common_dir,
            material_manifest_path=material_manifest_path,
            required_product_count=required_product_count,
            product_videos=product_videos,
            required_triple_count=required_triple_count,
            triple_videos=triple_videos,
            issues=[
                {
                    "type": "missing_triple_cta",
                    "required": required_triple_count,
                    "available": len(triple_videos),
                    "folder": str(common_dir),
                }
            ],
            selected=selected,
            next_hint={"action": "add_triple_cta_clip", "folder": str(common_dir)},
            ),
        )

    return _record_intro_preflight_pipeline(
        pipeline_path,
        _intro_preflight_result(
        ok=True,
        status="ready",
        message="intro preflight passed",
        plan_path=plan_path,
        template_id=template_id,
        root=root,
        category=category,
        category_dir=category_dir,
        common_dir=common_dir,
        material_manifest_path=material_manifest_path,
        required_product_count=required_product_count,
        product_videos=product_videos,
        required_triple_count=required_triple_count,
        triple_videos=triple_videos,
        issues=[],
        selected=selected,
        next_hint={"action": "prepare_intro_video"},
        ),
    )


def get_cutme_audio_duration(audio_path: str | Path) -> float:
    _ensure_cutme_import_path()
    from cutme.audio import get_audio_duration

    return float(get_audio_duration(audio_path))


def _ensure_selected_assets(
    plan: dict[str, Any],
    *,
    project: dict[str, Any],
    account_label: str,
    asset_root: str | Path,
    seed: str | None,
) -> dict[str, Any]:
    needed = _visual_cue_counts(plan)
    if not needed:
        return plan

    selected = dict(plan.get("selected_assets") or {})
    product_count = int(needed.get("product_demo") or 0)
    triple_count = int(needed.get("triple_cta") or 0)
    has_products = len(selected.get("product_demo") or []) >= product_count
    has_triple = not triple_count or bool(selected.get("triple_cta"))
    sfx_contract = plan.get("sfx_contract")
    has_sfx = not isinstance(sfx_contract, dict) or bool(selected.get("sfx"))
    if has_products and has_triple and has_sfx:
        return plan

    asset_contract = plan.get("asset_contract")
    if not isinstance(asset_contract, dict) or not asset_contract:
        raise ValueError("intro_plan 缺少 asset_contract，不能自动选择产品展示和引导三连素材")

    _ensure_cutme_import_path()
    from cutme.intro_assets import resolve_intro_assets

    asset_selection = resolve_intro_assets(
        asset_root=asset_root,
        category_folder=project_category_folder(project),
        asset_contract=asset_contract,
        sfx_contract=sfx_contract if isinstance(sfx_contract, dict) else None,
        scenes=list(plan.get("scenes") or []),
        seed=seed or f"{account_label}-{project_category_folder(project)}",
    )
    errors = (asset_selection.get("preflight") or {}).get("errors") or []
    if errors:
        raise ValueError("引言素材预检查失败：\n" + "\n".join(f"- {item}" for item in errors))

    result = dict(plan)
    asset_selection["selected_assets"] = {
        **selected,
        **dict(asset_selection.get("selected_assets") or {}),
    }
    result.update(asset_selection)
    return result


def _validate_plan_matches_intro_text(plan: dict[str, Any], expected_intro_text: str) -> None:
    full_script = safe_text(plan.get("full_script"))
    expected = safe_text(expected_intro_text)
    if not full_script or not expected:
        return
    if normalize_subtitle_alignment_text(full_script) != normalize_subtitle_alignment_text(expected):
        raise ValueError("intro_plan full_script 与当前引言文案不一致，请重新生成或选择匹配的引言计划 JSON")


def _has_complete_scene_timing(plan: dict[str, Any]) -> bool:
    scenes = plan.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        return False
    for scene in scenes:
        if not isinstance(scene, dict):
            return False
        timing = scene.get("timing")
        if not isinstance(timing, dict):
            return False
        try:
            start = float(timing.get("start"))
            duration = float(timing.get("duration"))
        except (TypeError, ValueError):
            return False
        if start < 0 or duration <= 0:
            return False
    return True


def _needs_visual_event_alignment(plan: dict[str, Any]) -> bool:
    specs = plan.get("visual_event_specs")
    if not isinstance(specs, list) or not specs:
        return False
    events = plan.get("visual_events")
    if not isinstance(events, list) or len(events) < len(specs):
        return True
    for event in events:
        if not isinstance(event, dict):
            return True
        timing = event.get("timing")
        if not isinstance(timing, dict):
            return True
        try:
            start = float(timing.get("start"))
            duration = float(timing.get("duration"))
        except (TypeError, ValueError):
            return True
        if start < 0 or duration <= 0:
            return True
    return False


def _visual_cue_counts(plan: dict[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for scene in plan.get("scenes") or []:
        if not isinstance(scene, dict):
            continue
        for cue in scene.get("visual_cues") or []:
            if not isinstance(cue, dict):
                continue
            role = safe_text(cue.get("clip_role"))
            if role:
                counts[role] = counts.get(role, 0) + 1
    return counts


def _intro_common_folder_name(plan: dict[str, Any]) -> str:
    asset_contract = plan.get("asset_contract")
    if isinstance(asset_contract, dict):
        value = safe_text(asset_contract.get("common_folder_name"))
        if value:
            return value
    return "1-通用"


def _list_intro_video_files(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    return sorted(
        path
        for path in folder.iterdir()
        if path.is_file() and path.suffix.lower() in INTRO_VIDEO_EXTS
    )


def _intro_product_demo_files(folder: Path) -> tuple[list[Path], Path | None]:
    manifest_path = folder / INTRO_MATERIAL_MANIFEST_NAME
    if not manifest_path.is_file():
        return _list_intro_video_files(folder), None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return [], manifest_path
    items = manifest.get("materials") if isinstance(manifest, dict) else []
    result: list[Path] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        if safe_text(item.get("role")) != "product_demo":
            continue
        status = safe_text(item.get("status")).casefold()
        approved = bool(item.get("approved")) or status == "approved"
        if not approved:
            continue
        file_value = safe_text(item.get("file") or item.get("path"))
        if not file_value:
            continue
        path = Path(file_value)
        if not path.is_absolute():
            path = folder / path
        if path.is_file() and path.suffix.lower() in INTRO_VIDEO_EXTS and _path_is_under(path, folder):
            result.append(path)
    return sorted(dict.fromkeys(result)), manifest_path


def _matching_triple_cta_files(common_dir: Path, plan: dict[str, Any]) -> list[Path]:
    keywords = _clip_slot_keywords(plan, "triple_cta") or ["引导三连"]
    return [
        path
        for path in _list_intro_video_files(common_dir)
        if any(keyword in path.stem for keyword in keywords)
    ]


def _clip_slot_keywords(plan: dict[str, Any], role: str) -> list[str]:
    asset_contract = plan.get("asset_contract")
    if not isinstance(asset_contract, dict):
        return []
    for slot in asset_contract.get("clip_slots") or []:
        if not isinstance(slot, dict) or slot.get("role") != role:
            continue
        return [safe_text(item) for item in slot.get("match_keywords") or [] if safe_text(item)]
    return []


def _selected_intro_asset_issues(
    *,
    selected: dict[str, Any],
    category_dir: Path,
    common_dir: Path,
    required_product_count: int,
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    selected_products = list(selected.get("product_demo") or [])
    if selected_products:
        if len(selected_products) < required_product_count:
            issues.append(
                {
                    "type": "selected_product_demo_count_shortage",
                    "required": required_product_count,
                    "selected": len(selected_products),
                }
            )
        for path in selected_products:
            if not _path_is_under(path, category_dir):
                issues.append(
                    {
                        "type": "selected_product_demo_outside_material_pool",
                        "path": str(path),
                        "expected_folder": str(category_dir),
                    }
                )

    selected_triple = safe_text(selected.get("triple_cta"))
    if selected_triple and not _path_is_under(selected_triple, common_dir):
        issues.append(
            {
                "type": "selected_triple_cta_outside_common_pool",
                "path": selected_triple,
                "expected_folder": str(common_dir),
            }
        )
    return issues


def _path_is_under(path: str | Path, parent: str | Path) -> bool:
    try:
        Path(path).resolve().relative_to(Path(parent).resolve())
        return True
    except (OSError, ValueError):
        return False


def _intro_preflight_result(
    *,
    ok: bool,
    status: str,
    message: str,
    plan_path: Path,
    template_id: str,
    root: Path,
    category: str,
    category_dir: Path,
    common_dir: Path,
    material_manifest_path: Path | None,
    required_product_count: int,
    product_videos: list[Path],
    required_triple_count: int,
    triple_videos: list[Path],
    issues: list[dict[str, Any]],
    selected: dict[str, Any],
    next_hint: dict[str, Any],
) -> dict[str, Any]:
    return {
        "ok": ok,
        "status": status,
        "message": message,
        "source_intro_plan_path": str(plan_path),
        "template_id": template_id,
        "asset_root": str(root),
        "category_folder": category,
        "category_material_folder": str(category_dir),
        "material_manifest_path": str(material_manifest_path) if material_manifest_path else "",
        "common_material_folder": str(common_dir),
        "requirements": {
            "product_demo": {
                "required": required_product_count,
                "available": len(product_videos),
                "files": [str(path) for path in product_videos],
            },
            "triple_cta": {
                "required": required_triple_count,
                "available": len(triple_videos),
                "files": [str(path) for path in triple_videos],
            },
        },
        "selected_assets": selected,
        "issues": issues,
        "next": next_hint,
    }


def _record_intro_preflight_pipeline(
    pipeline_path: str | Path | None,
    result: dict[str, Any],
) -> dict[str, Any]:
    if not pipeline_path:
        return result
    path = Path(pipeline_path)
    try:
        existing = json.loads(path.read_text(encoding="utf-8-sig")) if path.is_file() else {}
    except (OSError, json.JSONDecodeError):
        existing = {}
    if not isinstance(existing, dict):
        existing = {}

    phases = existing.get("phases") if isinstance(existing.get("phases"), dict) else {}
    intro_phase = phases.get("intro_video") if isinstance(phases.get("intro_video"), dict) else {}
    updated_at_utc = now_iso()
    updated_at_local = datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")
    intro_phase.update(
        {
            "status": "ready" if result.get("ok") else "blocked",
            "preflight_status": safe_text(result.get("status")),
            "source_intro_plan_path": safe_text(result.get("source_intro_plan_path")),
            "updated_at": updated_at_utc,
            "updated_at_utc": updated_at_utc,
            "updated_at_local": updated_at_local,
        }
    )
    if result.get("ok"):
        intro_phase.pop("last_error", None)
    else:
        intro_phase["last_error"] = {
            "code": safe_text(result.get("status")),
            "message": safe_text(result.get("message")),
        }

    phases["intro_video"] = intro_phase
    existing["phases"] = phases
    existing["current_phase"] = "intro_video"
    existing["resume_hint"] = result.get("next") or {}
    if result.get("ok"):
        existing.pop("last_error", None)
    else:
        existing["last_error"] = {
            "code": safe_text(result.get("status")),
            "message": safe_text(result.get("message")),
        }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def _write_intro_render_report(
    path: Path,
    *,
    prepared_intro_plan_path: Path,
    plan: dict[str, Any],
    preflight: dict[str, Any],
    renderer: str,
) -> None:
    report = {
        **preflight,
        "renderer": renderer,
        "selected_assets": dict(plan.get("selected_assets") or {}),
        "pc_workflow": dict(plan.get("pc_workflow") or {}),
        "prepared_intro_plan_path": str(prepared_intro_plan_path),
        "acceptance_checklist": _intro_acceptance_checklist(preflight),
    }
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def _intro_acceptance_checklist(preflight: dict[str, Any]) -> dict[str, Any]:
    return {
        "must_report_to_user": True,
        "requires_user_approval_before_phase_7": True,
        "items": [
            "核对引言模板为 pain_avoidance_priority_v1",
            "核对 product_demo 素材均来自标准品类素材池",
            "核对 triple_cta 素材来自通用素材池",
            "抽帧检查片头画面和产品展示不跑偏",
            "用户确认 OK 后再进入阶段 7",
        ],
        "source_intro_plan_path": safe_text(preflight.get("source_intro_plan_path")),
        "material_manifest_path": safe_text(preflight.get("material_manifest_path")),
    }


def _ensure_cutme_import_path() -> None:
    root = str(CUTME_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)


def build_intro_visual_seed(
    *,
    project: dict[str, Any],
    account_label: str,
    script_block_id: int,
) -> str:
    # Deliberately not derived from account/category/script id: each render should
    # get fresh visual randomization even for the same intro.
    return f"intro-{now_iso()}-{secrets.token_hex(6)}"


def _safe_path_part(value: str) -> str:
    text = safe_text(value)
    for char in '<>:"/\\|?*':
        text = text.replace(char, "_")
    return text.strip(" .")
