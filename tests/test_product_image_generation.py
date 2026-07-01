from __future__ import annotations

import json
from pathlib import Path

from bworkflow_sql.db import Database
from bworkflow_sql.product_image_generation import regenerate_product_card_images
from bworkflow_sql.render_package_builder import product_card_content_fingerprint
from bworkflow_sql.repositories import Repository
from bworkflow_sql.utils import now_iso


def _seed_project_with_stale_image(tmp_path: Path) -> tuple[Database, int, Path]:
    db = Database(tmp_path / "test.db")
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
                "spec": {"重量": "4.2克", "续航": "7h/24h"},
                "product_card_template_id": "xiaoran1",
            },
            {"uid": "P002", "title": "Beta Keyboard", "price_label": "399"},
        ],
    )
    image_path = tmp_path / "images" / "keyboard" / "小博" / "模板1" / "P001.png"
    image_path.parent.mkdir(parents=True, exist_ok=True)
    image_path.write_bytes(b"old image")
    ts = now_iso()
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO asset_bindings
                (project_id, uid, asset_type, account_label, path, status, source_kind, text_hash, created_at, updated_at)
            VALUES (?, 'P001', 'image', '小博', ?, 'ready', 'scan', 'old-fingerprint', ?, ?)
            """,
            (project_id, str(image_path), ts, ts),
        )
    return db, project_id, image_path


def test_regenerate_product_card_images_renders_stale_only_and_updates_binding(
    tmp_path: Path,
    monkeypatch,
):
    import bworkflow_sql.product_image_generation as product_images

    db, project_id, image_path = _seed_project_with_stale_image(tmp_path)
    monkeypatch.setattr(product_images, "PRODUCT_IMAGE_RENDER_JOB_ROOT", tmp_path / "jobs")
    calls: list[tuple[Path, str, Path]] = []

    def fake_render(package_path: Path, product_uid: str, output_path: Path) -> Path:
        calls.append((package_path, product_uid, output_path))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"new image")
        return output_path

    result = regenerate_product_card_images(
        db,
        project_id=project_id,
        account_label="小博",
        mode="stale",
        render_product_card_still=fake_render,
    )

    product = Repository(db).products(project_id, include_removed=False)[0]
    package_payload = json.loads(calls[0][0].read_text(encoding="utf-8"))
    segment = package_payload["segments"][0]
    expected_fingerprint = product_card_content_fingerprint(product, segment["productCard"])
    binding = db.fetchone(
        "SELECT * FROM asset_bindings WHERE project_id=? AND uid='P001' AND asset_type='image'",
        (project_id,),
    )

    assert result["ok"] is True
    assert result["mode"] == "stale"
    assert result["regenerated"][0]["uid"] == "P001"
    assert result["regenerated"][0]["path"] == str(image_path)
    assert calls == [(calls[0][0], "P001", image_path)]
    assert image_path.read_bytes() == b"new image"
    assert binding["text_hash"] == expected_fingerprint
    assert binding["source_kind"] == "remotion"
    assert segment["productCardFingerprint"] == expected_fingerprint
    assert not Path(segment["productCard"]["coverAsset"]).is_absolute()
    assert not Path(segment["productCard"]["dataMap"]["cover"]).is_absolute()


def test_regenerate_product_card_images_reports_noop_when_no_stale_images(tmp_path: Path):
    db, project_id, _image_path = _seed_project_with_stale_image(tmp_path)
    with db.connect() as conn:
        conn.execute("UPDATE asset_bindings SET text_hash='' WHERE asset_type='image'")

    result = regenerate_product_card_images(
        db,
        project_id=project_id,
        account_label="小博",
        mode="stale",
        render_product_card_still=lambda *_args: (_ for _ in ()).throw(AssertionError("no render")),
    )

    assert result["ok"] is True
    assert result["regenerated"] == []
    assert result["skipped"][0]["uid"] == "P001"
