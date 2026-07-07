from __future__ import annotations

import json
from pathlib import Path

from bworkflow_sql.db import Database
from bworkflow_sql.product_card_preflight import product_card_preflight
from bworkflow_sql.render_package_builder import product_card_content_fingerprint, product_card_payload_for_product
from bworkflow_sql.repositories import Repository
from bworkflow_sql.utils import now_iso


def _seed_preflight_project(tmp_path: Path) -> tuple[Database, int, str]:
    db = Database(tmp_path / "preflight.db")
    repo = Repository(db)
    project_id = db.upsert_project(
        {
            "name": "keyboard",
            "category_name": "keyboard",
            "image_root": str(tmp_path / "images"),
        }
    )
    cover = tmp_path / "covers" / "P001.png"
    cover.parent.mkdir(parents=True, exist_ok=True)
    cover.write_bytes(b"cover")
    repo.upsert_products_from_master(
        project_id,
        [
            {
                "uid": "P001",
                "title": "Alpha Keyboard",
                "price_label": "299",
                "cover": str(cover),
                "remark": "Stable wireless connection.",
                "spec": {"switch": "silver"},
            }
        ],
    )
    return db, project_id, str(cover)


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


def test_product_card_preflight_blocks_missing_local_cover_payload(tmp_path: Path):
    db, project_id, _cover = _seed_preflight_project(tmp_path)
    with db.connect() as conn:
        conn.execute(
            "UPDATE products SET product_card_json=? WHERE project_id=? AND uid='P001'",
            (json.dumps({"dataMap": {"title": "Alpha Keyboard", "price": "299"}}), project_id),
        )

    result = product_card_preflight(
        db,
        project_id=project_id,
        account_label="小博",
        product_card_template_id="muban-xiaobo-1",
        product_uid="P001",
    )

    assert result["ok"] is False
    assert result["status"] == "blocked"
    assert result["summary"]["errors"] == 1
    assert result["issues"][0]["code"] == "missing_cover_asset"
    assert result["next"]["action"] == "sync_master_then_recheck"
    assert "sync 1 --step master" in result["next"]["command"]


def test_product_card_preflight_passes_current_cover_template_and_binding(tmp_path: Path):
    db, project_id, cover = _seed_preflight_project(tmp_path)
    repo = Repository(db)
    product = repo.products(project_id, include_removed=False)[0]
    card = product_card_payload_for_product(
        product,
        project=repo.project(project_id) or {},
        fallback_image_path=None,
        account_label="小博",
        product_card_template_id="muban-xiaobo-1",
    )
    fingerprint = product_card_content_fingerprint(product, card)
    image_path = tmp_path / "images" / "keyboard" / "小博" / "模板1" / "P001.png"
    _insert_ready_image(
        db,
        project_id,
        uid="P001",
        account_label="小博",
        path=image_path,
        text_hash=fingerprint,
    )

    result = product_card_preflight(
        db,
        project_id=project_id,
        account_label="小博",
        product_card_template_id="muban-xiaobo-1",
        product_uid="P001",
        expect_cover=Path(cover).name,
    )

    assert result["ok"] is True
    assert result["status"] == "ok"
    assert result["summary"]["products_checked"] == 1
    assert result["products"][0]["uid"] == "P001"
    assert result["products"][0]["cover_match"] is True
    assert result["next"]["action"] == "run_product_images_or_continue"
