from __future__ import annotations

import json
from types import SimpleNamespace

import bworkflow_sql.workflow_service as workflow_service
from bworkflow_sql.workflow_service import (
    WorkflowService,
    build_template_calibration_probe_manifest,
    render_package_to_jianying_manifest,
)


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


def test_render_package_to_jianying_manifest_infers_template_from_product_card(tmp_path):
    package = _package()
    package["segments"][1]["productCard"] = {"templateId": "xiaoran1"}
    output = tmp_path / "package.manifest.json"

    manifest_path = render_package_to_jianying_manifest(
        package,
        output,
        project_id=3,
        account_label="小燃",
    )

    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    product_with_video = payload["entries"][1]
    assert payload["display_template"] == "小燃-模板1"
    assert product_with_video["display_video_slot"] == {
        "x": -830,
        "y": -77,
        "width": 970,
        "height": 590,
        "coordinate_mode": "clip_transform_pixels",
        "scale_x": 970 / 1936,
        "scale_y": 590 / 1080,
    }


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


def test_template_calibration_probe_manifest_keeps_one_product_and_slot():
    manifest = {
        "source": "render_package",
        "display_template": "小燃-模板1",
        "entries": [
            {
                "type": "transition",
                "product_uid": "PRICE_TRANSITION",
                "display_video_path": "",
                "display_video_slot": None,
            },
            {
                "type": "product",
                "order_index": 2,
                "section_order": 2,
                "product_uid": "P001",
                "product_name": "Alpha Earbuds",
                "display_video_path": "assets/P001.mp4",
                "display_video_slot": {
                    "x": -830,
                    "y": -77,
                    "width": 970,
                    "height": 590,
                    "coordinate_mode": "clip_transform_pixels",
                },
            },
            {
                "type": "product",
                "product_uid": "P002",
                "display_video_path": "assets/P002.mp4",
            },
        ],
    }

    probe = build_template_calibration_probe_manifest(
        manifest,
        product_uid="P001",
        created_from="full.manifest.json",
    )

    assert probe["mode"] == "template_calibration_probe"
    assert probe["display_template"] == "小燃-模板1"
    assert probe["created_from"] == "full.manifest.json"
    assert len(probe["entries"]) == 1
    assert probe["entries"][0]["product_uid"] == "P001"
    assert probe["entries"][0]["order_index"] == 1
    assert probe["entries"][0]["section_order"] == 1
    assert probe["entries"][0]["display_video_slot"]["coordinate_mode"] == "clip_transform_pixels"


def test_template_calibration_writes_probe_and_generates_single_item_draft(tmp_path, monkeypatch):
    full_manifest = {
        "source": "render_package",
        "display_template": "小燃-模板1",
        "entries": [
            {
                "type": "product",
                "order_index": 1,
                "section_order": 1,
                "product_uid": "P001",
                "product_name": "Alpha Earbuds",
                "display_video_path": "assets/P001.mp4",
                "display_video_slot": {"x": -830, "y": -77, "width": 970, "height": 590},
            },
            {
                "type": "product",
                "order_index": 2,
                "section_order": 2,
                "product_uid": "P002",
                "display_video_path": "assets/P002.mp4",
                "display_video_slot": {"x": 10, "y": 20, "width": 30, "height": 40},
            },
        ],
    }
    full_manifest_path = tmp_path / "full.jianying.manifest.json"
    full_manifest_path.write_text(json.dumps(full_manifest, ensure_ascii=False), encoding="utf-8")
    captured: dict[str, object] = {}
    service = _service()

    def fake_prepare(**kwargs):
        captured["prepare"] = kwargs
        return {
            "ok": True,
            "next": {"manifest_path": str(full_manifest_path)},
        }

    def fake_generate(project_id, *, manifest_path, draft_name, draft_root, **_kwargs):
        captured["draft"] = {
            "project_id": project_id,
            "manifest_path": manifest_path,
            "draft_name": draft_name,
            "draft_root": draft_root,
        }
        return workflow_service.WorkflowRunResult(["jianying"], returncode=0, stdout="draft ok\n")

    monkeypatch.setattr(service, "prepare_product_recommendation_output", fake_prepare)
    monkeypatch.setattr(service, "generate_jianying_draft", fake_generate)
    monkeypatch.setattr(workflow_service, "INTERNAL_WORKSPACE_ROOT", tmp_path)

    result = service.template_calibration_probe(
        3,
        account_label="小燃",
        product_uid="P001",
        draft_name="校准-P001",
        draft_root=tmp_path / "drafts",
    )

    probe_path = tmp_path / "project-3" / "template-calibration" / "template-calibrate-小燃-P001.manifest.json"
    probe = json.loads(probe_path.read_text(encoding="utf-8"))
    assert result["ok"] is True
    assert result["probe_manifest_path"] == str(probe_path)
    assert result["draft"]["returncode"] == 0
    assert len(probe["entries"]) == 1
    assert probe["entries"][0]["product_uid"] == "P001"
    assert captured["prepare"]["stale_product_image_policy"] == "reuse"
    assert captured["draft"]["manifest_path"] == probe_path


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
