from __future__ import annotations

import json
from pathlib import Path

import pytest

from bworkflow_sql.template_calibration_runner import (
    load_template_calibration_targets,
    run_template_calibration_targets,
    validate_probe_manifest,
)


def test_load_template_calibration_targets_filters_active_target(tmp_path: Path):
    config_path = tmp_path / "targets.json"
    config_path.write_text(
        json.dumps(
            {
                "targets": [
                    {
                        "id": "xiaobo-template2",
                        "project_id": 12,
                        "account": "xiaobo",
                        "template_id": "muban-xiaobo-2",
                        "product_uid": "EJRE014",
                        "draft_name": "calibrate-xiaobo-template2-EJRE014",
                        "active": True,
                    },
                    {
                        "id": "rongrong-template1",
                        "project_id": 2,
                        "account": "rongrong",
                        "template_id": "muban-rongrong-1",
                        "product_uid": "PMGD001",
                        "active": False,
                    },
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    targets = load_template_calibration_targets(config_path, target_id="xiaobo-template2")

    assert len(targets) == 1
    assert targets[0]["id"] == "xiaobo-template2"
    assert targets[0]["project_id"] == 12
    assert targets[0]["template_id"] == "muban-xiaobo-2"


def test_standard_template_calibration_config_includes_xiaowai_template2():
    targets = load_template_calibration_targets(include_inactive=True)
    target = next((item for item in targets if item["id"] == "xiaowai-template2"), None)

    assert target is not None
    assert target["project_id"] == 12
    assert target["account"] == "小歪"
    assert target["template_id"] == "muban-xiaowai-2"
    assert target["display_template"] == "小歪模板2"
    assert target["image_set"] == "模板2"
    assert target["product_uid"] == "EJRE014"
    assert target["active"] is True


def test_load_template_calibration_targets_rejects_missing_required_field(tmp_path: Path):
    config_path = tmp_path / "targets.json"
    config_path.write_text(
        json.dumps({"targets": [{"id": "bad", "project_id": 12}]}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="missing required field"):
        load_template_calibration_targets(config_path)


def test_validate_probe_manifest_requires_template_image_and_slot_match(tmp_path: Path):
    manifest_path = tmp_path / "probe.json"
    image_path = tmp_path / "images" / "xiaobo" / "template2" / "P001.png"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(b"image")
    manifest_path.write_text(
        json.dumps(
            {
                "display_template": "Xiaobo Template 2",
                "entries": [
                    {
                        "type": "product",
                        "image_path": str(image_path),
                        "display_video_slot": {"templateId": "muban-xiaobo-2"},
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = validate_probe_manifest(
        manifest_path,
        expected_template_id="muban-xiaobo-2",
        expected_display_template="Xiaobo Template 2",
        expected_image_set="template2",
    )

    assert result["ok"] is True
    assert result["image_path"] == str(image_path)


def test_run_template_calibration_targets_regenerates_then_calibrates(tmp_path: Path):
    probe_path = tmp_path / "probe.json"
    image_path = tmp_path / "images" / "xiaobo" / "template2" / "P001.png"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(b"image")
    probe_path.write_text(
        json.dumps(
            {
                "display_template": "Xiaobo Template 2",
                "entries": [
                    {
                        "type": "product",
                        "image_path": str(image_path),
                        "display_video_slot": {"templateId": "muban-xiaobo-2"},
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    calls: list[tuple[str, dict[str, object]]] = []

    class FakeWorkflow:
        def __init__(self) -> None:
            self.doctor_calls = 0

        def template_doctor(self, **kwargs):
            self.doctor_calls += 1
            calls.append(("doctor", kwargs))
            if self.doctor_calls == 1:
                return {
                    "ok": False,
                    "issues": [{"code": "wrong_template_binding"}],
                    "next": {"action": "regenerate_product_images"},
                }
            return {"ok": True, "issues": [], "summary": {"errors": 0, "warnings": 0}}

        def regenerate_product_card_images(self, **kwargs):
            calls.append(("images", kwargs))
            return {"ok": True, "regenerated": [{"uid": "P001"}], "skipped": []}

        def template_calibration_probe(self, **kwargs):
            calls.append(("calibrate", kwargs))
            return {
                "ok": True,
                "display_template": "Xiaobo Template 2",
                "probe_manifest_path": str(probe_path),
                "draft": {"returncode": 0},
            }

    targets = [
        {
            "id": "xiaobo-template2",
            "project_id": 12,
            "account": "xiaobo",
            "template_id": "muban-xiaobo-2",
            "display_template": "Xiaobo Template 2",
            "image_set": "template2",
            "product_uid": "P001",
            "draft_name": "calibrate-xiaobo-template2-P001",
        }
    ]

    result = run_template_calibration_targets(FakeWorkflow(), targets=targets, draft_suffix="v3")

    assert result["ok"] is True
    assert result["summary"] == {"total": 1, "succeeded": 1, "failed": 0}
    assert [name for name, _ in calls] == ["doctor", "images", "doctor", "calibrate"]
    assert calls[-1][1]["draft_name"] == "calibrate-xiaobo-template2-P001-v3"
    assert result["targets"][0]["manifest_check"]["ok"] is True
