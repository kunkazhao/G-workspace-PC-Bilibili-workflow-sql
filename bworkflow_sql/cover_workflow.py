from __future__ import annotations

import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from secrets import choice
from typing import Any
from uuid import uuid4

from PIL import Image

from .artifact_approvals import atomic_update_pipeline, build_artifact_approval, sha256_file
from .final_spoken_script import validate_spoken_script_evidence
from .settings import APP_ROOT, INTERNAL_WORKSPACE_ROOT
from .utils import now_iso, safe_text


COVER_PROMPTS_PATH = APP_ROOT / "config" / "cover-prompts.json"


def cover_context(pipeline_path: str | Path) -> dict[str, Any]:
    pipeline, payload, manifest_path, manifest, spoken = _confirmed_production_context(pipeline_path)
    account = safe_text(payload.get("account"))
    config = _account_config(account)
    portrait = _portrait_path(config)
    if not portrait.is_file():
        raise FileNotFoundError(f"account portrait does not exist: {portrait}")
    script_path = Path(spoken["snapshot_path"])
    return {
        "ok": True,
        "pipeline_path": str(pipeline),
        "project_id": int(payload.get("bworkflow_project_id") or 0),
        "category": safe_text(payload.get("category") or payload.get("project_name")),
        "account": account,
        "run_manifest_path": str(manifest_path),
        "spoken_script_path": str(script_path),
        "spoken_script_sha256": sha256_file(script_path),
        "spoken_script": script_path.read_text(encoding="utf-8-sig"),
        "cover_copy_constraints": {
            "candidate_count": 5,
            "recommended_length": "6-12 Chinese characters",
            "maximum_length": 16,
            "requirements": [
                "five distinct editorial angles",
                "no internal workflow labels or price-segment labels",
                "no unsupported product claims",
                "suitable for direct verbatim rendering inside one 4:3 cover",
            ],
        },
        "style": {
            "style_id": config["styleId"],
            "style_version": config["styleVersion"],
            "portrait_path": str(portrait),
        },
    }


def record_cover_copy_options(
    pipeline_path: str | Path,
    *,
    options_file: str | Path,
) -> dict[str, Any]:
    pipeline, payload, manifest_path, _, spoken = _confirmed_production_context(pipeline_path)
    options_payload = json.loads(Path(options_file).expanduser().resolve().read_text(encoding="utf-8-sig"))
    raw_options = options_payload.get("options") if isinstance(options_payload, dict) else options_payload
    if not isinstance(raw_options, list) or len(raw_options) != 5:
        raise ValueError("cover copy options must contain exactly 5 items")
    options: list[str] = []
    for item in raw_options:
        text = safe_text(item.get("text") if isinstance(item, dict) else item).strip()
        if not text or len(text) > 16:
            raise ValueError("each cover copy option must contain 1-16 characters")
        if text in options:
            raise ValueError("cover copy options must be unique")
        options.append(text)
    project_id = int(payload.get("bworkflow_project_id") or 0)
    timestamp = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}-{uuid4().hex[:8]}"
    plan_path = INTERNAL_WORKSPACE_ROOT / f"project-{project_id}" / "covers" / f"copy-options-{timestamp}.json"
    plan = {
        "schemaVersion": "1.0.0",
        "kind": "bworkflow.cover_copy_options",
        "createdAt": now_iso(),
        "projectId": project_id,
        "category": safe_text(payload.get("category") or payload.get("project_name")),
        "account": safe_text(payload.get("account")),
        "productionRunManifestPath": str(manifest_path),
        "productionRunManifestSha256": sha256_file(manifest_path),
        "spokenScriptSnapshotPath": spoken["snapshot_path"],
        "spokenScriptSnapshotSha256": spoken["snapshot_sha256"],
        "options": options,
    }
    _atomic_write_json(plan_path, plan)

    def update(data: dict[str, Any]) -> None:
        phases = data.get("phases") if isinstance(data.get("phases"), dict) else {}
        cover = phases.get("cover") if isinstance(phases.get("cover"), dict) else {}
        cover.update(
            {
                "status": "copy_options_ready",
                "copy_options_path": str(plan_path.resolve()),
                "copy_options_sha256": sha256_file(plan_path),
                "copy_options": options,
                "updated_at": now_iso(),
            }
        )
        cover.pop("selected_copy", None)
        cover.pop("selected_copy_index", None)
        cover.pop("current_attempt", None)
        phases["cover"] = cover
        data["phases"] = phases
        approvals = data.get("artifact_approvals") if isinstance(data.get("artifact_approvals"), dict) else {}
        approvals.pop("cover_image", None)
        data["artifact_approvals"] = approvals
        paths = data.get("paths") if isinstance(data.get("paths"), dict) else {}
        paths.pop("cover_image", None)
        paths.pop("cover_package", None)
        data["paths"] = paths
        data["current_phase"] = "cover"
        data["next_action"] = "confirm_cover_copy"

    atomic_update_pipeline(pipeline, update)
    return {"ok": True, "copy_options_path": str(plan_path.resolve()), "options": options}


def confirm_cover_copy(pipeline_path: str | Path, *, index: int) -> dict[str, Any]:
    pipeline, payload, _, _, _ = _confirmed_production_context(pipeline_path)
    cover = ((payload.get("phases") or {}).get("cover") or {})
    options = _validate_copy_options_evidence(cover)
    if index < 1 or index > len(options):
        raise ValueError(f"cover copy index must be between 1 and {len(options)}")
    selected = safe_text(options[index - 1])

    def update(data: dict[str, Any]) -> None:
        phases = data.get("phases") if isinstance(data.get("phases"), dict) else {}
        item = phases.get("cover") if isinstance(phases.get("cover"), dict) else {}
        item.update(
            {
                "status": "copy_confirmed",
                "selected_copy": selected,
                "selected_copy_index": index,
                "copy_confirmed_at": now_iso(),
                "updated_at": now_iso(),
            }
        )
        item.pop("current_attempt", None)
        phases["cover"] = item
        data["phases"] = phases
        data["current_phase"] = "cover"
        data["next_action"] = "generate_cover_image"

    atomic_update_pipeline(pipeline, update)
    return {"ok": True, "selected_copy": selected, "selected_copy_index": index}


def prepare_cover_generation(pipeline_path: str | Path) -> dict[str, Any]:
    pipeline, payload, manifest_path, _, spoken = _confirmed_production_context(pipeline_path)
    phases = payload.get("phases") if isinstance(payload.get("phases"), dict) else {}
    cover = phases.get("cover") if isinstance(phases.get("cover"), dict) else {}
    selected = safe_text(cover.get("selected_copy")).strip()
    if cover.get("status") not in {"copy_confirmed", "rejected"} or not selected:
        raise ValueError("cover copy must be explicitly confirmed before image generation")
    account = safe_text(payload.get("account"))
    category = safe_text(payload.get("category") or payload.get("project_name"))
    config = _account_config(account)
    portrait = _portrait_path(config)
    if not portrait.is_file():
        raise FileNotFoundError(f"account portrait does not exist: {portrait}")
    project_id = int(payload.get("bworkflow_project_id") or 0)
    options = _validate_copy_options_evidence(cover)
    selected_index = int(cover.get("selected_copy_index") or 0)
    if selected_index < 1 or selected_index > len(options) or options[selected_index - 1] != selected:
        raise ValueError("confirmed cover copy does not match the immutable options evidence")
    attempt_id = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}-{uuid4().hex[:8]}"
    attempt_dir = INTERNAL_WORKSPACE_ROOT / f"project-{project_id}" / "covers" / "attempts" / attempt_id
    attempt_dir.mkdir(parents=True, exist_ok=False)
    portrait_snapshot = attempt_dir / f"portrait{portrait.suffix.lower()}"
    shutil.copy2(portrait, portrait_snapshot)
    previous_attempt = cover.get("current_attempt") if isinstance(cover.get("current_attempt"), dict) else {}
    composition_variant = _select_composition_variant(
        config,
        previous_variant_id=safe_text(previous_attempt.get("composition_variant_id")),
    )
    prompt = safe_text(config["promptTemplate"]).replace("{category}", category).replace("{cover_copy}", selected)
    prompt = prompt.replace("{category_visual_guidance}", _category_visual_guidance(config, category))
    if composition_variant:
        prompt = prompt.replace("{composition_variant}", composition_variant["prompt"])
    package_path = attempt_dir / "cover-package.json"
    package = {
        "schemaVersion": "1.0.0",
        "kind": "bworkflow.cover_generation",
        "status": "ready",
        "createdAt": now_iso(),
        "attemptId": attempt_id,
        "projectId": project_id,
        "category": category,
        "account": account,
        "productionRunManifestPath": str(manifest_path),
        "productionRunManifestSha256": sha256_file(manifest_path),
        "spokenScriptSnapshotPath": spoken["snapshot_path"],
        "spokenScriptSnapshotSha256": spoken["snapshot_sha256"],
        "selectedCopy": selected,
        "copyOptionsPath": safe_text(cover.get("copy_options_path")),
        "copyOptionsSha256": safe_text(cover.get("copy_options_sha256")),
        "styleId": config["styleId"],
        "styleVersion": config["styleVersion"],
        "portraitSourcePath": str(portrait),
        "portraitSnapshotPath": str(portrait_snapshot),
        "portraitSnapshotSha256": sha256_file(portrait_snapshot),
        "prompt": prompt,
        "imageRequirements": {"aspectRatio": "4:3", "candidateCount": 1, "textMode": "model_native"},
    }
    if composition_variant:
        package.update(
            {
                "compositionVariantId": composition_variant["id"],
                "compositionVariant": composition_variant["prompt"],
            }
        )
    _atomic_write_json(package_path, package)
    package_sha256 = sha256_file(package_path)

    def update(data: dict[str, Any]) -> None:
        current_phases = data.get("phases") if isinstance(data.get("phases"), dict) else {}
        item = current_phases.get("cover") if isinstance(current_phases.get("cover"), dict) else {}
        attempt = {
            "attempt_id": attempt_id,
            "package_path": str(package_path.resolve()),
            "package_sha256": package_sha256,
            "portrait_path": str(portrait_snapshot.resolve()),
        }
        if composition_variant:
            attempt["composition_variant_id"] = composition_variant["id"]
        item.update({"status": "generation_ready", "current_attempt": attempt, "updated_at": now_iso()})
        current_phases["cover"] = item
        data["phases"] = current_phases
        data["current_phase"] = "cover"
        data["next_action"] = "invoke_cover_image_model"

    atomic_update_pipeline(pipeline, update)
    return {
        "ok": True,
        "attempt_id": attempt_id,
        "cover_package_path": str(package_path.resolve()),
        "portrait_path": str(portrait_snapshot.resolve()),
        "prompt": prompt,
        "composition_variant_id": composition_variant["id"] if composition_variant else "",
        "expected_output_path": str((attempt_dir / "cover-candidate.png").resolve()),
    }


def record_cover_image(
    pipeline_path: str | Path,
    *,
    cover_package_path: str | Path,
    image_path: str | Path,
) -> dict[str, Any]:
    pipeline, payload, _, _, _ = _confirmed_production_context(pipeline_path)
    package_path = Path(cover_package_path).expanduser().resolve()
    source_image = Path(image_path).expanduser().resolve()
    current = (((payload.get("phases") or {}).get("cover") or {}).get("current_attempt") or {})
    if Path(safe_text(current.get("package_path"))).expanduser().resolve() != package_path:
        raise ValueError("cover package is not the pipeline's current attempt")
    package = json.loads(package_path.read_text(encoding="utf-8-sig"))
    if safe_text(current.get("package_sha256")) != sha256_file(package_path):
        raise ValueError("cover package has changed since it was prepared")
    if package.get("status") != "ready":
        raise ValueError("cover package is not ready to receive an image")
    if not source_image.is_file():
        raise FileNotFoundError(f"generated cover image does not exist: {source_image}")
    with Image.open(source_image) as image:
        width, height = image.size
        image.verify()
    if height <= 0 or abs((width / height) - (4 / 3)) > 0.01:
        raise ValueError(f"cover image must be 4:3, got {width}x{height}")
    target = package_path.parent / f"cover-candidate{source_image.suffix.lower()}"
    if source_image != target:
        shutil.copy2(source_image, target)
    package.update(
        {
            "status": "generated",
            "generatedAt": now_iso(),
            "candidatePath": str(target.resolve()),
            "candidateSha256": sha256_file(target),
            "candidateSize": target.stat().st_size,
            "candidateWidth": width,
            "candidateHeight": height,
        }
    )
    _atomic_write_json(package_path, package)
    generated_package_sha256 = sha256_file(package_path)

    def update(data: dict[str, Any]) -> None:
        phases = data.get("phases") if isinstance(data.get("phases"), dict) else {}
        item = phases.get("cover") if isinstance(phases.get("cover"), dict) else {}
        attempt = item.get("current_attempt") if isinstance(item.get("current_attempt"), dict) else {}
        attempt.update(
            {
                "image_path": str(target.resolve()),
                "image_sha256": sha256_file(target),
                "package_sha256": generated_package_sha256,
            }
        )
        item.update({"status": "image_generated", "current_attempt": attempt, "updated_at": now_iso()})
        phases["cover"] = item
        data["phases"] = phases
        data["current_phase"] = "cover"
        data["next_action"] = "confirm_cover_image"

    atomic_update_pipeline(pipeline, update)
    return {"ok": True, "cover_package_path": str(package_path), "image_path": str(target.resolve())}


def confirm_cover_image(pipeline_path: str | Path) -> dict[str, Any]:
    pipeline, payload, _, _, _ = _confirmed_production_context(pipeline_path)
    cover = ((payload.get("phases") or {}).get("cover") or {})
    current = cover.get("current_attempt") if isinstance(cover.get("current_attempt"), dict) else {}
    package_path = Path(safe_text(current.get("package_path"))).expanduser().resolve()
    candidate = Path(safe_text(current.get("image_path"))).expanduser().resolve()
    if cover.get("status") != "image_generated" or not package_path.is_file() or not candidate.is_file():
        raise ValueError("there is no generated cover image waiting for approval")
    package = json.loads(package_path.read_text(encoding="utf-8-sig"))
    if safe_text(current.get("package_sha256")) != sha256_file(package_path):
        raise ValueError("cover package has changed since image generation")
    if safe_text(package.get("candidateSha256")) != sha256_file(candidate):
        raise ValueError("cover candidate has changed since generation")
    output_dir = Path(safe_text(payload.get("output_dir"))).expanduser().resolve()
    if not safe_text(payload.get("output_dir")):
        raise ValueError("pipeline output_dir is required for an accepted cover")
    output_dir.mkdir(parents=True, exist_ok=True)
    final_path = output_dir / f"封面图-{safe_text(package.get('attemptId'))}{candidate.suffix.lower()}"
    if final_path.exists() and sha256_file(final_path) != sha256_file(candidate):
        raise ValueError(f"accepted cover target already exists with different content: {final_path}")
    if not final_path.exists():
        shutil.copy2(candidate, final_path)
    approval = build_artifact_approval(
        "cover_image",
        final_path,
        approved_at=now_iso(),
        source_revision=sha256_file(package_path),
    )

    def update(data: dict[str, Any]) -> None:
        approvals = data.get("artifact_approvals") if isinstance(data.get("artifact_approvals"), dict) else {}
        approvals["cover_image"] = approval
        data["artifact_approvals"] = approvals
        phases = data.get("phases") if isinstance(data.get("phases"), dict) else {}
        item = phases.get("cover") if isinstance(phases.get("cover"), dict) else {}
        item.update(
            {
                "status": "accepted",
                "accepted": True,
                "accepted_at": approval["approved_at"],
                "output_image_path": str(final_path.resolve()),
                "cover_package_path": str(package_path),
                "updated_at": now_iso(),
            }
        )
        phases["cover"] = item
        data["phases"] = phases
        paths = data.get("paths") if isinstance(data.get("paths"), dict) else {}
        paths["cover_image"] = str(final_path.resolve())
        paths["cover_package"] = str(package_path)
        data["paths"] = paths
        data["current_phase"] = "publishing"
        data["next_action"] = "prepare_publishing_assets"

    atomic_update_pipeline(pipeline, update)
    return {"ok": True, "approval": approval, "cover_package_path": str(package_path)}


def reject_cover_image(pipeline_path: str | Path, *, reason: str) -> dict[str, Any]:
    pipeline, payload, _, _, _ = _confirmed_production_context(pipeline_path)
    cover = ((payload.get("phases") or {}).get("cover") or {})
    if cover.get("status") != "image_generated":
        raise ValueError("there is no generated cover image waiting for review")
    rejection = safe_text(reason).strip()
    if not rejection:
        raise ValueError("cover rejection reason is required")

    def update(data: dict[str, Any]) -> None:
        phases = data.get("phases") if isinstance(data.get("phases"), dict) else {}
        item = phases.get("cover") if isinstance(phases.get("cover"), dict) else {}
        item.update({"status": "rejected", "rejection_reason": rejection, "rejected_at": now_iso(), "updated_at": now_iso()})
        phases["cover"] = item
        data["phases"] = phases
        data["current_phase"] = "cover"
        data["next_action"] = "generate_cover_image"

    atomic_update_pipeline(pipeline, update)
    return {"ok": True, "status": "rejected", "reason": rejection}


def _confirmed_production_context(
    pipeline_path: str | Path,
) -> tuple[Path, dict[str, Any], Path, dict[str, Any], dict[str, Any]]:
    pipeline = Path(pipeline_path).expanduser().resolve()
    payload = json.loads(pipeline.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError("pipeline must contain a JSON object")
    phases = payload.get("phases") if isinstance(payload.get("phases"), dict) else {}
    assembly = phases.get("assembly") if isinstance(phases.get("assembly"), dict) else {}
    confirmation = payload.get("production_confirmation") if isinstance(payload.get("production_confirmation"), dict) else {}
    manifest_path = Path(safe_text(assembly.get("run_manifest_path"))).expanduser().resolve()
    if confirmation.get("status") != "confirmed" or Path(safe_text(confirmation.get("run_manifest_path"))).expanduser().resolve() != manifest_path:
        raise ValueError("formal production must be confirmed before cover generation")
    if not manifest_path.is_file():
        raise FileNotFoundError(f"confirmed run manifest does not exist: {manifest_path}")
    approvals = payload.get("artifact_approvals") if isinstance(payload.get("artifact_approvals"), dict) else {}
    full_approval = approvals.get("full_mp4") if isinstance(approvals.get("full_mp4"), dict) else {}
    full_path = Path(safe_text(full_approval.get("path"))).expanduser().resolve()
    if (
        full_approval.get("artifact_type") != "full_mp4"
        or not full_path.is_file()
        or int(full_approval.get("size") or 0) != full_path.stat().st_size
        or safe_text(full_approval.get("sha256")) != sha256_file(full_path)
        or safe_text(full_approval.get("source_revision")) != sha256_file(manifest_path)
    ):
        raise ValueError("confirmed production approval is missing or invalid")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    spoken = validate_spoken_script_evidence(manifest)
    return pipeline, payload, manifest_path, manifest, spoken


def _account_config(account: str) -> dict[str, Any]:
    payload = json.loads(COVER_PROMPTS_PATH.read_text(encoding="utf-8-sig"))
    accounts = payload.get("accounts") if isinstance(payload.get("accounts"), dict) else {}
    config = accounts.get(account)
    if not isinstance(config, dict):
        raise ValueError(f"cover prompt is not configured for account: {account}")
    required = ("styleId", "styleVersion", "portraitFilename", "promptTemplate")
    missing = [key for key in required if not safe_text(config.get(key)).strip()]
    prompt = safe_text(config.get("promptTemplate"))
    raw_variants = config.get("compositionVariants")
    has_variant_placeholder = "{composition_variant}" in prompt
    if (
        missing
        or "{category}" not in prompt
        or "{cover_copy}" not in prompt
        or has_variant_placeholder != bool(raw_variants)
    ):
        raise ValueError(f"cover prompt configuration is incomplete for account: {account}")
    return {
        **config,
        "portraitRoot": payload.get("portraitRoot"),
        "categoryVisualGuidance": payload.get("categoryVisualGuidance"),
    }


def _select_composition_variant(
    config: dict[str, Any],
    *,
    previous_variant_id: str,
) -> dict[str, str] | None:
    raw_variants = config.get("compositionVariants")
    if not raw_variants:
        return None
    if not isinstance(raw_variants, list) or len(raw_variants) < 2:
        raise ValueError("cover composition variants must contain at least 2 items")
    variants: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    for item in raw_variants:
        variant_id = safe_text(item.get("id") if isinstance(item, dict) else "").strip()
        variant_prompt = safe_text(item.get("prompt") if isinstance(item, dict) else "").strip()
        if not variant_id or not variant_prompt or variant_id in seen_ids:
            raise ValueError("cover composition variants must have unique non-empty ids and prompts")
        seen_ids.add(variant_id)
        variants.append({"id": variant_id, "prompt": variant_prompt})
    eligible = [item for item in variants if item["id"] != previous_variant_id] or variants
    return choice(eligible)


def _category_visual_guidance(config: dict[str, Any], category: str) -> str:
    guidance = config.get("categoryVisualGuidance")
    if isinstance(guidance, dict):
        exact = safe_text(guidance.get(category)).strip()
        if exact:
            return exact
    return "Keep every product faithful to the category's real primary form factor; do not substitute adjacent product types."


def _portrait_path(config: dict[str, Any]) -> Path:
    return (Path(safe_text(config.get("portraitRoot"))) / safe_text(config.get("portraitFilename"))).resolve()


def _validate_copy_options_evidence(cover: dict[str, Any]) -> list[str]:
    plan_path = Path(safe_text(cover.get("copy_options_path"))).expanduser().resolve()
    expected_hash = safe_text(cover.get("copy_options_sha256"))
    if not plan_path.is_file() or not expected_hash or sha256_file(plan_path) != expected_hash:
        raise ValueError("cover copy options evidence is missing or has changed")
    plan = json.loads(plan_path.read_text(encoding="utf-8-sig"))
    options = plan.get("options") if isinstance(plan, dict) else None
    pipeline_options = cover.get("copy_options")
    if not isinstance(options, list) or options != pipeline_options:
        raise ValueError("pipeline cover copy options do not match the immutable evidence")
    return [safe_text(item) for item in options]


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        staged.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
        os.replace(staged, path)
    finally:
        staged.unlink(missing_ok=True)
