from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .asset_paths import project_category_folder
from .intro_timeline import align_intro_plan_scenes_with_asr
from .settings import CUTME_ROOT, DEFAULT_INTRO_ASSET_ROOT, INTERNAL_WORKSPACE_ROOT
from .subtitle_helpers import normalize_subtitle_alignment_text
from .tts_helpers import normalize_audio_loudness
from .utils import now_iso, safe_text


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
    for path in sorted(workspace.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
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
        "aligned_with_asr": aligned_with_asr,
    }

    output_path = Path(output_plan_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
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
) -> dict[str, Any]:
    normalize_audio_loudness(Path(audio_path))
    duration = get_cutme_audio_duration(audio_path)
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
        "intro_plan_path": str(intro_plan_path),
    }
    target = Path(config_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    return config


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
    seed = f"{account_label}-{project_category_folder(project)}-{now_iso()}"
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
    )
    return PreparedCutMeIntro(
        intro_plan_path=intro_plan_path,
        config_path=config_path,
        selected_assets=dict(plan.get("selected_assets") or {}),
        preflight=dict(plan.get("preflight") or {}),
        aligned_with_asr=bool(plan.get("pc_workflow", {}).get("aligned_with_asr")),
    )


def run_cutme_render(config_path: str | Path, output_path: str | Path) -> Path:
    config = Path(config_path)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    cutme_root_text = str(CUTME_ROOT)
    env["PYTHONPATH"] = (
        cutme_root_text
        if not env.get("PYTHONPATH")
        else cutme_root_text + os.pathsep + env["PYTHONPATH"]
    )
    env["PYTHONIOENCODING"] = "utf-8"

    result = subprocess.run(
        [sys.executable, "-m", "cutme", str(config), "--output", str(output), "--clean"],
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
        seed=seed or f"{account_label}-{now_iso()}",
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


def _ensure_cutme_import_path() -> None:
    root = str(CUTME_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)


def _safe_path_part(value: str) -> str:
    text = safe_text(value)
    for char in '<>:"/\\|?*':
        text = text.replace(char, "_")
    return text.strip(" .")
