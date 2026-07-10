from __future__ import annotations

from collections.abc import Mapping
from functools import lru_cache
import json
from pathlib import Path
import re
from typing import Any

from jsonschema import Draft202012Validator


KIND = "BWorkflowObservation"
SCHEMA_VERSION = 1
CAPABILITY = "workflow_doctor"
OWNER = "bworkflow"

_PRIVATE_KEYS = {
    "next",
    "command",
    "follow_up_command",
    "argv",
    "cwd",
    "traceback",
    "stdout",
    "stderr",
    "environment",
    "env",
}
_SECRET_MARKERS = {
    "api_key",
    "access_token",
    "refresh_token",
    "auth_token",
    "password",
    "passwd",
    "client_secret",
    "secret_key",
    "authorization",
    "cookie",
}
_PRIVATE_KEY_COMPACT = {key.replace("_", "") for key in _PRIVATE_KEYS}
_SECRET_MARKER_COMPACT = {marker.replace("_", "") for marker in _SECRET_MARKERS}
_SCHEMA_PATH = (
    Path(__file__).resolve().parents[1]
    / "contracts"
    / "schemas"
    / "bworkflow-observation.v1.schema.json"
)


@lru_cache(maxsize=1)
def _contract_validator() -> Draft202012Validator:
    schema = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def validate_workflow_observation(payload: Mapping[str, Any]) -> None:
    _contract_validator().validate(dict(payload))


def _normalized_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value).casefold()).strip("_")


def _is_private_or_secret_key(value: Any) -> bool:
    normalized = _normalized_key(value)
    compact = normalized.replace("_", "")
    if normalized in _PRIVATE_KEYS or compact in _PRIVATE_KEY_COMPACT:
        return True
    if compact in {"token", "secret"} or compact.endswith(("token", "secret")):
        return True
    return any(marker in compact for marker in _SECRET_MARKER_COMPACT)


def _sanitize_public_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _sanitize_public_value(item)
            for key, item in value.items()
            if not _is_private_or_secret_key(key)
        }
    if isinstance(value, (list, tuple)):
        return [_sanitize_public_value(item) for item in value]
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"unsupported public diagnostic value: {type(value).__name__}")


def _normalize_text(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"expected text or null, got {type(value).__name__}")
    normalized = value.strip()
    return normalized or None


def _normalize_project_id(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def _normalize_subject(source: Mapping[str, Any] | None) -> dict[str, Any]:
    subject = source if isinstance(source, Mapping) else {}
    project_id = subject.get("project_id")
    if project_id is None:
        project_id = subject.get("id")
    project_name = subject.get("project_name")
    if project_name is None:
        project_name = subject.get("name")
    return {
        "project_id": _normalize_project_id(project_id),
        "project_name": _normalize_text(project_name),
        "account": _normalize_text(subject.get("account")),
    }


def _normalize_blockers(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw_items = [value]
    elif isinstance(value, (list, tuple)):
        raw_items = value
    else:
        raise TypeError("blocked_by must be null, a string, or a list of strings")
    blockers: list[str] = []
    for item in raw_items:
        if not isinstance(item, str):
            raise TypeError("blocked_by items must be strings")
        blocker = _normalize_text(item)
        if blocker and blocker not in blockers:
            blockers.append(blocker)
    return blockers


def _base_observation() -> dict[str, Any]:
    return {
        "kind": KIND,
        "schema_version": SCHEMA_VERSION,
        "capability": CAPABILITY,
        "authoritative": False,
    }


def build_workflow_observation(internal_result: Mapping[str, Any]) -> dict[str, Any]:
    status = _normalize_text(internal_result.get("status"))
    if status not in {"ready", "blocked"}:
        raise ValueError(f"unsupported workflow observation status: {status or '<empty>'}")

    project = internal_result.get("project")
    subject_source = dict(project) if isinstance(project, Mapping) else {}
    subject_source["account"] = internal_result.get("account")

    checks = internal_result.get("checks")
    safe_checks = _sanitize_public_value(checks) if isinstance(checks, Mapping) else {}
    raw_issues = internal_result.get("issues")
    safe_issues = (
        [
            _sanitize_public_value(issue)
            for issue in raw_issues
            if isinstance(issue, Mapping)
        ]
        if isinstance(raw_issues, (list, tuple))
        else []
    )

    next_hint = internal_result.get("next")
    next_mapping = next_hint if isinstance(next_hint, Mapping) else {}
    action = _normalize_text(next_mapping.get("action"))
    suggestion = (
        {"action": action, "task": _normalize_text(next_mapping.get("task"))}
        if action
        else None
    )

    payload = {
        **_base_observation(),
        "ok": True,
        "status": status,
        "subject": _normalize_subject(subject_source),
        "blocked_by": _normalize_blockers(internal_result.get("blocked_by")),
        "checks": safe_checks,
        "issues": safe_issues,
        "suggestion": suggestion,
        "error": None,
    }
    validate_workflow_observation(payload)
    return payload


def build_workflow_observation_error(
    code: str,
    message: str,
    retryable: bool = False,
    subject: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(retryable, bool):
        raise TypeError("retryable must be a boolean")
    payload = {
        **_base_observation(),
        "ok": False,
        "status": "failed",
        "subject": _normalize_subject(subject),
        "blocked_by": [],
        "checks": {},
        "issues": [],
        "suggestion": None,
        "error": {
            "code": _normalize_text(code) or "workflow_doctor_internal_error",
            "message": _normalize_text(message) or "Workflow diagnosis failed.",
            "retryable": retryable,
            "owner": OWNER,
        },
    }
    validate_workflow_observation(payload)
    return payload
