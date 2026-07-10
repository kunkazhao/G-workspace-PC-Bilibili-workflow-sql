from __future__ import annotations

from dataclasses import FrozenInstanceError
from copy import deepcopy
import importlib

import pytest
import requests


def _module():
    try:
        return importlib.import_module("bworkflow_sql.master_contracts")
    except ModuleNotFoundError:
        pytest.fail("bworkflow_sql.master_contracts must provide the unwired adapter")


class FakeResponse:
    def __init__(self, payload=None, *, status_code=200, json_error=None):
        self.payload = payload
        self.status_code = status_code
        self.json_error = json_error

    def json(self):
        if self.json_error is not None:
            raise self.json_error
        return self.payload


class FakeSession:
    def __init__(self, *results):
        self.results = list(results)
        self.calls: list[dict] = []

    def get(self, url, *, headers=None, params=None, timeout=None):
        self.calls.append(
            {
                "url": url,
                "headers": deepcopy(headers),
                "params": deepcopy(params),
                "timeout": timeout,
            }
        )
        if not self.results:
            raise AssertionError("unexpected HTTP request")
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


def _workspaces_payload(**extra):
    return {
        "schema_version": "1.0.0",
        "kind": "master.workspace_catalog",
        "generated_at_utc": "2026-07-10T12:00:00Z",
        "workspaces": [
            {
                "id": "workspace-1",
                "name": "赵二",
                "slug": "zhaoer",
                "future_workspace_field": "allowed",
            }
        ],
        "future_top_level": {"allowed": True},
        **extra,
    }


def _categories_payload(**extra):
    return {
        "schema_version": "1.2.0",
        "kind": "master.category_catalog",
        "generated_at_utc": "2026-07-10T12:00:00Z",
        "workspace": {"id": "workspace-1", "name": "赵二", "slug": "zhaoer"},
        "categories": [
            {
                "id": "category-digital",
                "name": "数码",
                "parent_id": None,
                "parent_name": None,
                "sort_order": 1,
                "children": [
                    {
                        "id": "category-speaker",
                        "name": "桌面音响",
                        "parent_id": "category-digital",
                        "parent_name": "数码",
                        "sort_order": 1,
                        "children": [],
                    }
                ],
            }
        ],
        **extra,
    }


def _schemes_payload(**extra):
    return {
        "schema_version": "1.0.1",
        "kind": "master.scheme_catalog",
        "generated_at_utc": "2026-07-10T12:00:00Z",
        "workspace": {"id": "workspace-1", "name": "赵二", "slug": "zhaoer"},
        "category": {"id": "category-speaker", "name": "桌面音响"},
        "schemes": [
            {
                "id": "scheme-1",
                "name": "主方案",
                "category_id": "category-speaker",
                "category_name": "桌面音响",
                "updated_at": "2026-07-07T08:17:05Z",
                "item_count": 1,
            }
        ],
        **extra,
    }


def _snapshot_payload(
    *, title="音响A", workspace_id="workspace-1", scheme_id="scheme-1", **extra
):
    return {
        "schema_version": "1.0.0",
        "kind": "master.scheme_snapshot",
        "generated_at_utc": "2026-07-10T12:00:00Z",
        "snapshot_id": "sha256:" + "a" * 64,
        "workspace": {"id": workspace_id, "name": "赵二", "slug": "zhaoer"},
        "scheme": {
            "id": scheme_id,
            "name": "主方案",
            "category": {"id": "category-speaker", "name": "桌面音响"},
            "updated_at": "2026-07-07T08:17:05Z",
        },
        "price_ranges": [
            {"min_amount": None, "max_amount": "100", "label": "100元以下"}
        ],
        "products": [
            {
                "master_item_id": "item-1",
                "uid": "SP001",
                "title": title,
                "sort_order": 1,
                "price": {
                    "amount": "99.9",
                    "currency": "CNY",
                    "source": "jd",
                    "display": "99.9元",
                },
                "card": {
                    "cover_url": None,
                    "remark": "近场听感均衡",
                    "spec_slots": [{"label": "连接方式", "value": "蓝牙/USB"}],
                    "template_id": "xiaobo1",
                    "future_card_field": "allowed",
                },
                "tags": ["桌面"],
                "featured": True,
                "source_updated_at": "2026-07-07T08:00:00Z",
                "future_product_field": "allowed",
            }
        ],
        "future_top_level": "allowed",
        **extra,
    }


def _adapter(*results, cache_size=64):
    module = _module()
    session = FakeSession(*[FakeResponse(result) if isinstance(result, dict) else result for result in results])
    adapter = module.MasterContractAdapter(
        api_base_url="http://master.test/",
        session=session,
        timeout=12.5,
        cache_size=cache_size,
    )
    return module, adapter, session


def test_adapter_calls_versioned_routes_with_exact_scope_and_returns_typed_values():
    module, adapter, session = _adapter(
        _workspaces_payload(),
        _categories_payload(),
        _schemes_payload(),
        _snapshot_payload(),
    )

    workspaces = adapter.fetch_workspaces()
    categories = adapter.fetch_categories("workspace-1")
    schemes = adapter.fetch_schemes("workspace-1", "category-speaker")
    snapshot = adapter.fetch_scheme_snapshot("workspace-1", "scheme-1")

    assert workspaces.workspaces[0] == module.MasterWorkspace(
        id="workspace-1", name="赵二", slug="zhaoer"
    )
    assert categories.categories[0].children[0].name == "桌面音响"
    assert schemes.schemes[0].item_count == 1
    assert snapshot.products[0].price.amount == "99.9"
    assert snapshot.products[0].card.spec_slots[0].label == "连接方式"
    assert snapshot.products[0].tags == ("桌面",)
    assert snapshot.price_ranges[0].max_amount == "100"
    assert [call["url"] for call in session.calls] == [
        "http://master.test/api/contracts/v1/workspaces",
        "http://master.test/api/contracts/v1/categories",
        "http://master.test/api/contracts/v1/schemes",
        "http://master.test/api/contracts/v1/schemes/scheme-1/snapshot",
    ]
    assert session.calls[0]["headers"] == {}
    assert session.calls[1]["headers"] == {"X-Workspace-Id": "workspace-1"}
    assert session.calls[2]["headers"] == {"X-Workspace-Id": "workspace-1"}
    assert session.calls[2]["params"] == {"category_id": "category-speaker"}
    assert session.calls[3]["headers"] == {"X-Workspace-Id": "workspace-1"}
    assert all(call["timeout"] == 12.5 for call in session.calls)


@pytest.mark.parametrize(
    "transport_error",
    [
        requests.Timeout("slow"),
        requests.ConnectionError("offline"),
    ],
)
def test_transport_failures_map_to_retryable_master_unavailable(transport_error):
    module, adapter, _session = _adapter(transport_error)

    with pytest.raises(module.MasterContractError) as caught:
        adapter.fetch_workspaces()

    assert caught.value.code == "master_unavailable"
    assert caught.value.retryable is True


def test_stable_producer_error_is_parsed_by_code_not_message():
    module = _module()
    session = FakeSession(
        FakeResponse(
            {
                "schema_version": "1.0.0",
                "kind": "master.contract_error",
                "generated_at_utc": "2026-07-10T12:00:00Z",
                "error": {
                    "code": "scheme_snapshot_incomplete",
                    "message": "this human text may change freely",
                    "retryable": False,
                    "details": {"missing_item_ids": ["item-9"]},
                },
            },
            status_code=409,
        )
    )
    adapter = module.MasterContractAdapter(session=session)

    with pytest.raises(module.MasterContractError) as caught:
        adapter.fetch_scheme_snapshot("workspace-1", "scheme-1")

    assert caught.value.code == "scheme_snapshot_incomplete"
    assert caught.value.retryable is False
    assert caught.value.status_code == 409
    assert caught.value.details == {"missing_item_ids": ["item-9"]}


def test_non_contract_server_error_maps_to_master_unavailable():
    module = _module()
    session = FakeSession(FakeResponse({"detail": "upstream failed"}, status_code=503))
    adapter = module.MasterContractAdapter(session=session)

    with pytest.raises(module.MasterContractError) as caught:
        adapter.fetch_workspaces()

    assert caught.value.code == "master_unavailable"
    assert caught.value.retryable is True


def test_invalid_json_maps_to_invalid_master_contract():
    module = _module()
    session = FakeSession(
        FakeResponse(json_error=ValueError("not json"), status_code=200)
    )
    adapter = module.MasterContractAdapter(session=session)

    with pytest.raises(module.MasterContractError) as caught:
        adapter.fetch_workspaces()

    assert caught.value.code == "invalid_master_contract"


def test_wrong_kind_maps_to_invalid_master_contract():
    module, adapter, _session = _adapter(
        _workspaces_payload(kind="master.scheme_catalog")
    )

    with pytest.raises(module.MasterContractError) as caught:
        adapter.fetch_workspaces()

    assert caught.value.code == "invalid_master_contract"


@pytest.mark.parametrize("version", ["2.0.0", "99.1.0"])
def test_unsupported_major_version_has_dedicated_error(version):
    module, adapter, _session = _adapter(
        _snapshot_payload(schema_version=version)
    )

    with pytest.raises(module.MasterContractError) as caught:
        adapter.fetch_scheme_snapshot("workspace-1", "scheme-1")

    assert caught.value.code == "unsupported_contract_version"
    assert caught.value.details == {"schema_version": version}


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload["products"][0].pop("uid"),
        lambda payload: payload["products"][0]["card"].pop("spec_slots"),
        lambda payload: payload["scheme"]["category"].pop("id"),
    ],
)
def test_missing_consumer_field_is_rejected(mutate):
    module = _module()
    payload = _snapshot_payload()
    mutate(payload)
    adapter = module.MasterContractAdapter(session=FakeSession(FakeResponse(payload)))

    with pytest.raises(module.MasterContractError) as caught:
        adapter.fetch_scheme_snapshot("workspace-1", "scheme-1")

    assert caught.value.code == "invalid_master_contract"


def test_response_identity_must_match_requested_scope():
    module, adapter, _session = _adapter(_snapshot_payload(workspace_id="workspace-2"))

    with pytest.raises(module.MasterContractError) as caught:
        adapter.fetch_scheme_snapshot("workspace-1", "scheme-1")

    assert caught.value.code == "invalid_master_contract"


def test_cache_is_scope_keyed_bounded_and_evicts_least_recently_used_entry():
    module, adapter, session = _adapter(
        _snapshot_payload(title="A1", workspace_id="workspace-a"),
        _snapshot_payload(title="B1", workspace_id="workspace-b"),
        _snapshot_payload(title="C1", workspace_id="workspace-c"),
        _snapshot_payload(title="B2", workspace_id="workspace-b"),
        cache_size=2,
    )

    first = adapter.fetch_scheme_snapshot("workspace-a", "scheme-1")
    adapter.fetch_scheme_snapshot("workspace-b", "scheme-1")
    assert adapter.fetch_scheme_snapshot("workspace-a", "scheme-1") is first
    adapter.fetch_scheme_snapshot("workspace-c", "scheme-1")
    refreshed_b = adapter.fetch_scheme_snapshot("workspace-b", "scheme-1")

    assert len(session.calls) == 4
    assert refreshed_b.products[0].title == "B2"
    assert len(adapter._cache) == 2


def test_cached_values_are_immutable_and_force_refresh_replaces_them():
    module = _module()
    first_payload = _snapshot_payload(title="旧标题")
    second_payload = _snapshot_payload(title="新标题")
    session = FakeSession(FakeResponse(first_payload), FakeResponse(second_payload))
    adapter = module.MasterContractAdapter(session=session)

    first = adapter.fetch_scheme_snapshot("workspace-1", "scheme-1")
    first_payload["products"][0]["title"] = "外部篡改"
    cached = adapter.fetch_scheme_snapshot("workspace-1", "scheme-1")

    assert cached.products[0].title == "旧标题"
    with pytest.raises(FrozenInstanceError):
        cached.products[0].title = "不能修改"

    refreshed = adapter.fetch_scheme_snapshot(
        "workspace-1", "scheme-1", force_refresh=True
    )

    assert refreshed.products[0].title == "新标题"
    assert len(session.calls) == 2
