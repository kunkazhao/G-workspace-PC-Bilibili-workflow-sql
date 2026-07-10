from __future__ import annotations

from collections import OrderedDict
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
import re
from threading import RLock
from typing import Any, Callable, TypeVar
from urllib.parse import quote

import requests

from .settings import DEFAULT_MASTER_API_BASE_URL


WORKSPACE_HEADER = "X-Workspace-Id"
SUPPORTED_CONTRACT_MAJOR = 1
DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_CACHE_SIZE = 64

_VERSION_PATTERN = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
_SNAPSHOT_ID_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
ValueT = TypeVar("ValueT")


class MasterContractError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        status_code: int | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message.strip() or code)
        self.code = code
        self.retryable = retryable
        self.status_code = status_code
        self.details = deepcopy(dict(details or {}))


@dataclass(frozen=True, slots=True)
class MasterWorkspace:
    id: str
    name: str
    slug: str | None


@dataclass(frozen=True, slots=True)
class MasterCategoryIdentity:
    id: str
    name: str


@dataclass(frozen=True, slots=True)
class MasterCategory:
    id: str
    name: str
    parent_id: str | None
    parent_name: str | None
    sort_order: int
    children: tuple["MasterCategory", ...]


@dataclass(frozen=True, slots=True)
class MasterSchemeHeader:
    id: str
    name: str
    category_id: str
    category_name: str
    updated_at: str | None
    item_count: int


@dataclass(frozen=True, slots=True)
class MasterSchemeIdentity:
    id: str
    name: str
    category: MasterCategoryIdentity
    updated_at: str | None


@dataclass(frozen=True, slots=True)
class MasterMoney:
    amount: str | None
    currency: str
    source: str
    display: str


@dataclass(frozen=True, slots=True)
class MasterPriceRange:
    min_amount: str | None
    max_amount: str | None
    label: str


@dataclass(frozen=True, slots=True)
class MasterSpecSlot:
    label: str
    value: str


@dataclass(frozen=True, slots=True)
class MasterProductCard:
    cover_url: str | None
    remark: str | None
    spec_slots: tuple[MasterSpecSlot, ...]
    template_id: str | None


@dataclass(frozen=True, slots=True)
class MasterSnapshotProduct:
    master_item_id: str
    uid: str
    title: str
    sort_order: int
    price: MasterMoney
    card: MasterProductCard
    tags: tuple[str, ...]
    featured: bool
    source_updated_at: str | None


@dataclass(frozen=True, slots=True)
class MasterWorkspaceCatalog:
    schema_version: str
    generated_at_utc: str
    workspaces: tuple[MasterWorkspace, ...]


@dataclass(frozen=True, slots=True)
class MasterCategoryCatalog:
    schema_version: str
    generated_at_utc: str
    workspace: MasterWorkspace
    categories: tuple[MasterCategory, ...]


@dataclass(frozen=True, slots=True)
class MasterSchemeCatalog:
    schema_version: str
    generated_at_utc: str
    workspace: MasterWorkspace
    category: MasterCategoryIdentity
    schemes: tuple[MasterSchemeHeader, ...]


@dataclass(frozen=True, slots=True)
class MasterSchemeSnapshot:
    schema_version: str
    generated_at_utc: str
    snapshot_id: str
    workspace: MasterWorkspace
    scheme: MasterSchemeIdentity
    price_ranges: tuple[MasterPriceRange, ...]
    products: tuple[MasterSnapshotProduct, ...]


class MasterContractAdapter:
    """Narrow B-Workflow consumer projection for Master v1 contracts."""

    def __init__(
        self,
        *,
        api_base_url: str = DEFAULT_MASTER_API_BASE_URL,
        session: Any | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        cache_size: int = DEFAULT_CACHE_SIZE,
    ) -> None:
        normalized_url = str(api_base_url or "").strip().rstrip("/")
        if not normalized_url:
            raise ValueError("api_base_url is required")
        if isinstance(cache_size, bool) or cache_size < 1:
            raise ValueError("cache_size must be at least 1")
        self.api_base_url = normalized_url
        self.timeout = float(timeout)
        self.cache_size = int(cache_size)
        self._session = session or requests.Session()
        self._cache: OrderedDict[tuple[str, ...], object] = OrderedDict()
        self._cache_lock = RLock()

    def fetch_workspaces(
        self, *, force_refresh: bool = False
    ) -> MasterWorkspaceCatalog:
        return self._fetch(
            path="/api/contracts/v1/workspaces",
            cache_key=("workspaces",),
            parser=_parse_workspace_catalog,
            force_refresh=force_refresh,
        )

    def fetch_categories(
        self,
        workspace_id: str,
        *,
        force_refresh: bool = False,
    ) -> MasterCategoryCatalog:
        workspace = _request_identity(workspace_id, field="workspace_id")
        return self._fetch(
            path="/api/contracts/v1/categories",
            cache_key=("categories", workspace),
            parser=lambda payload: _parse_category_catalog(
                payload, expected_workspace_id=workspace
            ),
            workspace_id=workspace,
            force_refresh=force_refresh,
        )

    def fetch_schemes(
        self,
        workspace_id: str,
        category_id: str,
        *,
        force_refresh: bool = False,
    ) -> MasterSchemeCatalog:
        workspace = _request_identity(workspace_id, field="workspace_id")
        category = _request_identity(category_id, field="category_id")
        return self._fetch(
            path="/api/contracts/v1/schemes",
            cache_key=("schemes", workspace, category),
            parser=lambda payload: _parse_scheme_catalog(
                payload,
                expected_workspace_id=workspace,
                expected_category_id=category,
            ),
            workspace_id=workspace,
            params={"category_id": category},
            force_refresh=force_refresh,
        )

    def fetch_scheme_snapshot(
        self,
        workspace_id: str,
        scheme_id: str,
        *,
        force_refresh: bool = False,
    ) -> MasterSchemeSnapshot:
        workspace = _request_identity(workspace_id, field="workspace_id")
        scheme = _request_identity(scheme_id, field="scheme_id")
        return self._fetch(
            path=f"/api/contracts/v1/schemes/{quote(scheme, safe='')}/snapshot",
            cache_key=("snapshot", workspace, scheme),
            parser=lambda payload: _parse_scheme_snapshot(
                payload,
                expected_workspace_id=workspace,
                expected_scheme_id=scheme,
            ),
            workspace_id=workspace,
            force_refresh=force_refresh,
        )

    def _fetch(
        self,
        *,
        path: str,
        cache_key: tuple[str, ...],
        parser: Callable[[Mapping[str, Any]], ValueT],
        workspace_id: str | None = None,
        params: Mapping[str, str] | None = None,
        force_refresh: bool,
    ) -> ValueT:
        if not force_refresh:
            cached = self._cache_get(cache_key)
            if cached is not None:
                return cached  # type: ignore[return-value]

        headers = {WORKSPACE_HEADER: workspace_id} if workspace_id else {}
        try:
            response = self._session.get(
                f"{self.api_base_url}{path}",
                headers=headers,
                params=dict(params) if params is not None else None,
                timeout=self.timeout,
            )
        except (requests.Timeout, requests.ConnectionError) as error:
            raise MasterContractError(
                "master_unavailable",
                "无法连接 Master 契约服务。",
                retryable=True,
            ) from error
        except requests.RequestException as error:
            raise MasterContractError(
                "master_unavailable",
                "Master 契约请求失败。",
                retryable=True,
            ) from error

        status_code = getattr(response, "status_code", None)
        if isinstance(status_code, bool) or not isinstance(status_code, int):
            raise _invalid_contract("response.status_code")
        try:
            payload = response.json()
        except (TypeError, ValueError) as error:
            if status_code >= 500:
                raise MasterContractError(
                    "master_unavailable",
                    "Master 契约服务暂时不可用。",
                    retryable=True,
                    status_code=status_code,
                ) from error
            raise _invalid_contract("response.json") from error

        if status_code >= 400:
            _raise_server_error(payload, status_code=status_code)
        if not isinstance(payload, Mapping):
            raise _invalid_contract("response")
        result = parser(payload)
        self._cache_put(cache_key, result)
        return result

    def _cache_get(self, key: tuple[str, ...]) -> object | None:
        with self._cache_lock:
            value = self._cache.get(key)
            if value is not None:
                self._cache.move_to_end(key)
            return value

    def _cache_put(self, key: tuple[str, ...], value: object) -> None:
        with self._cache_lock:
            self._cache[key] = value
            self._cache.move_to_end(key)
            while len(self._cache) > self.cache_size:
                self._cache.popitem(last=False)


def _invalid_contract(field: str) -> MasterContractError:
    return MasterContractError(
        "invalid_master_contract",
        "Master 返回的数据不满足 B-Workflow 消费契约。",
        details={"field": field},
    )


def _request_identity(value: Any, *, field: str) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    raise _invalid_contract(field)


def _mapping(value: Any, *, field: str) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    raise _invalid_contract(field)


def _sequence(value: Any, *, field: str) -> list[Any]:
    if isinstance(value, list):
        return value
    raise _invalid_contract(field)


def _text(value: Any, *, field: str) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    raise _invalid_contract(field)


def _optional_text(value: Any, *, field: str) -> str | None:
    if value is None:
        return None
    return _text(value, field=field)


def _integer(value: Any, *, field: str, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _invalid_contract(field)
    if minimum is not None and value < minimum:
        raise _invalid_contract(field)
    return value


def _boolean(value: Any, *, field: str) -> bool:
    if not isinstance(value, bool):
        raise _invalid_contract(field)
    return value


def _contract_identity(payload: Mapping[str, Any], *, kind: str) -> str:
    version = _text(payload.get("schema_version"), field="schema_version")
    match = _VERSION_PATTERN.fullmatch(version)
    if match is None:
        raise _invalid_contract("schema_version")
    if int(match.group(1)) != SUPPORTED_CONTRACT_MAJOR:
        raise MasterContractError(
            "unsupported_contract_version",
            "Master 契约主版本不受支持。",
            details={"schema_version": version},
        )
    if payload.get("kind") != kind:
        raise _invalid_contract("kind")
    return version


def _generated_at(payload: Mapping[str, Any]) -> str:
    return _text(payload.get("generated_at_utc"), field="generated_at_utc")


def _parse_workspace(value: Any, *, field: str) -> MasterWorkspace:
    row = _mapping(value, field=field)
    return MasterWorkspace(
        id=_text(row.get("id"), field=f"{field}.id"),
        name=_text(row.get("name"), field=f"{field}.name"),
        slug=_optional_text(row.get("slug"), field=f"{field}.slug"),
    )


def _parse_category_identity(value: Any, *, field: str) -> MasterCategoryIdentity:
    row = _mapping(value, field=field)
    return MasterCategoryIdentity(
        id=_text(row.get("id"), field=f"{field}.id"),
        name=_text(row.get("name"), field=f"{field}.name"),
    )


def _parse_category(value: Any, *, field: str) -> MasterCategory:
    row = _mapping(value, field=field)
    children = _sequence(row.get("children"), field=f"{field}.children")
    return MasterCategory(
        id=_text(row.get("id"), field=f"{field}.id"),
        name=_text(row.get("name"), field=f"{field}.name"),
        parent_id=_optional_text(row.get("parent_id"), field=f"{field}.parent_id"),
        parent_name=_optional_text(
            row.get("parent_name"), field=f"{field}.parent_name"
        ),
        sort_order=_integer(row.get("sort_order"), field=f"{field}.sort_order"),
        children=tuple(
            _parse_category(item, field=f"{field}.children[{index}]")
            for index, item in enumerate(children)
        ),
    )


def _parse_workspace_catalog(payload: Mapping[str, Any]) -> MasterWorkspaceCatalog:
    version = _contract_identity(payload, kind="master.workspace_catalog")
    rows = _sequence(payload.get("workspaces"), field="workspaces")
    return MasterWorkspaceCatalog(
        schema_version=version,
        generated_at_utc=_generated_at(payload),
        workspaces=tuple(
            _parse_workspace(item, field=f"workspaces[{index}]")
            for index, item in enumerate(rows)
        ),
    )


def _parse_category_catalog(
    payload: Mapping[str, Any], *, expected_workspace_id: str
) -> MasterCategoryCatalog:
    version = _contract_identity(payload, kind="master.category_catalog")
    workspace = _parse_workspace(payload.get("workspace"), field="workspace")
    if workspace.id != expected_workspace_id:
        raise _invalid_contract("workspace.id")
    rows = _sequence(payload.get("categories"), field="categories")
    return MasterCategoryCatalog(
        schema_version=version,
        generated_at_utc=_generated_at(payload),
        workspace=workspace,
        categories=tuple(
            _parse_category(item, field=f"categories[{index}]")
            for index, item in enumerate(rows)
        ),
    )


def _parse_scheme_catalog(
    payload: Mapping[str, Any], *, expected_workspace_id: str, expected_category_id: str
) -> MasterSchemeCatalog:
    version = _contract_identity(payload, kind="master.scheme_catalog")
    workspace = _parse_workspace(payload.get("workspace"), field="workspace")
    category = _parse_category_identity(payload.get("category"), field="category")
    if workspace.id != expected_workspace_id:
        raise _invalid_contract("workspace.id")
    if category.id != expected_category_id:
        raise _invalid_contract("category.id")
    rows = _sequence(payload.get("schemes"), field="schemes")
    schemes: list[MasterSchemeHeader] = []
    for index, value in enumerate(rows):
        field = f"schemes[{index}]"
        row = _mapping(value, field=field)
        schemes.append(
            MasterSchemeHeader(
                id=_text(row.get("id"), field=f"{field}.id"),
                name=_text(row.get("name"), field=f"{field}.name"),
                category_id=_text(
                    row.get("category_id"), field=f"{field}.category_id"
                ),
                category_name=_text(
                    row.get("category_name"), field=f"{field}.category_name"
                ),
                updated_at=_optional_text(
                    row.get("updated_at"), field=f"{field}.updated_at"
                ),
                item_count=_integer(
                    row.get("item_count"), field=f"{field}.item_count", minimum=0
                ),
            )
        )
    if any(item.category_id != expected_category_id for item in schemes):
        raise _invalid_contract("schemes.category_id")
    return MasterSchemeCatalog(
        schema_version=version,
        generated_at_utc=_generated_at(payload),
        workspace=workspace,
        category=category,
        schemes=tuple(schemes),
    )


def _parse_money(value: Any, *, field: str) -> MasterMoney:
    row = _mapping(value, field=field)
    return MasterMoney(
        amount=_optional_text(row.get("amount"), field=f"{field}.amount"),
        currency=_text(row.get("currency"), field=f"{field}.currency"),
        source=_text(row.get("source"), field=f"{field}.source"),
        display=_text(row.get("display"), field=f"{field}.display"),
    )


def _parse_card(value: Any, *, field: str) -> MasterProductCard:
    row = _mapping(value, field=field)
    raw_slots = _sequence(row.get("spec_slots"), field=f"{field}.spec_slots")
    slots: list[MasterSpecSlot] = []
    for index, value in enumerate(raw_slots):
        slot_field = f"{field}.spec_slots[{index}]"
        slot = _mapping(value, field=slot_field)
        slots.append(
            MasterSpecSlot(
                label=_text(slot.get("label"), field=f"{slot_field}.label"),
                value=_text(slot.get("value"), field=f"{slot_field}.value"),
            )
        )
    return MasterProductCard(
        cover_url=_optional_text(row.get("cover_url"), field=f"{field}.cover_url"),
        remark=_optional_text(row.get("remark"), field=f"{field}.remark"),
        spec_slots=tuple(slots),
        template_id=_optional_text(
            row.get("template_id"), field=f"{field}.template_id"
        ),
    )


def _parse_product(value: Any, *, field: str) -> MasterSnapshotProduct:
    row = _mapping(value, field=field)
    raw_tags = _sequence(row.get("tags"), field=f"{field}.tags")
    tags = tuple(
        _text(tag, field=f"{field}.tags[{index}]")
        for index, tag in enumerate(raw_tags)
    )
    return MasterSnapshotProduct(
        master_item_id=_text(
            row.get("master_item_id"), field=f"{field}.master_item_id"
        ),
        uid=_text(row.get("uid"), field=f"{field}.uid"),
        title=_text(row.get("title"), field=f"{field}.title"),
        sort_order=_integer(
            row.get("sort_order"), field=f"{field}.sort_order", minimum=1
        ),
        price=_parse_money(row.get("price"), field=f"{field}.price"),
        card=_parse_card(row.get("card"), field=f"{field}.card"),
        tags=tags,
        featured=_boolean(row.get("featured"), field=f"{field}.featured"),
        source_updated_at=_optional_text(
            row.get("source_updated_at"), field=f"{field}.source_updated_at"
        ),
    )


def _parse_scheme_snapshot(
    payload: Mapping[str, Any], *, expected_workspace_id: str, expected_scheme_id: str
) -> MasterSchemeSnapshot:
    version = _contract_identity(payload, kind="master.scheme_snapshot")
    snapshot_id = _text(payload.get("snapshot_id"), field="snapshot_id")
    if _SNAPSHOT_ID_PATTERN.fullmatch(snapshot_id) is None:
        raise _invalid_contract("snapshot_id")
    workspace = _parse_workspace(payload.get("workspace"), field="workspace")
    scheme_row = _mapping(payload.get("scheme"), field="scheme")
    scheme = MasterSchemeIdentity(
        id=_text(scheme_row.get("id"), field="scheme.id"),
        name=_text(scheme_row.get("name"), field="scheme.name"),
        category=_parse_category_identity(
            scheme_row.get("category"), field="scheme.category"
        ),
        updated_at=_optional_text(
            scheme_row.get("updated_at"), field="scheme.updated_at"
        ),
    )
    if workspace.id != expected_workspace_id:
        raise _invalid_contract("workspace.id")
    if scheme.id != expected_scheme_id:
        raise _invalid_contract("scheme.id")

    raw_ranges = _sequence(payload.get("price_ranges"), field="price_ranges")
    price_ranges: list[MasterPriceRange] = []
    for index, value in enumerate(raw_ranges):
        field = f"price_ranges[{index}]"
        row = _mapping(value, field=field)
        price_ranges.append(
            MasterPriceRange(
                min_amount=_optional_text(
                    row.get("min_amount"), field=f"{field}.min_amount"
                ),
                max_amount=_optional_text(
                    row.get("max_amount"), field=f"{field}.max_amount"
                ),
                label=_text(row.get("label"), field=f"{field}.label"),
            )
        )
    raw_products = _sequence(payload.get("products"), field="products")
    return MasterSchemeSnapshot(
        schema_version=version,
        generated_at_utc=_generated_at(payload),
        snapshot_id=snapshot_id,
        workspace=workspace,
        scheme=scheme,
        price_ranges=tuple(price_ranges),
        products=tuple(
            _parse_product(item, field=f"products[{index}]")
            for index, item in enumerate(raw_products)
        ),
    )


def _raise_server_error(payload: Any, *, status_code: int) -> None:
    if isinstance(payload, Mapping) and payload.get("kind") == "master.contract_error":
        _contract_identity(payload, kind="master.contract_error")
        error = _mapping(payload.get("error"), field="error")
        code = _text(error.get("code"), field="error.code")
        message = _text(error.get("message"), field="error.message")
        retryable = _boolean(error.get("retryable"), field="error.retryable")
        details = _mapping(error.get("details"), field="error.details")
        raise MasterContractError(
            code,
            message,
            retryable=retryable,
            status_code=status_code,
            details=details,
        )
    if status_code >= 500:
        raise MasterContractError(
            "master_unavailable",
            "Master 契约服务暂时不可用。",
            retryable=True,
            status_code=status_code,
        )
    raise _invalid_contract("error_envelope")
