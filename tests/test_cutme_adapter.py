from __future__ import annotations

import importlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest


def _adapter_module() -> ModuleType:
    spec = importlib.util.find_spec("bworkflow_sql.cutme_adapter")
    assert spec is not None, "B-Workflow must publish bworkflow_sql.cutme_adapter"
    module = importlib.import_module("bworkflow_sql.cutme_adapter")
    assert hasattr(module, "CutMeAdapter")
    assert hasattr(module, "CutMeAdapterError")
    return module


def _cutme_root(root: Path) -> Path:
    module_path = root / "cutme" / "render_cli.py"
    module_path.parent.mkdir(parents=True, exist_ok=True)
    module_path.write_text("# test module\n", encoding="utf-8")
    return root


def _touch(path: Path, content: bytes = b"artifact") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def _success(
    operation: str,
    artifacts: dict[str, Any],
    *,
    cache: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "kind": "cutme.render_result",
        "operation": operation,
        "ok": True,
        "status": "succeeded",
        "artifacts": artifacts,
        "cache": cache,
        "timings": {"total_ms": 12},
        "error": None,
    }


def _failure(
    operation: str,
    *,
    code: str = "cutme_render_failed",
    message: str = "renderer failed",
    retryable: bool = False,
) -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "kind": "cutme.render_result",
        "operation": operation,
        "ok": False,
        "status": "failed",
        "artifacts": {},
        "cache": None,
        "timings": {"total_ms": 8},
        "error": {
            "code": code,
            "message": message,
            "retryable": retryable,
        },
    }


class FakeProcess:
    def __init__(
        self,
        *,
        stdout: str = "",
        stderr: str = "",
        returncode: int = 0,
        timeout_once: bool = False,
    ) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode
        self.timeout_once = timeout_once
        self.killed = False
        self.communicate_timeouts: list[float | None] = []

    def communicate(self, timeout: float | None = None) -> tuple[str, str]:
        self.communicate_timeouts.append(timeout)
        if self.timeout_once and not self.killed:
            raise subprocess.TimeoutExpired(
                cmd="cutme",
                timeout=timeout or 0,
                output=self.stdout,
                stderr=self.stderr,
            )
        return self.stdout, self.stderr

    def kill(self) -> None:
        self.killed = True


class FakeProcessFactory:
    def __init__(
        self,
        process: FakeProcess | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self.process = process or FakeProcess()
        self.error = error
        self.calls: list[tuple[list[str], dict[str, Any]]] = []

    def __call__(self, argv: list[str], **kwargs: Any) -> FakeProcess:
        self.calls.append((argv, kwargs))
        if self.error is not None:
            raise self.error
        return self.process


def _json_stdout(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False) + "\n"


def test_validate_package_uses_python_list_cutme_cwd_utf8_env_and_no_shell(
    tmp_path: Path,
):
    module = _adapter_module()
    cutme_root = _cutme_root(tmp_path / "CutMe")
    package_path = _touch(tmp_path / "source" / "render-package.json")
    process = FakeProcess(
        stdout=_json_stdout(
            _success(
                "validate_package",
                {"source_package_path": str(package_path)},
            )
        )
    )
    factory = FakeProcessFactory(process)
    adapter = module.CutMeAdapter(cutme_root=cutme_root, process_factory=factory)

    result = adapter.validate_package(package_path, scope="source")

    argv, kwargs = factory.calls[0]
    assert argv == [
        sys.executable,
        "-m",
        "cutme.render_cli",
        "validate-package",
        "--package",
        str(package_path.resolve()),
        "--scope",
        "source",
    ]
    assert kwargs["cwd"] == str(cutme_root.resolve())
    assert kwargs["shell"] is False
    assert kwargs["stdout"] is subprocess.PIPE
    assert kwargs["stderr"] is subprocess.PIPE
    assert kwargs["text"] is True
    assert kwargs["encoding"] == "utf-8"
    assert kwargs["env"]["PYTHONUTF8"] == "1"
    assert kwargs["env"]["PYTHONIOENCODING"] == "utf-8"
    assert str(cutme_root.resolve()) in kwargs["env"]["PYTHONPATH"]
    assert process.communicate_timeouts == [module.VALIDATE_TIMEOUT_SECONDS]
    assert result["artifacts"]["source_package_path"] == str(package_path.resolve())


def test_environment_override_selects_cutme_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    module = _adapter_module()
    override = _cutme_root(tmp_path / "override-cutme")
    monkeypatch.setenv("BWORKFLOW_CUTME_ROOT", str(override))

    adapter = module.CutMeAdapter(process_factory=FakeProcessFactory())

    assert adapter.cutme_root == override.resolve()


def test_render_final_builds_explicit_argv_and_normalizes_artifact_paths(
    tmp_path: Path,
):
    module = _adapter_module()
    cutme_root = _cutme_root(tmp_path / "CutMe")
    source = _touch(cutme_root / "source.json")
    job_package = _touch(cutme_root / "jobs" / "job-1" / "render-package.json")
    output = _touch(cutme_root / "output" / "final.mp4")
    cache_manifest = _touch(cutme_root / "cache" / "clip-cache-manifest.json")
    process = FakeProcess(
        stdout=_json_stdout(
            _success(
                "render_final",
                {
                    "source_package_path": "source.json",
                    "job_package_path": "jobs/job-1/render-package.json",
                    "output_path": "output/final.mp4",
                    "cache_manifest_path": "cache/clip-cache-manifest.json",
                },
                cache={"segments_total": 3, "cache_hits": 2, "rendered": 1},
            )
        )
    )
    factory = FakeProcessFactory(process)
    adapter = module.CutMeAdapter(cutme_root=cutme_root, process_factory=factory)

    result = adapter.render_final(
        source,
        output_path=output,
        cache_dir=cache_manifest.parent,
    )

    argv, _ = factory.calls[0]
    assert argv == [
        sys.executable,
        "-m",
        "cutme.render_cli",
        "render-final",
        "--package",
        str(source.resolve()),
        "--output",
        str(output.resolve()),
        "--cache-dir",
        str(cache_manifest.parent.resolve()),
    ]
    assert process.communicate_timeouts == [module.RENDER_FINAL_TIMEOUT_SECONDS]
    assert result["artifacts"] == {
        "source_package_path": str(source.resolve()),
        "job_package_path": str(job_package.resolve()),
        "output_path": str(output.resolve()),
        "cache_manifest_path": str(cache_manifest.resolve()),
    }


def test_render_product_card_builds_explicit_argv(
    tmp_path: Path,
):
    module = _adapter_module()
    cutme_root = _cutme_root(tmp_path / "CutMe")
    package_path = _touch(cutme_root / "job" / "render-package.json")
    output = _touch(cutme_root / "output" / "product.png")
    process = FakeProcess(
        stdout=_json_stdout(
            _success(
                "render_product_card",
                {
                    "job_package_path": str(package_path),
                    "output_path": str(output),
                },
            )
        )
    )
    factory = FakeProcessFactory(process)
    adapter = module.CutMeAdapter(cutme_root=cutme_root, process_factory=factory)

    adapter.render_product_card(package_path, product_uid="SKU-001", output_path=output)

    argv, _ = factory.calls[0]
    assert argv == [
        sys.executable,
        "-m",
        "cutme.render_cli",
        "render-product-card",
        "--package",
        str(package_path.resolve()),
        "--product-uid",
        "SKU-001",
        "--output",
        str(output.resolve()),
    ]
    assert process.communicate_timeouts == [module.RENDER_PRODUCT_CARD_TIMEOUT_SECONDS]


@pytest.mark.parametrize("returncode", (0, 5))
def test_failed_envelope_raises_its_stable_error_for_any_process_exit(
    tmp_path: Path,
    returncode: int,
):
    module = _adapter_module()
    cutme_root = _cutme_root(tmp_path / "CutMe")
    package_path = _touch(tmp_path / "source.json")
    process = FakeProcess(
        stdout=_json_stdout(
            _failure(
                "validate_package",
                code="cutme_contract_invalid",
                message="voiceAsset missing",
            )
        ),
        returncode=returncode,
    )
    adapter = module.CutMeAdapter(
        cutme_root=cutme_root,
        process_factory=FakeProcessFactory(process),
    )

    with pytest.raises(module.CutMeAdapterError) as caught:
        adapter.validate_package(package_path, scope="source")

    assert caught.value.code == "cutme_contract_invalid"
    assert caught.value.returncode == returncode
    assert caught.value.result["error"]["message"] == "voiceAsset missing"


@pytest.mark.parametrize("stdout", ("", "{", "{}\n{}\n"))
def test_zero_exit_with_empty_malformed_or_multiple_stdout_is_protocol_error(
    tmp_path: Path,
    stdout: str,
):
    module = _adapter_module()
    cutme_root = _cutme_root(tmp_path / "CutMe")
    package_path = _touch(tmp_path / "source.json")
    adapter = module.CutMeAdapter(
        cutme_root=cutme_root,
        process_factory=FakeProcessFactory(FakeProcess(stdout=stdout)),
    )

    with pytest.raises(module.CutMeAdapterError) as caught:
        adapter.validate_package(package_path, scope="source")

    assert caught.value.code == "cutme_protocol_error"


def test_nonzero_exit_without_envelope_is_process_error(tmp_path: Path):
    module = _adapter_module()
    cutme_root = _cutme_root(tmp_path / "CutMe")
    package_path = _touch(tmp_path / "source.json")
    adapter = module.CutMeAdapter(
        cutme_root=cutme_root,
        process_factory=FakeProcessFactory(
            FakeProcess(returncode=9, stderr="renderer crashed")
        ),
    )

    with pytest.raises(module.CutMeAdapterError) as caught:
        adapter.validate_package(package_path, scope="source")

    assert caught.value.code == "cutme_process_error"
    assert caught.value.returncode == 9
    assert "renderer crashed" in caught.value.stderr


def test_missing_cutme_module_is_dependency_error_without_process_start(tmp_path: Path):
    module = _adapter_module()
    factory = FakeProcessFactory()
    adapter = module.CutMeAdapter(
        cutme_root=tmp_path / "missing-cutme",
        process_factory=factory,
    )

    with pytest.raises(module.CutMeAdapterError) as caught:
        adapter.validate_package(tmp_path / "source.json", scope="source")

    assert caught.value.code == "cutme_dependency_missing"
    assert factory.calls == []


def test_process_start_failure_is_dependency_error(tmp_path: Path):
    module = _adapter_module()
    cutme_root = _cutme_root(tmp_path / "CutMe")
    package_path = _touch(tmp_path / "source.json")
    adapter = module.CutMeAdapter(
        cutme_root=cutme_root,
        process_factory=FakeProcessFactory(error=FileNotFoundError("python missing")),
    )

    with pytest.raises(module.CutMeAdapterError) as caught:
        adapter.validate_package(package_path, scope="source")

    assert caught.value.code == "cutme_dependency_missing"


def test_timeout_kills_process_and_returns_retryable_timeout_error(tmp_path: Path):
    module = _adapter_module()
    cutme_root = _cutme_root(tmp_path / "CutMe")
    package_path = _touch(tmp_path / "source.json")
    process = FakeProcess(
        stderr="API_KEY=super-secret\n" + ("x" * 5000),
        timeout_once=True,
    )
    adapter = module.CutMeAdapter(
        cutme_root=cutme_root,
        process_factory=FakeProcessFactory(process),
    )

    with pytest.raises(module.CutMeAdapterError) as caught:
        adapter.validate_package(package_path, scope="source")

    assert process.killed is True
    assert process.communicate_timeouts == [module.VALIDATE_TIMEOUT_SECONDS, None]
    assert caught.value.code == "cutme_timeout"
    assert caught.value.retryable is True
    assert "super-secret" not in caught.value.stderr
    assert len(caught.value.stderr) <= module.MAX_DIAGNOSTIC_CHARS


def test_success_envelope_without_expected_output_is_artifact_error(tmp_path: Path):
    module = _adapter_module()
    cutme_root = _cutme_root(tmp_path / "CutMe")
    source = _touch(cutme_root / "source.json")
    job_package = _touch(cutme_root / "job" / "render-package.json")
    missing_output = cutme_root / "output" / "missing.mp4"
    process = FakeProcess(
        stdout=_json_stdout(
            _success(
                "render_final",
                {
                    "source_package_path": str(source),
                    "job_package_path": str(job_package),
                    "output_path": str(missing_output),
                },
            )
        )
    )
    adapter = module.CutMeAdapter(
        cutme_root=cutme_root,
        process_factory=FakeProcessFactory(process),
    )

    with pytest.raises(module.CutMeAdapterError) as caught:
        adapter.render_final(source, output_path=missing_output)

    assert caught.value.code == "cutme_artifact_missing"
    assert "output_path" in str(caught.value)


def test_stderr_diagnostics_are_bounded_and_secrets_are_redacted(tmp_path: Path):
    module = _adapter_module()
    cutme_root = _cutme_root(tmp_path / "CutMe")
    package_path = _touch(tmp_path / "source.json")
    stderr = (
        ("x" * 5000)
        + '\n{"api_key":"json-secret"}'
        + "\nAuthorization: Bearer abc123\nTOKEN=private-token"
    )
    adapter = module.CutMeAdapter(
        cutme_root=cutme_root,
        process_factory=FakeProcessFactory(
            FakeProcess(returncode=1, stderr=stderr)
        ),
    )

    with pytest.raises(module.CutMeAdapterError) as caught:
        adapter.validate_package(package_path, scope="source")

    diagnostic = caught.value.stderr
    assert len(diagnostic) <= module.MAX_DIAGNOSTIC_CHARS
    assert "abc123" not in diagnostic
    assert "private-token" not in diagnostic
    assert "json-secret" not in diagnostic
    assert "[REDACTED]" in diagnostic


def test_success_envelope_with_wrong_operation_is_protocol_error(tmp_path: Path):
    module = _adapter_module()
    cutme_root = _cutme_root(tmp_path / "CutMe")
    package_path = _touch(tmp_path / "source.json")
    process = FakeProcess(
        stdout=_json_stdout(
            _success(
                "render_final",
                {"source_package_path": str(package_path)},
            )
        )
    )
    adapter = module.CutMeAdapter(
        cutme_root=cutme_root,
        process_factory=FakeProcessFactory(process),
    )

    with pytest.raises(module.CutMeAdapterError) as caught:
        adapter.validate_package(package_path, scope="source")

    assert caught.value.code == "cutme_protocol_error"


@pytest.mark.parametrize("missing_field", ("cache", "error"))
def test_success_envelope_missing_required_field_is_protocol_error(
    tmp_path: Path,
    missing_field: str,
):
    module = _adapter_module()
    cutme_root = _cutme_root(tmp_path / "CutMe")
    package_path = _touch(tmp_path / "source.json")
    payload = _success(
        "validate_package",
        {"source_package_path": str(package_path)},
    )
    payload.pop(missing_field)
    adapter = module.CutMeAdapter(
        cutme_root=cutme_root,
        process_factory=FakeProcessFactory(
            FakeProcess(stdout=_json_stdout(payload))
        ),
    )

    with pytest.raises(module.CutMeAdapterError) as caught:
        adapter.validate_package(package_path, scope="source")

    assert caught.value.code == "cutme_protocol_error"


def test_success_envelope_with_invalid_cache_type_is_protocol_error(tmp_path: Path):
    module = _adapter_module()
    cutme_root = _cutme_root(tmp_path / "CutMe")
    package_path = _touch(tmp_path / "source.json")
    payload = _success(
        "validate_package",
        {"source_package_path": str(package_path)},
    )
    payload["cache"] = "not-an-object"
    adapter = module.CutMeAdapter(
        cutme_root=cutme_root,
        process_factory=FakeProcessFactory(
            FakeProcess(stdout=_json_stdout(payload))
        ),
    )

    with pytest.raises(module.CutMeAdapterError) as caught:
        adapter.validate_package(package_path, scope="source")

    assert caught.value.code == "cutme_protocol_error"
