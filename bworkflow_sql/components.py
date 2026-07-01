from __future__ import annotations

"""标准化 UI 组件库 —— 基于 customtkinter 封装的项目级组件。"""

import tkinter as tk
import customtkinter as ctk
from typing import Any

from .style_config import UIStyle


def _button_cget(button: Any, key: str, default: str = "") -> str:
    try:
        return str(button.cget(key))
    except Exception:
        return default


def set_button_loading(button: Any, loading_text: str = "处理中...") -> None:
    if button is None or getattr(button, "_bworkflow_loading", False):
        return
    button._bworkflow_loading = True
    button._bworkflow_loading_text = _button_cget(button, "text")
    button._bworkflow_loading_state = _button_cget(button, "state", "normal")
    button.configure(text=loading_text, state="disabled")


def restore_button_loading(button: Any) -> None:
    if button is None or not getattr(button, "_bworkflow_loading", False):
        return
    text = getattr(button, "_bworkflow_loading_text", _button_cget(button, "text"))
    state = getattr(button, "_bworkflow_loading_state", "normal")
    button.configure(text=text, state=state)
    button._bworkflow_loading = False


class HoverTooltip:
    """Simple hover tooltip for compact CustomTkinter controls."""

    def __init__(self, widget: Any | tuple[Any, ...], text: str, *, delay_ms: int = 350, wraplength: int = 680):
        self.widgets = widget if isinstance(widget, tuple) else (widget,)
        self.text = text
        self.delay_ms = delay_ms
        self.wraplength = wraplength
        self._tip: tk.Toplevel | None = None
        self._after_id: str | None = None
        for item in self.widgets:
            item.bind("<Enter>", self._schedule, add="+")
            item.bind("<Leave>", self._hide, add="+")
            item.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _event: tk.Event | None = None) -> None:
        self._cancel_scheduled()
        if not self.text:
            return
        anchor = self.widgets[0]
        self._after_id = anchor.after(self.delay_ms, self._show)

    def _cancel_scheduled(self) -> None:
        if self._after_id:
            try:
                self.widgets[0].after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None

    def _hide(self, _event: tk.Event | None = None) -> None:
        self._cancel_scheduled()
        if self._tip:
            self._tip.destroy()
            self._tip = None

    def _show(self) -> None:
        self._hide()
        anchor = self.widgets[0]
        self._tip = tip = tk.Toplevel(anchor)
        tip.wm_overrideredirect(True)
        tip.wm_attributes("-topmost", True)
        label = tk.Label(
            tip,
            text=self.text,
            background="#1E293B",
            foreground="#F1F5F9",
            relief="solid",
            borderwidth=1,
            font=UIStyle.FONT_SMALL,
            wraplength=self.wraplength,
            justify="left",
            padx=8,
            pady=5,
        )
        label.pack()
        x = anchor.winfo_rootx()
        y = anchor.winfo_rooty() + anchor.winfo_height() + 6
        tip.wm_geometry(f"+{x}+{y}")


def add_hover_tooltip(widget: Any | tuple[Any, ...], text: str) -> HoverTooltip | None:
    if not text:
        return None
    tooltip = HoverTooltip(widget, text)
    widgets = widget if isinstance(widget, tuple) else (widget,)
    for item in widgets:
        tooltips = list(getattr(item, "_bworkflow_tooltips", []))
        tooltips.append(tooltip)
        item._bworkflow_tooltips = tooltips
    return tooltip


class NavButton(ctk.CTkFrame):
    """侧边栏导航项：图标 + 文案 + 选中强调条。"""

    def __init__(self, master, text: str, command=None, icon: str = "", **kwargs):
        height = kwargs.pop("height", 40)
        super().__init__(
            master,
            fg_color="transparent",
            corner_radius=UIStyle.RADIUS_MD,
            height=height,
            **kwargs,
        )
        self.command = command
        self._active = False
        self.grid_propagate(False)
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(2, weight=1)

        self.accent = ctk.CTkFrame(self, fg_color="transparent", width=3, corner_radius=999)
        self.accent.grid(row=0, column=0, sticky="nsw", pady=8)

        self.icon_label = ctk.CTkLabel(
            self,
            text=icon,
            width=28,
            font=UIStyle.FONT_NAV_ICON,
            text_color=UIStyle.COLOR_NAV_ICON,
        )
        self.icon_label.grid(row=0, column=1, sticky="w", padx=(12, 4))

        self.text_label = ctk.CTkLabel(
            self,
            text=text,
            font=UIStyle.FONT_BODY,
            text_color=UIStyle.COLOR_TEXT_DIM,
            anchor="w",
        )
        self.text_label.grid(row=0, column=2, sticky="ew", padx=(0, 10))

        for widget in (self, self.accent, self.icon_label, self.text_label):
            widget.bind("<Button-1>", self._on_click)
            widget.bind("<Enter>", self._on_enter)
            widget.bind("<Leave>", self._on_leave)

    def _on_click(self, _event=None) -> None:
        if self.command:
            self.command()

    def _on_enter(self, _event=None) -> None:
        if not self._active:
            self.configure(fg_color=UIStyle.COLOR_NAV_HOVER)

    def _on_leave(self, _event=None) -> None:
        if not self._active:
            self.configure(fg_color="transparent")

    def set_active(self, active: bool) -> None:
        self._active = active
        self.configure(fg_color=UIStyle.COLOR_NAV_ACTIVE if active else "transparent")
        self.accent.configure(fg_color=UIStyle.COLOR_PRIMARY if active else "transparent")
        self.icon_label.configure(text_color=UIStyle.COLOR_PRIMARY if active else UIStyle.COLOR_NAV_ICON)
        self.text_label.configure(text_color=UIStyle.COLOR_PRIMARY if active else UIStyle.COLOR_TEXT_DIM)


class PrimaryButton(ctk.CTkButton):
    """主操作按钮：品牌色填充，白色文字。"""

    def __init__(self, master, text: str, command=None, **kwargs):
        height = kwargs.pop("height", UIStyle.BUTTON_HEIGHT)
        super().__init__(
            master,
            text=text,
            command=command,
            fg_color=UIStyle.COLOR_PRIMARY,
            text_color="white",
            hover_color=UIStyle.COLOR_PRIMARY_HOVER,
            font=UIStyle.FONT_BUTTON,
            height=height,
            corner_radius=UIStyle.RADIUS_MD,
            **kwargs,
        )


class DangerButton(ctk.CTkButton):
    """危险操作按钮：红色填充。"""

    def __init__(self, master, text: str, command=None, **kwargs):
        height = kwargs.pop("height", UIStyle.BUTTON_HEIGHT)
        super().__init__(
            master,
            text=text,
            command=command,
            fg_color=UIStyle.COLOR_ERROR,
            text_color="white",
            hover_color=UIStyle.COLOR_ERROR_HOVER,
            font=UIStyle.FONT_BUTTON,
            height=height,
            corner_radius=UIStyle.RADIUS_MD,
            **kwargs,
        )


class GhostButton(ctk.CTkButton):
    """次要操作按钮：透明填充，带边框。"""

    def __init__(self, master, text: str, command=None, **kwargs):
        height = kwargs.pop("height", UIStyle.BUTTON_HEIGHT)
        super().__init__(
            master,
            text=text,
            command=command,
            fg_color="transparent",
            text_color=UIStyle.COLOR_TEXT_MAIN,
            hover_color=UIStyle.COLOR_INPUT_BG,
            border_width=1,
            border_color=UIStyle.COLOR_BORDER,
            font=UIStyle.FONT_BODY,
            height=height,
            corner_radius=UIStyle.RADIUS_MD,
            **kwargs,
        )


class AppCard(ctk.CTkFrame):
    """功能区块卡片容器。"""

    def __init__(self, master, title: str = "", **kwargs):
        super().__init__(
            master,
            fg_color=UIStyle.COLOR_CARD_BG,
            corner_radius=UIStyle.RADIUS_LG,
            border_width=1,
            border_color=UIStyle.COLOR_BORDER,
            **kwargs,
        )
        self.pack(fill="x", pady=(0, UIStyle.PAD_MD), padx=0)
        self._title_label: ctk.CTkLabel | None = None
        if title:
            self._title_label = ctk.CTkLabel(
                self,
                text=title,
                font=UIStyle.FONT_H2,
                text_color=UIStyle.COLOR_TEXT_MAIN,
            )
            self._title_label.pack(
                anchor="w", padx=UIStyle.PAD_LG, pady=(UIStyle.PAD_LG, UIStyle.PAD_SM)
            )

    def add_content(self, widget: ctk.CTkBaseClass, **pack_kwargs) -> None:
        """向卡片内部添加内容组件。"""
        pk = {"padx": UIStyle.PAD_LG, "pady": (0, UIStyle.PAD_LG), "fill": "x"}
        pk.update(pack_kwargs)
        widget.pack(**pk)


class AppEntry(ctk.CTkEntry):
    """统一风格的文本输入框。"""

    def __init__(self, master, **kwargs):
        self._highlight_empty = kwargs.pop("highlight_empty", True)
        empty_placeholder = kwargs.pop("empty_placeholder", "未填写")
        textvariable = kwargs.get("textvariable")
        if self._highlight_empty:
            kwargs.setdefault("placeholder_text", empty_placeholder)
            kwargs.setdefault("placeholder_text_color", UIStyle.COLOR_FIELD_EMPTY_TEXT)
        super().__init__(
            master,
            fg_color=UIStyle.COLOR_INPUT_BG,
            text_color=UIStyle.COLOR_TEXT_MAIN,
            font=UIStyle.FONT_BODY,
            height=UIStyle.INPUT_HEIGHT,
            corner_radius=UIStyle.RADIUS_MD,
            border_width=1 if self._highlight_empty else 0,
            border_color=UIStyle.COLOR_FIELD_NORMAL_BORDER,
            **kwargs,
        )
        if self._highlight_empty:
            self._empty_textvariable = textvariable
            if textvariable is not None:
                textvariable.trace_add("write", lambda *_args: self._refresh_empty_state())
            self.bind("<FocusOut>", lambda _event: self._refresh_empty_state(), add="+")
            self.after_idle(self._refresh_empty_state)

    def _refresh_empty_state(self) -> None:
        if not self._highlight_empty or not self.winfo_exists():
            return
        value = self.get().strip()
        self.configure(
            border_color=UIStyle.COLOR_FIELD_EMPTY_BORDER
            if not value
            else UIStyle.COLOR_FIELD_NORMAL_BORDER
        )


class AppComboBox(ctk.CTkComboBox):
    """统一风格的下拉选择框。点击任意位置弹出下拉菜单，不可编辑文字。"""

    def __init__(self, master, values=None, **kwargs):
        self._highlight_empty = kwargs.pop("highlight_empty", True)
        variable = kwargs.get("variable")
        super().__init__(
            master,
            values=values or [],
            fg_color=UIStyle.COLOR_INPUT_BG,
            text_color=UIStyle.COLOR_TEXT_MAIN,
            font=UIStyle.FONT_BODY,
            dropdown_fg_color=UIStyle.COLOR_CARD_BG,
            dropdown_text_color=UIStyle.COLOR_TEXT_MAIN,
            dropdown_hover_color=UIStyle.COLOR_NAV_HOVER,
            height=UIStyle.INPUT_HEIGHT,
            corner_radius=UIStyle.RADIUS_MD,
            border_width=1 if self._highlight_empty else 0,
            border_color=UIStyle.COLOR_FIELD_NORMAL_BORDER,
            button_color=UIStyle.COLOR_PRIMARY,
            button_hover_color=UIStyle.COLOR_PRIMARY_HOVER,
            **kwargs,
        )
        self._entry.bind("<Button>", self._on_click)
        self._entry.bind("<Key>", lambda e: "break")
        if self._highlight_empty:
            self._empty_variable = variable
            if variable is not None:
                variable.trace_add("write", lambda *_args: self._refresh_empty_state())
            self.after_idle(self._refresh_empty_state)

    def _on_click(self, event=None):
        self.after_idle(self._safe_open_dropdown)
        return "break"

    def _safe_open_dropdown(self):
        try:
            if not self.winfo_exists():
                return
            self._canvas.focus_set()
            x = self.winfo_rootx()
            y = self.winfo_rooty() + self.winfo_height()
            self._dropdown_menu.tk_popup(x, y)
        except Exception:
            pass

    def _refresh_empty_state(self) -> None:
        if not self._highlight_empty or not self.winfo_exists():
            return
        value = self.get().strip()
        self.configure(
            border_color=UIStyle.COLOR_FIELD_EMPTY_BORDER
            if not value
            else UIStyle.COLOR_FIELD_NORMAL_BORDER
        )


class AppLabel(ctk.CTkLabel):
    """统一风格的标签。"""

    def __init__(self, master, text: str = "", **kwargs):
        super().__init__(
            master,
            text=text,
            font=UIStyle.FONT_BODY,
            text_color=UIStyle.COLOR_TEXT_MAIN,
            **kwargs,
        )


class AppTextbox(ctk.CTkTextbox):
    """统一风格的文本框（只读/编辑）。"""

    def __init__(self, master, **kwargs):
        super().__init__(
            master,
            fg_color=UIStyle.COLOR_CARD_BG,
            text_color=UIStyle.COLOR_TEXT_MAIN,
            font=UIStyle.FONT_SMALL,
            corner_radius=UIStyle.RADIUS_MD,
            **kwargs,
        )


class BasePage(ctk.CTkFrame):
    """所有子页面的基类：标准标题栏 + 内容区。"""

    def __init__(self, master, title: str, app, *, scrollable: bool = False, **kwargs):
        super().__init__(
            master,
            fg_color="transparent",
            **kwargs,
        )
        self.app = app
        self.page_title = title
        self.db = app.db
        self.repo = app.repo
        self.sync = app.sync
        self.workflow = app.workflow
        self.outline = app.outline
        self.legacy_import = app.legacy_import
        self.master_data = app.master_data

        # 标题栏
        self.header_frame = ctk.CTkFrame(self, fg_color="transparent", height=32)
        self.header_frame.pack(fill="x", pady=(0, UIStyle.PAD_SM))
        self.header_frame.pack_propagate(False)

        self.title_label = ctk.CTkLabel(
            self.header_frame,
            text=title,
            font=UIStyle.FONT_H1,
            text_color=UIStyle.COLOR_TEXT_MAIN,
        )
        self.title_label.pack(side="left")

        self.action_area = ctk.CTkFrame(self.header_frame, fg_color="transparent", width=1, height=1)
        self.action_area.pack(side="right")

        if scrollable:
            self.content: ctk.CTkFrame = ctk.CTkScrollableFrame(
                self,
                fg_color="transparent",
                corner_radius=0,
                scrollbar_button_color=UIStyle.COLOR_LOG_SCROLLBAR,
                scrollbar_button_hover_color=UIStyle.COLOR_LOG_SCROLLBAR_HOVER,
            )
        else:
            self.content = ctk.CTkFrame(
                self,
                fg_color="transparent",
                corner_radius=0,
            )
        self.content.pack(fill="both", expand=True, pady=0)

    def refresh(self) -> None:
        """子类重写，在页面显示时刷新数据。"""
        pass

    def toast(self, text: str, kind: str = "success", duration: int = 3000) -> None:
        self.app.toast(text, kind=kind, duration=duration)

    def set_status(self, text: str) -> None:
        self.app.set_status(text)


class FormRow(ctk.CTkFrame):
    """表单行：标签 + 输入框（grid 布局）。"""

    def __init__(self, master, label: str, widget: ctk.CTkBaseClass, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self.columnconfigure(1, weight=1)

        lbl = ctk.CTkLabel(
            self,
            text=label,
            font=UIStyle.FONT_BODY,
            text_color=UIStyle.COLOR_TEXT_DIM,
            anchor="w",
        )
        lbl.grid(row=0, column=0, sticky="w", padx=(0, UIStyle.PAD_SM), pady=UIStyle.PAD_XS)

        widget.grid(row=0, column=1, sticky="ew", pady=UIStyle.PAD_XS)
        self.widget = widget

    def grid(self, **kwargs) -> None:
        kwargs.setdefault("sticky", "ew")
        kwargs.setdefault("padx", (0, UIStyle.PAD_MD))
        kwargs.setdefault("pady", UIStyle.PAD_XS)
        super().grid(**kwargs)
