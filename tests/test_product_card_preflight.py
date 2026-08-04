from __future__ import annotations

import json
from pathlib import Path

import pytest

import bworkflow_sql.media_readiness as media_readiness_module
import bworkflow_sql.product_card_preflight as preflight_module
import bworkflow_sql.workflow_service as workflow_service_module
from bworkflow_sql.db import Database
from bworkflow_sql.dynamic_product_card import (
    category_leaf_name,
    format_display_price,
    match_price_band,
)
from bworkflow_sql.master_contracts import (
    MasterCategoryIdentity,
    MasterContractError,
    MasterMoney,
    MasterPriceRange,
    MasterProductCard,
    MasterSchemeIdentity,
    MasterSchemeSnapshot,
    MasterSnapshotProduct,
    MasterSpecSlot,
    MasterWorkspace,
)
from bworkflow_sql.product_card_preflight import dynamic_product_card_preflight
from bworkflow_sql.media_readiness import audit_product_video_media, snapshot_verified_product_videos
from bworkflow_sql.repositories import Repository
from bworkflow_sql.utils import now_iso, text_hash
from bworkflow_sql.workflow_service import WorkflowService


@pytest.fixture
def product_card_metadata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from bworkflow_sql import template_config

    path = tmp_path / "product-card-templates.json"
    path.write_text(
        json.dumps(
            {
                "slotRegistry": {
                    "title": {"type": "text", "source": "dataMap.title"},
                    "displayPrice": {"type": "text", "source": "dataMap.displayPrice"},
                    "specs": {"type": "label_value_list", "source": "slots"},
                    "review": {"type": "text", "source": "dataMap.review"},
                    "priceBandLabel": {"type": "text", "source": "dataMap.priceBandLabel"},
                    "categoryLabel": {"type": "text", "source": "dataMap.categoryLabel"},
                    "productMedia": {"type": "media", "source": "coverAsset"},
                },
                "templates": [
                    {
                        "templateId": "muban-test-1",
                        "displayName": "Test template",
                        "account": "xiaobo",
                        "slotDeclarations": [
                            {"key": "title", "required": True},
                            {"key": "displayPrice", "required": True},
                            {"key": "specs", "required": False, "emptyPolicy": "preserve"},
                            {"key": "review", "required": False, "emptyPolicy": "preserve"},
                            {"key": "priceBandLabel", "required": True},
                            {"key": "categoryLabel", "required": False, "emptyPolicy": "preserve"},
                            {"key": "productMedia", "required": True},
                        ],
                    },
                    {
                        "templateId": "muban-test-required-details",
                        "displayName": "Required details template",
                        "account": "xiaobo",
                        "slotDeclarations": [
                            {"key": "title", "required": True},
                            {"key": "displayPrice", "required": True},
                            {"key": "specs", "required": True},
                            {"key": "review", "required": True},
                            {"key": "priceBandLabel", "required": True},
                            {"key": "categoryLabel", "required": True},
                            {"key": "productMedia", "required": True},
                        ],
                    },
                    {
                        "templateId": "muban-other-1",
                        "displayName": "Other account template",
                        "account": "other-account",
                        "slotDeclarations": [
                            {"key": "title", "required": True},
                            {"key": "displayPrice", "required": True},
                            {"key": "priceBandLabel", "required": True},
                            {"key": "productMedia", "required": True},
                        ],
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(template_config, "REMOTION_TEMPLATE_METADATA_PATH", path)
    template_config._remotion_template_contract.cache_clear()
    template_config._remotion_template_metadata.cache_clear()
    yield path
    template_config._remotion_template_contract.cache_clear()
    template_config._remotion_template_metadata.cache_clear()


class FakeMasterAdapter:
    def __init__(self, snapshot: MasterSchemeSnapshot | None = None, error: Exception | None = None):
        self.snapshot = snapshot
        self.error = error
        self.calls: list[tuple[str, str, bool]] = []

    def fetch_scheme_snapshot(self, workspace_id: str, scheme_id: str, *, force_refresh: bool = False):
        self.calls.append((workspace_id, scheme_id, force_refresh))
        if self.error:
            raise self.error
        assert self.snapshot is not None
        return self.snapshot


def _snapshot_product(
    uid: str,
    *,
    title: str = "Air Fryer",
    amount: str | None = "59.5",
    cover: str | None = "https://example.test/cover.jpg",
    review: str | None = "Good value",
    specs: tuple[MasterSpecSlot, ...] = (),
) -> MasterSnapshotProduct:
    return MasterSnapshotProduct(
        master_item_id=f"master-{uid}",
        uid=uid,
        title=title,
        sort_order=1,
        price=MasterMoney(amount=amount, currency="CNY", source="manual", display=amount or ""),
        card=MasterProductCard(
            cover_url=cover,
            remark=review,
            spec_slots=specs,
            template_id=None,
        ),
        tags=(),
        featured=False,
        source_updated_at=None,
    )


def _snapshot(
    products: tuple[MasterSnapshotProduct, ...],
    *,
    ranges: tuple[MasterPriceRange, ...] = (
        MasterPriceRange(min_amount="0", max_amount="100", label="0-100"),
        MasterPriceRange(min_amount="100", max_amount="200", label="100-200"),
    ),
    category_name: str = "Kitchen",
) -> MasterSchemeSnapshot:
    return MasterSchemeSnapshot(
        schema_version="1.0.0",
        generated_at_utc="2026-07-22T00:00:00Z",
        snapshot_id="sha256:" + "a" * 64,
        workspace=MasterWorkspace(id="workspace-1", name="Workspace", slug=None),
        scheme=MasterSchemeIdentity(
            id="scheme-1",
            name="Scheme",
            category=MasterCategoryIdentity(id="category-1", name=category_name),
            updated_at=None,
        ),
        price_ranges=ranges,
        products=products,
    )


def _seed_project(
    tmp_path: Path,
    *,
    uids: tuple[str, ...] = ("P001",),
    with_voice: bool = True,
    with_video: bool = False,
    category_name: str = "Home - Kitchen - Air Fryers",
) -> tuple[Database, int]:
    db = Database(tmp_path / "preflight.db")
    repo = Repository(db)
    project_id = db.upsert_project(
        {
            "name": "air-fryers",
            "workspace_id": "workspace-1",
            "scheme_id": "scheme-1",
            "category_name": category_name,
            "video_root": str(tmp_path / "video"),
        }
    )
    ts = now_iso()
    with db.connect() as conn:
        for index, uid in enumerate(uids, start=1):
            conn.execute(
                """
                INSERT INTO products
                    (project_id, uid, title, price_label, sort_order, master_item_id,
                     product_card_json, active, removed_from_master, created_at, updated_at)
                VALUES (?, ?, ?, 'legacy transition text', ?, ?, '{}', 1, 0, ?, ?)
                """,
                (project_id, uid, f"Local {uid}", index, f"master-{uid}", ts, ts),
            )
            body = f"Script for {uid}"
            cursor = conn.execute(
                """
                INSERT INTO script_blocks
                    (project_id, script_type, owner_uid, block_label, body, text_hash,
                     active, created_at, updated_at)
                VALUES (?, 'product', ?, 'main', ?, ?, 1, ?, ?)
                """,
                (project_id, uid, body, text_hash(body), ts, ts),
            )
            block_id = int(cursor.lastrowid)
            if with_voice:
                voice = tmp_path / "voice" / f"{uid}.wav"
                voice.parent.mkdir(parents=True, exist_ok=True)
                voice.write_bytes(b"voice")
                conn.execute(
                    """
                    INSERT INTO asset_bindings
                        (project_id, uid, script_block_id, asset_type, account_label,
                         text_hash, path, status, source_kind, created_at, updated_at)
                    VALUES (?, ?, ?, 'voice', 'xiaobo', ?, ?, 'ready', 'test', ?, ?)
                    """,
                    (project_id, uid, block_id, text_hash(body), str(voice), ts, ts),
                )
            if with_video:
                video = tmp_path / "video" / f"{uid}.mp4"
                video.parent.mkdir(parents=True, exist_ok=True)
                video.write_bytes(b"video")
                conn.execute(
                    """
                    INSERT INTO asset_bindings
                        (project_id, uid, asset_type, path, status, source_kind, created_at, updated_at)
                    VALUES (?, ?, 'video', ?, 'ready', 'test', ?, ?)
                    """,
                    (project_id, uid, str(video), ts, ts),
                )
    return db, project_id


def _run(
    db: Database,
    project_id: int,
    snapshot: MasterSchemeSnapshot,
) -> dict:
    return dynamic_product_card_preflight(
        db,
        project_id=project_id,
        account_label="xiaobo",
        product_card_template_id="muban-test-1",
        master_contracts=FakeMasterAdapter(snapshot),
        probe_video=lambda _path: {"ok": True, "duration": 1.0, "has_video": True},
    )


def test_media_snapshot_rechecks_selected_file_and_records_fingerprint(tmp_path: Path):
    video = tmp_path / "P001.mp4"
    video.write_bytes(b"verified-video")
    snapshot = snapshot_verified_product_videos(
        {"selected_paths": {"P001": str(video)}},
        probe_video=lambda _path: {"ok": True, "duration": 1.0, "has_video": True},
    )

    assert snapshot["ok"] is True
    assert snapshot["items"][0]["uid"] == "P001"
    assert len(snapshot["items"][0]["sha256"]) == 64

    video.unlink()
    missing = snapshot_verified_product_videos(
        {"selected_paths": {"P001": str(video)}},
        probe_video=lambda _path: {"ok": True},
    )

    assert missing["ok"] is False
    assert missing["issues"][0]["code"] == "product_video_changed_or_unavailable"


def test_decimal_price_formatting_is_strict_and_rounds_half_up():
    assert format_display_price("59") == "59元"
    assert format_display_price("59.5") == "60元"
    with pytest.raises(ValueError):
        format_display_price("NaN")
    with pytest.raises(ValueError):
        format_display_price("Infinity")
    with pytest.raises(ValueError):
        format_display_price("-0.01")
    with pytest.raises(ValueError):
        format_display_price("1_0")
    with pytest.raises(ValueError):
        format_display_price("1e2")


def test_category_uses_last_non_empty_segment_and_price_boundary_uses_first_range():
    ranges = (
        MasterPriceRange(min_amount="0", max_amount="100", label="first"),
        MasterPriceRange(min_amount="100", max_amount="200", label="second"),
    )
    assert category_leaf_name("Home - Kitchen - Air Fryers - ") == "Air Fryers"
    assert match_price_band("100", ranges) == "first"


def test_open_ended_price_ranges_match_and_both_open_ends_are_invalid():
    ranges = (
        MasterPriceRange(min_amount=None, max_amount="100", label="under-100"),
        MasterPriceRange(min_amount="500", max_amount=None, label="over-500"),
    )
    assert match_price_band("0", ranges) == "under-100"
    assert match_price_band("100", ranges) == "under-100"
    assert match_price_band("500", ranges) == "over-500"
    assert match_price_band("999", ranges) == "over-500"
    with pytest.raises(ValueError):
        match_price_band(
            "10",
            (MasterPriceRange(min_amount=None, max_amount=None, label="all"),),
        )


@pytest.mark.parametrize(
    "price_range",
    [
        MasterPriceRange(min_amount="", max_amount="100", label="blank-min"),
        MasterPriceRange(min_amount="0", max_amount=" ", label="blank-max"),
    ],
)
def test_blank_price_range_bounds_are_invalid_not_open(price_range: MasterPriceRange):
    with pytest.raises(ValueError):
        match_price_band("50", (price_range,))


def test_preflight_builds_semantic_context_with_half_up_price_and_leaf_category(
    tmp_path: Path,
    product_card_metadata: Path,
):
    db, project_id = _seed_project(tmp_path)
    snapshot = _snapshot(
        (
            _snapshot_product(
                "P001",
                specs=(MasterSpecSlot(label="Power", value="1500W"),),
            ),
        ),
        category_name="Home - Kitchen - Air Fryers",
    )

    result = _run(db, project_id, snapshot)

    assert result["ok"] is True
    assert result["snapshot_id"] == snapshot.snapshot_id
    assert result["contexts"][0]["data_map"] == {
        "title": "Air Fryer",
        "displayPrice": "60元",
        "review": "Good value",
        "priceBandLabel": "0-100",
        "categoryLabel": "Air Fryers",
        "productMedia": "https://example.test/cover.jpg",
    }
    assert result["contexts"][0]["specs"] == [{"label": "Power", "value": "1500W"}]


def test_snapshot_category_is_authoritative_when_local_project_category_conflicts(
    tmp_path: Path,
    product_card_metadata: Path,
):
    db, project_id = _seed_project(tmp_path, category_name="Stale - Local Category")
    result = _run(
        db,
        project_id,
        _snapshot(
            (_snapshot_product("P001"),),
            category_name="Current - Master - Air Fryers",
        ),
    )
    assert result["contexts"][0]["data_map"]["categoryLabel"] == "Air Fryers"


def test_template_display_name_resolves_to_canonical_id(
    tmp_path: Path,
    product_card_metadata: Path,
):
    db, project_id = _seed_project(tmp_path)
    result = dynamic_product_card_preflight(
        db,
        project_id=project_id,
        account_label="xiaobo",
        product_card_template_id="Test template",
        master_contracts=FakeMasterAdapter(_snapshot((_snapshot_product("P001"),))),
    )
    assert result["ok"] is True
    assert result["product_card_template_id"] == "muban-test-1"


@pytest.mark.parametrize(
    "template_name",
    ["Other account template", "unknown-template"],
)
def test_cross_account_and_unknown_templates_are_blocked(
    tmp_path: Path,
    product_card_metadata: Path,
    template_name: str,
):
    db, project_id = _seed_project(tmp_path)
    result = dynamic_product_card_preflight(
        db,
        project_id=project_id,
        account_label="xiaobo",
        product_card_template_id=template_name,
        master_contracts=FakeMasterAdapter(_snapshot((_snapshot_product("P001"),))),
    )
    assert result["ok"] is False
    assert result["error_code"] == "invalid_product_card_template"


def test_video_uses_verified_disk_file_even_when_binding_is_stale(
    tmp_path: Path,
    product_card_metadata: Path,
):
    db, project_id = _seed_project(tmp_path, with_video=True)
    result = _run(db, project_id, _snapshot((_snapshot_product("P001"),)))
    assert result["contexts"][0]["media_kind"] == "video"
    assert result["contexts"][0]["media_asset"].endswith("P001.mp4")

    with db.connect() as conn:
        conn.execute("UPDATE asset_bindings SET status='missing' WHERE asset_type='video'")
    result = _run(db, project_id, _snapshot((_snapshot_product("P001"),)))
    assert result["contexts"][0]["media_kind"] == "video"
    audit_item = result["media_readiness"]["items"][0]
    assert audit_item["status"] == "verified"
    assert audit_item["candidates"][0]["binding_statuses"] == ["missing"]

    (tmp_path / "video" / "P001.mp4").unlink()
    result = _run(db, project_id, _snapshot((_snapshot_product("P001"),)))
    assert result["contexts"][0]["media_kind"] == "cover"
    assert result["contexts"][0]["media_asset"] == "https://example.test/cover.jpg"


def test_video_audit_rejects_nonready_binding_not_rediscovered_in_scan_root(tmp_path: Path):
    video_root = tmp_path / "project-videos"
    video_root.mkdir()
    unrelated_video = tmp_path / "other-category" / "YX044.mp4"
    unrelated_video.parent.mkdir()
    unrelated_video.write_bytes(b"video")

    result = audit_product_video_media(
        [{"uid": "YX044", "title": "Headset"}],
        [
            {
                "uid": "YX044",
                "asset_type": "video",
                "path": str(unrelated_video),
                "status": "stale",
            }
        ],
        video_root=video_root,
        probe_video=lambda _path: {"ok": True, "duration": 1.0, "has_video": True},
    )

    assert result["selected_paths"] == {}
    assert result["items"][0]["candidates"][0]["rejection"] == "binding_not_ready_and_not_rediscovered"


def test_video_audit_scopes_shared_root_to_project_category(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    video_root = tmp_path / "shared-videos"
    headset_video = video_root / "数码-头戴游戏耳机" / "149元-YX044-雷蛇北海巨妖标准版X.mp4"
    gamepad_video = video_root / "数码-游戏手柄" / "80元-YX044-墨将凌云.mp4"
    headset_video.parent.mkdir(parents=True)
    gamepad_video.parent.mkdir(parents=True)
    headset_video.write_bytes(b"headset")
    gamepad_video.write_bytes(b"gamepad")
    monkeypatch.setattr(media_readiness_module, "DEFAULT_VIDEO_ROOT", video_root, raising=False)

    result = audit_product_video_media(
        [{"uid": "YX044", "title": "雷蛇北海巨妖标准版X"}],
        [],
        video_root=video_root,
        project={
            "name": "数码-头戴游戏耳机",
            "category_parent_name": "数码",
            "category_name": "头戴游戏耳机",
        },
        probe_video=lambda _path: {"ok": True, "duration": 1.0, "has_video": True},
    )

    assert result["selected_paths"] == {"YX044": str(headset_video.resolve())}
    assert [item["path"] for item in result["items"][0]["candidates"]] == [str(headset_video.resolve())]


@pytest.mark.parametrize(
    "cover",
    [
        "https://exa mple.com/x",
        "http://[bad]/x",
        "https://example.com:99999/x",
        "https:///missing-host/x",
        "https://example.com/line\nbreak",
    ],
)
def test_malformed_cover_urls_become_missing_media_issues(
    tmp_path: Path,
    product_card_metadata: Path,
    cover: str,
):
    db, project_id = _seed_project(tmp_path)
    result = _run(db, project_id, _snapshot((_snapshot_product("P001", cover=cover),)))
    assert result["ok"] is False
    assert "missing_product_media" in {item["code"] for item in result["issues"]}


def test_local_cover_path_probe_errors_are_treated_as_missing(
    monkeypatch: pytest.MonkeyPatch,
):
    class ErrorPath:
        def is_file(self):
            raise OSError("unreadable path")

    monkeypatch.setattr(preflight_module, "Path", lambda value: ErrorPath())
    assert preflight_module._valid_cover("local-cover.png") is False


def test_malformed_http_cover_never_falls_back_to_a_local_path_probe(
    monkeypatch: pytest.MonkeyPatch,
):
    def unexpected_path_probe(value):
        raise AssertionError(f"must not probe malformed URL as a local path: {value}")

    monkeypatch.setattr(preflight_module, "Path", unexpected_path_probe)
    assert preflight_module._valid_cover("https://exa mple.com/x") is False


def test_optional_slots_may_be_blank_and_complete_product_png_is_not_required(
    tmp_path: Path,
    product_card_metadata: Path,
):
    db, project_id = _seed_project(tmp_path)
    result = _run(
        db,
        project_id,
        _snapshot((_snapshot_product("P001", review=None, specs=()),)),
    )
    assert result["ok"] is True
    assert result["contexts"][0]["data_map"]["review"] == ""
    assert result["contexts"][0]["specs"] == []
    assert all(issue["code"] != "missing_ready_image_binding" for issue in result["issues"])


@pytest.mark.parametrize(
    ("amount", "ranges", "expected_code"),
    [
        ("NaN", (MasterPriceRange("0", "100", "valid"),), "invalid_product_price"),
        ("-1", (MasterPriceRange("0", "100", "valid"),), "invalid_product_price"),
        ("250", (MasterPriceRange("0", "100", "valid"),), "price_band_not_matched"),
        ("59", (MasterPriceRange("100", "100", "invalid"),), "invalid_price_range"),
        ("59", (MasterPriceRange("bad", "100", "invalid"),), "invalid_price_range"),
        ("59", (MasterPriceRange("0", "100", ""),), "invalid_price_range"),
    ],
)
def test_invalid_prices_ranges_and_unmatched_prices_fail_closed(
    tmp_path: Path,
    product_card_metadata: Path,
    amount: str,
    ranges: tuple[MasterPriceRange, ...],
    expected_code: str,
):
    db, project_id = _seed_project(tmp_path)
    result = _run(db, project_id, _snapshot((_snapshot_product("P001", amount=amount),), ranges=ranges))
    assert result["ok"] is False
    assert expected_code in {issue["code"] for issue in result["issues"]}


def test_preflight_aggregates_title_voice_media_and_missing_snapshot_uid(
    tmp_path: Path,
    product_card_metadata: Path,
):
    db, project_id = _seed_project(tmp_path, uids=("P001", "P002"), with_voice=False)
    snapshot = _snapshot((_snapshot_product("P001", title="", cover=None),))

    result = _run(db, project_id, snapshot)

    assert result["ok"] is False
    issues = {(item["product_uid"], item["code"]) for item in result["issues"]}
    assert ("P001", "missing_product_title") in issues
    assert ("P001", "missing_product_voice") in issues
    assert ("P001", "missing_product_media") in issues
    assert ("P002", "snapshot_product_missing") in issues
    assert result["summary"]["products_checked"] == 2


@pytest.mark.parametrize(
    "binding_change",
    [
        {"account_label": "other-account"},
        {"block_text_hash": "", "asset_text_hash": "wrong-hash"},
        {"status": "missing"},
        {"delete_file": True},
    ],
)
def test_voice_must_match_account_hash_ready_status_and_existing_file(
    tmp_path: Path,
    product_card_metadata: Path,
    binding_change: dict[str, object],
):
    db, project_id = _seed_project(tmp_path)
    row = db.fetchone(
        "SELECT * FROM asset_bindings WHERE project_id=? AND asset_type='voice'",
        (project_id,),
    )
    assert row is not None
    with db.connect() as conn:
        if "account_label" in binding_change:
            conn.execute(
                "UPDATE asset_bindings SET account_label=? WHERE id=?",
                (binding_change["account_label"], row["id"]),
            )
        if "status" in binding_change:
            conn.execute(
                "UPDATE asset_bindings SET status=? WHERE id=?",
                (binding_change["status"], row["id"]),
            )
        if "block_text_hash" in binding_change:
            conn.execute(
                "UPDATE script_blocks SET text_hash=? WHERE id=?",
                (binding_change["block_text_hash"], row["script_block_id"]),
            )
            conn.execute(
                "UPDATE asset_bindings SET text_hash=? WHERE id=?",
                (binding_change["asset_text_hash"], row["id"]),
            )
    if binding_change.get("delete_file"):
        Path(row["path"]).unlink()

    result = _run(db, project_id, _snapshot((_snapshot_product("P001"),)))
    assert result["ok"] is False
    assert "missing_product_voice" in {item["code"] for item in result["issues"]}


def test_required_slot_issues_are_aggregated_with_core_and_global_errors(
    tmp_path: Path,
    product_card_metadata: Path,
):
    db, project_id = _seed_project(tmp_path, with_voice=False)
    snapshot = _snapshot(
        (
            _snapshot_product(
                "P001",
                title="",
                amount="NaN",
                cover=None,
                review=None,
                specs=(),
            ),
        ),
        ranges=(MasterPriceRange(None, None, "invalid"),),
        category_name="",
    )

    result = dynamic_product_card_preflight(
        db,
        project_id=project_id,
        account_label="xiaobo",
        product_card_template_id="muban-test-required-details",
        master_contracts=FakeMasterAdapter(snapshot),
    )

    assert result["ok"] is False
    by_uid = {
        (item["product_uid"], item["code"], item["field"])
        for item in result["issues"]
    }
    assert ("", "invalid_price_range", "price_ranges[0]") in by_uid
    assert ("P001", "missing_product_title", "title") in by_uid
    assert ("P001", "invalid_product_price", "price.amount") in by_uid
    assert ("P001", "missing_product_voice", "voice") in by_uid
    assert ("P001", "missing_product_media", "productMedia") in by_uid
    assert ("P001", "missing_required_product_card_slot", "specs") in by_uid
    assert ("P001", "missing_required_product_card_slot", "review") in by_uid
    assert ("P001", "missing_required_product_card_slot", "categoryLabel") in by_uid
    generic_required_fields = {
        item["field"]
        for item in result["issues"]
        if item["product_uid"] == "P001"
        and item["code"] == "missing_required_product_card_slot"
    }
    assert generic_required_fields == {"specs", "review", "categoryLabel"}


def test_master_failure_and_project_contract_gaps_are_structured_failures(
    tmp_path: Path,
    product_card_metadata: Path,
):
    db, project_id = _seed_project(tmp_path)
    adapter = FakeMasterAdapter(error=MasterContractError("master_unavailable", "offline"))
    result = dynamic_product_card_preflight(
        db,
        project_id=project_id,
        account_label="xiaobo",
        product_card_template_id="muban-test-1",
        master_contracts=adapter,
    )
    assert result["ok"] is False
    assert result["error_code"] == "master_unavailable"

    with db.connect() as conn:
        conn.execute("UPDATE projects SET workspace_id='' WHERE id=?", (project_id,))
    result = dynamic_product_card_preflight(
        db,
        project_id=project_id,
        account_label="xiaobo",
        product_card_template_id="muban-test-1",
        master_contracts=FakeMasterAdapter(_snapshot((_snapshot_product("P001"),))),
    )
    assert result["ok"] is False
    assert result["error_code"] == "master_identity_missing"


def test_unexpected_master_adapter_programming_error_propagates(
    tmp_path: Path,
    product_card_metadata: Path,
):
    db, project_id = _seed_project(tmp_path)
    with pytest.raises(AssertionError, match="adapter bug"):
        dynamic_product_card_preflight(
            db,
            project_id=project_id,
            account_label="xiaobo",
            product_card_template_id="muban-test-1",
            master_contracts=FakeMasterAdapter(error=AssertionError("adapter bug")),
        )


def test_workflow_service_reuses_injected_master_adapter(
    tmp_path: Path,
    product_card_metadata: Path,
):
    db, project_id = _seed_project(tmp_path)
    adapter = FakeMasterAdapter(_snapshot((_snapshot_product("P001"),)))
    service = WorkflowService(db, master_contracts=adapter)

    result = service.dynamic_product_card_preflight(
        project_id,
        account_label="xiaobo",
        product_card_template_id="muban-test-1",
    )

    assert result["ok"] is True
    assert adapter.calls == [("workspace-1", "scheme-1", True)]


def test_workflow_service_lazily_creates_and_reuses_default_master_adapter(
    tmp_path: Path,
    product_card_metadata: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    db, project_id = _seed_project(tmp_path)
    adapter = FakeMasterAdapter(_snapshot((_snapshot_product("P001"),)))
    creations: list[FakeMasterAdapter] = []

    def create_adapter():
        creations.append(adapter)
        return adapter

    monkeypatch.setattr(workflow_service_module, "MasterContractAdapter", create_adapter)
    service = WorkflowService(db)
    assert creations == []

    for _ in range(2):
        result = service.dynamic_product_card_preflight(
            project_id,
            account_label="xiaobo",
            product_card_template_id="muban-test-1",
        )
        assert result["ok"] is True
    assert creations == [adapter]
    assert len(adapter.calls) == 2
