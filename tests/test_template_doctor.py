from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from bworkflow_sql.db import Database
from bworkflow_sql.repositories import Repository
from bworkflow_sql.template_config import get_remotion_template_metadata
from bworkflow_sql.utils import now_iso


def _seed_template_doctor_project(tmp_path: Path) -> tuple[Database, int]:
    db = Database(tmp_path / "doctor.db")
    repo = Repository(db)
    project_id = db.upsert_project(
        {
            "name": "keyboard",
            "category_name": "keyboard",
            "image_root": str(tmp_path / "images"),
        }
    )
    cover_root = tmp_path / "covers"
    cover_root.mkdir(parents=True, exist_ok=True)
    for uid in ("P001", "P002"):
        (cover_root / f"{uid}.png").write_bytes(b"cover")
    repo.upsert_products_from_master(
        project_id,
        [
            {
                "uid": "P001",
                "title": "Alpha Keyboard",
                "price_label": "299",
                "cover": str(cover_root / "P001.png"),
                "remark": "Stable wireless connection.",
                "spec": {"switch": "silver"},
                "product_card_template_id": "xiaoran1",
            },
            {
                "uid": "P002",
                "title": "Beta Keyboard",
                "price_label": "399",
                "cover": str(cover_root / "P002.png"),
                "remark": "Long battery life.",
                "spec": {"battery": "4000mAh"},
                "product_card_template_id": "xiaoran1",
            },
        ],
    )
    return db, project_id


def _insert_ready_image(
    db: Database,
    project_id: int,
    *,
    uid: str,
    account_label: str,
    path: Path,
    text_hash: str,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"image")
    ts = now_iso()
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO asset_bindings
                (project_id, uid, asset_type, account_label, path, status, source_kind, text_hash, created_at, updated_at)
            VALUES (?, ?, 'image', ?, ?, 'ready', 'test', ?, ?, ?)
            """,
            (project_id, uid, account_label, str(path), text_hash, ts, ts),
        )


def _diagnose_template_flow():
    try:
        module = importlib.import_module("bworkflow_sql.template_doctor")
    except ModuleNotFoundError:
        pytest.fail("bworkflow_sql.template_doctor should provide diagnose_template_flow")
    return module.diagnose_template_flow


def test_template_doctor_requires_explicit_product_card_template(tmp_path: Path):
    db, project_id = _seed_template_doctor_project(tmp_path)
    diagnose_template_flow = _diagnose_template_flow()

    result = diagnose_template_flow(
        db,
        project_id=project_id,
        account_label="灏忓崥",
    )

    assert result["ok"] is False
    assert result["status"] == "issues_found"
    assert result["summary"]["errors"] == 1
    assert result["issues"][0]["code"] == "product_card_template_required"
    assert result["issues"][0]["level"] == "error"
    assert result["next"]["action"] == "confirm_product_card_template"


def test_template_doctor_reports_wrong_binding_and_unknown_legacy_hash(tmp_path: Path):
    db, project_id = _seed_template_doctor_project(tmp_path)
    diagnose_template_flow = _diagnose_template_flow()
    account_label = get_remotion_template_metadata("muban-xiaobo-1")["account"]
    wrong_template = tmp_path / "images" / "keyboard" / account_label / "模板2" / "P001.png"
    correct_template = tmp_path / "images" / "keyboard" / account_label / "模板1" / "P002.png"
    _insert_ready_image(
        db,
        project_id,
        uid="P001",
        account_label=account_label,
        path=wrong_template,
        text_hash="old-fingerprint",
    )
    _insert_ready_image(
        db,
        project_id,
        uid="P002",
        account_label=account_label,
        path=correct_template,
        text_hash="",
    )

    result = diagnose_template_flow(
        db,
        project_id=project_id,
        account_label=account_label,
        product_card_template_id="muban-xiaobo-1",
    )
    issues = {(item["code"], item.get("uid")): item for item in result["issues"]}

    assert result["ok"] is False
    assert result["template"]["id"] == "muban-xiaobo-1"
    assert result["template"]["confirmed"] is True
    assert result["template"]["selectionSource"] == "explicit"
    assert issues[("wrong_template_binding", "P001")]["level"] == "error"
    assert issues[("unknown_legacy_image_hash", "P002")]["level"] == "warning"
    assert "product-images" in result["next"]["command"]
    assert "--product-card-template-id muban-xiaobo-1" in result["next"]["command"]
