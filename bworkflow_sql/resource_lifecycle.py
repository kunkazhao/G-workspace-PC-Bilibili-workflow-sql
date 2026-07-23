from __future__ import annotations

import json
import hashlib
import os
import secrets
import shutil
import stat
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from .asset_paths import project_category_folder
from .db import Database
from .repositories import Repository
from .settings import (
    DEFAULT_IMAGE_ROOT,
    DEFAULT_VIDEO_ROOT,
    DEFAULT_VOICE_ROOT,
    INTERNAL_WORKSPACE_ROOT,
)
from .utils import now_iso, safe_text


PRODUCT_IMAGE_JOB_RETENTION_DAYS = 7
PRODUCT_COVER_CACHE_RETENTION_DAYS = 30
DERIVED_ASSET_RETENTION_DAYS = 14
LEGACY_JOB_MIN_AGE_DAYS = 1


def _utc_now(value: datetime | None = None) -> datetime:
    current = value or datetime.now(timezone.utc)
    return current if current.tzinfo else current.replace(tzinfo=timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_iso(value: Any) -> datetime | None:
    text = safe_text(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return _utc_now(parsed)


def _path_key(value: str | Path) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(Path(value).expanduser())))


def _path_size(path: Path) -> int:
    if path.is_file():
        try:
            return int(path.stat().st_size)
        except OSError:
            return 0
    if not path.is_dir():
        return 0
    total = 0
    try:
        for item in path.rglob("*"):
            if item.is_file():
                try:
                    total += int(item.stat().st_size)
                except OSError:
                    continue
    except OSError:
        return total
    return total


def _has_reparse_point(path: Path) -> bool:
    try:
        attrs = int(getattr(path.lstat(), "st_file_attributes", 0))
    except OSError:
        return False
    return bool(attrs & int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)))


def _entry_snapshot(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ValueError("resource_missing")
    if path.is_symlink() or _has_reparse_point(path):
        raise ValueError("reparse_point_not_deletable")
    if path.is_file():
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        stat_result = path.stat()
        return {
            "entry_kind": "file",
            "size_bytes": int(stat_result.st_size),
            "mtime_ns": int(stat_result.st_mtime_ns),
            "fingerprint": digest.hexdigest(),
        }
    if not path.is_dir():
        raise ValueError("unsupported_entry_kind")

    digest = hashlib.sha256()
    total_size = 0
    latest_mtime_ns = int(path.stat().st_mtime_ns)
    for child in sorted(path.rglob("*"), key=lambda item: item.as_posix().casefold()):
        if child.is_symlink() or _has_reparse_point(child):
            raise ValueError("nested_reparse_point_not_deletable")
        child_stat = child.stat()
        relative = child.relative_to(path).as_posix()
        entry_kind = "directory" if child.is_dir() else "file" if child.is_file() else "other"
        size = int(child_stat.st_size) if child.is_file() else 0
        total_size += size
        latest_mtime_ns = max(latest_mtime_ns, int(child_stat.st_mtime_ns))
        digest.update(
            json.dumps(
                [relative, entry_kind, size, int(child_stat.st_mtime_ns)],
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        )
    return {
        "entry_kind": "directory",
        "size_bytes": total_size,
        "mtime_ns": latest_mtime_ns,
        "fingerprint": digest.hexdigest(),
    }


def _within_root(path: Path, root: Path) -> bool:
    target = os.path.normcase(os.path.abspath(os.fspath(path)))
    boundary = os.path.normcase(os.path.abspath(os.fspath(root)))
    if target == boundary:
        return False
    try:
        return os.path.commonpath((target, boundary)) == boundary
    except ValueError:
        return False


def _managed_delete_roots(project: dict[str, Any]) -> list[Path]:
    category = project_category_folder(project)
    return [
        Path(safe_text(project.get("image_root")) or DEFAULT_IMAGE_ROOT) / category,
        Path(safe_text(project.get("voice_root")) or DEFAULT_VOICE_ROOT) / category,
        Path(safe_text(project.get("video_root")) or DEFAULT_VIDEO_ROOT) / category,
        INTERNAL_WORKSPACE_ROOT / "product-image-jobs" / f"project-{project['id']}",
    ]


def _path_is_managed(path: Path, project: dict[str, Any]) -> bool:
    return any(_within_root(path, root) for root in _managed_delete_roots(project))


def _modified_at(path: Path) -> datetime:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
    except OSError:
        return datetime.fromtimestamp(0, timezone.utc)


def _looks_like_path(value: str) -> bool:
    text = safe_text(value)
    if not text or len(text) > 4096:
        return False
    return bool(Path(text).is_absolute() or "\\" in text or "/" in text)


def _collect_json_references(path: Path) -> set[str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return set()
    references: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for child in value.values():
                visit(child)
            return
        if isinstance(value, list):
            for child in value:
                visit(child)
            return
        if not isinstance(value, str) or not _looks_like_path(value):
            return
        candidate = Path(value).expanduser()
        if not candidate.is_absolute():
            candidate = path.parent / candidate
        references.add(_path_key(candidate))

    visit(payload)
    return references


def _protected(path: Path, references: set[str]) -> bool:
    key = _path_key(path)
    if key in references:
        return True
    prefix = key.rstrip("\\/") + os.sep
    return any(reference.startswith(prefix) for reference in references)


def _item(
    *,
    classification: str,
    resource_kind: str,
    path: str | Path,
    reason: str,
    **extra: Any,
) -> dict[str, Any]:
    resolved = Path(path).expanduser()
    exists = resolved.exists()
    result = {
        "classification": classification,
        "resource_kind": resource_kind,
        "path": str(resolved),
        "reason": reason,
        "exists": exists,
        "size_bytes": _path_size(resolved) if exists else 0,
    }
    result.update(extra)
    return result


def register_cleanup_candidate(
    db: Database,
    *,
    project_id: int,
    resource_kind: str,
    path: str | Path,
    reason: str,
    eligible_at: datetime,
    details: dict[str, Any] | None = None,
) -> None:
    with db.connect() as conn:
        register_cleanup_candidate_in_connection(
            conn,
            project_id=project_id,
            resource_kind=resource_kind,
            path=path,
            reason=reason,
            eligible_at=eligible_at,
            details=details,
        )


def register_cleanup_candidate_in_connection(
    conn: Any,
    *,
    project_id: int,
    resource_kind: str,
    path: str | Path,
    reason: str,
    eligible_at: datetime,
    details: dict[str, Any] | None = None,
) -> None:
    ts = now_iso()
    normalized_path = str(Path(path).expanduser())
    previous = conn.execute(
        "SELECT id, status FROM resource_cleanup_candidates WHERE path=?",
        (normalized_path,),
    ).fetchone()
    conn.execute(
        """
        INSERT INTO resource_cleanup_candidates
            (project_id, resource_kind, path, reason, status, eligible_at,
             details_json, first_seen_at, last_seen_at)
        VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, ?)
        ON CONFLICT(path) DO UPDATE SET
            project_id=excluded.project_id,
            resource_kind=excluded.resource_kind,
            reason=excluded.reason,
            status=CASE
                WHEN resource_cleanup_candidates.status='quarantined'
                THEN resource_cleanup_candidates.status
                ELSE 'pending'
            END,
            eligible_at=excluded.eligible_at,
            details_json=excluded.details_json,
            last_seen_at=excluded.last_seen_at,
            resolved_at=NULL
        """,
        (
            project_id,
            safe_text(resource_kind),
            normalized_path,
            safe_text(reason),
            _iso(eligible_at),
            json.dumps(details or {}, ensure_ascii=False, sort_keys=True),
            ts,
            ts,
        ),
    )
    current = conn.execute(
        "SELECT id, status FROM resource_cleanup_candidates WHERE path=?",
        (normalized_path,),
    ).fetchone()
    previous_status = safe_text(previous["status"]) if previous else ""
    current_status = safe_text(current["status"]) if current else "pending"
    if previous is None or previous_status != current_status:
        record_resource_state_event_in_connection(
            conn,
            project_id=project_id,
            resource_kind=safe_text(resource_kind),
            resource_key=f"cleanup_candidate:{int(current['id'])}",
            path=normalized_path,
            previous_state=previous_status,
            new_state=current_status,
            reason=safe_text(reason),
            source=safe_text((details or {}).get("discovered_by")) or "resource_registration",
            details={"eligible_at": _iso(eligible_at), **(details or {})},
            created_at=ts,
        )


def record_resource_state_event_in_connection(
    conn: Any,
    *,
    project_id: int,
    resource_kind: str,
    resource_key: str,
    new_state: str,
    previous_state: str = "",
    path: str | Path = "",
    reason: str = "",
    source: str = "",
    account_label: str = "",
    details: dict[str, Any] | None = None,
    created_at: str | None = None,
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO resource_state_events
            (project_id, resource_kind, resource_key, path, previous_state,
             new_state, reason, source, account_label, details_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            project_id,
            safe_text(resource_kind),
            safe_text(resource_key),
            str(Path(path).expanduser()) if safe_text(path) else "",
            safe_text(previous_state),
            safe_text(new_state),
            safe_text(reason),
            safe_text(source),
            safe_text(account_label),
            json.dumps(details or {}, ensure_ascii=False, sort_keys=True),
            safe_text(created_at) or now_iso(),
        ),
    )
    return int(cursor.lastrowid)


def record_resource_state_event(
    db: Database,
    **kwargs: Any,
) -> int:
    with db.connect() as conn:
        return record_resource_state_event_in_connection(conn, **kwargs)


def list_resource_state_events(
    db: Database,
    *,
    project_id: int,
    resource_kind: str = "",
    account_label: str = "",
    new_state: str = "",
    limit: int = 200,
) -> dict[str, Any]:
    project = Repository(db).project(project_id)
    if not project:
        raise ValueError(f"project does not exist: {project_id}")
    bounded_limit = max(1, min(int(limit), 1000))
    clauses = ["project_id=?"]
    params: list[Any] = [project_id]
    for column, value in (
        ("resource_kind", resource_kind),
        ("account_label", account_label),
        ("new_state", new_state),
    ):
        text = safe_text(value)
        if not text:
            continue
        clauses.append(f"{column}=?")
        params.append(text)
    rows = db.fetchall(
        f"""
        SELECT * FROM resource_state_events
        WHERE {' AND '.join(clauses)}
        ORDER BY id DESC
        LIMIT ?
        """,
        (*params, bounded_limit),
    )
    events: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        try:
            item["details"] = json.loads(safe_text(item.pop("details_json")) or "{}")
        except json.JSONDecodeError:
            item["details"] = {}
        events.append(item)
    return {
        "ok": True,
        "mode": "resource_state_history",
        "project": {"id": project_id, "name": safe_text(project.get("name"))},
        "filters": {
            "resource_kind": safe_text(resource_kind),
            "account_label": safe_text(account_label),
            "new_state": safe_text(new_state),
            "limit": bounded_limit,
        },
        "count": len(events),
        "events": events,
    }


def register_completed_product_image_job(
    db: Database,
    *,
    project_id: int,
    package_path: str | Path,
    now: datetime | None = None,
) -> None:
    job_root = Path(package_path).expanduser().parent
    current = _utc_now(now)
    register_cleanup_candidate(
        db,
        project_id=project_id,
        resource_kind="product_image_job",
        path=job_root,
        reason="render_completed",
        eligible_at=current + timedelta(days=PRODUCT_IMAGE_JOB_RETENTION_DAYS),
        details={"package_path": str(Path(package_path).expanduser())},
    )


def _pipeline_path(project: dict[str, Any], explicit: str | Path | None) -> Path | None:
    if explicit:
        return Path(explicit).expanduser()
    spoken = safe_text(project.get("spoken_md_path"))
    return Path(spoken).expanduser().parent / ".pipeline.json" if spoken else None


def _reference_set(
    db: Database,
    *,
    project: dict[str, Any],
    assets: list[dict[str, Any]],
    pipeline_path: Path | None,
) -> tuple[set[str], set[str]]:
    current: set[str] = {
        _path_key(asset["path"])
        for asset in assets
        if safe_text(asset.get("status")) == "ready" and safe_text(asset.get("path"))
    }
    history: set[str] = set()
    if pipeline_path and pipeline_path.is_file():
        current.add(_path_key(pipeline_path))
        current.update(_collect_json_references(pipeline_path))

    runs = db.fetchall(
        "SELECT * FROM production_runs WHERE project_id=? ORDER BY id",
        (project["id"],),
    )
    for run in runs:
        for field in (
            "run_manifest_path",
            "full_mp4_path",
            "original_full_mp4_path",
            "recipe_path",
        ):
            value = safe_text(run[field])
            if not value:
                continue
            path = Path(value).expanduser()
            history.add(_path_key(path))
            if path.suffix.casefold() == ".json" and path.is_file():
                history.update(_collect_json_references(path))

    project_workspace = INTERNAL_WORKSPACE_ROOT / f"project-{project['id']}"
    if project_workspace.is_dir():
        for json_path in project_workspace.rglob("*.json"):
            current.add(_path_key(json_path))
            current.update(_collect_json_references(json_path))
    return current, history


def _binding_items(
    assets: list[dict[str, Any]],
    *,
    current_refs: set[str],
    history_refs: set[str],
    candidate_by_path: dict[str, dict[str, Any]],
    pipeline_exists: bool,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for asset in assets:
        path_text = safe_text(asset.get("path"))
        if not path_text:
            continue
        path = Path(path_text).expanduser()
        status = safe_text(asset.get("status"))
        source = safe_text(asset.get("source_kind"))
        details = {
            "asset_binding_id": int(asset.get("id") or 0),
            "asset_type": safe_text(asset.get("asset_type")),
            "status": status,
            "source_kind": source,
            "account_label": safe_text(asset.get("account_label")),
        }
        if status == "ready":
            classification = "current" if path.is_file() else "broken_reference"
            reason = "ready_binding" if path.is_file() else "ready_binding_file_missing"
        elif not path.exists():
            classification = "metadata_only"
            reason = "inactive_binding_file_missing"
        elif source == "manual":
            classification = "protected_source"
            reason = "manual_assets_are_never_automatic_cleanup_candidates"
        elif _protected(path, history_refs):
            classification = "history"
            reason = "referenced_by_formal_production_history"
        elif _protected(path, current_refs):
            classification = "current"
            reason = "referenced_by_current_working_state"
        elif _path_key(path) in candidate_by_path:
            classification = "cleanup_candidate"
            reason = "registered_delayed_cleanup_candidate"
        elif pipeline_exists:
            classification = "reclaimable"
            reason = "inactive_generated_binding_without_live_reference"
        else:
            classification = "uncertain"
            reason = "pipeline_missing_cannot_prove_inactive_asset_is_unreferenced"
        items.append(
            _item(
                classification=classification,
                resource_kind=f"asset_{safe_text(asset.get('asset_type'))}",
                path=path,
                reason=reason,
                **details,
            )
        )
    return items


def _job_items(
    *,
    project_id: int,
    current_refs: set[str],
    history_refs: set[str],
    candidate_by_path: dict[str, dict[str, Any]],
    now: datetime,
) -> list[dict[str, Any]]:
    root = INTERNAL_WORKSPACE_ROOT / "product-image-jobs" / f"project-{project_id}"
    if not root.is_dir():
        return []
    items: list[dict[str, Any]] = []
    for path in sorted((item for item in root.iterdir() if item.is_dir()), key=lambda item: item.name):
        key = _path_key(path)
        candidate = candidate_by_path.get(key)
        age_days = max(0.0, (now - _modified_at(path)).total_seconds() / 86400)
        if _protected(path, history_refs):
            classification = "history"
            reason = "job_directory_referenced_by_formal_history"
        elif _protected(path, current_refs):
            classification = "current"
            reason = "job_directory_referenced_by_current_working_state"
        elif candidate:
            classification = "cleanup_candidate"
            reason = safe_text(candidate.get("reason")) or "registered_cleanup_candidate"
        elif age_days >= LEGACY_JOB_MIN_AGE_DAYS:
            classification = "reclaimable"
            reason = "legacy_completed_job_without_live_reference"
        else:
            classification = "working_cache"
            reason = "recent_unregistered_job_may_still_be_in_use"
        items.append(
            _item(
                classification=classification,
                resource_kind="product_image_job",
                path=path,
                reason=reason,
                age_days=round(age_days, 2),
                candidate_status=safe_text(candidate.get("status")) if candidate else "",
                eligible_at=safe_text(candidate.get("eligible_at")) if candidate else "",
            )
        )
    return items


def _unbound_source_items(
    project: dict[str, Any],
    *,
    assets: list[dict[str, Any]],
    current_refs: set[str],
    history_refs: set[str],
    candidate_by_path: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    category = project_category_folder(project)
    roots = {
        "image": Path(safe_text(project.get("image_root")) or DEFAULT_IMAGE_ROOT) / category,
        "voice": Path(safe_text(project.get("voice_root")) or DEFAULT_VOICE_ROOT) / category,
        "video": Path(safe_text(project.get("video_root")) or DEFAULT_VIDEO_ROOT) / category,
    }
    bound = {
        _path_key(asset["path"])
        for asset in assets
        if safe_text(asset.get("path"))
    }
    items: list[dict[str, Any]] = []
    for asset_type, root in roots.items():
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or _path_key(path) in bound:
                continue
            if _protected(path, history_refs):
                classification = "history"
                reason = "unbound_file_referenced_by_formal_history"
            elif _protected(path, current_refs):
                classification = "current"
                reason = "unbound_file_referenced_by_current_working_state"
            elif _path_key(path) in candidate_by_path:
                classification = "cleanup_candidate"
                reason = "registered_delayed_cleanup_candidate"
            else:
                classification = "uncertain"
                reason = "file_under_managed_source_root_without_asset_binding"
            items.append(
                _item(
                    classification=classification,
                    resource_kind=f"unbound_{asset_type}",
                    path=path,
                    reason=reason,
                )
            )
    return items


def _summary(items: Iterable[dict[str, Any]]) -> dict[str, dict[str, int]]:
    summary: dict[str, dict[str, int]] = {}
    for item in items:
        key = safe_text(item.get("classification")) or "unknown"
        group = summary.setdefault(key, {"count": 0, "size_bytes": 0})
        group["count"] += 1
        group["size_bytes"] += int(item.get("size_bytes") or 0)
    return summary


def audit_project_resources(
    db: Database,
    *,
    project_id: int,
    pipeline_path: str | Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    repo = Repository(db)
    project = repo.project(project_id)
    if not project:
        raise ValueError(f"project does not exist: {project_id}")
    assets = repo.asset_bindings(project_id)
    resolved_pipeline = _pipeline_path(project, pipeline_path)
    pipeline_exists = bool(resolved_pipeline and resolved_pipeline.is_file())
    current_refs, history_refs = _reference_set(
        db,
        project=project,
        assets=assets,
        pipeline_path=resolved_pipeline,
    )
    candidate_rows = [
        dict(row)
        for row in db.fetchall(
            "SELECT * FROM resource_cleanup_candidates WHERE project_id=? ORDER BY id",
            (project_id,),
        )
    ]
    candidate_by_path = {
        _path_key(row["path"]): row
        for row in candidate_rows
        if safe_text(row.get("path"))
    }
    current_time = _utc_now(now)
    items = _binding_items(
        assets,
        current_refs=current_refs,
        history_refs=history_refs,
        candidate_by_path=candidate_by_path,
        pipeline_exists=pipeline_exists,
    )
    items.extend(
        _job_items(
            project_id=project_id,
            current_refs=current_refs,
            history_refs=history_refs,
            candidate_by_path=candidate_by_path,
            now=current_time,
        )
    )
    items.extend(
        _unbound_source_items(
            project,
            assets=assets,
            current_refs=current_refs,
            history_refs=history_refs,
            candidate_by_path=candidate_by_path,
        )
    )
    inactive_scripts = int(
        db.fetchone(
            "SELECT COUNT(*) AS count FROM script_blocks WHERE project_id=? AND active=0",
            (project_id,),
        )["count"]
    )
    return {
        "ok": True,
        "mode": "read_only_audit",
        "project": {"id": project_id, "name": safe_text(project.get("name"))},
        "pipeline": {
            "path": str(resolved_pipeline) if resolved_pipeline else "",
            "exists": pipeline_exists,
            "asset_cleanup_suspended": not pipeline_exists,
            "reason": "pipeline_missing" if not pipeline_exists else "",
        },
        "metadata": {
            "inactive_script_blocks": inactive_scripts,
            "cleanup_candidate_rows": len(candidate_rows),
        },
        "summary": _summary(items),
        "items": items,
    }


def assess_cleanup_candidates(
    db: Database,
    *,
    project_id: int,
    pipeline_path: str | Path | None = None,
    account_label: str = "",
    resource_kind: str = "",
    now: datetime | None = None,
) -> dict[str, Any]:
    repo = Repository(db)
    project = repo.project(project_id)
    if not project:
        raise ValueError(f"project does not exist: {project_id}")
    current = _utc_now(now)
    audit = audit_project_resources(
        db,
        project_id=project_id,
        pipeline_path=pipeline_path,
        now=current,
    )
    audit_by_path = {
        _path_key(item["path"]): item
        for item in audit["items"]
        if safe_text(item.get("path"))
    }
    candidates = [
        dict(row)
        for row in db.fetchall(
            """
            SELECT * FROM resource_cleanup_candidates
            WHERE project_id=?
            ORDER BY eligible_at, id
            """,
            (project_id,),
        )
    ]
    ready: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []

    for candidate in candidates:
        path = Path(safe_text(candidate.get("path"))).expanduser()
        item = audit_by_path.get(_path_key(path))
        candidate_kind = safe_text(candidate.get("resource_kind"))
        candidate_status = safe_text(candidate.get("status"))
        account = safe_text((item or {}).get("account_label"))
        result = {
            "candidate_id": int(candidate["id"]),
            "resource_kind": candidate_kind,
            "path": str(path),
            "reason": safe_text(candidate.get("reason")),
            "status": candidate_status,
            "eligible_at": safe_text(candidate.get("eligible_at")),
            "account_label": account,
            "size_bytes": int((item or {}).get("size_bytes") or 0),
        }
        if resource_kind and candidate_kind != safe_text(resource_kind):
            continue
        if account_label and account != safe_text(account_label):
            continue

        blocked_reason = ""
        if candidate_status != "pending":
            blocked_reason = f"candidate_status_{candidate_status or 'missing'}"
        elif (_parse_iso(candidate.get("eligible_at")) or datetime.max.replace(tzinfo=timezone.utc)) > current:
            blocked_reason = "retention_period_not_elapsed"
        elif item is None:
            blocked_reason = "resource_not_found_by_current_audit"
        elif safe_text(item.get("classification")) != "cleanup_candidate":
            blocked_reason = f"current_classification_{safe_text(item.get('classification')) or 'unknown'}"
        elif candidate_kind.startswith("asset_") and not bool(audit["pipeline"]["exists"]):
            blocked_reason = "pipeline_missing_cannot_prove_asset_is_unreferenced"
        elif not path.exists():
            blocked_reason = "resource_missing"
        elif not _path_is_managed(path, project):
            blocked_reason = "path_outside_managed_roots"
        else:
            try:
                snapshot = _entry_snapshot(path)
            except (OSError, ValueError) as exc:
                blocked_reason = safe_text(exc) or type(exc).__name__
            else:
                ready.append({**result, **snapshot})
                continue
        blocked.append({**result, "blocked_reason": blocked_reason})

    return {
        "ok": True,
        "mode": "cleanup_candidate_assessment",
        "project": audit["project"],
        "pipeline": audit["pipeline"],
        "filters": {
            "account_label": safe_text(account_label),
            "resource_kind": safe_text(resource_kind),
        },
        "ready": ready,
        "blocked": blocked,
        "summary": {
            "ready_count": len(ready),
            "ready_size_bytes": sum(int(item["size_bytes"]) for item in ready),
            "blocked_count": len(blocked),
        },
    }


def _confirmation_token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _batch_snapshot_payload(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "candidate_id": int(item["candidate_id"]),
            "resource_kind": safe_text(item.get("resource_kind")),
            "path": safe_text(item.get("path")),
            "entry_kind": safe_text(item.get("entry_kind")),
            "size_bytes": int(item.get("size_bytes") or 0),
            "mtime_ns": int(item.get("mtime_ns") or 0),
            "fingerprint": safe_text(item.get("fingerprint")),
        }
        for item in sorted(items, key=lambda row: int(row["candidate_id"]))
    ]


def _batch_snapshot_hash(items: list[dict[str, Any]]) -> str:
    payload = json.dumps(
        _batch_snapshot_payload(items),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def prepare_cleanup_batch(
    db: Database,
    *,
    project_id: int,
    pipeline_path: str | Path | None = None,
    account_label: str = "",
    resource_kind: str = "",
    now: datetime | None = None,
) -> dict[str, Any]:
    assessment = assess_cleanup_candidates(
        db,
        project_id=project_id,
        pipeline_path=pipeline_path,
        account_label=account_label,
        resource_kind=resource_kind,
        now=now,
    )
    ready = assessment["ready"]
    if not ready:
        return {
            **assessment,
            "mode": "cleanup_batch_not_created",
            "batch": None,
            "note": "No resources passed every deletion gate.",
        }

    batch_id = f"cleanup-{uuid.uuid4().hex}"
    token = secrets.token_urlsafe(18)
    snapshot_hash = _batch_snapshot_hash(ready)
    created_at = now_iso()
    filters = {
        "pipeline_path": str(Path(pipeline_path).expanduser()) if pipeline_path else "",
        "account_label": safe_text(account_label),
        "resource_kind": safe_text(resource_kind),
    }
    with db.connect() as conn:
        conn.execute(
            """
            UPDATE resource_cleanup_batches
            SET status='superseded', result_json=?
            WHERE project_id=? AND status='prepared'
            """,
            (
                json.dumps(
                    {"reason": "newer_cleanup_batch_prepared", "superseded_by": batch_id},
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                project_id,
            ),
        )
        conn.execute(
            """
            INSERT INTO resource_cleanup_batches
                (id, project_id, status, filters_json, snapshot_hash,
                 confirmation_token_hash, candidate_count, total_size_bytes,
                 created_at)
            VALUES (?, ?, 'prepared', ?, ?, ?, ?, ?, ?)
            """,
            (
                batch_id,
                project_id,
                json.dumps(filters, ensure_ascii=False, sort_keys=True),
                snapshot_hash,
                _confirmation_token_hash(token),
                len(ready),
                sum(int(item["size_bytes"]) for item in ready),
                created_at,
            ),
        )
        for item in ready:
            conn.execute(
                """
                INSERT INTO resource_cleanup_batch_items
                    (batch_id, candidate_id, resource_kind, path, reason,
                     expected_entry_kind, expected_size_bytes, expected_mtime_ns,
                     expected_fingerprint)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    batch_id,
                    int(item["candidate_id"]),
                    safe_text(item.get("resource_kind")),
                    safe_text(item.get("path")),
                    safe_text(item.get("reason")),
                    safe_text(item.get("entry_kind")),
                    int(item.get("size_bytes") or 0),
                    int(item.get("mtime_ns") or 0),
                    safe_text(item.get("fingerprint")),
                ),
            )
    return {
        **assessment,
        "mode": "cleanup_batch_prepared",
        "batch": {
            "id": batch_id,
            "status": "prepared",
            "confirmation_token": token,
            "snapshot_hash": snapshot_hash,
            "candidate_count": len(ready),
            "total_size_bytes": sum(int(item["size_bytes"]) for item in ready),
            "created_at": created_at,
        },
        "note": "No files were deleted. The token is valid only while every snapshot remains unchanged.",
    }


def _stored_batch_items(db: Database, batch_id: str) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in db.fetchall(
            "SELECT * FROM resource_cleanup_batch_items WHERE batch_id=? ORDER BY id",
            (batch_id,),
        )
    ]


def _snapshot_mismatch(stored: dict[str, Any], current: dict[str, Any] | None) -> str:
    if current is None:
        return "candidate_no_longer_deletable"
    comparisons = (
        ("path", safe_text(stored.get("path")), safe_text(current.get("path"))),
        (
            "entry_kind",
            safe_text(stored.get("expected_entry_kind")),
            safe_text(current.get("entry_kind")),
        ),
        (
            "size_bytes",
            int(stored.get("expected_size_bytes") or 0),
            int(current.get("size_bytes") or 0),
        ),
        (
            "mtime_ns",
            int(stored.get("expected_mtime_ns") or 0),
            int(current.get("mtime_ns") or 0),
        ),
        (
            "fingerprint",
            safe_text(stored.get("expected_fingerprint")),
            safe_text(current.get("fingerprint")),
        ),
    )
    for field, expected, actual in comparisons:
        if expected != actual:
            return f"snapshot_changed_{field}"
    return ""


def _mark_batch_stale(
    db: Database,
    *,
    batch_id: str,
    mismatches: dict[int, str],
) -> None:
    result = {
        "reason": "batch_snapshot_changed",
        "mismatches": [
            {"candidate_id": candidate_id, "reason": reason}
            for candidate_id, reason in sorted(mismatches.items())
        ],
    }
    with db.connect() as conn:
        conn.execute(
            "UPDATE resource_cleanup_batches SET status='stale', result_json=? WHERE id=?",
            (json.dumps(result, ensure_ascii=False, sort_keys=True), batch_id),
        )
        for candidate_id, reason in mismatches.items():
            conn.execute(
                """
                UPDATE resource_cleanup_batch_items
                SET status='stale', result_message=?
                WHERE batch_id=? AND candidate_id=?
                """,
                (reason, batch_id, candidate_id),
            )


def delete_cleanup_batch(
    db: Database,
    *,
    batch_id: str,
    confirmation_token: str,
    confirmed_by: str = "user",
    now: datetime | None = None,
) -> dict[str, Any]:
    batch_row = db.fetchone("SELECT * FROM resource_cleanup_batches WHERE id=?", (batch_id,))
    if not batch_row:
        raise ValueError(f"cleanup batch does not exist: {batch_id}")
    batch = dict(batch_row)
    if safe_text(batch.get("status")) != "prepared":
        raise ValueError(f"cleanup batch is not executable: {safe_text(batch.get('status'))}")
    if not secrets.compare_digest(
        safe_text(batch.get("confirmation_token_hash")),
        _confirmation_token_hash(safe_text(confirmation_token)),
    ):
        raise ValueError("cleanup confirmation token does not match")

    try:
        filters = json.loads(safe_text(batch.get("filters_json")) or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError("cleanup batch filters are invalid") from exc
    assessment = assess_cleanup_candidates(
        db,
        project_id=int(batch["project_id"]),
        pipeline_path=safe_text(filters.get("pipeline_path")) or None,
        account_label=safe_text(filters.get("account_label")),
        resource_kind=safe_text(filters.get("resource_kind")),
        now=now,
    )
    current_by_candidate = {
        int(item["candidate_id"]): item for item in assessment["ready"]
    }
    stored_items = _stored_batch_items(db, batch_id)
    mismatches: dict[int, str] = {}
    for item in stored_items:
        candidate_id = int(item["candidate_id"])
        mismatch = _snapshot_mismatch(item, current_by_candidate.get(candidate_id))
        if mismatch:
            mismatches[candidate_id] = mismatch
    if _batch_snapshot_hash(
        [current_by_candidate[int(item["candidate_id"])] for item in stored_items if int(item["candidate_id"]) in current_by_candidate]
    ) != safe_text(batch.get("snapshot_hash")):
        for item in stored_items:
            candidate_id = int(item["candidate_id"])
            mismatches.setdefault(candidate_id, "batch_snapshot_hash_changed")
    if mismatches:
        _mark_batch_stale(db, batch_id=batch_id, mismatches=mismatches)
        return {
            "ok": False,
            "mode": "cleanup_batch_rejected",
            "batch_id": batch_id,
            "status": "stale",
            "deleted_count": 0,
            "mismatches": [
                {"candidate_id": candidate_id, "reason": reason}
                for candidate_id, reason in sorted(mismatches.items())
            ],
            "note": "No files were deleted. Prepare and confirm a new batch.",
        }

    confirmed_at = _iso(_utc_now(now))
    with db.connect() as conn:
        conn.execute(
            """
            UPDATE resource_cleanup_batches
            SET status='confirmed', confirmed_by=?, confirmed_at=?
            WHERE id=? AND status='prepared'
            """,
            (safe_text(confirmed_by) or "user", confirmed_at, batch_id),
        )

    deleted: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    project = Repository(db).project(int(batch["project_id"]))
    if not project:
        raise ValueError(f"project does not exist: {batch['project_id']}")
    for item in stored_items:
        candidate_id = int(item["candidate_id"])
        path = Path(safe_text(item.get("path"))).expanduser()
        with db.connect() as conn:
            conn.execute(
                "UPDATE resource_cleanup_batch_items SET status='deleting' WHERE id=?",
                (int(item["id"]),),
            )
        try:
            if not _path_is_managed(path, project):
                raise RuntimeError("path_outside_managed_roots")
            current_snapshot = _entry_snapshot(path)
            mismatch = _snapshot_mismatch(item, {"path": str(path), **current_snapshot})
            if mismatch:
                raise RuntimeError(mismatch)
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                shutil.rmtree(path)
            else:
                raise RuntimeError("unsupported_entry_kind")
            if path.exists():
                raise RuntimeError("resource_still_exists_after_delete")
        except (OSError, RuntimeError, ValueError) as exc:
            message = safe_text(exc) or type(exc).__name__
            failed.append({"candidate_id": candidate_id, "path": str(path), "reason": message})
            with db.connect() as conn:
                conn.execute(
                    """
                    UPDATE resource_cleanup_batch_items
                    SET status='failed', result_message=?
                    WHERE id=?
                    """,
                    (message, int(item["id"])),
                )
            continue

        deleted_at = _iso(_utc_now())
        with db.connect() as conn:
            conn.execute(
                """
                UPDATE resource_cleanup_batch_items
                SET status='deleted', result_message='permanently_deleted', deleted_at=?
                WHERE id=?
                """,
                (deleted_at, int(item["id"])),
            )
            conn.execute(
                """
                UPDATE resource_cleanup_candidates
                SET status='purged', resolved_at=?, last_seen_at=?
                WHERE id=?
                """,
                (deleted_at, deleted_at, candidate_id),
            )
            record_resource_state_event_in_connection(
                conn,
                project_id=int(batch["project_id"]),
                resource_kind=safe_text(item.get("resource_kind")),
                resource_key=f"cleanup_candidate:{candidate_id}",
                path=path,
                previous_state="pending",
                new_state="purged",
                reason=safe_text(item.get("reason")),
                source="resource_cleanup_delete",
                details={
                    "batch_id": batch_id,
                    "expected_entry_kind": safe_text(item.get("expected_entry_kind")),
                    "expected_size_bytes": int(item.get("expected_size_bytes") or 0),
                    "expected_mtime_ns": int(item.get("expected_mtime_ns") or 0),
                    "expected_fingerprint": safe_text(item.get("expected_fingerprint")),
                    "confirmed_by": safe_text(confirmed_by) or "user",
                },
                created_at=deleted_at,
            )
        deleted.append({"candidate_id": candidate_id, "path": str(path)})

    status = "completed" if not failed else "partial"
    executed_at = _iso(_utc_now())
    result_payload = {
        "deleted_count": len(deleted),
        "failed_count": len(failed),
        "deleted": deleted,
        "failed": failed,
    }
    with db.connect() as conn:
        conn.execute(
            """
            UPDATE resource_cleanup_batches
            SET status=?, executed_at=?, result_json=?
            WHERE id=?
            """,
            (
                status,
                executed_at,
                json.dumps(result_payload, ensure_ascii=False, sort_keys=True),
                batch_id,
            ),
        )
    post_audit = audit_project_resources(
        db,
        project_id=int(batch["project_id"]),
        pipeline_path=safe_text(filters.get("pipeline_path")) or None,
        now=now,
    )
    return {
        "ok": not failed,
        "mode": "cleanup_batch_executed",
        "batch_id": batch_id,
        "status": status,
        **result_payload,
        "post_audit_summary": post_audit["summary"],
    }


def reconcile_project_resources(
    db: Database,
    *,
    project_id: int,
    pipeline_path: str | Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = _utc_now(now)
    audit = audit_project_resources(
        db,
        project_id=project_id,
        pipeline_path=pipeline_path,
        now=current,
    )
    corrected_missing_bindings: list[int] = []
    broken_bindings = [
        item
        for item in audit["items"]
        if item["classification"] == "broken_reference"
        and int(item.get("asset_binding_id") or 0)
    ]
    if broken_bindings:
        corrected_at = _iso(current)
        with db.connect() as conn:
            for item in broken_bindings:
                binding_id = int(item["asset_binding_id"])
                cursor = conn.execute(
                    """
                    UPDATE asset_bindings
                    SET status='missing', updated_at=?
                    WHERE project_id=? AND id=? AND status='ready'
                    """,
                    (corrected_at, project_id, binding_id),
                )
                if cursor.rowcount != 1:
                    continue
                corrected_missing_bindings.append(binding_id)
                record_resource_state_event_in_connection(
                    conn,
                    project_id=project_id,
                    resource_kind=safe_text(item.get("asset_type")),
                    resource_key=f"asset_binding:{binding_id}",
                    path=safe_text(item.get("path")),
                    previous_state="ready",
                    new_state="missing",
                    reason="ready_binding_file_missing",
                    source="resource_reconcile",
                    account_label=safe_text(item.get("account_label")),
                    created_at=corrected_at,
                )
    registered: list[str] = []
    for item in audit["items"]:
        if item["classification"] != "reclaimable":
            continue
        kind = safe_text(item.get("resource_kind"))
        if kind == "product_image_job":
            eligible_at = _modified_at(Path(item["path"])) + timedelta(
                days=PRODUCT_IMAGE_JOB_RETENTION_DAYS
            )
        elif kind == "product_cover_cache":
            eligible_at = _modified_at(Path(item["path"])) + timedelta(
                days=PRODUCT_COVER_CACHE_RETENTION_DAYS
            )
        else:
            eligible_at = current + timedelta(days=DERIVED_ASSET_RETENTION_DAYS)
        register_cleanup_candidate(
            db,
            project_id=project_id,
            resource_kind=kind,
            path=item["path"],
            reason=safe_text(item.get("reason")),
            eligible_at=eligible_at,
            details={"discovered_by": "resource_reconcile"},
        )
        registered.append(item["path"])
    refreshed = audit_project_resources(
        db,
        project_id=project_id,
        pipeline_path=pipeline_path,
        now=current,
    )
    return {
        "ok": True,
        "mode": "reconcile_candidates_only",
        "project": refreshed["project"],
        "pipeline": refreshed["pipeline"],
        "registered_count": len(registered),
        "registered_paths": registered,
        "corrected_missing_binding_count": len(corrected_missing_bindings),
        "corrected_missing_binding_ids": corrected_missing_bindings,
        "summary": refreshed["summary"],
        "note": "No files were moved or deleted. Broken ready bindings were corrected to missing.",
    }
