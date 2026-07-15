from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def build_artifact_approval(
    artifact_type: str,
    path: str | Path,
    *,
    approved_at: str,
    source_revision: str,
) -> dict[str, Any]:
    artifact = Path(path).expanduser().resolve()
    if not artifact.is_file():
        raise FileNotFoundError(f"artifact does not exist: {artifact}")
    return {
        "artifact_type": str(artifact_type),
        "path": str(artifact),
        "sha256": sha256_file(artifact),
        "size": artifact.stat().st_size,
        "approved_at": str(approved_at),
        "source_revision": str(source_revision),
    }


def ensure_episode_id(payload: dict[str, Any], *, pipeline_path: Path) -> str:
    explicit = str(payload.get("episode_id") or "").strip()
    if explicit:
        return explicit
    identity = {
        "workspace_id": str(payload.get("workspace_id") or ""),
        "category": str(payload.get("category") or payload.get("project_name") or ""),
        "account": str(payload.get("account") or ""),
        "scheme_id": str(payload.get("scheme_id") or ""),
        "project_id": str(payload.get("bworkflow_project_id") or ""),
    }
    if not any(identity.values()):
        identity["pipeline_path"] = str(pipeline_path.resolve())
    encoded = "\x1f".join(f"{key}={identity[key]}" for key in sorted(identity)).encode("utf-8")
    episode_id = "episode:sha256:" + hashlib.sha256(encoded).hexdigest()
    payload["episode_id"] = episode_id
    return episode_id


def atomic_update_pipeline(
    pipeline_path: str | Path,
    update: Callable[[dict[str, Any]], None],
) -> dict[str, Any]:
    pipeline = Path(pipeline_path).expanduser().resolve()
    payload = json.loads(pipeline.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError("pipeline must contain a JSON object")
    ensure_episode_id(payload, pipeline_path=pipeline)
    update(payload)
    staged = pipeline.with_name(f".{pipeline.name}.{uuid4().hex}.tmp")
    try:
        staged.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(staged, pipeline)
    finally:
        staged.unlink(missing_ok=True)
    return payload


def confirm_intro_video(
    pipeline_path: str | Path,
    intro_video_path: str | Path,
    *,
    approved_at: str,
    source_revision: str = "",
    source_plan_path: str | Path | None = None,
) -> dict[str, Any]:
    artifact = Path(intro_video_path).expanduser().resolve()
    source_plan = Path(source_plan_path).expanduser().resolve() if source_plan_path else None
    approval = build_artifact_approval(
        "intro_video",
        artifact,
        approved_at=approved_at,
        source_revision=source_revision,
    )

    def update(payload: dict[str, Any]) -> None:
        approvals = payload.get("artifact_approvals")
        approvals = approvals if isinstance(approvals, dict) else {}
        approvals["intro_video"] = approval
        payload["artifact_approvals"] = approvals
        paths = payload.get("paths") if isinstance(payload.get("paths"), dict) else {}
        paths["intro_video"] = str(artifact)
        if source_plan is not None:
            paths["source_intro_plan"] = str(source_plan)
        payload["paths"] = paths
        phases = payload.get("phases") if isinstance(payload.get("phases"), dict) else {}
        intro = phases.get("intro_video") if isinstance(phases.get("intro_video"), dict) else {}
        intro.update(
            {
                "status": "accepted",
                "accepted": True,
                "output_mp4_path": str(artifact),
                "user_accepted_at": approved_at,
            }
        )
        if source_plan is not None:
            intro["source_intro_plan_path"] = str(source_plan)
        phases["intro_video"] = intro
        payload["phases"] = phases

    atomic_update_pipeline(pipeline_path, update)
    return approval


def write_production_confirmation(
    pipeline_path: str | Path,
    production: dict[str, Any],
) -> dict[str, Any]:
    manifest_path = Path(str(production.get("run_manifest_path") or "")).expanduser().resolve()
    full_mp4_path = Path(str(production.get("full_mp4_path") or "")).expanduser().resolve()
    approval = build_artifact_approval(
        "full_mp4",
        full_mp4_path,
        approved_at=str(production.get("confirmed_at") or ""),
        source_revision=sha256_file(manifest_path),
    )

    def update(payload: dict[str, Any]) -> None:
        payload["production_confirmation"] = {
            "status": "confirmed",
            "production_run_id": production["id"],
            "run_manifest_path": str(manifest_path),
            "confirmed_at": production["confirmed_at"],
        }
        approvals = payload.get("artifact_approvals")
        approvals = approvals if isinstance(approvals, dict) else {}
        approvals["full_mp4"] = approval
        payload["artifact_approvals"] = approvals
        phases = payload.get("phases") if isinstance(payload.get("phases"), dict) else {}
        assembly = phases.get("assembly") if isinstance(phases.get("assembly"), dict) else {}
        assembly["run_manifest_path"] = str(manifest_path)
        assembly["full_mp4_path"] = str(full_mp4_path)
        pending = assembly.get("pending_candidate") if isinstance(assembly.get("pending_candidate"), dict) else {}
        if str(pending.get("run_manifest_path") or "") == str(manifest_path):
            assembly.pop("pending_candidate", None)
        phases["assembly"] = assembly
        payload["phases"] = phases

    atomic_update_pipeline(pipeline_path, update)
    return approval
