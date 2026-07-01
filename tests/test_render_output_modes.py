from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import bworkflow_sql.workflow_service as workflow_service
from bworkflow_sql.workflow_service import WorkflowService


def _service(db: object = "db") -> WorkflowService:
    service = WorkflowService.__new__(WorkflowService)
    service.db = db
    return service


def test_prepare_product_recommendation_output_writes_draft_package(
    tmp_path,
    monkeypatch,
):
    calls: list[dict[str, object]] = []
    package = {"schemaVersion": "1.0.0", "segments": [{"type": "product_recommendation"}]}

    def fake_build(db, *, project_id, account_label, output_mode):
        calls.append(
            {
                "db": db,
                "project_id": project_id,
                "account_label": account_label,
                "output_mode": output_mode,
            }
        )
        return SimpleNamespace(package=package, missing=[])

    output = tmp_path / "render-package.json"
    monkeypatch.setattr(workflow_service, "build_product_recommendation_package", fake_build)

    result = _service().prepare_product_recommendation_output(
        3,
        account_label="灏忓崥",
        output_mode="jianying_draft",
        package_output_path=output,
    )

    assert result["ok"] is True
    assert result["package_path"] == str(output)
    assert result["output_mode"] == "jianying_draft"
    assert result["next"]["mode"] == "jianying_draft"
    assert result["next"]["status"] == "ready"
    assert result["next"]["manifest_path"] == str(output.with_suffix(".jianying.manifest.json"))
    assert "final_mp4" not in result["next"]
    assert json.loads(output.read_text(encoding="utf-8")) == package
    assert calls == [
        {
            "db": "db",
            "project_id": 3,
            "account_label": "灏忓崥",
            "output_mode": "jianying_draft",
        }
    ]


def test_prepare_product_recommendation_output_returns_final_mp4_next_command(
    tmp_path,
    monkeypatch,
):
    package = {"schemaVersion": "1.0.0", "segments": [{"type": "price_transition"}]}

    def fake_build(db, *, project_id, account_label, output_mode):
        return SimpleNamespace(package=package, missing=[])

    output = tmp_path / "render-package.json"
    monkeypatch.setattr(workflow_service, "build_product_recommendation_package", fake_build)

    result = _service().prepare_product_recommendation_output(
        7,
        account_label="xiaobo",
        output_mode="final_mp4",
        package_output_path=output,
    )

    assert result["ok"] is True
    assert result["next"]["mode"] == "final_mp4"
    assert result["next"]["target_mp4"].endswith(".mp4")
    assert "python -m cutme --package" in result["next"]["command"]
    assert "BilibiliFullVideo" in result["next"]["render_command_after_job"]


def test_prepare_product_recommendation_output_rejects_invalid_mode_before_build(
    tmp_path,
    monkeypatch,
):
    calls: list[object] = []

    def fake_build(*args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(package={}, missing=[])

    monkeypatch.setattr(workflow_service, "build_product_recommendation_package", fake_build)

    with pytest.raises(ValueError, match="output_mode"):
        _service().prepare_product_recommendation_output(
            3,
            account_label="xiaobo",
            output_mode="preview_only",
            package_output_path=tmp_path / "render-package.json",
        )

    assert calls == []


def test_prepare_product_recommendation_output_reports_missing_without_package(
    tmp_path,
    monkeypatch,
):
    missing = [{"kind": "product_voice", "uid": "P001"}]

    def fake_build(db, *, project_id, account_label, output_mode):
        return SimpleNamespace(package={"segments": []}, missing=missing)

    output = tmp_path / "render-package.json"
    monkeypatch.setattr(workflow_service, "build_product_recommendation_package", fake_build)

    result = _service().prepare_product_recommendation_output(
        3,
        account_label="xiaobo",
        output_mode="jianying_draft",
        package_output_path=output,
    )

    assert result["ok"] is False
    assert result["missing"] == missing
    assert result["next"] is None
    assert not output.exists()
