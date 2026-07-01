from __future__ import annotations

import threading
import tkinter as tk
import traceback
from typing import Any, Callable

import customtkinter as ctk
from tkinter import messagebox

from .components import (
    AppComboBox,
    NavButton,
    restore_button_loading,
    set_button_loading,
)
from .db import Database
from .master_data import MasterDataService
from .master_service import MasterServiceManager
from .repositories import Repository
from .style_config import UIStyle
from .sync_service import SyncService
from .utils import safe_text
from .pages import PAGE_MAP
from .ui_helpers import (
    configure_treeview_style,
    project_id_from_selector_value,
    project_selector_value,
)


ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")

UI_VERSION = "G-UI-2026-05-10-sync-redesign"

class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("B-Workflow SQL 资产工作台")
        self.geometry("1400x900")
        self.minsize(1180, 760)
        self.after(50, lambda: self.state("zoomed"))

        self._busy = False
        self._toast_items: list[ctk.CTkFrame] = []
        self.project_selector_var = ctk.StringVar()
        self._project_selector_widgets: list[AppComboBox] = []
        self._project_selector_id_by_value: dict[str, int] = {}

        self.db = Database()
        self.repo = Repository(self.db)
        self.sync = SyncService(self.db)
        self._workflow = None
        self.master_service = MasterServiceManager()
        self._outline = None
        self._legacy_import = None
        self.master_data = MasterDataService()

        self.current_project_id: int | None = self.db.latest_project_id()
        self.pages: dict[str, ctk.CTkFrame] = {}
        self.nav_buttons: dict[str, NavButton] = {}

        configure_treeview_style(self)
        self._build_shell()
        self.sync_project_selectors()
        self.show_page("品类项目")

    @property
    def workflow(self):
        if self._workflow is None:
            from .workflow_service import WorkflowService
            self._workflow = WorkflowService(self.db)
        return self._workflow

    @property
    def outline(self):
        if self._outline is None:
            from .outline_service import OutlineService
            self._outline = OutlineService(self.db)
        return self._outline

    @property
    def legacy_import(self):
        if self._legacy_import is None:
            from .legacy_import import LegacyImportService
            self._legacy_import = LegacyImportService(self.db)
        return self._legacy_import

    def _build_shell(self) -> None:
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # Sidebar
        sidebar = ctk.CTkFrame(self, fg_color=UIStyle.COLOR_SIDEBAR_BG, width=UIStyle.SIDEBAR_WIDTH, corner_radius=0)
        sidebar.grid(row=0, column=0, sticky="ns")
        sidebar.grid_propagate(False)

        title = ctk.CTkLabel(
            sidebar,
            text="B-Workflow",
            font=("Noto Sans SC", 13, "bold"),
            text_color=UIStyle.COLOR_TEXT_MAIN,
        )
        title.pack(pady=(UIStyle.PAD_LG, UIStyle.PAD_SM), padx=UIStyle.PAD_LG, anchor="w")

        nav_frame = ctk.CTkScrollableFrame(sidebar, fg_color="transparent")
        nav_frame.pack(fill="both", expand=True, padx=UIStyle.PAD_SM, pady=(0, UIStyle.PAD_MD))

        group_icons = {
            "配置": "\uE713",
            "工作流": "\uE9D9",
            "工具": "\uE90F",
        }
        page_icons = {
            "品类项目": "\uECAA",
            "文案中心": "\uE8A5",
            "资产中心": "\uE8B7",
            "同步中心": "\uE895",
            "用户管理": "\uE716",
            "生成配音": "\uE720",
            "组合口播稿": "\uE8FD",
            "生成剪映草稿": "\uE8C6",
            "单独配音": "\uE720",
            "roll-b改名": "\uE8AC",
            "导出字幕 SRT": "\uEDE1",
            "CutMe 引言": "",
        }
        groups = (
            ("配置", ("品类项目", "文案中心", "资产中心", "同步中心", "用户管理")),
            ("工作流", ("生成配音", "组合口播稿", "生成剪映草稿")),
            ("工具", ("单独配音", "roll-b改名", "导出字幕 SRT", "CutMe 引言")),
        )

        def add_nav_group(group: str) -> None:
            header = ctk.CTkFrame(nav_frame, fg_color="transparent", height=30)
            header.pack(fill="x", padx=UIStyle.PAD_XS, pady=(UIStyle.PAD_LG, UIStyle.PAD_XS))
            header.grid_propagate(False)
            header.grid_rowconfigure(0, weight=1)
            header.grid_columnconfigure(2, weight=1)
            ctk.CTkLabel(
                header,
                text=group_icons.get(group, ""),
                width=26,
                font=UIStyle.FONT_NAV_ICON,
                text_color=UIStyle.COLOR_NAV_GROUP,
            ).grid(row=0, column=0, sticky="w", padx=(10, 4))
            ctk.CTkLabel(
                header,
                text=group,
                font=UIStyle.FONT_NAV_GROUP,
                text_color=UIStyle.COLOR_NAV_GROUP,
                anchor="w",
            ).grid(row=0, column=1, sticky="w")

        for group, names in groups:
            add_nav_group(group)
            for name in names:
                btn = NavButton(
                    nav_frame,
                    text=name,
                    icon=page_icons.get(name, ""),
                    command=lambda page=name: self.show_page(page),
                )
                btn.pack(fill="x", padx=UIStyle.PAD_XS, pady=2)
                self.nav_buttons[name] = btn

        # Content area
        self.content_frame = ctk.CTkFrame(self, fg_color=UIStyle.COLOR_BG, corner_radius=0)
        self.content_frame.grid(row=0, column=1, sticky="nsew")
        self.content_frame.grid_rowconfigure(0, weight=1)
        self.content_frame.grid_columnconfigure(0, weight=1)

        # Status bar
        self.status_var = ctk.StringVar(value="就绪")
        status_bar = ctk.CTkFrame(self, fg_color=UIStyle.COLOR_SIDEBAR_BG, corner_radius=0, height=28)
        status_bar.grid(row=1, column=0, columnspan=2, sticky="ew")
        status_bar.grid_propagate(False)
        ctk.CTkLabel(
            status_bar, textvariable=self.status_var,
            font=UIStyle.FONT_SMALL, text_color=UIStyle.COLOR_TEXT_DIM,
        ).pack(side="left", padx=UIStyle.PAD_MD)

    def register_project_selector(self, combo: AppComboBox) -> None:
        if combo not in self._project_selector_widgets:
            self._project_selector_widgets.append(combo)
        self.sync_project_selectors()

    def sync_project_selectors(self) -> None:
        projects = self.repo.projects()
        values = [project_selector_value(project) for project in projects]
        self._project_selector_id_by_value = {project_selector_value(project): int(project["id"]) for project in projects}
        live_widgets: list[AppComboBox] = []
        for combo in self._project_selector_widgets:
            if not combo.winfo_exists():
                continue
            combo.configure(values=values)
            live_widgets.append(combo)
        self._project_selector_widgets = live_widgets
        project = self.current_project()
        if not project and projects:
            self.current_project_id = int(projects[0]["id"])
            project = projects[0]
        self.project_selector_var.set(project_selector_value(project))

    def project_id_for_selector_value(self, value: str) -> int | None:
        text = safe_text(value)
        if not text:
            return None
        project_id = self._project_selector_id_by_value.get(text)
        if project_id is not None:
            return project_id
        return project_id_from_selector_value(text)

    def show_page(self, name: str) -> None:
        for btn_name, btn in self.nav_buttons.items():
            btn.set_active(btn_name == name)
        for page in self.pages.values():
            page.grid_remove()
        if name not in self.pages:
            try:
                page_cls = PAGE_MAP[name]
                self.pages[name] = page_cls(self.content_frame, app=self)
                self.pages[name].grid(row=0, column=0, sticky="nsew", padx=UIStyle.PAD_LG, pady=(UIStyle.PAD_LG, UIStyle.PAD_SM))
            except Exception as exc:
                messagebox.showerror("页面加载失败", f"{name} 页面初始化出错：\n{exc}")
                traceback.print_exc()
                return
        page = self.pages[name]
        page.grid()
        try:
            page.refresh()
        except Exception as exc:
            messagebox.showerror("页面刷新失败", f"{name} 页面刷新出错：\n{exc}")
            traceback.print_exc()

    def current_project(self) -> dict[str, Any] | None:
        if not self.current_project_id:
            return None
        return self.repo.project(self.current_project_id)

    def set_current_project(self, project_id: int) -> None:
        self.current_project_id = project_id
        self.sync_project_selectors()
        for page in self.pages.values():
            page.refresh()

    def set_status(self, text: str) -> None:
        self.status_var.set(text or "就绪")

    def toast(self, text: str, *, kind: str = "success", duration: int = 3000) -> None:
        palette = {
            "success": (UIStyle.COLOR_SUCCESS, UIStyle.ICON_SUCCESS),
            "info": (UIStyle.COLOR_INFO, UIStyle.ICON_INFO),
            "warning": (UIStyle.COLOR_WARNING, UIStyle.ICON_WARNING),
            "error": (UIStyle.COLOR_ERROR, UIStyle.ICON_ERROR),
        }
        accent, icon = palette.get(kind, palette["success"])
        frame = ctk.CTkFrame(
            self,
            fg_color=UIStyle.COLOR_TOAST_BG,
            corner_radius=UIStyle.RADIUS_MD,
            border_width=1,
            border_color=accent,
        )
        ctk.CTkLabel(
            frame,
            text=icon,
            font=UIStyle.FONT_ICON_SM,
            text_color=accent,
            width=24,
        ).pack(side="left", padx=(UIStyle.PAD_MD, UIStyle.PAD_XS), pady=UIStyle.PAD_SM)
        ctk.CTkLabel(
            frame,
            text=text,
            text_color=UIStyle.COLOR_TEXT_MAIN,
            font=UIStyle.FONT_BODY,
            wraplength=420,
            justify="left",
        ).pack(side="left", padx=(0, UIStyle.PAD_MD), pady=UIStyle.PAD_SM)
        self._toast_items.append(frame)
        self._layout_toasts(offset=28)
        frame.lift()

        def close_toast() -> None:
            self._dismiss_toast(frame)

        self.after(duration, close_toast)
        self.after(20, lambda: self._layout_toasts(offset=0))

    def _layout_toasts(self, *, offset: int = 0) -> None:
        live_items = [item for item in self._toast_items if item.winfo_exists()]
        self._toast_items = live_items[-4:]
        y = UIStyle.PAD_XL
        for item in self._toast_items:
            item.update_idletasks()
            item.place(relx=1.0, y=y, x=-(UIStyle.PAD_XL + offset), anchor="ne")
            y += item.winfo_reqheight() + UIStyle.PAD_SM

    def _dismiss_toast(self, frame: ctk.CTkFrame) -> None:
        if frame not in self._toast_items or not frame.winfo_exists():
            return
        frame.place_configure(x=-(UIStyle.PAD_XL + 24))
        self.after(80, lambda: self._destroy_toast(frame))

    def _destroy_toast(self, frame: ctk.CTkFrame) -> None:
        if frame in self._toast_items:
            self._toast_items.remove(frame)
        if frame.winfo_exists():
            frame.destroy()
        self._layout_toasts(offset=0)

    def run_background(
        self, title: str, work: Callable[[], Any],
        *,
        on_success: Callable[[Any], None] | None = None,
        on_error: Callable[[Exception, str], None] | None = None,
        on_done: Callable[[], None] | None = None,
        success_message: str | None = None,
        silent: bool = False,
        show_success_toast: bool = True,
        loading_widget: Any | None = None,
        loading_text: str | None = None,
    ) -> bool:
        if self._busy:
            self.toast("当前已有任务在执行，请稍等。", kind="warning")
            return False
        self._busy = True
        self.set_status(f"{title}中...")
        self.configure(cursor="watch")
        if loading_widget is not None:
            set_button_loading(loading_widget, loading_text or f"{title}中...")

        def finish(result: Any = None, error: Exception | None = None, tb: str = "") -> None:
            self._busy = False
            self.configure(cursor="")
            try:
                if error is not None:
                    self.set_status(f"{title}失败")
                    if on_error:
                        on_error(error, tb)
                    else:
                        messagebox.showerror(f"{title}失败", str(error))
                    if not silent:
                        self.toast(f"{title}失败", kind="error")
                else:
                    if on_success:
                        on_success(result)
                    message = success_message if success_message is not None else f"{title}完成"
                    self.set_status(message)
                    if message and not silent and show_success_toast:
                        self.toast(message, kind="success")
            finally:
                if loading_widget is not None:
                    restore_button_loading(loading_widget)
                if on_done:
                    on_done()

        def worker() -> None:
            try:
                result = work()
            except Exception as exc:
                tb = traceback.format_exc()
                self.after(0, lambda exc=exc, tb=tb: finish(error=exc, tb=tb))
                return
            self.after(0, lambda result=result: finish(result=result))

        threading.Thread(target=worker, daemon=True).start()
        return True


# ── 分页 ──
