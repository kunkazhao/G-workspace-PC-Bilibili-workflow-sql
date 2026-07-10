from __future__ import annotations

from types import SimpleNamespace

import pytest

from bworkflow_sql import master_contracts as contracts
from bworkflow_sql.db import Database
from bworkflow_sql.legacy_import import LegacyImportService
from bworkflow_sql.pages.project_page import ProjectPageDialog


class FakeVar:
    def __init__(self, value=""):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class FakeCombo:
    def __init__(self):
        self.values = []

    def configure(self, **kwargs):
        if "values" in kwargs:
            self.values = list(kwargs["values"])


class FakeDialog:
    def __init__(self, alive=True):
        self.alive = alive

    def winfo_exists(self):
        return self.alive


def _state(*, alive=True):
    fields = {
        key: FakeVar()
        for key in (
            "name",
            "workspace_id",
            "workspace_name",
            "category_parent_id",
            "category_parent_name",
            "category_id",
            "category_name",
            "scheme_id",
            "scheme_name",
        )
    }
    return SimpleNamespace(
        dialog=FakeDialog(alive=alive),
        fields=fields,
        workspace_var=FakeVar(),
        parent_category_var=FakeVar(),
        child_category_var=FakeVar(),
        scheme_var=FakeVar(),
        parent_combo=FakeCombo(),
        child_combo=FakeCombo(),
        scheme_combo=FakeCombo(),
    )


def _workspace(workspace_id, name, slug=None):
    return contracts.MasterWorkspace(id=workspace_id, name=name, slug=slug)


def _category(category_id, name, *, children=(), parent_id=None, parent_name=None):
    return contracts.MasterCategory(
        id=category_id,
        name=name,
        parent_id=parent_id,
        parent_name=parent_name,
        sort_order=1,
        children=tuple(children),
    )


def _workspace_catalog(*workspaces):
    return contracts.MasterWorkspaceCatalog(
        schema_version="1.0.0",
        generated_at_utc="2026-07-10T12:00:00Z",
        workspaces=tuple(workspaces),
    )


def _category_catalog(workspace, *categories):
    return contracts.MasterCategoryCatalog(
        schema_version="1.0.0",
        generated_at_utc="2026-07-10T12:00:00Z",
        workspace=workspace,
        categories=tuple(categories),
    )


def _scheme_catalog(workspace, category, *schemes):
    return contracts.MasterSchemeCatalog(
        schema_version="1.0.0",
        generated_at_utc="2026-07-10T12:00:00Z",
        workspace=workspace,
        category=contracts.MasterCategoryIdentity(
            id=category.id, name=category.name
        ),
        schemes=tuple(schemes),
    )


class FakeAdapter:
    def __init__(self, *, workspaces, categories=None, schemes=None):
        self.workspace_result = workspaces
        self.category_result = categories
        self.scheme_result = schemes
        self.calls = []

    def fetch_workspaces(self, *, force_refresh=False):
        self.calls.append(("workspaces", force_refresh))
        if isinstance(self.workspace_result, BaseException):
            raise self.workspace_result
        return self.workspace_result

    def fetch_categories(self, workspace_id, *, force_refresh=False):
        self.calls.append(("categories", workspace_id, force_refresh))
        if isinstance(self.category_result, BaseException):
            raise self.category_result
        return self.category_result

    def fetch_schemes(self, workspace_id, category_id, *, force_refresh=False):
        self.calls.append(("schemes", workspace_id, category_id, force_refresh))
        if isinstance(self.scheme_result, BaseException):
            raise self.scheme_result
        return self.scheme_result


class ImmediateApp:
    def run_background(self, _title, work, *, on_success=None, on_error=None, on_done=None, **_kwargs):
        try:
            result = work()
        except Exception as exc:
            if on_error:
                on_error(exc, "trace")
        else:
            if on_success:
                on_success(result)
        finally:
            if on_done:
                on_done()
        return True


def _page(adapter):
    page = ProjectPageDialog.__new__(ProjectPageDialog)
    page.app = ImmediateApp()
    page.master_contracts = adapter
    page.workspaces = []
    page.category_tree = []
    page.schemes = []
    page._workspaces_loading = False
    page.log_messages = []
    page.log = page.log_messages.append
    page._editor_on_child_selected = lambda *_args, **_kwargs: None
    return page


def test_default_workspace_handles_zero_and_prefers_zhaoer_among_many():
    page = _page(None)
    assert page._default_workspace() is None

    other = _workspace("workspace-2", "其他", "other")
    zhaoer = _workspace("workspace-1", "赵二", "zhaoer")
    page.workspaces = [other, zhaoer]

    assert page._default_workspace() == zhaoer


def test_workspace_load_consumes_typed_catalog_and_clears_empty_result():
    zhaoer = _workspace("workspace-1", "赵二", "zhaoer")
    child = _category(
        "category-1",
        "桌面音响",
        parent_id="parent-1",
        parent_name="数码",
    )
    parent = _category("parent-1", "数码", children=(child,))
    adapter = FakeAdapter(
        workspaces=_workspace_catalog(zhaoer),
        categories=_category_catalog(zhaoer, parent),
    )
    page = _page(adapter)
    state = _state()

    page._load_workspaces(force_refresh=True, editor_state=state)

    assert page.workspaces == [zhaoer]
    assert page.category_tree == [parent]
    assert state.workspace_var.get() == "赵二"
    assert state.fields["workspace_id"].get() == "workspace-1"
    assert state.parent_combo.values == ["数码"]
    assert adapter.calls == [
        ("workspaces", True),
        ("categories", "workspace-1", True),
    ]

    empty = FakeAdapter(workspaces=_workspace_catalog())
    page.master_contracts = empty
    state.workspace_var.set("旧空间")
    state.fields["workspace_id"].set("old")
    page._load_workspaces(force_refresh=True, editor_state=state)

    assert page.workspaces == []
    assert page.category_tree == []
    assert state.workspace_var.get() == ""
    assert state.fields["workspace_id"].get() == ""


def test_empty_scheme_catalog_clears_loading_value_and_closed_dialog_ignores_stale_response():
    workspace = _workspace("workspace-1", "赵二", "zhaoer")
    child = _category("category-1", "桌面音响")
    adapter = FakeAdapter(
        workspaces=_workspace_catalog(workspace),
        schemes=_scheme_catalog(workspace, child),
    )
    page = _page(adapter)
    page.workspaces = [workspace]
    page.category_tree = [_category("parent-1", "数码", children=(child,))]
    state = _state()
    state.workspace_var.set("赵二")
    state.parent_category_var.set("数码")
    state.child_category_var.set("桌面音响")

    page._editor_on_child_selected(state)

    assert state.scheme_var.get() == ""
    assert state.scheme_combo.values == []

    state.dialog.alive = False
    page.schemes = [
        contracts.MasterSchemeHeader(
            id="keep",
            name="保留",
            category_id="category-1",
            category_name="桌面音响",
            updated_at=None,
            item_count=0,
        )
    ]
    page._editor_on_child_selected(state)
    assert [scheme.id for scheme in page.schemes] == ["keep"]


def test_legacy_import_reuses_one_adapter_for_catalogs_and_snapshot_sync(tmp_path):
    workspace = _workspace("workspace-1", "赵二", "zhaoer")
    child = _category(
        "category-1",
        "桌面音响",
        parent_id="parent-1",
        parent_name="数码",
    )
    parent = _category("parent-1", "数码", children=(child,))
    scheme = contracts.MasterSchemeHeader(
        id="scheme-1",
        name="主方案",
        category_id="category-1",
        category_name="桌面音响",
        updated_at=None,
        item_count=0,
    )
    adapter = FakeAdapter(
        workspaces=_workspace_catalog(workspace),
        categories=_category_catalog(workspace, parent),
        schemes=_scheme_catalog(workspace, child, scheme),
    )
    db = Database(tmp_path / "legacy.db")

    service = LegacyImportService(db, master_contracts=adapter)
    selected_workspace = service._zhaoer_workspace()
    selected_parent, selected_child = service._find_category(
        workspace_id=selected_workspace.id,
        parent_name="数码",
        child_name="桌面音响",
    )

    assert selected_workspace == workspace
    assert selected_parent == parent
    assert selected_child == child
    assert service.master_contracts is adapter
    assert service.sync.master_contracts is adapter
    db.close()


def test_legacy_import_propagates_typed_unavailable_error(tmp_path):
    error = contracts.MasterContractError(
        "master_unavailable", "Master offline", retryable=True
    )
    adapter = FakeAdapter(workspaces=error)
    db = Database(tmp_path / "offline.db")

    with pytest.raises(contracts.MasterContractError) as caught:
        LegacyImportService(db, master_contracts=adapter)._zhaoer_workspace()

    assert caught.value.code == "master_unavailable"
    db.close()
