from __future__ import annotations

from pathlib import Path

from bworkflow_sql.db import Database
from bworkflow_sql.render_package_builder import (
    build_product_recommendation_package,
    product_card_content_fingerprint,
)
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


def _seed_price_group_package_data(tmp_path: Path) -> tuple[Database, int]:
    db = Database(tmp_path / "price-groups.db")
    repo = Repository(db)
    project_id = db.upsert_project(
        {
            "name": "earbuds",
            "category_name": "earbuds",
            "scheme_id": "scheme-price-groups",
        }
    )
    cover_root = tmp_path / "covers"
    cover_root.mkdir(parents=True, exist_ok=True)
    items = [
        ("P001", "Alpha", "199", "200元以下"),
        ("P002", "Beta", "168", "200元以下"),
        ("P003", "Gamma", "279", "200-400元"),
        ("P004", "Delta", "619", "400元以上"),
    ]
    repo.upsert_products_from_master(
        project_id,
        [
            {
                "uid": uid,
                "title": title,
                "price_label": price,
                "cover": str(cover_root / f"{uid}.png"),
                "remark": f"{title} remark.",
                "spec": {"weight": "4g"},
                "product_card_template_id": "xiaoran1",
            }
            for uid, title, price, _range_label in items
        ],
    )
    assets = tmp_path / "assets"
    price_labels = ["200元以下", "200-400元", "400元以上"]
    for label in price_labels:
        body = f"{label} transition."
        block_id = _insert_script(
            db,
            project_id,
            script_type="price_transition",
            price_range_label=label,
            body=body,
        )
        _insert_asset(
            db,
            project_id,
            uid="PRICE_TRANSITION",
            asset_type="voice",
            path=assets / f"price-{label}.wav",
            account_label="小燃",
            script_block_id=block_id,
            block_label=label,
            block_hash=text_hash(body),
        )
    for uid, title, _price, _range_label in items:
        body = f"{title} recommendation."
        block_id = _insert_script(
            db,
            project_id,
            script_type="product",
            owner_uid=uid,
            body=body,
        )
        _insert_asset(
            db,
            project_id,
            uid=uid,
            asset_type="image",
            path=assets / f"{uid}.png",
            account_label="小燃",
        )
        _insert_asset(
            db,
            project_id,
            uid=uid,
            asset_type="voice",
            path=assets / f"{uid}.wav",
            account_label="小燃",
            script_block_id=block_id,
            block_label="正文",
            block_hash=text_hash(body),
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
    price_transition = result.package["segments"][0]
    assert price_transition["priceTransitionCard"]["rangeLabel"] == "200-300"
    assert price_transition["priceTransitionCard"]["keyPoints"]
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
    assert "fallbackImageAsset" not in product_card
    assert product_card["slots"] == [
        {"label": "switch", "value": "silver"},
        {"label": "battery", "value": "4000mAh"},
    ]
    assert "productCard" not in products[1]
    assert all(Path(segment["voiceAsset"]).is_absolute() for segment in result.package["segments"])
    assert all(Path(segment["imageCardAsset"]).is_absolute() for segment in products)


def test_price_transition_card_uses_fill_slots_with_voice_timing(
    tmp_path: Path,
    monkeypatch,
):
    import bworkflow_sql.render_package_builder as builder

    db, project_id = _seed_ready_package_data(tmp_path)
    body = "两百到三百元这个价位，重点看品牌完成度和音质细节，通话、连接和漏音控制，也会更稳，适合准备长期用的人。"
    with db.connect() as conn:
        block_id = conn.execute(
            "SELECT id FROM script_blocks WHERE script_type='price_transition'"
        ).fetchone()["id"]
        conn.execute(
            """
            UPDATE script_blocks
            SET body=?, text_hash=?
            WHERE id=?
            """,
            (body, text_hash(body), block_id),
        )
        conn.execute(
            """
            UPDATE asset_bindings
            SET text_hash=?
            WHERE asset_type='voice' AND script_block_id=?
            """,
            (text_hash(body), block_id),
        )
    monkeypatch.setattr(builder, "get_audio_duration_seconds", lambda _path: 10.0)

    result = build_product_recommendation_package(
        db,
        project_id=project_id,
        account_label="小博",
        output_mode="final_mp4",
    )

    card = result.package["segments"][0]["priceTransitionCard"]
    labels = [item["label"] for item in card["items"]]
    starts = [item["timing"]["start"] for item in card["items"]]

    assert result.missing == []
    assert labels == ["品牌完成度", "音质细节", "通话 / 连接 / 漏音控制"]
    assert [item["triggerText"] for item in card["items"]] == ["品牌完成度", "音质细节", "通话"]
    assert starts == sorted(starts)
    assert all(0 <= start < 10.0 for start in starts)
    assert card["keyPoints"] == labels
    assert card["visualEvents"] == [
        {
            "target": "price_param_01",
            "text": "品牌完成度",
            "trigger_text": "品牌完成度",
            "timing": card["items"][0]["timing"],
        },
        {
            "target": "price_param_02",
            "text": "音质细节",
            "trigger_text": "音质细节",
            "timing": card["items"][1]["timing"],
        },
        {
            "target": "price_param_03",
            "text": "通话 / 连接 / 漏音控制",
            "trigger_text": "通话",
            "timing": card["items"][2]["timing"],
        },
    ]
    assert body not in card.values()


def test_price_transition_card_fallback_stays_as_short_parameter_slots(
    tmp_path: Path,
    monkeypatch,
):
    import bworkflow_sql.render_package_builder as builder

    db, project_id = _seed_ready_package_data(tmp_path)
    body = "下面是 400 元以上这个旗舰区间，基本上都是各品牌的高端型号，有侧重睡眠的，有侧重玩法的，预算充足的人可以看看。"
    with db.connect() as conn:
        block_id = conn.execute(
            "SELECT id FROM script_blocks WHERE script_type='price_transition'"
        ).fetchone()["id"]
        conn.execute(
            "UPDATE script_blocks SET body=?, text_hash=? WHERE id=?",
            (body, text_hash(body), block_id),
        )
        conn.execute(
            "UPDATE asset_bindings SET text_hash=? WHERE asset_type='voice' AND script_block_id=?",
            (text_hash(body), block_id),
        )
    monkeypatch.setattr(builder, "get_audio_duration_seconds", lambda _path: 7.5)

    result = build_product_recommendation_package(
        db,
        project_id=project_id,
        account_label="小博",
        output_mode="final_mp4",
    )

    card = result.package["segments"][0]["priceTransitionCard"]

    assert [item["label"] for item in card["items"]] == ["高端型号", "睡眠场景", "玩法"]
    assert all(len(item["label"]) <= 8 for item in card["items"])
    assert "基本上都是各品牌的高端型" not in card["keyPoints"]


def test_build_product_recommendation_package_reports_stale_product_image(
    tmp_path: Path,
    monkeypatch,
):
    import bworkflow_sql.render_package_builder as builder

    db, project_id = _seed_ready_package_data(tmp_path)
    monkeypatch.setattr(builder, "get_audio_duration_seconds", lambda _path: 5.0)
    with db.connect() as conn:
        conn.execute(
            "UPDATE asset_bindings SET text_hash='old-card-fingerprint' WHERE asset_type='image' AND uid='P001'"
        )

    result = build_product_recommendation_package(
        db,
        project_id=project_id,
        account_label="小博",
        output_mode="jianying_draft",
    )

    stale = result.stale_product_images
    product = next(
        segment
        for segment in result.package["segments"]
        if segment.get("productUid") == "P001"
    )

    assert result.missing == []
    assert len(stale) == 1
    assert stale[0]["kind"] == "stale_product_image"
    assert stale[0]["uid"] == "P001"
    assert stale[0]["stored_fingerprint"] == "old-card-fingerprint"
    assert stale[0]["expected_fingerprint"] == product_card_content_fingerprint(
        {"uid": "P001", "title": "Alpha Keyboard", "price_label": "200-300"},
        product["productCard"],
    )
    assert product["productCardFingerprint"] == stale[0]["expected_fingerprint"]


def test_build_product_recommendation_package_can_force_cover_only_media(
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
        output_mode="final_mp4",
        product_media_mode="cover_only",
    )

    products = [
        segment
        for segment in result.package["segments"]
        if segment["type"] == "product_recommendation"
    ]
    assert result.package["output"]["productMediaMode"] == "cover_only"
    assert products[0]["productMediaMode"] == "cover_only"
    assert products[0]["videoAsset"] is None


def test_build_product_recommendation_package_orders_price_groups_after_top_products(
    tmp_path: Path,
    monkeypatch,
):
    import bworkflow_sql.render_package_builder as builder

    db, project_id = _seed_price_group_package_data(tmp_path)
    monkeypatch.setattr(builder, "get_audio_duration_seconds", lambda _path: 5.0)

    result = build_product_recommendation_package(
        db,
        project_id=project_id,
        account_label="小燃",
        output_mode="final_mp4",
        mode="top",
        top_uids=["P003", "P001"],
    )

    assert result.missing == []
    assert [
        (
            segment["type"],
            segment.get("productUid") or segment.get("priceRangeLabel"),
        )
        for segment in result.package["segments"]
    ] == [
        ("product_recommendation", "P003"),
        ("product_recommendation", "P001"),
        ("price_transition", "200元以下"),
        ("product_recommendation", "P002"),
        ("price_transition", "400元以上"),
        ("product_recommendation", "P004"),
    ]


def test_build_final_mp4_package_uses_product_card_without_legacy_image(
    tmp_path: Path,
    monkeypatch,
):
    import bworkflow_sql.render_package_builder as builder

    db, project_id = _seed_ready_package_data(tmp_path)
    monkeypatch.setattr(builder, "get_audio_duration_seconds", lambda _path: 5.0)
    with db.connect() as conn:
        conn.execute("UPDATE asset_bindings SET status='missing' WHERE asset_type='image' AND uid='P001'")
        account_label = conn.execute(
            "SELECT account_label FROM asset_bindings WHERE asset_type='voice' AND uid='P001'"
        ).fetchone()[0]

    result = build_product_recommendation_package(
        db,
        project_id=project_id,
        account_label=account_label,
        output_mode="final_mp4",
    )

    product = next(
        segment
        for segment in result.package["segments"]
        if segment.get("productUid") == "P001"
    )

    assert not any(
        item["kind"] == "product_image" and item["uid"] == "P001"
        for item in result.missing
    )
    assert product["imageCardAsset"] is None
    assert product["assetBindingIds"]["image"] is None
    assert product["productCard"]["coverAsset"].endswith("P001.png")
    assert "fallbackImageAsset" not in product["productCard"]


def test_build_product_recommendation_package_downloads_remote_cover_to_category_cache(
    tmp_path: Path,
    monkeypatch,
):
    import bworkflow_sql.render_package_builder as builder

    db, project_id = _seed_ready_package_data(tmp_path)
    with db.connect() as conn:
        conn.execute(
            "UPDATE products SET product_card_json=? WHERE project_id=? AND uid='P001'",
            (
                '{"coverAsset":"https://img.example.com/covers/P001.webp","dataMap":{"title":"Alpha Keyboard","price":"200-300","cover":"https://img.example.com/covers/P001.webp"},"slots":[]}',
                project_id,
            ),
        )
    monkeypatch.setattr(builder, "get_audio_duration_seconds", lambda _path: 5.0)
    monkeypatch.setattr(builder, "PRODUCT_COVER_CACHE_ROOT", tmp_path / "cover-cache")
    monkeypatch.setattr(builder, "_download_url_bytes", lambda _url: b"cover-bytes")

    result = build_product_recommendation_package(
        db,
        project_id=project_id,
        account_label="小博",
        output_mode="final_mp4",
    )

    product = next(
        segment
        for segment in result.package["segments"]
        if segment.get("productUid") == "P001"
    )
    cover_path = Path(product["productCard"]["coverAsset"])

    assert cover_path == tmp_path / "cover-cache" / "keyboard" / "P001.webp"
    assert cover_path.read_bytes() == b"cover-bytes"
    assert product["productCard"]["dataMap"]["cover"] == str(cover_path)


def test_build_product_recommendation_package_downloads_data_map_cover_url(
    tmp_path: Path,
    monkeypatch,
):
    import bworkflow_sql.render_package_builder as builder

    db, project_id = _seed_ready_package_data(tmp_path)
    with db.connect() as conn:
        conn.execute(
            "UPDATE products SET product_card_json=? WHERE project_id=? AND uid='P001'",
            (
                '{"dataMap":{"title":"Alpha Keyboard","price":"200-300","cover":"https://img.example.com/covers/P001.jpg"},"slots":[]}',
                project_id,
            ),
        )
    monkeypatch.setattr(builder, "get_audio_duration_seconds", lambda _path: 5.0)
    monkeypatch.setattr(builder, "PRODUCT_COVER_CACHE_ROOT", tmp_path / "cover-cache")
    monkeypatch.setattr(builder, "_download_url_bytes", lambda _url: b"cover-bytes")

    result = build_product_recommendation_package(
        db,
        project_id=project_id,
        account_label="小博",
        output_mode="final_mp4",
    )

    product = next(
        segment
        for segment in result.package["segments"]
        if segment.get("productUid") == "P001"
    )
    cover_path = tmp_path / "cover-cache" / "keyboard" / "P001.jpg"

    assert product["productCard"]["coverAsset"] == str(cover_path)
    assert product["productCard"]["dataMap"]["cover"] == str(cover_path)


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
