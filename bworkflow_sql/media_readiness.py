from __future__ import annotations

import json
import hashlib
import subprocess
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable

from .utils import safe_text


VIDEO_SUFFIXES = frozenset({".mp4", ".mov", ".mkv", ".avi"})
ProbeVideo = Callable[[Path], dict[str, Any]]


def audit_product_video_media(
    products: list[dict[str, Any]],
    asset_bindings: list[dict[str, Any]],
    *,
    video_root: str | Path | None = None,
    probe_video: ProbeVideo | None = None,
) -> dict[str, Any]:
    """Read-only local-video audit.

    The filesystem decides whether a candidate is usable. Asset bindings provide
    provenance and reconciliation hints, but a ready binding never proves that
    its path still exists.
    """
    active = {
        safe_text(product.get("uid")): safe_text(product.get("title"))
        for product in products
        if safe_text(product.get("uid"))
    }
    candidates: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)

    def add_candidate(uid: str, path_value: Any, *, discovery: str, binding_status: str = "") -> None:
        if uid not in active:
            return
        path_text = safe_text(path_value)
        if not path_text:
            return
        try:
            path = Path(path_text).resolve()
        except (OSError, RuntimeError, ValueError):
            return
        key = str(path)
        candidate = candidates[uid].setdefault(
            key,
            {
                "path": key,
                "discoveries": [],
                "binding_statuses": [],
            },
        )
        if discovery not in candidate["discoveries"]:
            candidate["discoveries"].append(discovery)
        if binding_status and binding_status not in candidate["binding_statuses"]:
            candidate["binding_statuses"].append(binding_status)

    for binding in asset_bindings:
        if safe_text(binding.get("asset_type")) != "video":
            continue
        add_candidate(
            safe_text(binding.get("uid")),
            binding.get("path"),
            discovery="asset_binding",
            binding_status=safe_text(binding.get("status")),
        )

    root_text = safe_text(video_root)
    root = Path(root_text) if root_text else None
    if root is not None and root.is_dir():
        uid_tokens = {uid.casefold(): uid for uid in active}
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix.casefold() not in VIDEO_SUFFIXES:
                continue
            name = path.name.casefold()
            for token, uid in uid_tokens.items():
                if token in name:
                    add_candidate(uid, path, discovery="filesystem_scan")

    probe = probe_video or probe_local_video
    items: list[dict[str, Any]] = []
    selected: dict[str, str] = {}
    for uid in sorted(active):
        evaluated = [_evaluate_candidate(item, probe=probe) for item in candidates.get(uid, {}).values()]
        usable = [item for item in evaluated if item.get("usable") is True]
        usable.sort(key=_candidate_rank)
        if usable:
            selected[uid] = safe_text(usable[0].get("path"))
        items.append(
            {
                "uid": uid,
                "title": active[uid],
                "selected_path": selected.get(uid, ""),
                "status": "verified" if uid in selected else "missing",
                "candidates": evaluated,
            }
        )
    return {
        "schema_version": 1,
        "kind": "bworkflow.local_product_video_readiness",
        "asset_root": str(root.resolve()) if root is not None and root.exists() else root_text,
        "total_products": len(active),
        "verified_video_count": len(selected),
        "missing_video_count": len(active) - len(selected),
        "selected_paths": selected,
        "items": items,
    }


def probe_local_video(path: Path) -> dict[str, Any]:
    completed = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-show_streams",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode != 0:
        return {"ok": False, "reason": "ffprobe_failed", "detail": completed.stderr.strip()[-500:]}
    try:
        payload = json.loads(completed.stdout or "{}")
        duration = float(safe_text(payload.get("format", {}).get("duration")) or "0")
    except (TypeError, ValueError, json.JSONDecodeError):
        return {"ok": False, "reason": "ffprobe_invalid_output"}
    has_video = any(safe_text(stream.get("codec_type")) == "video" for stream in payload.get("streams") or [])
    if duration <= 0 or not has_video:
        return {"ok": False, "reason": "not_decodable_video", "duration": duration, "has_video": has_video}
    return {"ok": True, "duration": duration, "has_video": True}


def snapshot_verified_product_videos(
    readiness: dict[str, Any],
    *,
    probe_video: ProbeVideo | None = None,
) -> dict[str, Any]:
    """Revalidate chosen local videos immediately before formal rendering.

    The returned immutable facts make a render reproducible and prevent a
    previously successful preflight from masking a file replacement or removal.
    """
    selected = readiness.get("selected_paths") if isinstance(readiness, dict) else None
    if not isinstance(selected, dict):
        return {"ok": False, "status": "invalid_readiness", "items": [], "issues": [{"code": "media_readiness_missing"}]}
    probe = probe_video or probe_local_video
    items: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    for uid, path_value in sorted(selected.items()):
        path = Path(safe_text(path_value))
        item: dict[str, Any] = {"uid": safe_text(uid), "path": str(path)}
        if not path.is_file():
            item.update({"ok": False, "reason": "file_missing"})
        else:
            stat = path.stat()
            if stat.st_size <= 0:
                item.update({"ok": False, "reason": "file_empty", "size": stat.st_size})
            else:
                probe_result = probe(path)
                item.update(
                    {
                        "ok": probe_result.get("ok") is True,
                        "size": stat.st_size,
                        "mtime_ns": stat.st_mtime_ns,
                        "sha256": _path_sha256(path),
                        "probe": probe_result,
                    }
                )
                if item["ok"] is not True:
                    item["reason"] = safe_text(probe_result.get("reason")) or "probe_failed"
        if item.get("ok") is not True:
            issues.append({"code": "product_video_changed_or_unavailable", "uid": item["uid"], "path": item["path"], "reason": item.get("reason")})
        items.append(item)
    return {"ok": not issues, "status": "verified" if not issues else "blocked", "items": items, "issues": issues}


def _evaluate_candidate(candidate: dict[str, Any], *, probe: ProbeVideo) -> dict[str, Any]:
    result = dict(candidate)
    path = Path(safe_text(result.get("path")))
    if not path.is_file():
        result.update({"usable": False, "rejection": "file_missing", "size": 0})
        return result
    size = path.stat().st_size
    if size <= 0:
        result.update({"usable": False, "rejection": "file_empty", "size": size})
        return result
    probe_result = probe(path)
    result["size"] = size
    result["probe"] = probe_result
    if probe_result.get("ok") is not True:
        result.update({"usable": False, "rejection": safe_text(probe_result.get("reason")) or "probe_failed"})
        return result
    result["usable"] = True
    return result


def _candidate_rank(candidate: dict[str, Any]) -> tuple[int, int, str]:
    statuses = set(candidate.get("binding_statuses") or [])
    return (
        0 if "ready" in statuses else 1,
        0 if "asset_binding" in (candidate.get("discoveries") or []) else 1,
        safe_text(candidate.get("path")),
    )


def _path_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
