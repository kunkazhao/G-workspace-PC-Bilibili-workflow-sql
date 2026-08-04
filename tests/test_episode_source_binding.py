from __future__ import annotations

from types import SimpleNamespace

import pytest

from bworkflow_sql.episode_source_binding import EpisodeSourceBindingError, _sync_diff, resolve_episode_source_binding


SNAPSHOT = "sha256:" + "a" * 64


class FakeRepo:
    def __init__(self, *, applied_snapshot_id: str = SNAPSHOT) -> None:
        self.project_data = {
            "id": 23,
            "workspace_id": "workspace-1",
            "scheme_id": "scheme-1",
            "scheme_name": "主方案",
            "master_snapshot_id": applied_snapshot_id,
            "master_snapshot_applied_at": "2026-07-28T10:00:00+08:00",
        }

    def project(self, project_id: int):
        return self.project_data if project_id == 23 else None

    def products(self, project_id: int, *, include_removed: bool):
        assert project_id == 23 and include_removed is False
        return [{"uid": "P001"}, {"uid": "P002"}]


class FakeSync:
    def __init__(self, snapshot_id: str = SNAPSHOT) -> None:
        self.snapshot_id = snapshot_id
        self.calls = []

    def sync_master_scheme(self, project_id: int, **kwargs):
        self.calls.append((project_id, kwargs))
        return {"snapshot_id": self.snapshot_id, "change_count": 0}

    def master_snapshot_plan(self, project_id: int, **kwargs):
        self.calls.append((project_id, kwargs))
        snapshot = SimpleNamespace(
            generated_at_utc="2026-07-30T15:52:42Z",
            scheme=SimpleNamespace(
                id="scheme-1",
                name="主方案",
                category=SimpleNamespace(id="category-1", name="测试品类"),
            ),
        )
        return snapshot, SimpleNamespace(
            snapshot_id=self.snapshot_id,
            records=[SimpleNamespace(uid="P001"), SimpleNamespace(uid="P002")],
            change_count=0,
            unchanged=[SimpleNamespace(uid="P001"), SimpleNamespace(uid="P002")],
            historical_unchanged=[],
            added=[],
            updated=[],
            removed=[],
            reactivated=[],
        )


def test_current_binding_requires_applied_snapshot_to_match_fresh_master_preview():
    result = resolve_episode_source_binding(
        FakeRepo(), FakeSync(), 23, expected_snapshot_id=SNAPSHOT, require_current=True
    )

    assert result["status"] == "ready"
    assert result["schema_version"] == 2
    assert result["source"] == {
        "authority": "master_scheme_snapshot",
        "snapshot_id": SNAPSHOT,
        "generated_at_utc": "2026-07-30T15:52:42Z",
        "scheme_id": "scheme-1",
        "scheme_name": "主方案",
        "category_id": "category-1",
        "category_name": "测试品类",
        "current_product_count": 2,
    }
    assert result["binding"] == {
        "contract_version": 1,
        "issuer": "bworkflow",
        "mode": "frozen",
        "bworkflow_project_id": 23,
        "workspace_id": "workspace-1",
        "scheme_id": "scheme-1",
        "scheme_name": "主方案",
        "master_snapshot_id": SNAPSHOT,
        "master_snapshot_applied_at": "2026-07-28T10:00:00+08:00",
        "product_count": 2,
        "episode_id": "",
        "source_sha256": "",
    }


def test_changed_master_snapshot_requires_explicit_sync_before_binding():
    fresh = "sha256:" + "b" * 64
    result = resolve_episode_source_binding(FakeRepo(), FakeSync(fresh), 23)

    assert result["status"] == "sync_required"
    assert result["expected_snapshot_id"] == fresh
    assert result["binding"] is None
    assert result["source"]["current_product_count"] == 2
    assert result["sync_diff"] == {
        "current": {
            "unchanged_count": 2,
            "added_count": 0,
            "updated_count": 0,
            "reactivated_count": 0,
            "removed_count": 0,
        },
        "history": {"unchanged_count": 0},
        "changes": [],
    }


def test_apply_requires_the_episode_identity_before_any_source_is_written():
    sync = FakeSync()
    with pytest.raises(EpisodeSourceBindingError) as error:
        resolve_episode_source_binding(FakeRepo(), sync, 23, expected_snapshot_id=SNAPSHOT, apply=True)
    assert error.value.code == "episode_id_required"
    assert sync.calls == []


def test_sync_diff_exposes_semantic_product_card_paths_instead_of_json_blob():
    change = SimpleNamespace(
        uid="EJLY069",
        changed_fields=("product_card_json",),
        before=SimpleNamespace(product_card_json='{"featured":true,"dataMap":{"price":"149元"}}'),
        after=SimpleNamespace(product_card_json='{"featured":false,"dataMap":{"price":"149元"}}'),
    )
    plan = SimpleNamespace(
        unchanged=[object()] * 18,
        historical_unchanged=[object()] * 10,
        added=[],
        updated=[change],
        reactivated=[],
        removed=[],
    )

    result = _sync_diff(plan)

    assert result["changes"] == [
        {"action": "update", "uid": "EJLY069", "changed_fields": ["product_card.featured"]}
    ]
