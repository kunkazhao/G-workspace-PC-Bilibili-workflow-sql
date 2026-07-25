from __future__ import annotations

import json
import os
import secrets
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from .settings import INTERNAL_WORKSPACE_ROOT
from .utils import safe_text


class RenderBusyError(RuntimeError):
    code = "render_busy"
    retryable = True

    def __init__(self, owner: dict[str, Any] | None = None) -> None:
        self.owner = owner or {}
        category = safe_text(self.owner.get("category")) or "未知品类"
        account = safe_text(self.owner.get("account")) or "未知账号"
        episode_id = safe_text(self.owner.get("episode_id")) or "未知任务"
        phase = safe_text(self.owner.get("phase")) or "正式渲染"
        super().__init__(
            f"当前有渲染任务正在占用：{category} / {account} / {episode_id}（{phase}）。"
            "请等待它渲染完成后，在当前任务中再次发送“继续”。"
        )


def build_render_owner(
    *,
    phase: str,
    pipeline_path: str | Path | None = None,
    project_id: int | None = None,
    category: str = "",
    account: str = "",
) -> dict[str, Any]:
    pipeline: dict[str, Any] = {}
    if pipeline_path:
        try:
            value = json.loads(Path(pipeline_path).expanduser().resolve().read_text(encoding="utf-8-sig"))
            if isinstance(value, dict):
                pipeline = value
        except (OSError, UnicodeError, json.JSONDecodeError):
            pipeline = {}
    return {
        "episode_id": safe_text(pipeline.get("episode_id")),
        "category": safe_text(pipeline.get("category") or pipeline.get("project_name") or category),
        "account": safe_text(pipeline.get("account") or account),
        "project_id": int(pipeline.get("bworkflow_project_id") or project_id or 0),
        "phase": safe_text(phase) or "formal_render",
    }


def _try_lock(handle: Any) -> bool:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False

    import fcntl

    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _unlock(handle: Any) -> None:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return

    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _read_owner(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_owner(path: Path, owner: dict[str, Any]) -> None:
    temp = path.with_name(f".{path.name}.{owner['lock_token']}.tmp")
    temp.write_text(json.dumps(owner, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)


@contextmanager
def acquire_production_render_slot(
    owner: dict[str, Any],
    *,
    lock_root: str | Path = INTERNAL_WORKSPACE_ROOT,
) -> Iterator[dict[str, Any]]:
    lock_dir = Path(lock_root).expanduser().resolve() / ".locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / "production-render.lock"
    owner_path = lock_dir / "production-render-owner.json"
    handle = lock_path.open("a+b")
    if lock_path.stat().st_size == 0:
        handle.write(b"\0")
        handle.flush()
    if not _try_lock(handle):
        handle.close()
        raise RenderBusyError(_read_owner(owner_path))

    active_owner = {
        **owner,
        "pid": os.getpid(),
        "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "lock_token": secrets.token_hex(8),
    }
    try:
        _write_owner(owner_path, active_owner)
        yield active_owner
    finally:
        try:
            current = _read_owner(owner_path)
            if current.get("lock_token") == active_owner["lock_token"]:
                owner_path.unlink(missing_ok=True)
        finally:
            _unlock(handle)
            handle.close()
