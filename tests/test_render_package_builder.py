from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from bworkflow_sql.db import Database
from bworkflow_sql.render_package_builder import (
    _product_motion_seed,
    _price_transition_sound_effects,
    build_product_recommendation_package as _build_product_recommendation_package,
    product_card_content_fingerprint,
)
from bworkflow_sql.repositories import Repository
from bworkflow_sql.subtitle_rules import split_subtitle_text
from bworkflow_sql.template_config import get_remotion_template_metadata
from bworkflow_sql.utils import now_iso, text_hash


def _contexts_for_builder_test(db: Database, project_id: int) -> list[dict[str, object]]:
    import bworkflow_sql.render_package_builder as builder

    repo = Repository(db)
    products = repo.products(project_id, include_removed=False)
    blocks = {
        str(item.get("owner_uid") or ""): item
        for item in repo.script_blocks(project_id)
        if item.get("script_type") == "product"
    }
    price_blocks = [
        item for item in repo.script_blocks(project_id)
        if item.get("script_type") == "price_transition"
    ]
    assets = repo.asset_bindings(project_id)
    root = Path(db.path).parent / "dynamic-context-assets"
    root.mkdir(parents=True, exist_ok=True)
    contexts: list[dict[str, object]] = []
    for product in products:
        uid = str(product.get("uid") or "")
        block = blocks[uid]
        voice = next(
            item for item in assets
            if item.get("asset_type") == "voice"
            and item.get("uid") == uid
            and int(item.get("script_block_id") or 0) == int(block.get("id") or 0)
        )
        video = next(
            (
                item for item in assets
                if item.get("asset_type") == "video"
                and item.get("uid") == uid
                and Path(str(item.get("path") or "")).is_file()
            ),
            None,
        )
        if video:
            media_kind = "video"
            media_asset = str(video["path"])
        else:
            cover = root / f"{uid}.png"
            cover.write_bytes(b"dynamic cover")
            media_kind = "cover"
            media_asset = str(cover)
        raw_card = str(product.get("product_card_json") or "")
        card = json.loads(raw_card) if raw_card else {}
        raw_data = card.get("dataMap") if isinstance(card.get("dataMap"), dict) else {}
        price_band = builder._matching_price_label(product, price_blocks) or str(
            product.get("price_label") or "test-price-band"
        )
        contexts.append(
            {
                "product_uid": uid,
                "data_map": {
                    "title": str(product.get("title") or uid),
                    "displayPrice": f"{builder._first_number(str(product.get('price_label') or '0')) or 0:g}元",
                    "review": str(raw_data.get("remark") or ""),
                    "priceBandLabel": price_band,
                    "categoryLabel": "test-category",
                    "productMedia": media_asset,
                },
                "specs": card.get("slots") if isinstance(card.get("slots"), list) else [],
                "media_kind": media_kind,
                "media_asset": media_asset,
                "voice_asset": str(voice["path"]),
                "spoken_text": str(block.get("body") or ""),
                "source_script_block_id": int(block.get("id") or 0),
            }
        )
    return contexts


def build_product_recommendation_package(db: Database, **kwargs):
    if kwargs.get("output_mode") == "final_mp4" and "dynamic_product_contexts" not in kwargs:
        project_id = int(kwargs["project_id"])
        kwargs["dynamic_product_contexts"] = _contexts_for_builder_test(db, project_id)
        kwargs["master_snapshot_id"] = f"test-snapshot-{project_id}"
    return _build_product_recommendation_package(db, **kwargs)


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


def test_price_transition_sound_effects_use_the_shared_asset_contract(tmp_path: Path):
    sfx_dir = tmp_path / "1-音效"
    sfx_dir.mkdir()
    for filename in (
        "sfx_title_hit.wav",
        "sfx_progress_tick.wav",
        "sfx_transition_whoosh.wav",
    ):
        (sfx_dir / filename).write_bytes(b"sfx")

    assert _price_transition_sound_effects(tmp_path) == {
        "titleHit": str(sfx_dir / "sfx_title_hit.wav"),
        "itemTick": str(sfx_dir / "sfx_progress_tick.wav"),
        "exitWhoosh": str(sfx_dir / "sfx_transition_whoosh.wav"),
    }


def test_product_motion_seed_is_stable_per_project_account_and_product():
    first = _product_motion_seed(23, "小博", "P001")

    assert first == _product_motion_seed(23, "小博", "P001")
    assert first != _product_motion_seed(23, "小燃", "P001")
    assert first != _product_motion_seed(24, "小博", "P001")


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


def _dynamic_contexts(tmp_path: Path) -> list[dict[str, object]]:
    second_cover = tmp_path / "assets" / "P002-cover.png"
    second_cover.write_bytes(b"cover")
    return [
        {
            "product_uid": "P001",
            "data_map": {
                "title": "Frozen Alpha",
                "displayPrice": "299元",
                "review": "Frozen review",
                "priceBandLabel": "200-300",
                "categoryLabel": "机械键盘",
                "productMedia": str(tmp_path / "assets" / "P001.mp4"),
            },
            "specs": [{"label": "轴体", "value": "银轴"}],
            "media_kind": "video",
            "media_asset": str(tmp_path / "assets" / "P001.mp4"),
            "voice_asset": str(tmp_path / "assets" / "P001.wav"),
            "spoken_text": "Frozen Alpha voice text.",
            "source_script_block_id": 101,
        },
        {
            "product_uid": "P002",
            "data_map": {
                "title": "Frozen Beta",
                "displayPrice": "259元",
                "review": "",
                "priceBandLabel": "200-300",
                "categoryLabel": "机械键盘",
                "productMedia": str(second_cover),
            },
            "specs": [],
            "media_kind": "cover",
            "media_asset": str(second_cover),
            "voice_asset": str(tmp_path / "assets" / "P002.wav"),
            "spoken_text": "Frozen Beta voice text.",
            "source_script_block_id": 102,
        },
    ]


def _encoded_image_bytes(image_format: str = "PNG") -> bytes:
    output = BytesIO()
    Image.new("RGB", (4, 3), (12, 34, 56)).save(output, format=image_format)
    return output.getvalue()


def test_final_mp4_uses_only_frozen_dynamic_contexts_and_no_product_png(
    tmp_path: Path,
    monkeypatch,
):
    import bworkflow_sql.render_package_builder as builder

    db, project_id = _seed_ready_package_data(tmp_path)
    monkeypatch.setattr(builder, "get_audio_duration_seconds", lambda _path: 5.0)
    monkeypatch.setattr(builder, "_price_transition_sound_effects", lambda: {})

    result = build_product_recommendation_package(
        db,
        project_id=project_id,
        account_label="小博",
        output_mode="final_mp4",
        product_order_strategy="stable",
        subtitle_alignment="proportional",
        dynamic_product_contexts=_dynamic_contexts(tmp_path),
        master_snapshot_id="snapshot-frozen-1",
    )

    assert result.missing == []
    assert result.stale_product_images == []
    assert result.package["project"]["masterSnapshotId"] == "snapshot-frozen-1"
    products = [
        item for item in result.package["segments"]
        if item["type"] == "product_recommendation"
    ]
    assert [item["productUid"] for item in products] == ["P001", "P002"]
    first = products[0]
    assert "imageCardAsset" not in first
    assert "image" not in first.get("assetBindingIds", {})
    assert first["productMediaMode"] == "video_preferred"
    assert first["productTitle"] == "Frozen Alpha"
    assert first["spokenText"] == "Frozen Alpha voice text."
    assert first["sourceScriptBlockId"] == 101
    assert first["productCard"]["dataMap"] == {
        "title": "Frozen Alpha",
        "displayPrice": "299元",
        "review": "Frozen review",
        "priceBandLabel": "200-300",
        "categoryLabel": "机械键盘",
    }
    assert first["productCard"]["slots"] == [{"label": "轴体", "value": "银轴"}]
    assert first["priceRangeLabel"] == "200-300"
    assert first["videoAsset"].endswith("P001.mp4")
    assert "coverAsset" not in first["productCard"]
    second = products[1]
    assert "videoAsset" not in second
    assert second["productCard"]["coverAsset"].endswith("P002-cover.png")


def test_final_mp4_rejects_missing_duplicate_and_unknown_dynamic_contexts(
    tmp_path: Path,
    monkeypatch,
):
    import bworkflow_sql.render_package_builder as builder

    db, project_id = _seed_ready_package_data(tmp_path)
    monkeypatch.setattr(builder, "get_audio_duration_seconds", lambda _path: 5.0)
    monkeypatch.setattr(builder, "_price_transition_sound_effects", lambda: {})
    contexts = _dynamic_contexts(tmp_path)

    for malformed in (
        contexts[:1],
        [contexts[0], contexts[0]],
        [*contexts, {**contexts[0], "product_uid": "UNKNOWN"}],
    ):
        result = build_product_recommendation_package(
            db,
            project_id=project_id,
            account_label="小博",
            output_mode="final_mp4",
            product_order_strategy="stable",
            subtitle_alignment="proportional",
            dynamic_product_contexts=malformed,
            master_snapshot_id="snapshot-frozen-1",
        )
        assert any(item["kind"] == "dynamic_product_context" for item in result.missing)
        assert not any(
            item["type"] == "product_recommendation"
            for item in result.package["segments"]
        )


def test_final_context_validation_aggregates_before_any_remote_cover_mutation(
    tmp_path: Path,
    monkeypatch,
):
    import bworkflow_sql.render_package_builder as builder

    db, project_id = _seed_ready_package_data(tmp_path)
    contexts = _contexts_for_builder_test(db, project_id)
    remote_url = "https://img.example.com/covers/P001.jpg"
    contexts[0]["media_kind"] = "cover"
    contexts[0]["media_asset"] = remote_url
    contexts[1]["data_map"] = {
        **contexts[1]["data_map"],
        "title": 123,
        "review": [],
    }
    contexts[1]["specs"] = [
        {"label": ["not", "text"], "value": "valid"},
        {"label": "valid", "value": 42},
    ]
    contexts[1]["spoken_text"] = ["not", "text"]
    contexts[1]["source_script_block_id"] = {}
    contexts[1]["media_kind"] = True
    contexts[1]["media_asset"] = []
    contexts[1]["voice_asset"] = 123
    cache_root = tmp_path / "cover-cache"
    monkeypatch.setattr(builder, "PRODUCT_COVER_CACHE_ROOT", cache_root)
    monkeypatch.setattr(builder, "get_audio_duration_seconds", lambda _path: 5.0)
    target = builder._cover_cache_path(category="keyboard", uid="P001", url=remote_url)
    target.parent.mkdir(parents=True)
    sentinel = b"cache-must-not-be-inspected-before-all-contexts-validate"
    target.write_bytes(sentinel)
    downloads = []
    monkeypatch.setattr(builder, "_download_url_bytes", lambda url: downloads.append(url))

    result = build_product_recommendation_package(
        db,
        project_id=project_id,
        account_label="小博",
        output_mode="final_mp4",
        product_order_strategy="stable",
        subtitle_alignment="proportional",
        dynamic_product_contexts=contexts,
        master_snapshot_id="snapshot-invalid-context",
    )

    issue_fields = {item.get("field") for item in result.missing}
    assert {
        "data_map.title",
        "data_map.review",
        "specs[0].label",
        "specs[1].value",
        "spoken_text",
        "source_script_block_id",
        "media_kind",
        "media_asset",
        "voice_asset",
    }.issubset(issue_fields)
    assert downloads == []
    assert target.read_bytes() == sentinel
    assert not any(
        item["type"] == "product_recommendation"
        for item in result.package["segments"]
    )


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
        product_order_strategy="stable",
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
    assert product_card["templateId"] == "muban-xiaobo-1"
    assert product_card["templateVersion"] == "1.0.2"
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


def test_final_mp4_builds_one_batch_with_deterministic_whole_video_outro(
    tmp_path: Path,
    monkeypatch,
):
    import bworkflow_sql.render_package_builder as builder

    db, project_id = _seed_ready_package_data(tmp_path)
    intro = tmp_path / "raw-intro.mp4"
    closing = tmp_path / "closing.mp3"
    intro.write_bytes(b"intro")
    closing.write_bytes(b"closing")
    outro_dir = tmp_path / "outro-assets" / "1-通用"
    outro_dir.mkdir(parents=True)
    outro_a = outro_dir / "整片结尾-A.mp4"
    outro_b = outro_dir / "整片结尾-B.mp4"
    outro_a.write_bytes(b"outro-a")
    outro_b.write_bytes(b"outro-b")
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO accounts
                (label, account_id, voice_id, voice_name, media_identity, closing_audio_path, enabled, created_at, updated_at)
            VALUES ('小博', 'xiaobo', '', '', '', ?, 1, 'now', 'now')
            """,
            (str(closing),),
        )

    captured_jobs = []

    def fake_grouped(jobs, **kwargs):
        captured_jobs.extend(jobs)
        return [[(0.1, 0.9, job["text"])] for job in jobs]

    monkeypatch.setattr(builder, "get_audio_duration_seconds", lambda _path: 5.0)
    monkeypatch.setattr(builder, "probe_media_duration_seconds", lambda path: 10.0 if "整片结尾" in Path(path).name else 4.0)
    monkeypatch.setattr(builder, "align_subtitle_jobs_with_asr_grouped", fake_grouped)
    monkeypatch.setattr(builder, "DEFAULT_INTRO_ASSET_ROOT", outro_dir.parent)

    result = build_product_recommendation_package(
        db,
        project_id=project_id,
        account_label="小博",
        output_mode="final_mp4",
        product_order_strategy="stable",
        subtitle_alignment="asr",
        intro_video_path=intro,
        intro_video_text="这是无字幕引言。",
        include_outro=True,
        closing_text="感谢观看，评论区留言。",
    )

    assert result.missing == []
    segment_types = [segment["type"] for segment in result.package["segments"]]
    assert segment_types == [
        "intro",
        "price_transition",
        "product_recommendation",
        "product_recommendation",
        "outro",
    ]
    assert [job["label"] for job in captured_jobs] == [
        "intro-raw",
        "price-1",
        "product-P001",
        "product-P002",
        "outro-fixed",
    ]
    outro = result.package["segments"][-1]
    assert Path(outro["videoAsset"]) in {outro_a.resolve(), outro_b.resolve()}
    assert outro["seed"]
    assert "templateId" not in outro
    repeated = build_product_recommendation_package(
        db,
        project_id=project_id,
        account_label="小博",
        output_mode="final_mp4",
        product_order_strategy="stable",
        subtitle_alignment="asr",
        intro_video_path=intro,
        intro_video_text="这是无字幕引言。",
        include_outro=True,
        closing_text="感谢观看，评论区留言。",
    )
    assert repeated.package["segments"][-1]["videoAsset"] == outro["videoAsset"]
    assert repeated.package["segments"][-1]["seed"] == outro["seed"]
    assert all(segment["subtitles"] for segment in result.package["segments"])
    assert result.package["output"]["subtitles"]["styleScope"] == "global"


def test_final_mp4_package_includes_subtitles_from_shared_split_rules(
    tmp_path: Path,
    monkeypatch,
):
    import bworkflow_sql.render_package_builder as builder

    db, project_id = _seed_ready_package_data(tmp_path)
    text = "这台机器加热速度非常快而且操作也很简单适合家里每个人用"
    with db.connect() as conn:
        conn.execute(
            "UPDATE script_blocks SET body=?, text_hash=? WHERE script_type='product' AND owner_uid='P001'",
            (text, text_hash(text)),
        )
        block_id = conn.execute(
            "SELECT id FROM script_blocks WHERE script_type='product' AND owner_uid='P001'"
        ).fetchone()["id"]
        conn.execute(
            "UPDATE asset_bindings SET text_hash=? WHERE asset_type='voice' AND uid='P001' AND script_block_id=?",
            (text_hash(text), block_id),
        )
    monkeypatch.setattr(builder, "get_audio_duration_seconds", lambda _path: 6.0)
    monkeypatch.setattr(builder, "_choose_subtitle_style_id", lambda _package=None: "impact_yellow")

    result = build_product_recommendation_package(
        db,
        project_id=project_id,
        account_label="小博",
        output_mode="final_mp4",
        product_card_template_id="muban-xiaobo-1",
    )

    assert result.package["output"]["subtitles"]["enabled"] is True
    assert result.package["output"]["subtitles"]["styleId"] == "impact_yellow"
    assert result.package["output"]["subtitles"]["styleScope"] == "global"
    product = next(
        segment
        for segment in result.package["segments"]
        if segment["type"] == "product_recommendation" and segment["productUid"] == "P001"
    )
    subtitle_texts = [item["text"] for item in product["subtitles"]]
    assert subtitle_texts == split_subtitle_text(text)
    assert product["subtitles"][0]["start"] == 0.0
    assert product["subtitles"][-1]["end"] == 6.0
    assert all(item["end"] > item["start"] for item in product["subtitles"])


def test_final_mp4_package_can_align_subtitles_with_asr(
    tmp_path: Path,
    monkeypatch,
):
    import bworkflow_sql.render_package_builder as builder

    db, project_id = _seed_ready_package_data(tmp_path)
    text = "Alpha first. Beta second."
    with db.connect() as conn:
        conn.execute(
            "UPDATE script_blocks SET body=?, text_hash=? WHERE script_type='product' AND owner_uid='P001'",
            (text, text_hash(text)),
        )
        block_id = conn.execute(
            "SELECT id FROM script_blocks WHERE script_type='product' AND owner_uid='P001'"
        ).fetchone()["id"]
        conn.execute(
            "UPDATE asset_bindings SET text_hash=? WHERE asset_type='voice' AND uid='P001' AND script_block_id=?",
            (text_hash(text), block_id),
        )
    monkeypatch.setattr(builder, "get_audio_duration_seconds", lambda _path: 6.0)

    def fake_align_grouped(jobs, **_kwargs):
        results = []
        for job in jobs:
            if Path(job["audio_path"]).name != "P001.wav":
                results.append([(0.0, 1.0, job["text"])])
                continue
            assert job["text"] == text
            results.append([(0.25, 1.2, "Alpha first."), (1.45, 2.8, "Beta second.")])
        return results

    monkeypatch.setattr(builder, "align_subtitle_jobs_with_asr_grouped", fake_align_grouped)

    result = build_product_recommendation_package(
        db,
        project_id=project_id,
        account_label="小博",
        output_mode="final_mp4",
        subtitle_alignment="asr",
    )

    product = next(
        segment
        for segment in result.package["segments"]
        if segment["type"] == "product_recommendation" and segment["productUid"] == "P001"
    )
    assert result.package["output"]["subtitles"]["alignment"] == "asr"
    assert product["subtitles"] == [
        {"start": 0.25, "end": 1.2, "text": "Alpha first."},
        {"start": 1.45, "end": 2.8, "text": "Beta second."},
    ]


def test_final_mp4_subtitle_random_pool_has_six_styles():
    import bworkflow_sql.render_package_builder as builder

    assert builder.GLOBAL_SUBTITLE_STYLE_IDS == (
        "classic_white",
        "impact_yellow",
        "panel_white",
        "warm_cream",
        "tech_cyan",
        "orange_energy",
    )


def test_final_mp4_subtitle_style_is_stable_for_same_package_inputs(
    tmp_path: Path,
    monkeypatch,
):
    import bworkflow_sql.render_package_builder as builder

    db, project_id = _seed_ready_package_data(tmp_path)
    monkeypatch.setattr(builder, "get_audio_duration_seconds", lambda _path: 6.0)

    first = build_product_recommendation_package(
        db,
        project_id=project_id,
        account_label="小博",
        output_mode="final_mp4",
        product_card_template_id="muban-xiaobo-1",
    )
    second = build_product_recommendation_package(
        db,
        project_id=project_id,
        account_label="小博",
        output_mode="final_mp4",
        product_card_template_id="muban-xiaobo-1",
    )

    assert first.package["output"]["subtitles"]["styleId"] == second.package["output"]["subtitles"]["styleId"]


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


def test_structured_price_transition_plan_disables_keyword_inference(
    tmp_path: Path,
    monkeypatch,
):
    import json
    import bworkflow_sql.price_transition_plan as plan_module
    import bworkflow_sql.render_package_builder as builder

    monkeypatch.setattr(plan_module, "INTERNAL_WORKSPACE_ROOT", tmp_path / "workspace")
    db, project_id = _seed_ready_package_data(tmp_path)
    body = "两百到三百元重点看水流稳定、档位调节和喷嘴适配，续航也够日常使用，适合正畸人群。"
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
    plan = plan_module.validate_price_transition_plan_set(
        {
            "transitions": [
                {
                    "price_range_label": "200-300",
                    "block_label": "正文",
                    "transition_text": body,
                    "audience": "适合正畸人群",
                    "items": [
                        {"label": "水流稳定", "trigger_text": "水流稳定"},
                        {"label": "档位调节", "trigger_text": "档位调节"},
                        {"label": "喷嘴适配", "trigger_text": "喷嘴适配"},
                    ],
                }
            ]
        }
    )
    target = plan_module.price_transition_plan_path(project_id)
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(builder, "get_audio_duration_seconds", lambda _path: 9.0)

    result = build_product_recommendation_package(
        db,
        project_id=project_id,
        account_label="小博",
        output_mode="final_mp4",
    )

    segment = result.package["segments"][0]
    assert result.missing == []
    assert segment["priceTransitionPlanVersion"] == "1.0.0"
    assert [item["label"] for item in segment["priceTransitionCard"]["items"]] == [
        "水流稳定",
        "档位调节",
        "喷嘴适配",
    ]
    assert "续航" not in segment["priceTransitionCard"]["keyPoints"]


def test_structured_price_transition_plan_hash_mismatch_blocks_package(
    tmp_path: Path,
    monkeypatch,
):
    import json
    import bworkflow_sql.price_transition_plan as plan_module
    import bworkflow_sql.render_package_builder as builder

    monkeypatch.setattr(plan_module, "INTERNAL_WORKSPACE_ROOT", tmp_path / "workspace")
    db, project_id = _seed_ready_package_data(tmp_path)
    plan_body = "两百到三百元重点看水流稳定和档位调节，适合第一次购买的人。"
    plan = plan_module.validate_price_transition_plan_set(
        {
            "transitions": [
                {
                    "price_range_label": "200-300",
                    "block_label": "正文",
                    "transition_text": plan_body,
                    "audience": "适合第一次购买的人",
                    "items": [
                        {"label": "水流稳定", "trigger_text": "水流稳定"},
                        {"label": "档位调节", "trigger_text": "档位调节"},
                    ],
                }
            ]
        }
    )
    target = plan_module.price_transition_plan_path(project_id)
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(builder, "get_audio_duration_seconds", lambda _path: 9.0)

    result = build_product_recommendation_package(
        db,
        project_id=project_id,
        account_label="小博",
        output_mode="final_mp4",
    )

    assert any(item["kind"] == "price_transition_plan" for item in result.missing)
    assert not any(segment["type"] == "price_transition" for segment in result.package["segments"])


def test_structured_price_transition_timings_are_rebased_to_asr_subtitles():
    from bworkflow_sql.render_package_builder import _align_price_transition_card_with_subtitles

    segment = {
        "type": "price_transition",
        "duration": 8.0,
        "transitionText": "这个价位先看水流稳定。接着看档位调节，适合第一次购买的人。",
        "subtitles": [
            {"start": 0.2, "end": 2.8, "text": "这个价位先看水流稳定"},
            {"start": 3.6, "end": 7.4, "text": "接着看档位调节，适合第一次购买的人"},
        ],
        "priceTransitionCard": {
            "items": [
                {"label": "水流稳定", "triggerText": "水流稳定", "timing": {"start": 0.5, "duration": 7.5}},
                {"label": "档位调节", "triggerText": "档位调节", "timing": {"start": 1.0, "duration": 7.0}},
            ]
        },
    }

    _align_price_transition_card_with_subtitles(segment)

    starts = [item["timing"]["start"] for item in segment["priceTransitionCard"]["items"]]
    assert 1.5 < starts[0] < 2.8
    assert 4.0 < starts[1] < 6.0
    assert starts == sorted(starts)


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


def test_build_product_recommendation_package_prefers_selected_template_image_binding(
    tmp_path: Path,
    monkeypatch,
):
    import bworkflow_sql.render_package_builder as builder

    db, project_id = _seed_ready_package_data(tmp_path)
    account_label = get_remotion_template_metadata("muban-xiaobo-2")["account"]
    selected_image = tmp_path / "assets" / account_label / "模板2" / "P001.png"
    _insert_asset(
        db,
        project_id,
        uid="P001",
        asset_type="image",
        path=selected_image,
        account_label=account_label,
    )
    monkeypatch.setattr(builder, "get_audio_duration_seconds", lambda _path: 5.0)

    result = build_product_recommendation_package(
        db,
        project_id=project_id,
        account_label=account_label,
        output_mode="jianying_draft",
        product_card_template_id="muban-xiaobo-2",
    )

    product = next(
        segment
        for segment in result.package["segments"]
        if segment.get("productUid") == "P001"
    )

    assert product["imageCardAsset"] == str(selected_image)
    assert product["productCard"]["templateId"] == "muban-xiaobo-2"


def test_build_product_recommendation_package_normalizes_formal_media_mode(
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
    assert result.package["output"]["productMediaMode"] == "video_preferred"
    assert all(item["productMediaMode"] == "video_preferred" for item in products)
    assert any(item["videoAsset"] for item in products)


def test_build_package_ignores_legacy_image_layout_for_formal_product_card(
    tmp_path: Path,
    monkeypatch,
):
    import bworkflow_sql.render_package_builder as builder

    db, project_id = _seed_ready_package_data(tmp_path)
    image_path = tmp_path / "素材-商品ppt图片" / "keyboard" / "小博" / "模板2" / "P001.png"
    image_path.parent.mkdir(parents=True, exist_ok=True)
    image_path.write_bytes(b"image")
    with db.connect() as conn:
        conn.execute("UPDATE products SET product_card_json='' WHERE project_id=? AND uid='P001'", (project_id,))
        conn.execute(
            "UPDATE asset_bindings SET path=? WHERE project_id=? AND asset_type='image' AND uid='P001'",
            (str(image_path), project_id),
        )
    monkeypatch.setattr(builder, "get_audio_duration_seconds", lambda _path: 5.0)

    result = build_product_recommendation_package(
        db,
        project_id=project_id,
        account_label="小博",
        output_mode="final_mp4",
        product_media_mode="video_preferred",
    )

    product = next(segment for segment in result.package["segments"] if segment.get("productUid") == "P001")

    assert product["productCard"]["templateId"] == "muban-xiaobo-1"
    assert product["videoAsset"]
    assert "displayTemplate" not in product
    assert "displayVideoSlot" not in product
    assert "imageCardAsset" not in product


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


def test_build_product_recommendation_package_shuffles_within_price_groups_by_default(
    tmp_path: Path,
    monkeypatch,
):
    import bworkflow_sql.render_package_builder as builder

    db, project_id = _seed_price_group_package_data(tmp_path)
    monkeypatch.setattr(builder, "get_audio_duration_seconds", lambda _path: 5.0)
    monkeypatch.setattr(builder, "_shuffle_products", lambda products: list(reversed(products)))

    result = build_product_recommendation_package(
        db,
        project_id=project_id,
        account_label="小燃",
        output_mode="final_mp4",
    )

    assert [
        segment.get("productUid") or segment.get("priceRangeLabel")
        for segment in result.package["segments"]
    ] == [
        "200元以下",
        "P002",
        "P001",
        "200-400元",
        "P003",
        "400元以上",
        "P004",
    ]
    assert result.package["output"]["productOrderStrategy"] == "price_segment_shuffle"


def test_build_product_recommendation_package_can_keep_stable_price_group_order(
    tmp_path: Path,
    monkeypatch,
):
    import bworkflow_sql.render_package_builder as builder

    db, project_id = _seed_price_group_package_data(tmp_path)
    monkeypatch.setattr(builder, "get_audio_duration_seconds", lambda _path: 5.0)
    monkeypatch.setattr(builder, "_shuffle_products", lambda products: list(reversed(products)))

    result = build_product_recommendation_package(
        db,
        project_id=project_id,
        account_label="小燃",
        output_mode="final_mp4",
        product_order_strategy="stable",
    )

    assert [
        segment.get("productUid") or segment.get("priceRangeLabel")
        for segment in result.package["segments"]
    ] == [
        "200元以下",
        "P001",
        "P002",
        "200-400元",
        "P003",
        "400元以上",
        "P004",
    ]
    assert result.package["output"]["productOrderStrategy"] == "stable"


def test_build_product_recommendation_package_keeps_top_products_order_before_shuffle(
    tmp_path: Path,
    monkeypatch,
):
    import bworkflow_sql.render_package_builder as builder

    db, project_id = _seed_price_group_package_data(tmp_path)
    monkeypatch.setattr(builder, "get_audio_duration_seconds", lambda _path: 5.0)
    monkeypatch.setattr(builder, "_shuffle_products", lambda products: list(reversed(products)))

    result = build_product_recommendation_package(
        db,
        project_id=project_id,
        account_label="小燃",
        output_mode="final_mp4",
        mode="top",
        top_uids=["P003", "P001"],
    )

    assert [
        segment.get("productUid") or segment.get("priceRangeLabel")
        for segment in result.package["segments"]
    ] == [
        "P003",
        "P001",
        "200元以下",
        "P002",
        "400元以上",
        "P004",
    ]


def test_build_product_recommendation_package_does_not_shuffle_when_no_price_groups(
    tmp_path: Path,
    monkeypatch,
):
    import bworkflow_sql.render_package_builder as builder

    db, project_id = _seed_price_group_package_data(tmp_path)
    with db.connect() as conn:
        conn.execute("UPDATE script_blocks SET active=0 WHERE script_type='price_transition'")
    monkeypatch.setattr(builder, "get_audio_duration_seconds", lambda _path: 5.0)
    monkeypatch.setattr(builder, "_shuffle_products", lambda products: list(reversed(products)))

    result = build_product_recommendation_package(
        db,
        project_id=project_id,
        account_label="小燃",
        output_mode="final_mp4",
    )

    assert [segment["productUid"] for segment in result.package["segments"]] == [
        "P001",
        "P002",
        "P003",
        "P004",
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
    assert "imageCardAsset" not in product
    assert "image" not in product.get("assetBindingIds", {})
    assert product["videoAsset"].endswith("P001.mp4")
    assert "coverAsset" not in product["productCard"]
    assert "fallbackImageAsset" not in product["productCard"]


def test_build_package_overrides_legacy_product_card_template_with_account_remotion_template(
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
    )

    product = next(
        segment
        for segment in result.package["segments"]
        if segment.get("productUid") == "P001"
    )

    assert product["productCard"]["templateId"] == "muban-xiaobo-1"
    assert product["productCard"]["templateVersion"] == "1.0.2"
    assert result.package["output"]["productCardTemplateId"] == "muban-xiaobo-1"
    assert product["productCard"]["coverMediaSlot"]["x"] == 442
    assert product["productCard"]["cardPlacement"] == {
        "x": 0,
        "y": 0,
        "width": 1920,
        "height": 960,
        "anchor": "top",
        "bottomReserve": 120,
    }
    assert product["productCard"]["outputCanvas"] == {"width": 1920, "height": 1080}


def test_build_package_passes_rongrong_2_video_overlay_slot_to_cutme():
    import bworkflow_sql.render_package_builder as builder

    product_card = builder.product_card_payload_for_product(
        {
            "uid": "P001",
            "title": "Alpha",
            "price_label": "199",
            "product_card_json": '{"dataMap":{"title":"Alpha","price":"199","remark":"good"},"slots":[]}',
        },
        project={"category_name": "keyboard"},
        account_label=get_remotion_template_metadata("muban-rongrong-2")["account"],
        product_card_template_id="muban-rongrong-2",
    )

    assert product_card is not None
    assert product_card["templateId"] == "muban-rongrong-2"
    assert product_card["coverMediaSlot"]["height"] == 340
    assert product_card["videoOverlaySlot"]["height"] == 220
    assert product_card["videoOverlaySlot"]["clearSlot"]["height"] == 340


def test_build_package_records_explicit_product_card_template_selection(
    tmp_path: Path,
    monkeypatch,
):
    import bworkflow_sql.render_package_builder as builder

    db, project_id = _seed_ready_package_data(tmp_path)
    account_label = get_remotion_template_metadata("muban-xiaobo-1")["account"]
    monkeypatch.setattr(builder, "get_audio_duration_seconds", lambda _path: 5.0)

    result = build_product_recommendation_package(
        db,
        project_id=project_id,
        account_label=account_label,
        output_mode="final_mp4",
        product_card_template_id="muban-xiaobo-1",
    )

    assert result.package["output"]["productCardTemplate"] == {
        "id": "muban-xiaobo-1",
        "displayName": get_remotion_template_metadata("muban-xiaobo-1")["displayName"],
        "version": "1.0.2",
        "confirmed": True,
        "selectionSource": "explicit",
    }


def test_build_package_marks_account_default_template_as_compatibility_fallback(
    tmp_path: Path,
    monkeypatch,
):
    import bworkflow_sql.render_package_builder as builder

    db, project_id = _seed_ready_package_data(tmp_path)
    account_label = get_remotion_template_metadata("muban-xiaobo-1")["account"]
    monkeypatch.setattr(builder, "get_audio_duration_seconds", lambda _path: 5.0)

    result = build_product_recommendation_package(
        db,
        project_id=project_id,
        account_label=account_label,
        output_mode="final_mp4",
    )

    assert result.package["output"]["productCardTemplate"] == {
        "id": "muban-xiaobo-1",
        "displayName": get_remotion_template_metadata("muban-xiaobo-1")["displayName"],
        "version": "1.0.2",
        "confirmed": False,
        "selectionSource": "account_default_compat",
    }


def test_build_product_recommendation_package_downloads_remote_cover_to_category_cache(
    tmp_path: Path,
    monkeypatch,
):
    import bworkflow_sql.render_package_builder as builder

    db, project_id = _seed_ready_package_data(tmp_path)
    contexts = _contexts_for_builder_test(db, project_id)
    contexts[0]["media_kind"] = "cover"
    contexts[0]["media_asset"] = "https://img.example.com/covers/P001.webp"
    monkeypatch.setattr(builder, "get_audio_duration_seconds", lambda _path: 5.0)
    monkeypatch.setattr(builder, "PRODUCT_COVER_CACHE_ROOT", tmp_path / "cover-cache")
    png_bytes = _encoded_image_bytes()
    download_calls = []

    def download(_url):
        download_calls.append(_url)
        return png_bytes

    monkeypatch.setattr(builder, "_download_url_bytes", download)

    result = build_product_recommendation_package(
        db,
        project_id=project_id,
        account_label="小博",
        output_mode="final_mp4",
        dynamic_product_contexts=contexts,
        master_snapshot_id="snapshot-remote-cover",
    )

    product = next(
        segment
        for segment in result.package["segments"]
        if segment.get("productUid") == "P001"
    )
    cover_path = Path(product["productCard"]["coverAsset"])

    assert cover_path == builder._cover_cache_path(
        category="keyboard",
        uid="P001",
        url="https://img.example.com/covers/P001.webp",
    ).with_suffix(".png")
    assert cover_path.read_bytes() == png_bytes
    assert "cover" not in product["productCard"]["dataMap"]
    assert builder._ensure_remote_cover_cached(
        "https://img.example.com/covers/P001.webp",
        category="keyboard",
        uid="P001",
    ) == cover_path
    assert len(download_calls) == 1


def test_build_product_recommendation_package_downloads_data_map_cover_url(
    tmp_path: Path,
    monkeypatch,
):
    import bworkflow_sql.render_package_builder as builder

    db, project_id = _seed_ready_package_data(tmp_path)
    contexts = _contexts_for_builder_test(db, project_id)
    contexts[0]["media_kind"] = "cover"
    contexts[0]["media_asset"] = "https://img.example.com/covers/P001.jpg"
    monkeypatch.setattr(builder, "get_audio_duration_seconds", lambda _path: 5.0)
    monkeypatch.setattr(builder, "PRODUCT_COVER_CACHE_ROOT", tmp_path / "cover-cache")
    jpeg_bytes = _encoded_image_bytes("JPEG")
    monkeypatch.setattr(builder, "_download_url_bytes", lambda _url: jpeg_bytes)

    result = build_product_recommendation_package(
        db,
        project_id=project_id,
        account_label="小博",
        output_mode="final_mp4",
        dynamic_product_contexts=contexts,
        master_snapshot_id="snapshot-data-map-cover",
    )

    product = next(
        segment
        for segment in result.package["segments"]
        if segment.get("productUid") == "P001"
    )
    cover_path = builder._cover_cache_path(
        category="keyboard",
        uid="P001",
        url="https://img.example.com/covers/P001.jpg",
    )

    assert product["productCard"]["coverAsset"] == str(cover_path)
    assert "cover" not in product["productCard"]["dataMap"]


@pytest.mark.parametrize(
    "payload",
    (
        b"<html><body>200 OK but not an image</body></html>",
        b"RIFF\x10\x00\x00\x00WEBPbroken",
        b"\xff\xd8\xff\xe0truncated-jpeg",
    ),
    ids=("html-200", "broken-webp", "truncated-jpeg"),
)
def test_formal_remote_cover_rejects_invalid_payload_without_cache_or_package(
    tmp_path: Path,
    monkeypatch,
    payload: bytes,
):
    import bworkflow_sql.render_package_builder as builder
    import bworkflow_sql.workflow_service as workflow_service
    from bworkflow_sql.workflow_service import WorkflowService

    db, project_id = _seed_ready_package_data(tmp_path)
    contexts = _contexts_for_builder_test(db, project_id)
    url = "https://img.example.com/covers/P001.jpg"
    contexts[0]["media_kind"] = "cover"
    contexts[0]["media_asset"] = url
    cache_root = tmp_path / "cover-cache"
    output = tmp_path / "formal-render-package.json"
    monkeypatch.setattr(builder, "get_audio_duration_seconds", lambda _path: 5.0)
    monkeypatch.setattr(builder, "_price_transition_sound_effects", lambda: {})
    monkeypatch.setattr(builder, "PRODUCT_COVER_CACHE_ROOT", cache_root)
    monkeypatch.setattr(builder, "_download_url_bytes", lambda _url: payload)
    monkeypatch.setattr(workflow_service, "_product_card_text_capacity_issues", lambda **_kwargs: [])

    result = WorkflowService(db).prepare_product_recommendation_output(
        project_id,
        account_label="小博",
        output_mode="final_mp4",
        product_order_strategy="stable",
        package_output_path=output,
        subtitle_alignment="proportional",
        dynamic_product_contexts=contexts,
        master_snapshot_id="snapshot-invalid-cover",
    )

    assert result["ok"] is False
    assert any(
        item["kind"] == "product_cover"
        and item["uid"] == "P001"
        and "P001" in item["message"]
        and url in item["message"]
        for item in result["missing"]
    )
    target = builder._cover_cache_path(category="keyboard", uid="P001", url=url)
    assert not target.exists()
    assert not target.with_suffix(".png").exists()
    assert not list(target.parent.glob("*.tmp"))
    assert not output.exists()


def test_formal_remote_cover_download_failure_is_uid_scoped_missing(
    tmp_path: Path,
    monkeypatch,
):
    import bworkflow_sql.render_package_builder as builder

    db, project_id = _seed_ready_package_data(tmp_path)
    contexts = _contexts_for_builder_test(db, project_id)
    contexts[0]["media_kind"] = "cover"
    contexts[0]["media_asset"] = "https://img.example.com/covers/P001.jpg"
    monkeypatch.setattr(builder, "get_audio_duration_seconds", lambda _path: 5.0)
    monkeypatch.setattr(builder, "PRODUCT_COVER_CACHE_ROOT", tmp_path / "cover-cache")

    def fail_download(_url):
        raise OSError("network unavailable")

    monkeypatch.setattr(builder, "_download_url_bytes", fail_download)

    result = build_product_recommendation_package(
        db,
        project_id=project_id,
        account_label="小博",
        output_mode="final_mp4",
        dynamic_product_contexts=contexts,
        master_snapshot_id="snapshot-download-failure",
    )

    assert any(
        item["kind"] == "product_cover"
        and item["uid"] == "P001"
        and "failed to cache product cover" in item["message"]
        for item in result.missing
    )


def test_remote_cover_atomic_replace_failure_cleans_temporary_file(
    tmp_path: Path,
    monkeypatch,
):
    import bworkflow_sql.render_package_builder as builder

    url = "https://img.example.com/covers/P001.png"
    cache_root = tmp_path / "cover-cache"
    monkeypatch.setattr(builder, "PRODUCT_COVER_CACHE_ROOT", cache_root)
    monkeypatch.setattr(builder, "_download_url_bytes", lambda _url: _encoded_image_bytes())

    def fail_replace(self, target):
        raise OSError("replace denied")

    monkeypatch.setattr(Path, "replace", fail_replace)

    with pytest.raises(
        builder.ProductCoverMaterializationError,
        match=r"P001.*https://img\.example\.com/covers/P001\.png.*replace denied",
    ):
        builder._ensure_remote_cover_cached(url, category="keyboard", uid="P001")

    target = builder._cover_cache_path(category="keyboard", uid="P001", url=url)
    assert not target.exists()
    assert not list(target.parent.glob("*.tmp"))


def test_corrupt_remote_cover_cache_is_replaced_by_valid_redownload(
    tmp_path: Path,
    monkeypatch,
):
    import bworkflow_sql.render_package_builder as builder

    url = "https://img.example.com/covers/P001.jpg"
    cache_root = tmp_path / "cover-cache"
    monkeypatch.setattr(builder, "PRODUCT_COVER_CACHE_ROOT", cache_root)
    target = builder._cover_cache_path(category="keyboard", uid="P001", url=url)
    target.parent.mkdir(parents=True)
    target.write_bytes(b"<html>corrupt cache</html>")
    valid_jpeg = _encoded_image_bytes("JPEG")
    downloads = []

    def download(_url):
        downloads.append(_url)
        return valid_jpeg

    monkeypatch.setattr(builder, "_download_url_bytes", download)

    resolved = builder._ensure_remote_cover_cached(url, category="keyboard", uid="P001")

    assert resolved == target
    assert target.read_bytes() == valid_jpeg
    assert downloads == [url]


def test_remote_cover_cache_read_error_preserves_candidate(
    tmp_path: Path,
    monkeypatch,
):
    import bworkflow_sql.render_package_builder as builder

    url = "https://img.example.com/covers/P001.jpg"
    cache_root = tmp_path / "cover-cache"
    monkeypatch.setattr(builder, "PRODUCT_COVER_CACHE_ROOT", cache_root)
    target = builder._cover_cache_path(category="keyboard", uid="P001", url=url)
    target.parent.mkdir(parents=True)
    target.write_bytes(b"unreadable-sentinel")
    original_read_bytes = Path.read_bytes

    def fail_target_read(path):
        if path == target:
            raise PermissionError("read denied")
        return original_read_bytes(path)

    downloads = []
    monkeypatch.setattr(Path, "read_bytes", fail_target_read)
    monkeypatch.setattr(builder, "_download_url_bytes", lambda value: downloads.append(value))

    with pytest.raises(builder.ProductCoverMaterializationError, match="read denied"):
        builder._ensure_remote_cover_cached(url, category="keyboard", uid="P001")

    assert target.exists()
    assert downloads == []


def test_failed_remote_cover_call_does_not_delete_concurrent_valid_cache(
    tmp_path: Path,
    monkeypatch,
):
    import bworkflow_sql.render_package_builder as builder

    url = "https://img.example.com/covers/P001.jpg"
    cache_root = tmp_path / "cover-cache"
    monkeypatch.setattr(builder, "PRODUCT_COVER_CACHE_ROOT", cache_root)
    target = builder._cover_cache_path(category="keyboard", uid="P001", url=url)
    valid_jpeg = _encoded_image_bytes("JPEG")

    def interleaved_failure(_url):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(valid_jpeg)
        raise OSError("first caller failed after another caller completed")

    monkeypatch.setattr(builder, "_download_url_bytes", interleaved_failure)

    with pytest.raises(builder.ProductCoverMaterializationError):
        builder._ensure_remote_cover_cached(url, category="keyboard", uid="P001")

    assert target.read_bytes() == valid_jpeg


def test_two_successful_remote_cover_calls_share_atomic_cache(
    tmp_path: Path,
    monkeypatch,
):
    from concurrent.futures import ThreadPoolExecutor
    import bworkflow_sql.render_package_builder as builder

    url = "https://img.example.com/covers/P001.png"
    cache_root = tmp_path / "cover-cache"
    valid_png = _encoded_image_bytes("PNG")
    monkeypatch.setattr(builder, "PRODUCT_COVER_CACHE_ROOT", cache_root)
    monkeypatch.setattr(builder, "_download_url_bytes", lambda _url: valid_png)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda _index: builder._ensure_remote_cover_cached(
                    url,
                    category="keyboard",
                    uid="P001",
                ),
                range(2),
            )
        )

    assert results[0] == results[1]
    assert results[0].read_bytes() == valid_png
    assert not list(results[0].parent.glob("*.tmp"))


def test_remote_cover_cache_path_changes_when_url_changes_but_suffix_does_not(
    tmp_path: Path,
    monkeypatch,
):
    import bworkflow_sql.render_package_builder as builder

    monkeypatch.setattr(builder, "PRODUCT_COVER_CACHE_ROOT", tmp_path / "cover-cache")

    old_path = builder._cover_cache_path(
        category="speaker",
        uid="ZMYX005",
        url="https://cdn.example.com/covers/old-cover.jpg",
    )
    new_path = builder._cover_cache_path(
        category="speaker",
        uid="ZMYX005",
        url="https://cdn.example.com/covers/new-cover.jpg",
    )

    assert old_path != new_path
    assert old_path.name.startswith("ZMYX005-")
    assert new_path.name.startswith("ZMYX005-")
    assert old_path.suffix == ".jpg"
    assert new_path.suffix == ".jpg"


def test_remote_cover_cache_decodes_webp_as_png_when_url_ends_with_jpg(
    tmp_path: Path,
    monkeypatch,
):
    import bworkflow_sql.render_package_builder as builder

    monkeypatch.setattr(builder, "PRODUCT_COVER_CACHE_ROOT", tmp_path / "cover-cache")
    webp_buffer = BytesIO()
    Image.new("RGBA", (3, 2), (12, 34, 56, 128)).save(webp_buffer, format="WEBP", lossless=True)
    webp_bytes = webp_buffer.getvalue()
    monkeypatch.setattr(builder, "_download_url_bytes", lambda _url: webp_bytes)

    cover_path = builder._ensure_remote_cover_cached(
        "https://img.example.com/covers/P001.jpg",
        category="keyboard",
        uid="P001",
    )

    assert cover_path.suffix == ".png"
    with Image.open(cover_path) as decoded:
        assert decoded.format == "PNG"
        assert decoded.size == (3, 2)


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


def test_build_product_recommendation_package_skips_missing_price_script(
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

    assert result.missing == []
    assert [segment["type"] for segment in result.package["segments"]] == [
        "product_recommendation",
        "product_recommendation",
    ]
    assert [segment["productUid"] for segment in result.package["segments"]] == ["P001", "P002"]


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
