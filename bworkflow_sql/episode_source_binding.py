from __future__ import annotations

from typing import Any

from .repositories import Repository
from .sync_service import SyncService
from .episode_source_snapshot import build_episode_source_payload
from .utils import safe_text


class EpisodeSourceBindingError(RuntimeError):
    def __init__(self, code: str, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = dict(details or {})


def resolve_episode_source_binding(
    repo: Repository,
    sync: SyncService,
    project_id: int,
    *,
    expected_snapshot_id: str = "",
    episode_id: str = "",
    require_current: bool = False,
    apply: bool = False,
) -> dict[str, Any]:
    """Return a B-Workflow-owned frozen source binding for one new episode."""
    expected = safe_text(expected_snapshot_id)
    if apply and not expected:
        raise EpisodeSourceBindingError(
            "expected_snapshot_id_required",
            "应用 Master 同步前必须携带预检返回的 master_snapshot_id。",
        )
    if apply:
        if not safe_text(episode_id):
            raise EpisodeSourceBindingError("episode_id_required", "签发当期来源快照必须提供 episode_id。")
        snapshot, plan = sync.master_snapshot_plan(
            project_id, force_refresh=True, expected_snapshot_id=expected
        )
        repo.apply_master_snapshot_plan(plan)
        payload, source_json, source_sha256 = build_episode_source_payload(snapshot, plan.records)
        row = repo.create_episode_source_snapshot(
            project_id=project_id,
            episode_id=episode_id,
            master_snapshot_id=snapshot.snapshot_id,
            source_sha256=source_sha256,
            source_json=source_json,
        )
        return _ready_binding(
            repo, project_id, expected_snapshot_id=expected, episode_id=episode_id,
            source_sha256=safe_text(row.get("source_sha256")), applied_at=safe_text(row.get("created_at")),
        )

    if safe_text(episode_id):
        return _stored_binding(repo, project_id, episode_id, expected_snapshot_id=expected)

    if require_current or not expected:
        _snapshot, plan = sync.master_snapshot_plan(
            project_id, force_refresh=True, expected_snapshot_id=expected or None
        )
        preview = {
            "snapshot_id": plan.snapshot_id,
            "change_count": plan.change_count,
            "unchanged_count": len(plan.unchanged),
            "added": [item.uid for item in plan.added],
            "updated": [item.uid for item in plan.updated],
            "removed": [item.uid for item in plan.removed],
        }
        candidate_snapshot_id = safe_text(preview.get("snapshot_id"))
        if not candidate_snapshot_id:
            raise EpisodeSourceBindingError(
                "master_snapshot_missing",
                "B-Workflow 未从 Master 取得可用于本期的快照 ID。",
            )
        project = _project(repo, project_id)
        if candidate_snapshot_id != safe_text(project.get("master_snapshot_id")):
            return {
                "kind": "EpisodeSourceBinding",
                "schema_version": 1,
                "ok": True,
                "status": "sync_required",
                "expected_snapshot_id": candidate_snapshot_id,
                "preview": preview,
                "binding": None,
            }

    return _ready_binding(repo, project_id, expected_snapshot_id=expected)


def _ready_binding(
    repo: Repository,
    project_id: int,
    *,
    expected_snapshot_id: str,
    episode_id: str = "",
    source_sha256: str = "",
    applied_at: str = "",
) -> dict[str, Any]:
    project = _project(repo, project_id)
    snapshot_id = safe_text(project.get("master_snapshot_id"))
    project_applied_at = safe_text(project.get("master_snapshot_applied_at"))
    workspace_id = safe_text(project.get("workspace_id"))
    scheme_id = safe_text(project.get("scheme_id"))
    scheme_name = safe_text(project.get("scheme_name"))
    expected = safe_text(expected_snapshot_id)
    if not snapshot_id or not project_applied_at:
        raise EpisodeSourceBindingError(
            "master_snapshot_not_applied",
            "B-Workflow 项目尚未应用 Master 快照，不能创建新一期。",
        )
    if expected and snapshot_id != expected:
        raise EpisodeSourceBindingError(
            "bound_snapshot_mismatch",
            "B-Workflow 已应用的快照与请求的来源快照不一致。",
            details={"expected_snapshot_id": expected, "actual_snapshot_id": snapshot_id},
        )
    if not workspace_id or not scheme_id or not scheme_name:
        raise EpisodeSourceBindingError(
            "project_source_identity_missing",
            "B-Workflow 项目缺少 workspace、方案 ID 或方案名称，不能签发来源绑定。",
        )
    return {
        "kind": "EpisodeSourceBinding",
        "schema_version": 1,
        "ok": True,
        "status": "ready",
        "binding": {
            "contract_version": 1,
            "issuer": "bworkflow",
            "mode": "frozen",
            "bworkflow_project_id": project_id,
            "workspace_id": workspace_id,
            "scheme_id": scheme_id,
            "scheme_name": scheme_name,
            "master_snapshot_id": snapshot_id,
            "master_snapshot_applied_at": applied_at or project_applied_at,
            "product_count": len(repo.products(project_id, include_removed=False)),
            "episode_id": safe_text(episode_id),
            "source_sha256": safe_text(source_sha256),
        },
    }


def _project(repo: Repository, project_id: int) -> dict[str, Any]:
    project = repo.project(project_id)
    if not project:
        raise EpisodeSourceBindingError("project_not_found", f"B-Workflow 项目不存在：{project_id}")
    return project


def _stored_binding(
    repo: Repository, project_id: int, episode_id: str, *, expected_snapshot_id: str
) -> dict[str, Any]:
    row = repo.episode_source_snapshot(project_id, episode_id)
    if row is None:
        raise EpisodeSourceBindingError("episode_source_not_found", "未找到该期的冻结来源快照。")
    project = _project(repo, project_id)
    snapshot_id = safe_text(row.get("master_snapshot_id"))
    expected = safe_text(expected_snapshot_id)
    if expected and expected != snapshot_id:
        raise EpisodeSourceBindingError(
            "bound_snapshot_mismatch", "该期冻结的 Master 快照与请求不一致。",
            details={"expected_snapshot_id": expected, "actual_snapshot_id": snapshot_id},
        )
    products = repo.episode_products(project_id, episode_id)
    return {
        "kind": "EpisodeSourceBinding", "schema_version": 1, "ok": True, "status": "ready",
        "binding": {
            "contract_version": 1, "issuer": "bworkflow", "mode": "frozen",
            "bworkflow_project_id": project_id,
            "workspace_id": safe_text(project.get("workspace_id")),
            "scheme_id": safe_text(project.get("scheme_id")),
            "scheme_name": safe_text(project.get("scheme_name")),
            "master_snapshot_id": snapshot_id,
            "master_snapshot_applied_at": safe_text(row.get("created_at")),
            "product_count": len(products or []),
            "episode_id": safe_text(episode_id),
            "source_sha256": safe_text(row.get("source_sha256")),
        },
    }
