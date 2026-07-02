from bworkflow_sql.ui import App
from bworkflow_sql.pages.project_page import ProjectPageDialog


class _FakePage:
    def __init__(self) -> None:
        self.refresh_count = 0
        self.hide_count = 0
        self.show_count = 0

    def refresh(self) -> None:
        self.refresh_count += 1

    def grid_remove(self) -> None:
        self.hide_count += 1

    def grid(self, *args, **kwargs) -> None:
        self.show_count += 1


class _FakeNavButton:
    def __init__(self) -> None:
        self.active = False

    def set_active(self, active: bool) -> None:
        self.active = active


def test_project_switch_refreshes_only_visible_page():
    app = App.__new__(App)
    app.current_project_id = None
    app.current_page_name = "同步中心"
    app._page_refresh_generation = 0
    scheduled = []
    app.after = lambda _delay, callback: scheduled.append(callback)
    app.sync_project_selectors = lambda: None
    app.pages = {
        "品类项目": _FakePage(),
        "同步中心": _FakePage(),
        "生成配音": _FakePage(),
    }

    App.set_current_project(app, 7)

    assert scheduled
    scheduled[-1]()
    assert app.pages["同步中心"].refresh_count == 1
    assert app.pages["品类项目"].refresh_count == 0
    assert app.pages["生成配音"].refresh_count == 0


def test_show_page_defers_refresh_until_page_is_visible():
    app = App.__new__(App)
    app.current_page_name = "品类项目"
    app._page_refresh_generation = 0
    scheduled = []
    app.after = lambda _delay, callback: scheduled.append(callback)
    app.nav_buttons = {"品类项目": _FakeNavButton(), "同步中心": _FakeNavButton()}
    app.pages = {"品类项目": _FakePage(), "同步中心": _FakePage()}

    App.show_page(app, "同步中心")

    assert app.current_page_name == "同步中心"
    assert app.pages["同步中心"].show_count == 1
    assert app.pages["同步中心"].refresh_count == 0

    scheduled[-1]()

    assert app.pages["同步中心"].refresh_count == 1


class _FakeVar:
    def __init__(self) -> None:
        self.value = ""

    def set(self, value: str) -> None:
        self.value = value


class _FakeRepo:
    def projects(self):
        return [{"id": 7, "name": "数码-耳夹蓝牙耳机"}]


class _FakeApp:
    current_project_id = 7

    def sync_project_selectors(self) -> None:
        pass


def test_project_page_refresh_does_not_auto_load_master_workspaces():
    page = ProjectPageDialog.__new__(ProjectPageDialog)
    page.repo = _FakeRepo()
    page.app = _FakeApp()
    page.project_var = _FakeVar()
    page.fields = {}
    page.display_labels = {}
    page.display_label_widgets = {}
    page.workspaces = []
    filled = []
    load_calls = []
    page._fill = lambda project_id: filled.append(project_id)
    page._load_workspaces = lambda **kwargs: load_calls.append(kwargs)

    ProjectPageDialog.refresh(page)

    assert filled == [7]
    assert load_calls == []
