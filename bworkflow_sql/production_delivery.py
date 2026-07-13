from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from .utils import safe_text


DEFAULT_PRODUCTION_DELIVERY_ROOT = Path(r"G:\2026项目-b站")


def _safe_path_component(value: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*]+', "-", safe_text(value)).strip(" .-")
    return cleaned or "unnamed"


def resolve_project_delivery_dir(
    *,
    project: dict[str, Any],
    account_label: str,
    pipeline_path: str | Path,
    delivery_root: str | Path = DEFAULT_PRODUCTION_DELIVERY_ROOT,
    now: datetime | None = None,
) -> Path:
    """Return the one user-facing delivery directory and persist it in pipeline."""
    path = Path(pipeline_path).expanduser().resolve()
    payload = _read_pipeline(path)
    configured = safe_text(payload.get("output_dir"))
    if configured:
        output_dir = Path(configured).expanduser().resolve()
    else:
        category = _short_category_name(
            safe_text(project.get("name")) or safe_text(project.get("category_name"))
        )
        account = _safe_path_component(safe_text(account_label))
        if not category or not account:
            raise ValueError("cannot create delivery directory without category and account")
        date_prefix = (now or datetime.now()).strftime("%m%d")
        output_dir = Path(delivery_root).expanduser().resolve() / _safe_path_component(
            f"{date_prefix}-{category}-{account}"
        )
        payload["output_dir"] = str(output_dir)
        _write_pipeline(path, payload)
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def record_pipeline_video_path(
    pipeline_path: str | Path,
    *,
    key: str,
    video_path: str | Path,
) -> None:
    path = Path(pipeline_path).expanduser().resolve()
    payload = _read_pipeline(path)
    target = Path(video_path).expanduser().resolve()
    output_dir = Path(safe_text(payload.get("output_dir"))).expanduser().resolve()
    try:
        relative = target.relative_to(output_dir)
    except ValueError as exc:
        raise ValueError(f"video path is outside pipeline output_dir: {target}") from exc
    paths = payload.get("paths") if isinstance(payload.get("paths"), dict) else {}
    paths[key] = str(target)
    paths[f"{key}_relative"] = str(relative)
    payload["paths"] = paths
    _write_pipeline(path, payload)


def resolve_pipeline_output_dir(pipeline_path: str | Path | None) -> Path | None:
    if not pipeline_path:
        return None
    payload = _read_pipeline(Path(pipeline_path).expanduser().resolve())
    value = safe_text(payload.get("output_dir"))
    return Path(value).expanduser().resolve() if value else None


def _short_category_name(value: str) -> str:
    return _safe_path_component(value.rsplit("-", 1)[-1].strip())


def _read_pipeline(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"pipeline does not exist: {path}")
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"pipeline root must be an object: {path}")
    return payload


def _write_pipeline(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
