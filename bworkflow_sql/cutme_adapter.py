from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .settings import resolve_cutme_root


VALIDATE_TIMEOUT_SECONDS = 60.0
RENDER_FINAL_TIMEOUT_SECONDS = 7200.0
RENDER_PRODUCT_CARD_TIMEOUT_SECONDS = 180.0
RENDER_PRODUCT_CARDS_TIMEOUT_SECONDS = 1200.0
MAX_DIAGNOSTIC_CHARS = 2048

_SECRET_ASSIGNMENT = re.compile(
    r"(?i)([A-Z0-9_]*(?:API[_-]?KEY|ACCESS[_-]?TOKEN|AUTHORIZATION|SECRET|TOKEN))"
    r"(\s*[:=]\s*)(?:Bearer\s+)?([^\s,;]+)"
)
_BEARER_TOKEN = re.compile(r"(?i)\bBearer\s+[^\s,;]+")
_QUOTED_SECRET = re.compile(
    r'''(["'][A-Z0-9_]*(?:API[_-]?KEY|ACCESS[_-]?TOKEN|AUTHORIZATION|SECRET|TOKEN)'''
    r'''["']\s*:\s*["'])[^"']*(["'])''',
    re.IGNORECASE,
)

ProcessFactory = Callable[..., Any]
_REQUIRED_RESULT_FIELDS = {
    "schema_version",
    "kind",
    "operation",
    "ok",
    "status",
    "artifacts",
    "cache",
    "timings",
    "error",
}


class CutMeAdapterError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        returncode: int | None = None,
        stderr: str = "",
        result: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message.strip() or code)
        self.code = code
        self.retryable = retryable
        self.returncode = returncode
        self.stderr = _bounded_diagnostic(stderr)
        self.result = result


class CutMeAdapter:
    def __init__(
        self,
        *,
        cutme_root: str | Path | None = None,
        process_factory: ProcessFactory = subprocess.Popen,
    ) -> None:
        root = Path(cutme_root) if cutme_root is not None else resolve_cutme_root()
        self.cutme_root = root.resolve()
        self._process_factory = process_factory

    def validate_package(
        self,
        package_path: str | Path,
        *,
        scope: str,
    ) -> dict[str, Any]:
        if scope not in {"source", "job"}:
            raise CutMeAdapterError(
                "cutme_contract_invalid",
                "scope must be source or job",
            )
        package = Path(package_path).resolve()
        artifact_key = "source_package_path" if scope == "source" else "job_package_path"
        return self._invoke(
            [
                "validate-package",
                "--package",
                str(package),
                "--scope",
                scope,
            ],
            operation="validate_package",
            timeout=VALIDATE_TIMEOUT_SECONDS,
            required_artifacts=(artifact_key,),
        )

    def render_final(
        self,
        package_path: str | Path,
        *,
        output_path: str | Path,
        cache_dir: str | Path | None = None,
    ) -> dict[str, Any]:
        package = Path(package_path).resolve()
        output = Path(output_path).resolve()
        command = [
            "render-final",
            "--package",
            str(package),
            "--output",
            str(output),
        ]
        if cache_dir is not None:
            command.extend(["--cache-dir", str(Path(cache_dir).resolve())])
        return self._invoke(
            command,
            operation="render_final",
            timeout=RENDER_FINAL_TIMEOUT_SECONDS,
            required_artifacts=(
                "source_package_path",
                "job_package_path",
                "output_path",
            ),
        )

    def render_product_card(
        self,
        package_path: str | Path,
        *,
        product_uid: str,
        output_path: str | Path,
    ) -> dict[str, Any]:
        uid = str(product_uid).strip()
        if not uid:
            raise CutMeAdapterError(
                "cutme_contract_invalid",
                "product_uid is required",
            )
        package = Path(package_path).resolve()
        output = Path(output_path).resolve()
        return self._invoke(
            [
                "render-product-card",
                "--package",
                str(package),
                "--product-uid",
                uid,
                "--output",
                str(output),
            ],
            operation="render_product_card",
            timeout=RENDER_PRODUCT_CARD_TIMEOUT_SECONDS,
            required_artifacts=("job_package_path", "output_path"),
        )

    def render_product_cards(
        self,
        package_path: str | Path,
        outputs: list[tuple[str, str | Path]],
        *,
        max_workers: int = 3,
    ) -> dict[str, Any]:
        package = Path(package_path).resolve()
        normalized = [(str(uid).strip(), Path(path).resolve()) for uid, path in outputs]
        if not normalized or any(not uid for uid, _path in normalized):
            raise CutMeAdapterError(
                "cutme_contract_invalid",
                "render_product_cards requires at least one output with a product uid",
            )
        manifest_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                suffix=".json",
                prefix="cutme-product-card-outputs-",
                dir=package.parent,
                delete=False,
            ) as handle:
                manifest_path = Path(handle.name)
                json.dump(
                    [
                        {"product_uid": uid, "output_path": str(output)}
                        for uid, output in normalized
                    ],
                    handle,
                    ensure_ascii=False,
                )
            result = self._invoke(
                [
                    "render-product-cards",
                    "--package",
                    str(package),
                    "--outputs-json",
                    str(manifest_path),
                    "--workers",
                    str(max(1, min(int(max_workers), 4))),
                ],
                operation="render_product_cards",
                timeout=RENDER_PRODUCT_CARDS_TIMEOUT_SECONDS,
                required_artifacts=("job_package_path",),
            )
        finally:
            if manifest_path is not None:
                manifest_path.unlink(missing_ok=True)
        missing = [str(path) for _uid, path in normalized if not path.is_file()]
        if missing:
            raise CutMeAdapterError(
                "cutme_artifact_missing",
                f"CutMe product-card batch is missing outputs: {missing}",
                result=result,
            )
        return result

    def _invoke(
        self,
        command: list[str],
        *,
        operation: str,
        timeout: float,
        required_artifacts: tuple[str, ...],
    ) -> dict[str, Any]:
        self._require_dependency()
        argv = [sys.executable, "-m", "cutme.render_cli", *command]
        try:
            process = self._process_factory(
                argv,
                cwd=str(self.cutme_root),
                env=self._process_env(),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                shell=False,
            )
        except OSError as exc:
            raise CutMeAdapterError(
                "cutme_dependency_missing",
                str(exc),
            ) from exc

        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            try:
                process.kill()
            finally:
                try:
                    stdout, stderr = process.communicate()
                except Exception:
                    stdout, stderr = _as_text(exc.output), _as_text(exc.stderr)
            raise CutMeAdapterError(
                "cutme_timeout",
                f"CutMe operation timed out after {timeout:g}s",
                retryable=True,
                returncode=_returncode(process),
                stderr=stderr,
            ) from exc

        returncode = _returncode(process)
        diagnostic = _bounded_diagnostic(stderr)
        try:
            result = _decode_result(stdout, expected_operation=operation)
        except ValueError as exc:
            code = "cutme_process_error" if returncode != 0 else "cutme_protocol_error"
            message = (
                f"CutMe process exited with code {returncode} without a valid envelope"
                if returncode != 0
                else str(exc)
            )
            raise CutMeAdapterError(
                code,
                message,
                returncode=returncode,
                stderr=diagnostic,
            ) from exc

        if result["ok"] is False:
            error = result["error"]
            raise CutMeAdapterError(
                str(error["code"]),
                _bounded_diagnostic(str(error["message"])),
                retryable=bool(error["retryable"]),
                returncode=returncode,
                stderr=diagnostic,
                result=result,
            )
        if returncode != 0:
            raise CutMeAdapterError(
                "cutme_process_error",
                f"CutMe process returned success envelope with exit code {returncode}",
                returncode=returncode,
                stderr=diagnostic,
                result=result,
            )

        normalized = _normalize_artifacts(result, root=self.cutme_root)
        _require_artifacts(
            normalized,
            required_artifacts=required_artifacts,
            returncode=returncode,
            stderr=diagnostic,
        )
        return normalized

    def _require_dependency(self) -> None:
        module_path = self.cutme_root / "cutme" / "render_cli.py"
        if not self.cutme_root.is_dir() or not module_path.is_file():
            raise CutMeAdapterError(
                "cutme_dependency_missing",
                f"CutMe machine module is missing: {module_path}",
            )

    def _process_env(self) -> dict[str, str]:
        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        existing = env.get("PYTHONPATH", "")
        root = str(self.cutme_root)
        env["PYTHONPATH"] = root if not existing else root + os.pathsep + existing
        return env


def _decode_result(stdout: object, *, expected_operation: str) -> dict[str, Any]:
    lines = [line.strip() for line in _as_text(stdout).splitlines() if line.strip()]
    if len(lines) != 1:
        raise ValueError("CutMe stdout must contain exactly one JSON object")
    try:
        payload = json.loads(lines[0])
    except json.JSONDecodeError as exc:
        raise ValueError("CutMe stdout is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("CutMe result must be an object")
    missing = sorted(_REQUIRED_RESULT_FIELDS - payload.keys())
    _require_protocol(not missing, f"missing required fields: {', '.join(missing)}")
    _require_protocol(payload.get("schema_version") == "1.0.0", "invalid schema_version")
    _require_protocol(payload.get("kind") == "cutme.render_result", "invalid kind")
    _require_protocol(payload.get("operation") == expected_operation, "operation mismatch")
    _require_protocol(isinstance(payload.get("ok"), bool), "ok must be boolean")
    _require_protocol(isinstance(payload.get("artifacts"), dict), "artifacts must be object")
    _require_protocol(
        payload.get("cache") is None or isinstance(payload.get("cache"), dict),
        "cache must be object or null",
    )
    timings = payload.get("timings")
    _require_protocol(
        isinstance(timings, dict) and isinstance(timings.get("total_ms"), (int, float)),
        "timings.total_ms is required",
    )
    if payload["ok"]:
        _require_protocol(payload.get("status") == "succeeded", "invalid success status")
        _require_protocol(payload.get("error") is None, "success error must be null")
    else:
        error = payload.get("error")
        _require_protocol(payload.get("status") == "failed", "invalid failure status")
        _require_protocol(isinstance(error, dict), "failure error must be object")
        _require_protocol(
            isinstance(error.get("code"), str) and bool(error.get("code").strip()),
            "error.code is required",
        )
        _require_protocol(
            isinstance(error.get("message"), str) and bool(error.get("message").strip()),
            "error.message is required",
        )
        _require_protocol(isinstance(error.get("retryable"), bool), "error.retryable is required")
    return payload


def _normalize_artifacts(
    result: dict[str, Any],
    *,
    root: Path,
) -> dict[str, Any]:
    normalized = dict(result)
    artifacts = dict(result["artifacts"])
    for key, value in artifacts.items():
        if not key.endswith("_path") or not isinstance(value, str) or not value.strip():
            continue
        path = Path(value)
        if not path.is_absolute():
            path = root / path
        artifacts[key] = str(path.resolve())
    normalized["artifacts"] = artifacts
    return normalized


def _require_artifacts(
    result: dict[str, Any],
    *,
    required_artifacts: tuple[str, ...],
    returncode: int,
    stderr: str,
) -> None:
    artifacts = result["artifacts"]
    for key in required_artifacts:
        value = artifacts.get(key)
        if not isinstance(value, str) or not value.strip() or not Path(value).is_file():
            raise CutMeAdapterError(
                "cutme_artifact_missing",
                f"CutMe success result is missing artifact {key}: {value or '<empty>'}",
                returncode=returncode,
                stderr=stderr,
                result=result,
            )


def _bounded_diagnostic(value: object) -> str:
    text = _as_text(value)
    text = _QUOTED_SECRET.sub(r"\1[REDACTED]\2", text)
    text = _BEARER_TOKEN.sub("Bearer [REDACTED]", text)
    text = _SECRET_ASSIGNMENT.sub(r"\1\2[REDACTED]", text)
    if len(text) <= MAX_DIAGNOSTIC_CHARS:
        return text
    marker = "[truncated]\n"
    return marker + text[-(MAX_DIAGNOSTIC_CHARS - len(marker)) :]


def _as_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _returncode(process: object) -> int:
    value = getattr(process, "returncode", None)
    return int(value) if isinstance(value, int) else -1


def _require_protocol(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(f"CutMe protocol error: {message}")
