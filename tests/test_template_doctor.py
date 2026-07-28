from __future__ import annotations

import bworkflow_sql.template_doctor as template_doctor


def _metadata() -> dict:
    return {
        "templateId": "muban-test-1",
        "sourceCanvas": {"width": 970, "height": 480},
        "outputCanvas": {"width": 1920, "height": 1080},
        "cardPlacement": {"width": 1920, "height": 960},
        "coverMediaSlot": {"width": 496, "height": 279},
        "slotDeclarations": [{"key": "title", "required": True}],
    }


def test_template_doctor_accepts_current_slot_declaration_contract(monkeypatch) -> None:
    monkeypatch.setattr(
        template_doctor,
        "product_card_text_capacity_certification_issues",
        lambda _metadata: [],
    )

    assert template_doctor._template_metadata_issues(_metadata()) == []


def test_template_doctor_reports_missing_slot_declarations(monkeypatch) -> None:
    monkeypatch.setattr(
        template_doctor,
        "product_card_text_capacity_certification_issues",
        lambda _metadata: [],
    )
    metadata = _metadata()
    metadata.pop("slotDeclarations")

    issues = template_doctor._template_metadata_issues(metadata)

    assert issues == [
        {
            "level": "error",
            "code": "template_metadata_incomplete",
            "template_id": "muban-test-1",
            "field": "slotDeclarations",
            "message": "Remotion template metadata is missing or invalid: slotDeclarations",
        }
    ]


def test_template_doctor_dynamic_card_does_not_require_legacy_image_bindings(monkeypatch) -> None:
    class FakeRepository:
        def __init__(self, _db) -> None:
            pass

        def project(self, _project_id: int) -> dict:
            return {"video_root": ""}

        def asset_bindings(self, _project_id: int) -> list[dict]:
            return []

    product = {"uid": "P001", "title": "动态商品"}
    monkeypatch.setattr(template_doctor, "Repository", FakeRepository)
    monkeypatch.setattr(template_doctor, "resolve_product_card_template", lambda *_args, **_kwargs: _metadata())
    monkeypatch.setattr(template_doctor, "_template_metadata_issues", lambda _metadata: [])
    monkeypatch.setattr(template_doctor, "display_video_slot_for_product_card_template_id", lambda _template_id: "media")
    monkeypatch.setattr(
        template_doctor,
        "audit_product_video_media",
        lambda *_args, **_kwargs: {"selected_paths": {"P001": "product.mp4"}},
    )
    monkeypatch.setattr(
        template_doctor,
        "product_card_payload_for_product",
        lambda *_args, **_kwargs: {"coverMediaSlot": {"key": "media"}},
    )

    result = template_doctor.diagnose_template_flow(
        object(),
        project_id=1,
        account_label="小博",
        product_card_template_id="muban-test-1",
        product_media_mode="video_preferred",
        products=[product],
    )

    assert result["ok"] is True
    assert result["issues"] == []
    assert result["next"]["action"] == "continue_phase_7"
