from __future__ import annotations

from pathlib import Path

from bworkflow_sql.db import Database
from bworkflow_sql.render_package_builder import build_product_recommendation_package
from bworkflow_sql.repositories import Repository
from bworkflow_sql.utils import now_iso, text_hash


def _seed_project(tmp_path: Path) -> tuple[Database, int]:
    db = Database(tmp_path / "test.db")
    repo = Repository(db)
    project_id = db.upsert_project(
        {
            "name": "keyboard",
            "category_name": "keyboard",
            "scheme_id": "scheme-1",
        }
    )
    cover = tmp_path / "assets" / "covers" / "P001.png"
    cover.parent.mkdir(parents=True, exist_ok=True)
    cover.write_bytes(b"cover")
    repo.upsert_products_from_master(
        project_id,
        [
            {
                "uid": "P001",
                "title": "Alpha Keyboard",
                "price_label": "200-300",
                "cover": str(cover),
                "remark": "A compact keyboard with stable wireless connection.",
                "spec": {
                    "switch": "silver",
                    "battery": "4000mAh",
                    "_internal": "ignored",
                },
                "product_card_template_id": "xiaoran1",
            },
            {"uid": "P002", "title": "Beta Keyboard", "price_label": "200-300"},
        ],
    )
    return db, project_id


def _insert_script(
    db: Database,
    project_id: int,
    *,
    script_type: str,
    body: str,
    owner_uid: str = "",
    price_range_label: str = "",
    block_label: str = "正文",
) -> int:
    ts = now_iso()
    block_hash = text_hash(body)
    script_id = f"{script_type}:{owner_uid or price_range_label or block_label}:V001"
    with db.connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO script_blocks
                (project_id, script_type, owner_uid, price_range_label, block_label, script_id, body, text_hash, source, source_anchor, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'test', '', ?, ?)
            """,
            (
                project_id,
                script_type,
                owner_uid,
                price_range_label,
                block_label,
                script_id,
                body,
                block_hash,
                ts,
                ts,
            ),
        )
        return int(cursor.lastrowid)


def _insert_asset(
    db: Database,
    project_id: int,
    *,
    uid: str,
    asset_type: str,
    path: Path,
    account_label: str = "",
    script_block_id: int | None = None,
    block_label: str = "",
    block_hash: str = "",
) -> int:
    ts = now_iso()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"asset")
    with db.connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO asset_bindings
                (project_id, uid, script_block_id, asset_type, account_label, block_label, text_hash, path, status, source_kind, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'ready', 'test', ?, ?)
            """,
            (
                project_id,
                uid,
                script_block_id,
                asset_type,
                account_label,
                block_label,
                block_hash,
                str(path),
                ts,
                ts,
            ),
        )
        return int(cursor.lastrowid)


def _seed_ready_package_data(tmp_path: Path) -> tuple[Database, int]:
    db, project_id = _seed_project(tmp_path)
    price_block = _insert_script(
        db,
        project_id,
        script_type="price_transition",
        price_range_label="200-300",
        body="Two to three hundred yuan focuses on brand maturity.",
    )
    first_block = _insert_script(
        db,
        project_id,
        script_type="product",
        owner_uid="P001",
        body="Alpha is the first recommendation.",
    )
    second_block = _insert_script(
        db,
        project_id,
        script_type="product",
        owner_uid="P002",
        body="Beta is the second recommendation.",
    )
    assets = tmp_path / "assets"
    _insert_asset(
        db,
        project_id,
        uid="PRICE_TRANSITION",
        asset_type="voice",
        path=assets / "price.wav",
        account_label="小博",
        script_block_id=price_block,
        block_label="200-300",
        block_hash=text_hash("Two to three hundred yuan focuses on brand maturity."),
    )
    for uid, block_id, body in [
        ("P001", first_block, "Alpha is the first recommendation."),
        ("P002", second_block, "Beta is the second recommendation."),
    ]:
        _insert_asset(
            db,
            project_id,
            uid=uid,
            asset_type="image",
            path=assets / f"{uid}.png",
            account_label="小博",
        )
        _insert_asset(
            db,
            project_id,
            uid=uid,
            asset_type="voice",
            path=assets / f"{uid}.wav",
            account_label="小博",
            script_block_id=block_id,
            block_label="正文",
            block_hash=text_hash(body),
        )
    _insert_asset(
        db,
        project_id,
        uid="P001",
        asset_type="video",
        path=assets / "P001.mp4",
    )
    return db, project_id


def test_build_product_recommendation_package_from_ready_assets(
    tmp_path: Path,
    monkeypatch,
):
    import bworkflow_sql.render_package_builder as builder

    db, project_id = _seed_ready_package_data(tmp_path)
    monkeypatch.setattr(builder, "get_audio_duration_seconds", lambda _path: 5.0)

    result = build_product_recommendation_package(
        db,
        project_id=project_id,
        account_label="小博",
        output_mode="jianying_draft",
    )

    assert result.missing == []
    assert result.package["schemaVersion"] == "1.0.0"
    assert result.package["output"]["mode"] == "jianying_draft"
    assert result.package["approval"]["productRecommendationBatch"]["status"] == "pending"
    assert [segment["type"] for segment in result.package["segments"]] == [
        "price_transition",
        "product_recommendation",
        "product_recommendation",
    ]
    products = [
        segment
        for segment in result.package["segments"]
        if segment["type"] == "product_recommendation"
    ]
    assert [segment["productUid"] for segment in products] == ["P001", "P002"]
    assert products[0]["videoAsset"]
    assert products[1]["videoAsset"] is None
    product_card = products[0]["productCard"]
    assert product_card["templateId"] == "xiaoran1"
    assert product_card["dataMap"]["title"] == "Alpha Keyboard"
    assert product_card["dataMap"]["price"] == "200-300"
    assert product_card["dataMap"]["remark"] == "A compact keyboard with stable wireless connection."
    assert product_card["coverAsset"].endswith("P001.png")
    assert product_card["fallbackImageAsset"] == products[0]["imageCardAsset"]
    assert product_card["slots"] == [
        {"label": "switch", "value": "silver"},
        {"label": "battery", "value": "4000mAh"},
    ]
    assert "productCard" not in products[1]
    assert all(Path(segment["voiceAsset"]).is_absolute() for segment in result.package["segments"])
    assert all(Path(segment["imageCardAsset"]).is_absolute() for segment in products)


def test_build_product_recommendation_package_reports_missing_required_assets(
    tmp_path: Path,
    monkeypatch,
):
    import bworkflow_sql.render_package_builder as builder

    db, project_id = _seed_ready_package_data(tmp_path)
    monkeypatch.setattr(builder, "get_audio_duration_seconds", lambda _path: 5.0)
    with db.connect() as conn:
        conn.execute("UPDATE asset_bindings SET status='missing' WHERE asset_type='image' AND uid='P001'")
        conn.execute("UPDATE asset_bindings SET status='missing' WHERE asset_type='voice' AND uid='P002'")
        conn.execute("UPDATE asset_bindings SET status='missing' WHERE asset_type='voice' AND uid='PRICE_TRANSITION'")

    result = build_product_recommendation_package(
        db,
        project_id=project_id,
        account_label="小博",
        output_mode="jianying_draft",
    )

    assert {item["kind"] for item in result.missing} == {
        "product_image",
        "product_voice",
        "price_voice",
    }
    assert any(item["uid"] == "P001" for item in result.missing)
    assert any(item["uid"] == "P002" for item in result.missing)


def test_build_product_recommendation_package_reports_missing_price_script(
    tmp_path: Path,
    monkeypatch,
):
    import bworkflow_sql.render_package_builder as builder

    db, project_id = _seed_ready_package_data(tmp_path)
    monkeypatch.setattr(builder, "get_audio_duration_seconds", lambda _path: 5.0)
    with db.connect() as conn:
        conn.execute("UPDATE script_blocks SET active=0 WHERE script_type='price_transition'")

    result = build_product_recommendation_package(
        db,
        project_id=project_id,
        account_label="小博",
        output_mode="jianying_draft",
    )

    assert result.missing[0]["kind"] == "price_script"
    assert result.missing[0]["price_range_label"] == "200-300"


def test_build_product_recommendation_package_rejects_invalid_output_mode(
    tmp_path: Path,
):
    db, project_id = _seed_ready_package_data(tmp_path)

    try:
        build_product_recommendation_package(
            db,
            project_id=project_id,
            account_label="小博",
            output_mode="preview_only",
        )
    except ValueError as exc:
        assert "output_mode" in str(exc)
    else:
        raise AssertionError("invalid output mode should fail")
