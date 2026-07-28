from __future__ import annotations

from bworkflow_sql.db import Database
from bworkflow_sql.episode_source_snapshot import build_episode_source_payload, snapshot_from_episode_source, source_payload_from_row
from bworkflow_sql.master_contracts import (
    MasterCategoryIdentity, MasterMoney, MasterProductCard, MasterSchemeIdentity,
    MasterSchemeSnapshot, MasterSnapshotProduct, MasterWorkspace,
)
from bworkflow_sql.master_snapshot_sync import ProductState
from bworkflow_sql.repositories import Repository


def _snapshot(snapshot_id: str, uid: str, title: str) -> MasterSchemeSnapshot:
    return MasterSchemeSnapshot(
        schema_version="1.0.0", generated_at_utc="2026-07-28T00:00:00Z", snapshot_id=snapshot_id,
        workspace=MasterWorkspace(id="workspace-1", name="workspace", slug=None),
        scheme=MasterSchemeIdentity(id="scheme-1", name="mouse", category=MasterCategoryIdentity(id="category-1", name="mouse"), updated_at=None),
        price_ranges=(),
        products=(MasterSnapshotProduct(
            master_item_id=f"master-{uid}", uid=uid, title=title, sort_order=1,
            price=MasterMoney(amount="99", currency="CNY", source="master", display="99元"),
            card=MasterProductCard(cover_url="https://example.test/cover.jpg", remark=None, spec_slots=(), template_id=None),
            tags=(), featured=False, source_updated_at=None,
        ),),
    )


def _record(uid: str, title: str) -> ProductState:
    return ProductState(1, uid, title, "99元", 1, f"master-{uid}", "{}", 1, 0)


def test_two_episodes_of_one_shared_project_keep_independent_source_projections(tmp_path):
    repo = Repository(Database(tmp_path / "workflow.sqlite3"))
    repo.db.upsert_project({"name": "mouse"})
    first_payload, first_json, first_hash = build_episode_source_payload(
        _snapshot("sha256:" + "a" * 64, "M001", "old-mouse"), (_record("M001", "old-mouse"),)
    )
    second_payload, second_json, second_hash = build_episode_source_payload(
        _snapshot("sha256:" + "b" * 64, "M002", "new-mouse"), (_record("M002", "new-mouse"),)
    )

    repo.create_episode_source_snapshot(project_id=1, episode_id="episode:one", master_snapshot_id="sha256:" + "a" * 64, source_sha256=first_hash, source_json=first_json)
    repo.create_episode_source_snapshot(project_id=1, episode_id="episode:two", master_snapshot_id="sha256:" + "b" * 64, source_sha256=second_hash, source_json=second_json)

    assert [item["uid"] for item in repo.episode_products(1, "episode:one") or []] == ["M001"]
    assert [item["uid"] for item in repo.episode_products(1, "episode:two") or []] == ["M002"]
    restored = snapshot_from_episode_source(source_payload_from_row(repo.episode_source_snapshot(1, "episode:one") or {}))
    assert restored.products[0].title == "old-mouse"
    assert first_payload["master_snapshot"]["snapshot_id"] != second_payload["master_snapshot"]["snapshot_id"]
