from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

from .artifact_approvals import atomic_update_pipeline
from .settings import DEFAULT_MASTER_API_BASE_URL
from .utils import now_iso, safe_text


class PublishingDeliveryError(ValueError):
    pass


def _request_json(method: str, url: str, *, workspace_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-Workspace-Id": workspace_id,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:  # nosec B310 - configured local Master API
            decoded = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise PublishingDeliveryError(f"Master 请求失败 ({exc.code}): {detail}") from exc
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise PublishingDeliveryError("Master 发布管理不可用") from exc
    if not isinstance(decoded, dict):
        raise PublishingDeliveryError("Master 返回了无效的发布任务数据")
    return decoded


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _approved_file(pipeline: dict[str, Any], approval_key: str) -> tuple[Path, str, int]:
    approvals = pipeline.get("artifact_approvals") if isinstance(pipeline.get("artifact_approvals"), dict) else {}
    approval = approvals.get(approval_key) if isinstance(approvals.get(approval_key), dict) else {}
    path = Path(safe_text(approval.get("path"))).expanduser().resolve()
    if not path.is_file():
        raise PublishingDeliveryError(f"未找到已验收的 {approval_key} 文件")
    expected_size = int(approval.get("size") or 0)
    expected_hash = safe_text(approval.get("sha256")).removeprefix("sha256:")
    actual_size = path.stat().st_size
    actual_hash = _sha256(path)
    if expected_size != actual_size or expected_hash != actual_hash:
        raise PublishingDeliveryError(f"已验收的 {approval_key} 文件已变化，拒绝上传")
    return path, actual_hash, actual_size


def _put_file(url: str, path: Path, headers: dict[str, Any]) -> None:
    request_headers = {str(key): str(value) for key, value in headers.items()}
    # R2's S3 PUT endpoint rejects chunked transfer encoding for these signed
    # single-object uploads. Supplying the known byte length keeps urllib on a
    # regular Content-Length request without loading a full video into memory.
    request_headers["Content-Length"] = str(path.stat().st_size)
    request = urllib.request.Request(
        url,
        data=path.open("rb"),
        method="PUT",
        headers=request_headers,
    )
    try:
        with urllib.request.urlopen(request, timeout=600):  # nosec B310 - Master supplied R2 presigned URL
            return
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise PublishingDeliveryError(f"上传 {path.name} 到发布管理失败 ({exc.code}): {detail}") from exc
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise PublishingDeliveryError(f"上传 {path.name} 到发布管理失败: {exc}") from exc


def _build_delivery_archive(video: Path, cover: Path) -> Path:
    output = video.parent / f"{video.parent.name}-发布素材.zip"
    with tempfile.NamedTemporaryFile(
        dir=output.parent,
        prefix=f".{output.stem}-",
        suffix=".tmp",
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
            archive.write(video, arcname=video.name)
            archive.write(cover, arcname=cover.name)
        with zipfile.ZipFile(temporary) as archive:
            if archive.testzip() is not None:
                raise PublishingDeliveryError("发布素材压缩包校验失败")
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return output


def upload_approved_publishing_assets(
    pipeline_path: str | Path,
    *,
    master_url: str = DEFAULT_MASTER_API_BASE_URL,
) -> dict[str, Any]:
    pipeline_file = Path(pipeline_path).expanduser().resolve()
    pipeline = json.loads(pipeline_file.read_text(encoding="utf-8-sig"))
    if not isinstance(pipeline, dict):
        raise PublishingDeliveryError("pipeline 必须是 JSON 对象")
    publishing = pipeline.get("publishing") if isinstance(pipeline.get("publishing"), dict) else {}
    task_id = safe_text(publishing.get("task_id"))
    workspace_id = safe_text((pipeline.get("source_binding") or {}).get("workspace_id") or pipeline.get("workspace_id"))
    if not task_id or not workspace_id:
        raise PublishingDeliveryError("pipeline 缺少 Master 发布任务身份")
    base_url = master_url.rstrip("/")
    current = _request_json("GET", f"{base_url}/api/publishing/tasks/{task_id}", workspace_id=workspace_id)
    task = current.get("task") if isinstance(current.get("task"), dict) else {}
    if safe_text(task.get("bilibili_url")):
        raise PublishingDeliveryError("发布管理已填写 B站链接；不能覆盖已发布任务的交付物")
    if safe_text(task.get("blue_link_status")) != "complete":
        raise PublishingDeliveryError("补充蓝链尚未完整，不能上传发布交付物")

    video, _, _ = _approved_file(pipeline, "full_mp4")
    cover, _, _ = _approved_file(pipeline, "cover_image")
    archive = _build_delivery_archive(video, cover)
    sha256 = _sha256(archive)
    size_bytes = archive.stat().st_size
    metadata = {
        "kind": "archive",
        "filename": archive.name,
        "content_type": mimetypes.guess_type(archive.name)[0] or "application/zip",
        "size_bytes": size_bytes,
        "sha256": sha256,
    }
    presign = _request_json(
        "POST",
        f"{base_url}/api/publishing/tasks/{task_id}/assets/presign",
        workspace_id=workspace_id,
        payload=metadata,
    )
    upload_url = safe_text(presign.get("upload_url"))
    object_key = safe_text(presign.get("object_key"))
    headers = presign.get("headers") if isinstance(presign.get("headers"), dict) else {}
    if not upload_url or not object_key:
        raise PublishingDeliveryError("Master 未返回有效的上传地址")
    _put_file(upload_url, archive, headers)
    _request_json(
        "POST",
        f"{base_url}/api/publishing/tasks/{task_id}/assets/confirm",
        workspace_id=workspace_id,
        payload={**metadata, "object_key": object_key},
    )
    confirmed = {"kind": "archive", "path": str(archive), "sha256": sha256, "size_bytes": size_bytes, "object_key": object_key}
    cleaned = _request_json(
        "DELETE",
        f"{base_url}/api/publishing/tasks/{task_id}/assets/legacy",
        workspace_id=workspace_id,
    )

    projection = _request_json(
        "PATCH",
        f"{base_url}/api/publishing/tasks/{task_id}/projection",
        workspace_id=workspace_id,
        payload={"current_phase": "awaiting_bilibili_publish"},
    )
    final_task = projection.get("task") if isinstance(projection.get("task"), dict) else {}
    if not final_task.get("delivery_ready") or final_task.get("publication_status") != "ready":
        raise PublishingDeliveryError("发布管理未确认完整交付物，拒绝推进流程")

    def mutate(current_pipeline: dict[str, Any]) -> None:
        current_publishing = current_pipeline.get("publishing") if isinstance(current_pipeline.get("publishing"), dict) else {}
        current_publishing.update({
            "status": "assets_uploaded",
            "task_id": task_id,
            "assets_uploaded_at": now_iso(),
            "remote_publication_status": "ready",
            "remote_current_phase": "awaiting_bilibili_publish",
            "uploaded_assets": [confirmed],
        })
        current_pipeline["publishing"] = current_publishing
        phases = current_pipeline.get("phases") if isinstance(current_pipeline.get("phases"), dict) else {}
        phase = phases.get("publishing") if isinstance(phases.get("publishing"), dict) else {}
        phase.update({"status": "assets_uploaded", "production_run_id": phase.get("production_run_id")})
        phases["publishing"] = phase
        current_pipeline["phases"] = phases
        current_pipeline["current_phase"] = "publishing"
        current_pipeline["next_action"] = "协作者可在发布管理下载成片和封面，手动发布后在网页填写真实 B站链接。"
        current_pipeline["updated_at"] = now_iso()

    atomic_update_pipeline(pipeline_file, mutate)
    return {"ok": True, "task_id": task_id, "assets": [confirmed], "task": final_task, "legacy_cleanup": cleaned.get("task")}
