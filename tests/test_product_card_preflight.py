from __future__ import annotations

import json
from pathlib import Path

import pytest

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
                    "displayPrice": {"type": "text", "source": "dataMap.price"},
                    "specs": {"type": "label_value_list", "source": "slots"},
                    "review": {"type": "text", "source": "dataMap.remark"},
                    "priceBandLabel": {"type": "text", "source": "dataMap.priceBandLabel"},
                    "categoryLabel": {"type": "text", "source": "dataMap.categoryLabel"},
                    "productMedia": {"type": "media", "source": "coverAsset"},
                },
                "templates": [
                    {
                        "templateId": "muban-test-1",
                        "displayName": "Test template",
                        "slotDeclarations": [
                            {"key": "title", "required": True},
                            {"key": "displayPrice", "required": True},
                            {"key": "specs", "required": False, "emptyPolicy": "preserve"},
                            {"key": "review", "required": False, "emptyPolicy": "preserve"},
                            {"key": "priceBandLabel", "required": True},
                            {"key": "categoryLabel", "required": False, "emptyPolicy": "preserve"},
                            {"key": "productMedia", "required": True},
                        ],
                    }
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
) -> MasterSchemeSnapshot:
    return MasterSchemeSnapshot(
        schema_version="1.0.0",
        generated_at_utc="2026-07-22T00:00:00Z",
        snapshot_id="sha256:" + "a" * 64,
        workspace=MasterWorkspace(id="workspace-1", name="Workspace", slug=None),
        scheme=MasterSchemeIdentity(
            id="scheme-1",
            name="Scheme",
            category=MasterCategoryIdentity(id="category-1", name="Kitchen"),
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
    )


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
        )
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


def test_video_is_preferred_and_remote_cover_is_the_fallback(
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
    assert result["contexts"][0]["media_kind"] == "cover"
    assert result["contexts"][0]["media_asset"] == "https://example.test/cover.jpg"


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
