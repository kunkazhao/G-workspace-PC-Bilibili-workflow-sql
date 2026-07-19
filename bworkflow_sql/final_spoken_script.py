from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from .settings import DEFAULT_SPOKEN_MD_ROOT
from .utils import now_iso, safe_text


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def build_final_spoken_markdown(
    package: dict[str, Any],
    *,
    project_name: str,
    account: str,
    run_id: str,
    render_package_path: str | Path,
) -> str:
    lines = [
        f"# {project_name}｜{account}｜完整口播稿",
        "",
        "> 本稿由最终 RenderPackage 自动还原，片段顺序、文案版本与成片一致；请勿据此反写商品文案库。",
        "",
        f"- 生成批次：`{run_id}`",
        f"- RenderPackage：`{Path(render_package_path).resolve()}`",
        "",
    ]
    segments = package.get("segments") if isinstance(package.get("segments"), list) else []
    for position, segment in enumerate(segments, start=1):
        if not isinstance(segment, dict):
            continue
        segment_type = safe_text(segment.get("type"))
        text = safe_text(segment.get("spokenText") or segment.get("transitionText")).strip()
        if not text:
            continue
        if segment_type == "intro":
            heading = "引言"
        elif segment_type == "product_recommendation":
            uid = safe_text(segment.get("productUid") or segment.get("uid"))
            title = safe_text(segment.get("productTitle")) or uid or f"商品 {position}"
            heading = f"商品｜{title}" + (f"｜{uid}" if uid and uid not in title else "")
        elif segment_type == "price_transition":
            label = safe_text(segment.get("priceRangeLabel")) or "价格段"
            heading = f"价格过渡｜{label}"
        elif segment_type == "outro":
            heading = "结尾"
        else:
            heading = f"片段 {position}｜{segment_type or 'unknown'}"
        lines.extend([f"## {heading}", ""])
        metadata = {
            "position": position,
            "type": segment_type,
            "product_uid": safe_text(segment.get("productUid") or segment.get("uid")),
            "source_script_block_id": segment.get("sourceScriptBlockId"),
        }
        lines.extend([f"<!-- segment: {json.dumps(metadata, ensure_ascii=False)} -->", "", text, ""])
    return "\n".join(lines).rstrip() + "\n"


def materialize_final_spoken_script(
    repository: Any,
    *,
    project_id: int,
    account: str,
    package: dict[str, Any],
    package_path: str | Path,
    run_id: str,
    snapshot_path: str | Path,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    project = repository.project(project_id)
    if not project:
        raise ValueError(f"project does not exist: {project_id}")
    moment = generated_at or datetime.now()
    project_name = safe_text(project.get("name"))
    current_path = DEFAULT_SPOKEN_MD_ROOT / project_name / f"{moment.month}月-{safe_text(account)}.md"
    snapshot = Path(snapshot_path).expanduser().resolve()
    content = build_final_spoken_markdown(
        package,
        project_name=project_name,
        account=safe_text(account),
        run_id=run_id,
        render_package_path=package_path,
    )
    _atomic_write_text(snapshot, content)
    _atomic_write_text(current_path, content)
    repository.db.execute(
        "UPDATE projects SET spoken_md_path=?, updated_at=? WHERE id=?",
        (str(current_path.resolve()), now_iso(), project_id),
    )
    return {
        "source": "final_render_package_segments",
        "segment_count": sum(1 for item in package.get("segments") or [] if isinstance(item, dict)),
        "current_path": str(current_path.resolve()),
        "current_sha256": sha256_file(current_path),
        "snapshot_path": str(snapshot),
        "snapshot_sha256": sha256_file(snapshot),
        "render_package_path": str(Path(package_path).resolve()),
        "render_package_sha256": sha256_file(package_path),
    }


def validate_spoken_script_evidence(payload: dict[str, Any]) -> dict[str, Any]:
    evidence = payload.get("spoken_script") if isinstance(payload.get("spoken_script"), dict) else None
    if not evidence:
        raise ValueError("run manifest is missing the final spoken-script snapshot")
    snapshot = Path(str(evidence.get("snapshot_path") or "")).expanduser().resolve()
    if not snapshot.is_file():
        raise ValueError(f"final spoken-script snapshot does not exist: {snapshot}")
    expected = str(evidence.get("snapshot_sha256") or "")
    if not expected or sha256_file(snapshot) != expected:
        raise ValueError("final spoken-script snapshot hash has changed")
    package = Path(str(evidence.get("render_package_path") or "")).expanduser().resolve()
    if not package.is_file():
        raise ValueError(f"spoken-script RenderPackage does not exist: {package}")
    package_hash = str(evidence.get("render_package_sha256") or "")
    if not package_hash or sha256_file(package) != package_hash:
        raise ValueError("spoken-script RenderPackage hash has changed")
    return evidence


def backfill_final_spoken_script(
    repository: Any,
    *,
    run_manifest_path: str | Path,
    pipeline_path: str | Path | None = None,
) -> dict[str, Any]:
    manifest_path = Path(run_manifest_path).expanduser().resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    if not isinstance(manifest, dict) or manifest.get("kind") != "bworkflow.final_video_run":
        raise ValueError("run manifest is not a bworkflow.final_video_run")
    project = manifest.get("project") if isinstance(manifest.get("project"), dict) else {}
    inputs = manifest.get("inputs") if isinstance(manifest.get("inputs"), dict) else {}
    package_path = Path(str(inputs.get("render_package_path") or "")).expanduser().resolve()
    if not package_path.is_file():
        raise FileNotFoundError(f"RenderPackage does not exist: {package_path}")
    package = json.loads(package_path.read_text(encoding="utf-8-sig"))
    pipeline: Path | None = None
    pipeline_payload: dict[str, Any] | None = None
    if pipeline_path:
        pipeline = Path(pipeline_path).expanduser().resolve()
        loaded = json.loads(pipeline.read_text(encoding="utf-8-sig"))
        if not isinstance(loaded, dict):
            raise ValueError("pipeline must contain a JSON object")
        phases = loaded.get("phases") if isinstance(loaded.get("phases"), dict) else {}
        assembly = phases.get("assembly") if isinstance(phases.get("assembly"), dict) else {}
        bound_manifest = safe_text(assembly.get("run_manifest_path"))
        if bound_manifest and Path(bound_manifest).expanduser().resolve() != manifest_path:
            raise ValueError("pipeline assembly is bound to a different run manifest")
        pipeline_payload = loaded
    created_at = datetime.fromisoformat(str(manifest.get("createdAt") or "").replace("Z", "+00:00"))
    run_id = _run_id_from_manifest_path(manifest_path, created_at)
    evidence = materialize_final_spoken_script(
        repository,
        project_id=int(project.get("id") or 0),
        account=safe_text(project.get("account")),
        package=package,
        package_path=package_path,
        run_id=run_id,
        snapshot_path=package_path.parent / "完整口播稿.md",
        generated_at=created_at,
    )
    manifest["schemaVersion"] = "1.1.0"
    manifest["spoken_script"] = evidence
    fingerprints = manifest.get("file_fingerprints") if isinstance(manifest.get("file_fingerprints"), list) else []
    fingerprints = [item for item in fingerprints if not (isinstance(item, dict) and item.get("role") == "spoken_script_snapshot")]
    snapshot = Path(evidence["snapshot_path"])
    fingerprints.append(
        {
            "role": "spoken_script_snapshot",
            "path": str(snapshot),
            "exists": True,
            "size": snapshot.stat().st_size,
            "sha256": evidence["snapshot_sha256"].removeprefix("sha256:"),
        }
    )
    manifest["file_fingerprints"] = fingerprints
    _atomic_write_json(manifest_path, manifest)

    if pipeline is not None and pipeline_payload is not None:
        payload = pipeline_payload
        phases = payload.get("phases") if isinstance(payload.get("phases"), dict) else {}
        assembly = phases.get("assembly") if isinstance(phases.get("assembly"), dict) else {}
        assembly["spoken_script"] = evidence
        phases["assembly"] = assembly
        payload["phases"] = phases
        paths = payload.get("paths") if isinstance(payload.get("paths"), dict) else {}
        paths["spoken_script"] = evidence["current_path"]
        paths["spoken_script_snapshot"] = evidence["snapshot_path"]
        payload["paths"] = paths
        _atomic_write_json(pipeline, payload)
    return {"ok": True, "run_manifest_path": str(manifest_path), "spoken_script": evidence}


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        staged.write_text(content, encoding="utf-8")
        os.replace(staged, path)
    finally:
        staged.unlink(missing_ok=True)


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    _atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n")


def _run_id_from_manifest_path(path: Path, created_at: datetime) -> str:
    prefix = "final-video-"
    if path.stem.startswith(prefix):
        return path.stem[len(prefix):].removesuffix(".run-manifest")
    return created_at.strftime("%Y%m%d_%H%M%S")
