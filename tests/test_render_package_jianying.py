from __future__ import annotations

import json
from types import SimpleNamespace

import bworkflow_sql.workflow_service as workflow_service
from bworkflow_sql.workflow_service import WorkflowService, render_package_to_jianying_manifest


def _package() -> dict:
    return {
        "schemaVersion": "1.0.0",
        "project": {"bworkflowProjectId": 3, "account": "xiaobo"},
        "segments": [
            {
                "type": "price_transition",
                "id": "price-001",
                "priceRangeLabel": "200-300",
                "transitionText": "Focus on brand maturity.",
                "voiceAsset": "audio/price.wav",
                "duration": 5.0,
                "sourceScriptBlockId": 101,
            },
            {
                "type": "product_recommendation",
                "id": "product-P001",
                "productUid": "P001",
                "productTitle": "Alpha Earbuds",
                "priceRangeLabel": "200-300",
                "spokenText": "Alpha is stable for long-term use.",
                "voiceAsset": "audio/P001.wav",
                "imageCardAsset": "assets/P001.png",
                "videoAsset": "assets/P001.mp4",
                "duration": 12.0,
                "sourceScriptBlockId": 201,
                "subtitles": [{"text": "Alpha is stable"}],
            },
            {
                "type": "product_recommendation",
                "id": "product-P002",
                "productUid": "P002",
                "productTitle": "Beta Earbuds",
                "priceRangeLabel": "300-400",
                "spokenText": "Beta keeps the basics reliable.",
                "voiceAsset": "audio/P002.wav",
                "imageCardAsset": "assets/P002.png",
                "videoAsset": None,
                "duration": 9.0,
                "sourceScriptBlockId": 202,
            },
        ],
    }


def _service(db: object = "db") -> WorkflowService:
    service = WorkflowService.__new__(WorkflowService)
    service.db = db
    return service


def test_render_package_to_jianying_manifest_maps_separate_assets(tmp_path):
    output = tmp_path / "package.manifest.json"

    manifest_path = render_package_to_jianying_manifest(
        _package(),
        output,
        project_id=3,
        account_label="xiaobo",
    )

    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert payload["source"] == "render_package"
    assert [entry["type"] for entry in payload["entries"]] == ["transition", "product", "product"]

    price = payload["entries"][0]
    assert price["section"] == "price_transition"
    assert price["product_uid"] == "PRICE_TRANSITION"
    assert price["text"] == "Focus on brand maturity."
    assert price["audio_path"] == "audio/price.wav"
    assert price["image_path"] == ""
    assert price["display_video_path"] == ""

    product_with_video = payload["entries"][1]
    assert product_with_video["product_uid"] == "P001"
    assert product_with_video["image_path"] == "assets/P001.png"
    assert product_with_video["audio_path"] == "audio/P001.wav"
    assert product_with_video["display_video_path"] == "assets/P001.mp4"
    assert product_with_video["video_path"] == "assets/P001.mp4"
    assert isinstance(product_with_video["display_video_slot"], dict)
    assert product_with_video["text"] == "Alpha is stable for long-term use."

    product_without_video = payload["entries"][2]
    assert product_without_video["product_uid"] == "P002"
    assert product_without_video["display_video_path"] == ""
    assert product_without_video["display_video_slot"] is None


def test_render_package_to_jianying_manifest_respects_cover_only_media_mode(tmp_path):
    package = _package()
    package["output"] = {"productMediaMode": "cover_only"}
    output = tmp_path / "package.manifest.json"

    manifest_path = render_package_to_jianying_manifest(
        package,
        output,
        project_id=3,
        account_label="xiaobo",
    )

    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    product_with_video = payload["entries"][1]
    assert product_with_video["product_uid"] == "P001"
    assert product_with_video["video_path"] == ""
    assert product_with_video["display_video_path"] == ""
    assert product_with_video["display_video_slot"] is None


def test_prepare_product_recommendation_output_writes_jianying_manifest(
    tmp_path,
    monkeypatch,
):
    def fake_build(db, *, project_id, account_label, output_mode, product_media_mode, mode, top_uids):
        return SimpleNamespace(package=_package(), missing=[])

    output = tmp_path / "render-package.json"
    monkeypatch.setattr(workflow_service, "build_product_recommendation_package", fake_build)

    result = _service().prepare_product_recommendation_output(
        3,
        account_label="xiaobo",
        output_mode="jianying_draft",
        package_output_path=output,
    )

    manifest_path = output.with_suffix(".jianying.manifest.json")
    assert result["ok"] is True
    assert result["next"]["mode"] == "jianying_draft"
    assert result["next"]["status"] == "ready"
    assert result["next"]["manifest_path"] == str(manifest_path)
    assert "python -m bworkflow_sql jianying" in result["next"]["command"]
    assert str(manifest_path) in result["next"]["command"]
    assert manifest_path.exists()
