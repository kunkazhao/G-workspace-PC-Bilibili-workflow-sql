from __future__ import annotations

import json
from pathlib import Path

from bworkflow_sql.db import Database
from bworkflow_sql.product_image_generation import regenerate_product_card_images
from bworkflow_sql.render_package_builder import product_card_content_fingerprint
from bworkflow_sql.repositories import Repository
from bworkflow_sql.template_config import get_remotion_template_metadata
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
        product_card_template_id="muban-xiaobo-1",
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
    assert segment["productCard"]["templateId"] == "muban-xiaobo-1"
    assert segment["productCard"]["templateVersion"] == "1.0.2"
    assert not Path(segment["productCard"]["coverAsset"]).is_absolute()
    assert not Path(segment["productCard"]["dataMap"]["cover"]).is_absolute()


def test_regenerate_product_card_images_requires_explicit_template_for_still_flow(tmp_path: Path):
    db, project_id, _image_path = _seed_project_with_stale_image(tmp_path)

    try:
        regenerate_product_card_images(
            db,
            project_id=project_id,
            account_label="小博",
            mode="stale",
            render_product_card_still=lambda *_args: (_ for _ in ()).throw(AssertionError("no render")),
        )
    except ValueError as exc:
        assert "必须明确选择商品图模板" in str(exc)
    else:
        raise AssertionError("expected missing product-card template to fail")


def test_regenerate_product_card_images_treats_empty_hash_as_unknown_legacy_stale(
    tmp_path: Path,
    monkeypatch,
):
    import bworkflow_sql.product_image_generation as product_images

    db, project_id, image_path = _seed_project_with_stale_image(tmp_path)
    with db.connect() as conn:
        conn.execute("UPDATE asset_bindings SET text_hash='' WHERE asset_type='image'")
    monkeypatch.setattr(product_images, "PRODUCT_IMAGE_RENDER_JOB_ROOT", tmp_path / "jobs")
    calls: list[tuple[Path, str, Path]] = []

    def fake_render(package_path: Path, product_uid: str, output_path: Path) -> Path:
        calls.append((package_path, product_uid, output_path))
        output_path.write_bytes(b"legacy refreshed")
        return output_path

    result = regenerate_product_card_images(
        db,
        project_id=project_id,
        account_label="小博",
        mode="stale",
        product_card_template_id="muban-xiaobo-1",
        render_product_card_still=fake_render,
    )

    assert result["ok"] is True
    assert result["regenerated"][0]["uid"] == "P001"
    assert result["regenerated"][0]["reason"] == "unknown_legacy_image_hash"
    assert calls == [(calls[0][0], "P001", image_path)]
    assert image_path.read_bytes() == b"legacy refreshed"


def test_regenerate_product_card_images_targets_default_account_template_dir(
    tmp_path: Path,
    monkeypatch,
):
    import bworkflow_sql.product_image_generation as product_images

    db, project_id, _image_path = _seed_project_with_stale_image(tmp_path)
    wrong_template_path = tmp_path / "images" / "keyboard" / "小博" / "模板2" / "P001.png"
    wrong_template_path.parent.mkdir(parents=True, exist_ok=True)
    wrong_template_path.write_bytes(b"old template 2 image")
    with db.connect() as conn:
        conn.execute(
            """
            UPDATE asset_bindings
            SET path=?, text_hash='old-template-2'
            WHERE project_id=? AND uid='P001' AND asset_type='image'
            """,
            (str(wrong_template_path), project_id),
        )
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
        mode="all",
        product_uid="P001",
        product_card_template_id="muban-xiaobo-1",
        render_product_card_still=fake_render,
    )

    expected_path = tmp_path / "images" / "keyboard" / "小博" / "模板1" / "299-P001-Alpha Keyboard.png"

    assert result["regenerated"][0]["path"] == str(expected_path)
    assert calls == [(calls[0][0], "P001", expected_path)]


def test_regenerate_product_card_images_stale_regenerates_wrong_template_binding(
    tmp_path: Path,
    monkeypatch,
):
    import bworkflow_sql.product_image_generation as product_images

    db, project_id, _image_path = _seed_project_with_stale_image(tmp_path)
    account_label = get_remotion_template_metadata("muban-xiaobo-2")["account"]
    wrong_path = tmp_path / "images" / "keyboard" / account_label / "模板1" / "P001.png"
    wrong_path.parent.mkdir(parents=True, exist_ok=True)
    wrong_path.write_bytes(b"old template 1 image")
    ts = now_iso()
    with db.connect() as conn:
        conn.execute("DELETE FROM asset_bindings WHERE asset_type='image'")
        conn.execute(
            """
            INSERT INTO asset_bindings
                (project_id, uid, asset_type, account_label, path, status, source_kind, text_hash, created_at, updated_at)
            VALUES (?, 'P001', 'image', ?, ?, 'ready', 'scan', '', ?, ?)
            """,
            (project_id, account_label, str(wrong_path), ts, ts),
        )
    monkeypatch.setattr(product_images, "PRODUCT_IMAGE_RENDER_JOB_ROOT", tmp_path / "jobs")
    calls: list[tuple[Path, str, Path]] = []

    def fake_render(package_path: Path, product_uid: str, output_path: Path) -> Path:
        calls.append((package_path, product_uid, output_path))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"new template 2 image")
        return output_path

    result = regenerate_product_card_images(
        db,
        project_id=project_id,
        account_label=account_label,
        mode="stale",
        product_uid="P001",
        product_card_template_id="muban-xiaobo-2",
        render_product_card_still=fake_render,
    )

    expected_path = tmp_path / "images" / "keyboard" / account_label / "模板2" / "299-P001-Alpha Keyboard.png"

    assert result["regenerated"][0]["path"] == str(expected_path)
    assert result["regenerated"][0]["reason"] == "wrong_template_binding"
    assert calls == [(calls[0][0], "P001", expected_path)]


def test_regenerate_product_card_images_prefers_ready_binding_for_selected_template(
    tmp_path: Path,
    monkeypatch,
):
    import bworkflow_sql.product_image_generation as product_images

    db, project_id, _image_path = _seed_project_with_stale_image(tmp_path)
    account_label = get_remotion_template_metadata("muban-xiaobo-2")["account"]
    selected_path = tmp_path / "images" / "keyboard" / account_label / "模板2" / "P001.png"
    selected_path.parent.mkdir(parents=True, exist_ok=True)
    selected_path.write_bytes(b"old template 2 image")
    ts = now_iso()
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO asset_bindings
                (project_id, uid, asset_type, account_label, path, status, source_kind, text_hash, created_at, updated_at)
            VALUES (?, 'P001', 'image', ?, ?, 'ready', 'scan', 'old-template-2', ?, ?)
            """,
            (project_id, account_label, str(selected_path), ts, ts),
        )
    monkeypatch.setattr(product_images, "PRODUCT_IMAGE_RENDER_JOB_ROOT", tmp_path / "jobs")
    calls: list[tuple[Path, str, Path]] = []

    def fake_render(package_path: Path, product_uid: str, output_path: Path) -> Path:
        calls.append((package_path, product_uid, output_path))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"new template 2 image")
        return output_path

    result = regenerate_product_card_images(
        db,
        project_id=project_id,
        account_label=account_label,
        mode="stale",
        product_uid="P001",
        product_card_template_id="muban-xiaobo-2",
        render_product_card_still=fake_render,
    )

    assert result["regenerated"][0]["path"] == str(selected_path)
    assert result["regenerated"][0]["reason"] == "stale_or_forced"
    assert calls == [(calls[0][0], "P001", selected_path)]
    assert selected_path.read_bytes() == b"new template 2 image"


def test_regenerate_product_card_images_can_filter_single_product_uid(
    tmp_path: Path,
    monkeypatch,
):
    import bworkflow_sql.product_image_generation as product_images

    db, project_id, _image_path = _seed_project_with_stale_image(tmp_path)
    monkeypatch.setattr(product_images, "PRODUCT_IMAGE_RENDER_JOB_ROOT", tmp_path / "jobs")
    with db.connect() as conn:
        conn.execute("DELETE FROM asset_bindings WHERE asset_type='image'")
    calls: list[tuple[Path, str, Path]] = []

    def fake_render(package_path: Path, product_uid: str, output_path: Path) -> Path:
        calls.append((package_path, product_uid, output_path))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"created image")
        return output_path

    result = regenerate_product_card_images(
        db,
        project_id=project_id,
        account_label="小博",
        mode="missing",
        product_uid="P001",
        product_card_template_id="muban-xiaobo-1",
        render_product_card_still=fake_render,
    )

    assert result["ok"] is True
    assert result["product_uid"] == "P001"
    assert [item["uid"] for item in result["regenerated"]] == ["P001"]
    assert [call[1] for call in calls] == ["P001"]
    assert all(item["uid"] != "P002" for item in result["skipped"])


def test_regenerate_product_card_images_creates_missing_account_binding(
    tmp_path: Path,
    monkeypatch,
):
    import bworkflow_sql.product_image_generation as product_images

    db, project_id, _image_path = _seed_project_with_stale_image(tmp_path)
    monkeypatch.setattr(product_images, "PRODUCT_IMAGE_RENDER_JOB_ROOT", tmp_path / "jobs")
    with db.connect() as conn:
        conn.execute("DELETE FROM asset_bindings WHERE asset_type='image'")
    calls: list[tuple[Path, str, Path]] = []

    def fake_render(package_path: Path, product_uid: str, output_path: Path) -> Path:
        calls.append((package_path, product_uid, output_path))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"created image")
        return output_path

    result = regenerate_product_card_images(
        db,
        project_id=project_id,
        account_label="小博",
        mode="missing",
        product_card_template_id="muban-xiaobo-1",
        render_product_card_still=fake_render,
    )

    product = Repository(db).products(project_id, include_removed=False)[0]
    expected_path = (
        tmp_path
        / "images"
        / "keyboard"
        / "小博"
        / "模板1"
        / "299-P001-Alpha Keyboard.png"
    )
    package_payload = json.loads(calls[0][0].read_text(encoding="utf-8"))
    segment = package_payload["segments"][0]
    expected_fingerprint = product_card_content_fingerprint(product, segment["productCard"])
    binding = db.fetchone(
        "SELECT * FROM asset_bindings WHERE project_id=? AND uid='P001' AND asset_type='image'",
        (project_id,),
    )

    assert result["ok"] is True
    assert result["mode"] == "missing"
    assert result["product_card_template_id"] == "muban-xiaobo-1"
    assert result["regenerated"][0]["uid"] == "P001"
    assert result["regenerated"][0]["reason"] == "missing_ready_image_binding"
    assert result["regenerated"][0]["path"] == str(expected_path)
    assert calls == [(calls[0][0], "P001", expected_path)]
    assert expected_path.read_bytes() == b"created image"
    assert binding["account_label"] == "小博"
    assert binding["path"] == str(expected_path)
    assert binding["status"] == "ready"
    assert binding["source_kind"] == "remotion"
    assert binding["text_hash"] == expected_fingerprint
    assert segment["productCard"]["templateId"] == "muban-xiaobo-1"


def test_regenerate_product_card_images_uses_cutme_adapter_by_default(
    tmp_path: Path,
    monkeypatch,
):
    import bworkflow_sql.product_image_generation as product_images

    db, project_id, image_path = _seed_project_with_stale_image(tmp_path)
    monkeypatch.setattr(product_images, "PRODUCT_IMAGE_RENDER_JOB_ROOT", tmp_path / "jobs")
    calls: list[tuple[Path, str, Path]] = []

    class FakeCutMeAdapter:
        def render_product_card(
            self,
            package_path: Path,
            *,
            product_uid: str,
            output_path: Path,
        ) -> dict:
            calls.append((package_path, product_uid, output_path))
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"adapter image")
            return {"ok": True, "artifacts": {"output_path": str(output_path)}}

    monkeypatch.setattr(product_images, "CutMeAdapter", FakeCutMeAdapter)

    result = regenerate_product_card_images(
        db,
        project_id=project_id,
        account_label="小博",
        mode="all",
        product_uid="P001",
        product_card_template_id="muban-xiaobo-1",
    )

    assert result["regenerated"][0]["path"] == str(image_path)
    assert len(calls) == 1
    package_path, product_uid, output_path = calls[0]
    assert product_uid == "P001"
    assert output_path == image_path
    package = json.loads(package_path.read_text(encoding="utf-8"))
    assert package["output"]["mode"] == "product_card_still"


def test_product_card_fingerprint_changes_when_template_version_changes() -> None:
    product = {"uid": "P001", "title": "Demo", "price_label": "199"}
    product_card = {
        "templateId": "muban-xiaobo-1",
        "templateVersion": "1.0.1",
        "dataMap": {"title": "Demo", "price": "199", "remark": "good", "cover": "cover.png"},
        "slots": [{"label": "重量", "value": "4g"}],
        "coverAsset": "cover.png",
    }

    first = product_card_content_fingerprint(product, product_card)
    changed = dict(product_card)
    changed["templateVersion"] = "1.0.2"
    second = product_card_content_fingerprint(product, changed)

    assert first != second
