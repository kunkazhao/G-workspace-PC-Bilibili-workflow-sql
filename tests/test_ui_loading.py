import inspect

from bworkflow_sql.components import HoverTooltip, restore_button_loading, set_button_loading
from bworkflow_sql.pages.sync_page import SyncStatusCard


class FakeButton:
    def __init__(self) -> None:
        self.values = {"text": "同步 Master", "state": "normal"}
        self.configured: list[dict[str, str]] = []

    def cget(self, key: str) -> str:
        return self.values[key]

    def configure(self, **kwargs: str) -> None:
        self.values.update(kwargs)
        self.configured.append(kwargs)


class FakeWidget:
    def __init__(self) -> None:
        self.bindings: dict[str, object] = {}

    def bind(self, sequence: str, callback, add: str | None = None) -> None:
        self.bindings[sequence] = callback


def test_button_loading_state_is_reusable_and_restores_original_button():
    button = FakeButton()

    set_button_loading(button, "同步中...")

    assert button.values["text"] == "同步中..."
    assert button.values["state"] == "disabled"

    restore_button_loading(button)

    assert button.values["text"] == "同步 Master"
    assert button.values["state"] == "normal"


def test_hover_tooltip_binds_enter_and_leave_handlers():
    widget = FakeWidget()

    tooltip = HoverTooltip(widget, "G:\\2026项目-b站\\素材-配音\\完整路径")

    assert tooltip.text.startswith("G:\\2026")
    assert "<Enter>" in widget.bindings
    assert "<Leave>" in widget.bindings
    assert "<ButtonPress>" in widget.bindings


def test_sync_asset_rows_reserve_fixed_action_column():
    source = inspect.getsource(SyncStatusCard.set_asset_rows)

    assert "row.grid_columnconfigure(1, weight=1)" in source
    assert "actions.grid(row=0, column=3" in source


def test_sync_asset_path_box_has_full_path_hover_tooltip():
    source = inspect.getsource(SyncStatusCard.set_asset_rows)

    assert "add_hover_tooltip((path_box, path_label), path)" in source
