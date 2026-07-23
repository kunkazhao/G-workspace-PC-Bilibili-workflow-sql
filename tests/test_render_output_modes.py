from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import bworkflow_sql.workflow_service as workflow_service
from bworkflow_sql.workflow_service import WorkflowService


@pytest.fixture(autouse=True)
def _certified_product_card_template(monkeypatch):
    monkeypatch.setattr(
        workflow_service,
        "_product_card_text_capacity_issues",
        lambda **_kwargs: [],
    )


def _service(db: object = "db") -> WorkflowService:
    service = WorkflowService.__new__(WorkflowService)
    service.db = db
    return service




def test_prepare_product_recommendation_output_returns_structured_final_mp4_readiness(
    tmp_path,
    monkeypatch,
):
    package = {"schemaVersion": "1.0.0", "segments": [{"type": "price_transition"}]}
    calls = []
    frozen_contexts = [{"product_uid": "P001"}]

    def fake_build(db, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(package=package, missing=[], stale_product_images=[])

    output = tmp_path / "render-package.json"
    monkeypatch.setattr(workflow_service, "build_product_recommendation_package", fake_build)

    result = _service().prepare_product_recommendation_output(
        7,
        account_label="xiaobo",
        output_mode="final_mp4",
        product_media_mode="video_preferred",
        package_output_path=output,
        dynamic_product_contexts=frozen_contexts,
        master_snapshot_id="snapshot-service-1",
    )

    assert result["ok"] is True
    assert result["next"] == {
        "mode": "final_mp4",
        "status": "ready",
        "action": "render_final_video",
        "target_mp4": str(output.with_suffix(".mp4")),
    }
    assert "cutme" not in json.dumps(result["next"], ensure_ascii=False).lower()
    assert result["product_media_mode"] == "video_preferred"
    assert calls[0]["product_media_mode"] == "video_preferred"
    assert calls[0]["dynamic_product_contexts"] is frozen_contexts
    assert calls[0]["master_snapshot_id"] == "snapshot-service-1"


def test_prepare_product_recommendation_output_can_pass_stable_product_order(
    tmp_path,
    monkeypatch,
):
    calls: list[dict[str, object]] = []

    def fake_build(db, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(package={"schemaVersion": "1.0.0", "segments": []}, missing=[], stale_product_images=[])

    monkeypatch.setattr(workflow_service, "build_product_recommendation_package", fake_build)

    result = _service().prepare_product_recommendation_output(
        7,
        account_label="xiaobo",
        output_mode="final_mp4",
        product_order_strategy="stable",
        package_output_path=tmp_path / "render-package.json",
    )

    assert result["ok"] is True
    assert result["product_order_strategy"] == "stable"
    assert calls[0]["product_order_strategy"] == "stable"


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

    def fake_build(db, **kwargs):
        return SimpleNamespace(package={"segments": []}, missing=missing, stale_product_images=[])

    output = tmp_path / "render-package.json"
    monkeypatch.setattr(workflow_service, "build_product_recommendation_package", fake_build)

    result = _service().prepare_product_recommendation_output(
        3,
        account_label="xiaobo",
        output_mode="final_mp4",
        package_output_path=output,
    )

    assert result["ok"] is False
    assert result["missing"] == missing
    assert result["next"] is None
    assert not output.exists()
