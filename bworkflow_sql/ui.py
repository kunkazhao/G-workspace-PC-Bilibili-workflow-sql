from __future__ import annotations

import re
import tkinter as tk
import json
import os
import threading
import traceback
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable

from .db import Database
from .legacy_import import LegacyImportService
from .master_data import MasterDataService, display_name
from .outline_service import OutlineService
from .repositories import Repository
from .settings import (
    DEFAULT_IMAGE_ROOT,
    DEFAULT_JIANYING_DRAFT_ROOT,
    DEFAULT_MARKDOWN_ROOT,
    DEFAULT_SPOKEN_MD_ROOT,
    DEFAULT_VIDEO_ROOT,
    DEFAULT_VOICE_ROOT,
    INTERNAL_WORKSPACE_ROOT,
)
from .sync_service import SyncService
from .utils import compact_path, now_iso, safe_text
from .workflow_service import WorkflowService


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self._apply_theme()
        self.title("B-Workflow SQL 资产工作台")
        self.geometry("1400x900")
        self.minsize(1180, 760)
        self.state("zoomed")
        self._busy = False
        self._toast_after: str | None = None
        self._task_disabled_buttons: list[ttk.Button] = []
        self.status_var = tk.StringVar(value="就绪")
        self.db = Database()
        self.repo = Repository(self.db)
        self.sync = SyncService(self.db)
        self.workflow = WorkflowService(self.db)
        self.outline = OutlineService(self.db)
        self.legacy_import = LegacyImportService(self.db)
        self.master_data = MasterDataService()
        self.current_project_id: int | None = self.db.latest_project_id()
        self.pages: dict[str, BasePage] = {}
        self.nav_buttons: dict[str, ttk.Button] = {}
        self._build_shell()
        self.show_page("品类项目")

    def _apply_theme(self) -> None:
        theme_applied = False
        try:
            import sv_ttk  # type: ignore

            sv_ttk.set_theme("light")
            theme_applied = True
        except Exception:
            pass
        style = ttk.Style(self)
        if not theme_applied:
            try:
                style.theme_use("clam")
            except tk.TclError:
                pass
        style.configure("TFrame", background="#f7faf9")
        style.configure("TLabel", background="#f7faf9", foreground="#173b33", font=("Microsoft YaHei UI", 10))
        style.configure("TButton", padding=(12, 6), font=("Microsoft YaHei UI", 10))
        style.configure("TEntry", padding=4)
        style.configure("TLabelframe", background="#f7faf9", borderwidth=0, relief="flat")
        style.configure("TLabelframe.Label", background="#f7faf9", foreground="#0f3d33", font=("Microsoft YaHei UI", 11, "bold"))
        style.configure("Treeview", rowheight=28, font=("Microsoft YaHei UI", 10))
        style.configure("Treeview.Heading", font=("Microsoft YaHei UI", 10, "bold"))

    def _build_shell(self) -> None:
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)
        self.sidebar = ttk.Frame(self, padding=14)
        self.sidebar.grid(row=0, column=0, sticky="ns")
        self.sidebar.columnconfigure(0, weight=1)
        title = ttk.Label(self.sidebar, text="B-Workflow SQL", font=("Microsoft YaHei UI", 18, "bold"))
        title.grid(row=0, column=0, sticky="w", pady=(0, 16))
        row = 1
        groups = {
            "配置": ["品类项目", "文案中心", "资产中心", "同步中心", "用户管理"],
            "工作流": ["生成配音", "组合口播稿", "生成剪映草稿"],
        }
        for group, names in groups.items():
            ttk.Label(self.sidebar, text=group, font=("Microsoft YaHei UI", 11, "bold")).grid(row=row, column=0, sticky="w", pady=(14, 6))
            row += 1
            for name in names:
                button = ttk.Button(self.sidebar, text=name, command=lambda page=name: self.show_page(page))
                button.grid(row=row, column=0, sticky="ew", pady=3)
                self.nav_buttons[name] = button
                row += 1
        self.content = ttk.Frame(self, padding=16)
        self.content.grid(row=0, column=1, sticky="nsew")
        self.content.rowconfigure(1, weight=1)
        self.content.columnconfigure(0, weight=1)
        self.header = ttk.Label(self.content, text="", font=("Microsoft YaHei UI", 18, "bold"))
        self.header.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        self.body = ttk.Frame(self.content)
        self.body.grid(row=1, column=0, sticky="nsew")
        self.body.rowconfigure(0, weight=1)
        self.body.columnconfigure(0, weight=1)

        self.status_bar = ttk.Frame(self, padding=(14, 6))
        self.status_bar.grid(row=1, column=0, columnspan=2, sticky="ew")
        self.status_bar.columnconfigure(0, weight=1)
        ttk.Label(self.status_bar, textvariable=self.status_var).grid(row=0, column=0, sticky="w")
        self.busy_bar = ttk.Progressbar(self.status_bar, mode="indeterminate", length=140)
        self.busy_bar.grid(row=0, column=1, sticky="e")
        self.busy_bar.grid_remove()

        self.toast_label = tk.Label(
            self,
            text="",
            bg="#0f766e",
            fg="white",
            padx=16,
            pady=10,
            font=("Microsoft YaHei UI", 10),
            relief="flat",
        )

    def show_page(self, name: str) -> None:
        self.header.configure(text=name)
        for page in self.pages.values():
            page.grid_remove()
        if name not in self.pages:
            page_cls = PAGE_MAP[name]
            self.pages[name] = page_cls(self.body, self)
            self.pages[name].grid(row=0, column=0, sticky="nsew")
        page = self.pages[name]
        page.grid()
        page.refresh()

    def current_project(self) -> dict[str, Any] | None:
        if not self.current_project_id:
            return None
        return self.repo.project(self.current_project_id)

    def set_current_project(self, project_id: int) -> None:
        self.current_project_id = project_id
        for page in self.pages.values():
            page.refresh()

    def set_status(self, text: str) -> None:
        self.status_var.set(text or "就绪")

    def toast(self, text: str, *, kind: str = "success", duration: int = 3000) -> None:
        colors = {
            "success": ("#0f766e", "white"),
            "info": ("#2563eb", "white"),
            "warning": ("#b45309", "white"),
            "error": ("#b91c1c", "white"),
        }
        bg, fg = colors.get(kind, colors["success"])
        if self._toast_after:
            self.after_cancel(self._toast_after)
            self._toast_after = None
        self.toast_label.configure(text=text, bg=bg, fg=fg)
        self.toast_label.place(relx=1.0, y=18, x=-22, anchor="ne")
        self.toast_label.lift()
        self._toast_after = self.after(duration, self.toast_label.place_forget)

    def run_background(
        self,
        title: str,
        work: Callable[[], Any],
        *,
        on_success: Callable[[Any], None] | None = None,
        on_error: Callable[[Exception, str], None] | None = None,
        on_done: Callable[[], None] | None = None,
        success_message: str | None = None,
        silent: bool = False,
        disable_buttons: bool = True,
        show_success_toast: bool = True,
    ) -> bool:
        if self._busy:
            self.toast("当前已有任务在执行，请稍等。", kind="warning")
            return False
        self._busy = True
        self.set_status(f"{title}中...")
        self.configure(cursor="watch")
        if disable_buttons:
            self._set_task_buttons_disabled(True)
        self.busy_bar.grid()
        self.busy_bar.start(12)

        def finish(result: Any = None, error: Exception | None = None, tb: str = "") -> None:
            self._busy = False
            self.busy_bar.stop()
            self.busy_bar.grid_remove()
            self.configure(cursor="")
            if disable_buttons:
                self._set_task_buttons_disabled(False)
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

    def _set_task_buttons_disabled(self, disabled: bool) -> None:
        if disabled:
            self._task_disabled_buttons = []

            def walk(widget: tk.Widget) -> None:
                for child in widget.winfo_children():
                    if isinstance(child, ttk.Button) and "disabled" not in child.state():
                        child.state(["disabled"])
                        self._task_disabled_buttons.append(child)
                    walk(child)

            walk(self)
            return
        for button in self._task_disabled_buttons:
            try:
                button.state(["!disabled"])
            except tk.TclError:
                pass
        self._task_disabled_buttons = []


class BasePage(ttk.Frame):
    def __init__(self, master, app: App):
        super().__init__(master)
        self.app = app
        self.db = app.db
        self.repo = app.repo
        self.sync = app.sync
        self.workflow = app.workflow
        self.outline = app.outline
        self.legacy_import = app.legacy_import
        self.master_data = app.master_data
        self.columnconfigure(0, weight=1)
        self.rowconfigure(99, weight=1)

    def refresh(self) -> None:
        pass

    def project_required(self) -> dict[str, Any] | None:
        project = self.app.current_project()
        if not project:
            messagebox.showinfo("需要品类项目", "请先在“品类项目”中创建或选择项目。")
            return None
        return project

    def log(self, text: str) -> None:
        if hasattr(self, "log_text"):
            self.log_text.configure(state="normal")
            self.log_text.insert("end", text.rstrip() + "\n")
            self.log_text.see("end")
            self.log_text.configure(state="disabled")

    def set_status(self, text: str) -> None:
        self.app.set_status(text)

    def toast(self, text: str, *, kind: str = "success", duration: int = 3000) -> None:
        self.app.toast(text, kind=kind, duration=duration)

    def run_task(
        self,
        title: str,
        work: Callable[[], Any],
        *,
        on_success: Callable[[Any], None] | None = None,
        on_error: Callable[[Exception, str], None] | None = None,
        on_done: Callable[[], None] | None = None,
        success_message: str | None = None,
        silent: bool = False,
        disable_buttons: bool = True,
        show_success_toast: bool = True,
    ) -> bool:
        return self.app.run_background(
            title,
            work,
            on_success=on_success,
            on_error=on_error,
            on_done=on_done,
            success_message=success_message,
            silent=silent,
            disable_buttons=disable_buttons,
            show_success_toast=show_success_toast,
        )


class ProjectPage(BasePage):
    def __init__(self, master, app: App):
        super().__init__(master, app)
        self.project_var = tk.StringVar()
        self.workspace_var = tk.StringVar()
        self.parent_category_var = tk.StringVar()
        self.child_category_var = tk.StringVar()
        self.scheme_var = tk.StringVar()
        self.workspaces: list[dict[str, Any]] = []
        self.category_tree: list[dict[str, Any]] = []
        self.schemes: list[dict[str, Any]] = []
        self.fields: dict[str, tk.StringVar] = {key: tk.StringVar() for key in [
            "name",
            "workspace_id",
            "workspace_name",
            "category_parent_id",
            "category_parent_name",
            "category_id",
            "category_name",
            "scheme_id",
            "scheme_name",
            "md_path",
            "spoken_md_path",
            "image_root",
            "video_root",
            "voice_root",
            "output_root",
        ]}
        self._build()

    def _build(self) -> None:
        top = ttk.Frame(self)
        top.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        top.columnconfigure(1, weight=1)
        ttk.Label(top, text="当前项目").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.project_combo = ttk.Combobox(top, textvariable=self.project_var, state="readonly")
        self.project_combo.grid(row=0, column=1, sticky="ew")
        self.project_combo.bind("<<ComboboxSelected>>", lambda _event: self._select_project())
        ttk.Button(top, text="新建", command=self._new_project).grid(row=0, column=2, padx=8)
        ttk.Button(top, text="保存", command=self._save_project).grid(row=0, column=3)

        form = ttk.LabelFrame(self, text="从 Master 选择品类方案", padding=12)
        form.grid(row=1, column=0, sticky="ew")
        form.columnconfigure(1, weight=1)
        form.columnconfigure(3, weight=1)
        ttk.Label(form, text="项目名称").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=5)
        ttk.Entry(form, textvariable=self.fields["name"]).grid(row=0, column=1, columnspan=3, sticky="ew", pady=5)

        ttk.Label(form, text="Master 工作空间").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=5)
        self.workspace_label = ttk.Label(form, text="赵二（默认）")
        self.workspace_label.grid(row=1, column=1, sticky="w", padx=(0, 12), pady=5)
        ttk.Button(form, text="刷新 Master", command=lambda: self._load_workspaces(force_refresh=True)).grid(row=1, column=2, sticky="w", padx=(0, 8), pady=5)

        ttk.Label(form, text="一级品类").grid(row=2, column=0, sticky="w", padx=(0, 8), pady=5)
        self.parent_combo = ttk.Combobox(form, textvariable=self.parent_category_var, state="readonly")
        self.parent_combo.grid(row=2, column=1, sticky="ew", padx=(0, 12), pady=5)
        self.parent_combo.bind("<<ComboboxSelected>>", lambda _event: self._on_parent_selected())
        ttk.Label(form, text="二级品类").grid(row=2, column=2, sticky="w", padx=(0, 8), pady=5)
        self.child_combo = ttk.Combobox(form, textvariable=self.child_category_var, state="readonly")
        self.child_combo.grid(row=2, column=3, sticky="ew", pady=5)
        self.child_combo.bind("<<ComboboxSelected>>", lambda _event: self._on_child_selected())

        ttk.Label(form, text="Master 方案").grid(row=3, column=0, sticky="w", padx=(0, 8), pady=5)
        self.scheme_combo = ttk.Combobox(form, textvariable=self.scheme_var, state="readonly")
        self.scheme_combo.grid(row=3, column=1, columnspan=3, sticky="ew", pady=5)
        self.scheme_combo.bind("<<ComboboxSelected>>", lambda _event: self._on_scheme_selected())

        path_form = ttk.LabelFrame(self, text="文案与素材来源", padding=12)
        path_form.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        path_form.columnconfigure(1, weight=1)
        path_form.columnconfigure(3, weight=1)
        labels = [
            ("商品文案 MD", "md_path"),
            ("图片根目录", "image_root"),
            ("视频根目录", "video_root"),
            ("配音根目录", "voice_root"),
        ]
        for index, (label, key) in enumerate(labels):
            row = index // 2
            col = (index % 2) * 2
            ttk.Label(path_form, text=label).grid(row=row, column=col, sticky="w", padx=(0, 8), pady=5)
            entry = ttk.Entry(path_form, textvariable=self.fields[key])
            entry.grid(row=row, column=col + 1, sticky="ew", padx=(0, 12), pady=5)
            ttk.Button(path_form, text="选", width=4, command=lambda item=key: self._browse(item)).grid(row=row, column=col + 1, sticky="e", padx=(0, 12))

        actions = ttk.Frame(self)
        actions.grid(row=3, column=0, sticky="ew", pady=12)
        ttk.Button(actions, text="创建/更新文案框架", command=self._init_outline).pack(side="left", padx=(0, 8))
        ttk.Label(actions, text="Master、MD、素材同步请到“同步中心”统一操作。").pack(side="left")
        self.log_text = tk.Text(self, height=14, state="disabled")
        self.log_text.grid(row=99, column=0, sticky="nsew", pady=(8, 0))

    def _browse(self, key: str) -> None:
        if key == "md_path":
            path = filedialog.askopenfilename(filetypes=[("Markdown", "*.md"), ("All", "*.*")], initialdir=str(DEFAULT_MARKDOWN_ROOT))
        else:
            path = filedialog.askdirectory()
        if path:
            self.fields[key].set(path.replace("/", "\\"))

    def _new_project(self) -> None:
        for key, var in self.fields.items():
            var.set("")
        self.workspace_var.set("")
        self.parent_category_var.set("")
        self.child_category_var.set("")
        self.scheme_var.set("")
        self.fields["image_root"].set(str(DEFAULT_IMAGE_ROOT))
        self.fields["video_root"].set(str(DEFAULT_VIDEO_ROOT))
        self.fields["voice_root"].set(str(DEFAULT_VOICE_ROOT))
        self.fields["output_root"].set(str(INTERNAL_WORKSPACE_ROOT))
        self.project_var.set("")

    def _payload(self) -> dict[str, Any]:
        payload = {key: var.get().strip() for key, var in self.fields.items()}
        payload["id"] = self.app.current_project_id if self.project_var.get() else 0
        return payload

    def _save_project(self) -> None:
        payload = self._payload()
        if not payload["name"]:
            messagebox.showwarning("缺少项目名", "请填写项目名。")
            return
        project_id = self.db.upsert_project(payload)
        self.app.set_current_project(project_id)
        self.log(f"已保存项目：{payload['name']}")
        self.toast("项目已保存")

    def _select_project(self) -> None:
        value = self.project_var.get()
        if not value:
            return
        project_id = int(value.split(" - ", 1)[0])
        self.app.set_current_project(project_id)
        self._fill(project_id)

    def _fill(self, project_id: int) -> None:
        project = self.repo.project(project_id)
        if not project:
            return
        for key, var in self.fields.items():
            var.set(safe_text(project.get(key)))
        self.project_var.set(f"{project_id} - {project['name']}")
        self.workspace_var.set(safe_text(project.get("workspace_name")))
        self.parent_category_var.set(safe_text(project.get("category_parent_name")))
        self.child_category_var.set(safe_text(project.get("category_name")))
        self.scheme_var.set(safe_text(project.get("scheme_name")))

    def _preview_master(self) -> None:
        project = self.project_required()
        if not project:
            return
        try:
            result = self.sync.sync_master_scheme(project["id"], apply_changes=False)
        except Exception as exc:
            messagebox.showerror("预览失败", str(exc))
            return
        self.log(f"Master 变化预览：新增 {len(result['added'])}，更新 {len(result['updated'])}，移除 {len(result['removed'])}")
        for key in ("added", "updated", "removed"):
            for item in result[key]:
                self.log(f"  {key}: {item.get('title')} / {item.get('uid')}")

    def _sync_master(self) -> None:
        project = self.project_required()
        if not project:
            return
        try:
            result = self.sync.sync_master_scheme(project["id"], apply_changes=True)
        except Exception as exc:
            messagebox.showerror("同步失败", str(exc))
            return
        self.log(f"Master 已同步：新增 {len(result['added'])}，更新 {len(result['updated'])}，移除 {len(result['removed'])}")

    def _sync_md(self) -> None:
        project = self.project_required()
        if not project:
            return
        try:
            result = self.sync.sync_markdown(project["id"])
        except Exception as exc:
            messagebox.showerror("同步失败", str(exc))
            return
        self.log(f"MD 已同步：入库 {result['upserted']} 条，额外商品 {len(result['extra_md'])}，缺文案 {len(result['missing_copy'])}")

    def _init_outline(self) -> None:
        project = self.project_required()
        if not project:
            return
        if not self.fields["md_path"].get().strip():
            self.fields["md_path"].set(str(self.outline.default_markdown_path(project["id"])))
        path = filedialog.asksaveasfilename(
            defaultextension=".md",
            filetypes=[("Markdown", "*.md"), ("All", "*.*")],
            initialdir=str(DEFAULT_MARKDOWN_ROOT),
            initialfile=Path(self.fields["md_path"].get()).name,
        )
        if not path:
            return

        def work() -> tuple[dict[str, Any], dict[str, Any]]:
            result = self.outline.init_or_update_outline(project["id"], path)
            sync_result = self.sync.sync_markdown(project["id"])
            return result, sync_result

        def on_success(payload: tuple[dict[str, Any], dict[str, Any]]) -> None:
            result, sync_result = payload
            self.fields["md_path"].set(result["target_path"])
            self.log(
                f"文案框架已更新：商品 {result['total']} 个，新增 {len(result['added'])}，保留 {len(result['preserved'])}。"
            )
            self.log(f"已同步 MD 到数据库：入库 {sync_result['upserted']} 条。")
            self.toast("文案框架已更新")

        self.run_task("创建文案框架", work, on_success=on_success, success_message="文案框架已更新", show_success_toast=False)

    def refresh(self) -> None:
        projects = self.repo.projects()
        values = [f"{item['id']} - {item['name']}" for item in projects]
        self.project_combo.configure(values=values)
        if self.app.current_project_id:
            self._fill(self.app.current_project_id)
        elif projects:
            self.app.current_project_id = projects[0]["id"]
            self._fill(projects[0]["id"])
        if not self.workspaces:
            self._load_workspaces(force_refresh=False, quiet=True)

    def _load_workspaces(self, *, force_refresh: bool = False, quiet: bool = False) -> None:
        if getattr(self, "_workspaces_loading", False):
            return
        self._workspaces_loading = True
        keep_existing = bool(self.fields["category_name"].get().strip())

        def choose_default(workspaces: list[dict[str, Any]]) -> dict[str, Any] | None:
            for item in workspaces:
                if safe_text(item.get("name")) == "赵二" or safe_text(item.get("slug")) == "zhaoer":
                    return item
            return workspaces[0] if workspaces else None

        def work() -> dict[str, Any]:
            workspaces = self.master_data.fetch_workspaces(force_refresh=force_refresh)
            workspace = choose_default(workspaces)
            tree: list[dict[str, Any]] = []
            source = ""
            if workspace:
                _workspace, tree, source = self.master_data.fetch_category_tree(safe_text(workspace.get("id")))
            return {"workspaces": workspaces, "workspace": workspace, "tree": tree, "source": source}

        def on_success(result: dict[str, Any]) -> None:
            self.workspaces = result["workspaces"]
            workspace = result["workspace"]
            if workspace:
                self.workspace_var.set(display_name(workspace))
                self.workspace_label.configure(text=f"{display_name(workspace)}（默认）")
                self.fields["workspace_id"].set(safe_text(workspace.get("id")))
                self.fields["workspace_name"].set(display_name(workspace))
                self._apply_category_tree(result["tree"], source=result["source"], keep_existing=keep_existing)
            if not quiet:
                self.log(f"已读取 Master 工作空间，当前固定使用：{self.workspace_var.get() or '赵二'}。")

        def on_error(exc: Exception, _tb: str) -> None:
            if not quiet:
                messagebox.showerror("读取 Master 失败", str(exc))

        self.run_task(
            "读取 Master",
            work,
            on_success=on_success,
            on_error=on_error,
            on_done=lambda: setattr(self, "_workspaces_loading", False),
            success_message=None if quiet else "Master 已刷新",
            silent=quiet,
            disable_buttons=not quiet,
        )

    def _selected_workspace(self) -> dict[str, Any] | None:
        name = self.workspace_var.get()
        for item in self.workspaces:
            if display_name(item) == name:
                return item
        return None

    def _default_workspace(self) -> dict[str, Any] | None:
        for item in self.workspaces:
            if safe_text(item.get("name")) == "赵二" or safe_text(item.get("slug")) == "zhaoer":
                return item
        return self.workspaces[0] if self.workspaces else None

    def _on_workspace_selected(self) -> None:
        workspace = self._selected_workspace()
        if not workspace:
            return
        self.fields["workspace_id"].set(safe_text(workspace.get("id")))
        self.fields["workspace_name"].set(display_name(workspace))
        self._load_category_tree(workspace, keep_existing=False)

    def _load_category_tree(self, workspace: dict[str, Any], *, keep_existing: bool) -> None:
        workspace_id = safe_text(workspace.get("id"))

        def work() -> tuple[list[dict[str, Any]], str]:
            _workspace, tree, source = self.master_data.fetch_category_tree(workspace_id)
            return tree, source

        def on_success(result: tuple[list[dict[str, Any]], str]) -> None:
            tree, source = result
            self._apply_category_tree(tree, source=source, keep_existing=keep_existing)

        self.run_task("读取品类", work, on_success=on_success, success_message="品类已刷新")

    def _apply_category_tree(self, tree: list[dict[str, Any]], *, source: str, keep_existing: bool) -> None:
        self.category_tree = tree
        parent_names = [safe_text(parent.get("name")) for parent in tree]
        self.parent_combo.configure(values=parent_names)
        self.parent_category_var.set("")
        self.child_category_var.set("")
        self.scheme_var.set("")
        self.scheme_combo.configure(values=[])
        if parent_names:
            saved_parent = self.fields["category_parent_name"].get().strip() if keep_existing else ""
            self.parent_category_var.set(saved_parent if saved_parent in parent_names else parent_names[0])
            self._on_parent_selected(keep_existing=keep_existing)
        self.log(f"已读取 Master 品类：{len(parent_names)} 个一级品类（来源：{source}）。")

    def _selected_parent(self) -> dict[str, Any] | None:
        name = self.parent_category_var.get()
        for parent in self.category_tree:
            if safe_text(parent.get("name")) == name:
                return parent
        return None

    def _on_parent_selected(self, *, keep_existing: bool = False) -> None:
        parent = self._selected_parent()
        if not parent:
            return
        self.fields["category_parent_id"].set(safe_text(parent.get("id")))
        self.fields["category_parent_name"].set(safe_text(parent.get("name")))
        children = parent.get("children") or []
        child_names = [safe_text(child.get("name")) for child in children if safe_text(child.get("name"))]
        self.child_combo.configure(values=child_names)
        saved_child = self.fields["category_name"].get().strip() if keep_existing else ""
        self.child_category_var.set(saved_child if saved_child in child_names else (child_names[0] if child_names else ""))
        self.scheme_var.set("")
        self.scheme_combo.configure(values=[])
        if child_names:
            self._on_child_selected(keep_existing=keep_existing)

    def _selected_child(self) -> dict[str, Any] | None:
        parent = self._selected_parent()
        if not parent:
            return None
        name = self.child_category_var.get()
        for child in parent.get("children") or []:
            if safe_text(child.get("name")) == name:
                return child
        return None

    def _on_child_selected(self, *, keep_existing: bool = False) -> None:
        workspace = self._selected_workspace()
        child = self._selected_child()
        if not workspace or not child:
            return
        self.fields["category_id"].set(safe_text(child.get("id")))
        self.fields["category_name"].set(safe_text(child.get("name")))
        workspace_id = safe_text(workspace.get("id"))
        child_id = safe_text(child.get("id"))
        child_name = safe_text(child.get("name"))
        self.scheme_combo.configure(values=[])
        self.scheme_var.set("读取中...")

        def work() -> tuple[list[dict[str, Any]], str]:
            return self.master_data.fetch_schemes(workspace_id=workspace_id, category_id=child_id)

        def on_success(result: tuple[list[dict[str, Any]], str]) -> None:
            self.schemes, source = result
            scheme_names = [display_name(item, safe_text(item.get("id"))) for item in self.schemes]
            self.scheme_combo.configure(values=scheme_names)
            saved_scheme = self.fields["scheme_name"].get().strip() if keep_existing else ""
            self.scheme_var.set(saved_scheme if saved_scheme in scheme_names else (scheme_names[0] if scheme_names else ""))
            if scheme_names:
                self._on_scheme_selected()
            self.log(f"已读取“{child_name}”方案：{len(scheme_names)} 个（来源：{source}）。")

        def on_error(exc: Exception, _tb: str) -> None:
            self.scheme_var.set("")
            messagebox.showerror("读取方案失败", str(exc))

        self.run_task("读取方案", work, on_success=on_success, on_error=on_error, success_message=None, silent=True)

    def _on_scheme_selected(self) -> None:
        name = self.scheme_var.get()
        for scheme in self.schemes:
            if display_name(scheme, safe_text(scheme.get("id"))) != name:
                continue
            self.fields["scheme_id"].set(safe_text(scheme.get("id")))
            self.fields["scheme_name"].set(name)
            if not self.fields["name"].get().strip():
                parent = self.fields["category_parent_name"].get().strip()
                child = self.fields["category_name"].get().strip()
                self.fields["name"].set(f"{parent}-{child}" if parent and child else name)
            return


class TablePage(BasePage):
    columns: tuple[str, ...] = ()

    def _build_table(self, row: int = 1) -> None:
        self.tree = ttk.Treeview(self, columns=self.columns, show="headings")
        for column in self.columns:
            self.tree.heading(column, text=column)
            self.tree.column(column, width=140, anchor="w")
        self.tree.grid(row=row, column=0, sticky="nsew")
        self.rowconfigure(row, weight=1)
        ybar = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=ybar.set)
        ybar.grid(row=row, column=1, sticky="ns")

    def _set_rows(self, rows: list[tuple[Any, ...]]) -> None:
        self.tree.delete(*self.tree.get_children())
        for row in rows:
            self.tree.insert("", "end", values=row)


TYPE_LABELS = {
    "intro": "引言",
    "product": "商品文案",
    "price_transition": "价格过渡",
}

COLUMN_WIDTHS = {
    "品类": 80,
    "类型": 80,
    "对象UID": 80,
    "产品名称": 110,
    "标签": 80,
    "正文预览": 400,
}


class CopyPage(TablePage):
    columns = ("品类", "类型", "对象UID", "产品名称", "标签", "正文预览")

    def __init__(self, master, app: App):
        super().__init__(master, app)
        self.category_var = tk.StringVar(value="全部")
        self._body_map: dict[str, str] = {}
        actions = ttk.Frame(self)
        actions.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(actions, text="品类").pack(side="left")
        self.category_combo = ttk.Combobox(actions, textvariable=self.category_var, state="readonly", width=16)
        self.category_combo.pack(side="left", padx=(6, 12))
        self.category_combo.bind("<<ComboboxSelected>>", lambda _event: self.refresh())
        ttk.Label(actions, text="单击正文可查看完整内容。同步 MD 请到“同步中心”。").pack(side="left")
        self._build_table()
        for col, width in COLUMN_WIDTHS.items():
            self.tree.column(col, width=width)
        self.tree.bind("<ButtonRelease-1>", self._on_body_click)

    def _on_body_click(self, event: tk.Event) -> None:
        region = self.tree.identify_region(event.x, event.y)
        if region != "cell":
            return
        column = self.tree.identify_column(event.x)
        col_index = int(column.replace("#", "")) - 1
        if col_index != 5:
            return
        row_id = self.tree.identify_row(event.y)
        if not row_id:
            return
        full_body = self._body_map.get(row_id, "")
        if full_body:
            self._show_body_popup(full_body)

    def _show_body_popup(self, text: str) -> None:
        dialog = tk.Toplevel(self)
        dialog.title("正文内容")
        dialog.geometry("700x500")
        dialog.minsize(500, 300)
        dialog.transient(self.winfo_toplevel())
        dialog.grab_set()
        text_widget = tk.Text(dialog, wrap="word", padx=16, pady=16, font=("Microsoft YaHei UI", 10))
        text_widget.pack(fill="both", expand=True)
        text_widget.insert("1.0", text)
        text_widget.configure(state="disabled")
        btn_frame = ttk.Frame(dialog)
        btn_frame.pack(fill="x", pady=(0, 10))
        ttk.Button(btn_frame, text="关闭", command=dialog.destroy).pack()
        dialog.update_idletasks()
        x = dialog.winfo_screenwidth() // 2 - dialog.winfo_width() // 2
        y = dialog.winfo_screenheight() // 2 - dialog.winfo_height() // 2
        dialog.geometry(f"+{x}+{y}")

    def refresh(self) -> None:
        projects = self.repo.projects()
        if not projects:
            self._set_rows([])
            return
        categories = sorted({item["category_name"] for item in projects if item["category_name"]})
        self.category_combo.configure(values=categories)
        if self.category_var.get() not in categories:
            self.category_var.set(categories[0] if categories else "")
        selected_category = self.category_var.get()
        self._body_map.clear()
        self.tree.delete(*self.tree.get_children())
        block_order = {"intro": 0, "price_transition": 1, "product": 2}
        for project in projects:
            if project["category_name"] != selected_category:
                continue
            products_map = {item["uid"]: item["title"] for item in self.repo.products(project["id"], include_removed=True)}
            category_name = project["category_name"] or ""
            blocks = list(self.repo.script_blocks(project["id"]))
            blocks.sort(key=lambda b: (block_order.get(b["script_type"], 99), b.get("owner_uid", ""), b.get("price_range_label", ""), b.get("block_label", "")))
            for block in blocks:
                uid = block["owner_uid"] or ""
                product_name = products_map.get(uid, "") if uid else ""
                owner_display = uid or block["price_range_label"] or ""
                type_label = TYPE_LABELS.get(block["script_type"], block["script_type"])
                row = (category_name, type_label, owner_display, product_name, block["block_label"], block["body"][:70])
                iid = self.tree.insert("", "end", values=row)
                self._body_map[iid] = block["body"]


class AssetPage(TablePage):
    columns = ("品类", "用户", "对象", "文案类型", "文案", "图片", "视频", "配音", "问题")

    def __init__(self, master, app: App):
        super().__init__(master, app)
        self.category_var = tk.StringVar(value="全部")
        self.status_var = tk.StringVar(value="全部")
        actions = ttk.Frame(self)
        actions.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(actions, text="这里只查看资产缺口。同步素材和导入旧数据请到“同步中心”。").pack(side="left")
        filters = ttk.Frame(self)
        filters.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(filters, text="品类").pack(side="left")
        self.category_combo = ttk.Combobox(filters, textvariable=self.category_var, state="readonly", width=18)
        self.category_combo.pack(side="left", padx=(6, 14))
        self.category_combo.bind("<<ComboboxSelected>>", lambda _event: self.refresh())
        ttk.Label(filters, text="用户").pack(side="left", anchor="n", pady=(2, 0))
        self.user_listbox = tk.Listbox(filters, selectmode=tk.MULTIPLE, height=4, width=16, exportselection=False)
        self.user_listbox.pack(side="left", padx=(6, 14))
        self.user_listbox.bind("<<ListboxSelect>>", lambda _event: self.refresh())
        ttk.Label(filters, text="筛选").pack(side="left")
        self.status_combo = ttk.Combobox(filters, textvariable=self.status_var, state="readonly", values=["全部", "缺文案", "缺图片", "缺视频", "缺配音", "配音过期"], width=14)
        self.status_combo.pack(side="left", padx=(6, 14))
        self.status_combo.bind("<<ComboboxSelected>>", lambda _event: self.refresh())
        self.summary_label = ttk.Label(self, text="")
        self.summary_label.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        self._build_table(row=3)
        self.tree.tag_configure("has_issues", background="#fff0ed")

    def _set_rows(self, rows: list[tuple[Any, ...]]) -> None:
        self.tree.delete(*self.tree.get_children())
        for row in rows:
            tags = ("has_issues",) if row[-1] else ()
            self.tree.insert("", "end", values=row, tags=tags)

    def _sync_assets(self) -> None:
        project = self.project_required()
        if not project:
            return
        try:
            result = self.sync.sync_assets(project["id"])
        except Exception as exc:
            messagebox.showerror("同步失败", str(exc))
            return
        messagebox.showinfo("同步完成", f"图片 {result['image']}，视频 {result['video']}，配音 {result['voice']}，未识别 {result['unmatched']}")
        self.refresh()

    def refresh(self) -> None:
        projects = self.repo.projects()
        if not projects:
            self._set_rows([])
            return
        categories = ["全部"] + sorted({item["category_name"] for item in projects if item["category_name"]})
        self.category_combo.configure(values=categories)
        if self.category_var.get() not in categories:
            self.category_var.set("全部")
        self.user_listbox.delete(0, "end")
        for item in self.repo.accounts():
            self.user_listbox.insert("end", item["label"])

        rows: list[tuple[Any, ...]] = []
        summary = {"copy": 0, "missing_copy": 0, "image": 0, "missing_image": 0, "video": 0, "missing_video": 0, "voice": 0, "missing_voice": 0}
        selected_category = self.category_var.get()
        selected_user_indices = self.user_listbox.curselection()
        selected_users = [self.user_listbox.get(i) for i in selected_user_indices]
        for project in projects:
            if selected_category != "全部" and project["category_name"] != selected_category:
                continue
            project_rows, project_summary = self._rows_for_project(project, selected_users=selected_users)
            rows.extend(project_rows)
            for key, value in project_summary.items():
                summary[key] += value
        rows = [row for row in rows if self._row_matches_filter(row)]
        self.summary_label.configure(
            text=(
                f"文案 {summary['copy']} / 缺 {summary['missing_copy']}  | "
                f"图片 {summary['image']} / 缺 {summary['missing_image']}  | "
                f"视频 {summary['video']} / 缺 {summary['missing_video']}  | "
                f"配音 {summary['voice']} / 缺 {summary['missing_voice']}"
            )
        )
        self._set_rows(rows)

    def _rows_for_project(self, project: dict[str, Any], *, selected_users: list[str]) -> tuple[list[tuple[Any, ...]], dict[str, int]]:
        blocks = self.repo.script_blocks(project["id"])
        assets = self.repo.asset_bindings(project["id"])
        products = self.repo.products(project["id"], include_removed=True)
        accounts = self.repo.accounts()
        if selected_users:
            accounts = [item for item in accounts if item["label"] in selected_users]
        if not accounts:
            accounts = [{"label": "未设置", "account_id": "", "media_identity": ""}]
        summary = {"copy": 0, "missing_copy": 0, "image": 0, "missing_image": 0, "video": 0, "missing_video": 0, "voice": 0, "missing_voice": 0}
        rows: list[tuple[Any, ...]] = []
        block_counts: dict[tuple[str, str], int] = {}
        block_hashes: dict[tuple[str, str], set[str]] = {}
        for block in blocks:
            if block["script_type"] == "product":
                block_counts[("product", block["owner_uid"])] = block_counts.get(("product", block["owner_uid"]), 0) + 1
                block_hashes.setdefault(("product", block["owner_uid"]), set()).add(block["text_hash"])
            elif block["script_type"] == "intro":
                block_counts[("intro", "INTRO")] = block_counts.get(("intro", "INTRO"), 0) + 1
                block_hashes.setdefault(("intro", "INTRO"), set()).add(block["text_hash"])
            elif block["script_type"] == "price_transition":
                price_label = block["price_range_label"] or "价格过渡"
                block_counts[("price_transition", price_label)] = block_counts.get(("price_transition", price_label), 0) + 1
                block_hashes.setdefault(("price_transition", price_label), set()).add(block["text_hash"])

        for account in accounts:
            rows.extend(self._shared_rows(project, account, assets, block_counts, block_hashes, summary))
            for product in products:
                rows.append(self._product_row(project, account, product, assets, block_counts, block_hashes, summary))
        return rows, summary

    def _shared_rows(
        self,
        project: dict[str, Any],
        account: dict[str, Any],
        assets: list[dict[str, Any]],
        block_counts: dict[tuple[str, str], int],
        block_hashes: dict[tuple[str, str], set[str]],
        summary: dict[str, int],
    ) -> list[tuple[Any, ...]]:
        rows = []
        shared_specs = [("INTRO", "引言文案", "引言文案", "intro", "INTRO", "")]
        for uid, object_label, copy_type, script_type, block_key, asset_block_label in shared_specs:
            copy_count = block_counts.get((script_type, block_key), 0)
            voice_count = self._asset_count(assets, uid=uid, asset_type="voice", account_label=account["label"], block_label=asset_block_label)
            issues = []
            if copy_count:
                summary["copy"] += 1
            else:
                summary["missing_copy"] += 1
                issues.append("缺文案")
            if voice_count:
                summary["voice"] += 1
            else:
                summary["missing_voice"] += 1
                issues.append("缺配音")
            if self._has_expired_voice(assets, uid=uid, account_label=account["label"], hashes=block_hashes.get((script_type, block_key), set()), block_label=asset_block_label):
                issues.append("配音过期")
            rows.append((project["category_name"], account["label"], object_label, copy_type, copy_count, "--", "--", voice_count, "，".join(issues)))
        return rows

    def _product_row(
        self,
        project: dict[str, Any],
        account: dict[str, Any],
        product: dict[str, Any],
        assets: list[dict[str, Any]],
        block_counts: dict[tuple[str, str], int],
        block_hashes: dict[tuple[str, str], set[str]],
        summary: dict[str, int],
    ) -> tuple[Any, ...]:
        uid = product["uid"]
        copy_count = block_counts.get(("product", uid), 0)
        image_count = self._asset_count(assets, uid=uid, asset_type="image", account_label=account["label"])
        video_count = self._asset_count(assets, uid=uid, asset_type="video")
        voice_count = self._asset_count(assets, uid=uid, asset_type="voice", account_label=account["label"])
        issues = []
        for key, count, label in [
            ("copy", copy_count, "缺文案"),
            ("image", image_count, "缺图片"),
            ("video", video_count, "缺视频"),
            ("voice", voice_count, "缺配音"),
        ]:
            if count:
                summary[key] += 1
            else:
                summary[f"missing_{key}"] += 1
                issues.append(label)
        if int(product["removed_from_master"]):
            issues.append("已从 Master 移除")
        if self._has_expired_voice(assets, uid=uid, account_label=account["label"], hashes=block_hashes.get(("product", uid), set())):
            issues.append("配音过期")
        return (project["category_name"], account["label"], f"{product['price_label']} / {uid} / {product['title']}", "商品文案", copy_count, image_count, video_count, voice_count, "，".join(issues))

    def _asset_count(self, assets: list[dict[str, Any]], *, uid: str, asset_type: str, account_label: str = "", block_label: str = "") -> int:
        return sum(
            1
            for asset in assets
            if asset["uid"] == uid
            and asset["asset_type"] == asset_type
            and asset["status"] == "ready"
            and (not account_label or asset["account_label"] == account_label or not asset["account_label"])
            and (not block_label or asset["block_label"] == block_label)
        )

    def _has_expired_voice(self, assets: list[dict[str, Any]], *, uid: str, account_label: str, hashes: set[str], block_label: str = "") -> bool:
        if not hashes:
            return False
        for asset in assets:
            if asset["uid"] != uid or asset["asset_type"] != "voice" or asset["status"] != "ready":
                continue
            if account_label and asset["account_label"] != account_label:
                continue
            if block_label and asset["block_label"] != block_label:
                continue
            text_hash = safe_text(asset["text_hash"])
            if text_hash and text_hash not in hashes:
                return True
        return False

    def _row_matches_filter(self, row: tuple[Any, ...]) -> bool:
        issue = str(row[-1] or "")
        value = self.status_var.get()
        if value == "全部":
            return True
        return value in issue

    def _import_legacy_accounts(self) -> None:
        accounts = self.legacy_import.import_accounts()
        voices = self.legacy_import.import_voice_profiles()
        messagebox.showinfo("导入完成", f"用户 {accounts} 个，音色 {voices} 个。")
        self.refresh()

    def _import_screen_light(self) -> None:
        try:
            result = self.legacy_import.import_category_project(
                parent_category="数码",
                category="屏幕挂灯",
                md_path=Path(r"G:\WriteSpace\B站-文案脚本\10_b站文案\3.商品文案\数码-屏幕挂灯.md"),
            )
        except Exception as exc:
            messagebox.showerror("导入失败", str(exc))
            return
        self.app.set_current_project(result["project_id"])
        messagebox.showinfo("导入完成", f"屏幕挂灯已导入：商品/文案/素材映射已写入数据库。\n{result}")
        self.refresh()


class SyncPage(BasePage):
    def __init__(self, master, app: App):
        super().__init__(master, app)
        self.project_var = tk.StringVar()
        self.user_var = tk.StringVar(value="全部")
        self._build()

    def _build(self) -> None:
        self.rowconfigure(1, weight=0)
        self.rowconfigure(99, weight=1)
        top = ttk.Frame(self)
        top.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        top.columnconfigure(1, weight=1)
        ttk.Label(top, text="本次品类项目").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.project_combo = ttk.Combobox(top, textvariable=self.project_var, state="readonly")
        self.project_combo.grid(row=0, column=1, sticky="ew", padx=(0, 12))
        self.project_combo.bind("<<ComboboxSelected>>", lambda _event: self._select_project())
        ttk.Label(top, text="用户").grid(row=0, column=2, sticky="w", padx=(0, 8))
        self.user_combo = ttk.Combobox(top, textvariable=self.user_var, state="readonly", width=14)
        self.user_combo.grid(row=0, column=3, sticky="w", padx=(0, 12))
        self.user_combo.bind("<<ComboboxSelected>>", lambda _event: self.refresh())
        ttk.Button(top, text="刷新状态", command=self.refresh).grid(row=0, column=4, padx=(0, 8))
        ttk.Button(top, text="一键同步当前品类", command=self._sync_all).grid(row=0, column=5, padx=(0, 8))
        ttk.Button(top, text="打开数据库目录", command=lambda: open_path(self.db.path.parent)).grid(row=0, column=6)

        grid = ttk.Frame(self)
        grid.grid(row=1, column=0, sticky="ew")
        grid.columnconfigure(0, weight=1, uniform="sync_cards")
        grid.columnconfigure(1, weight=1, uniform="sync_cards")
        grid.rowconfigure(0, weight=1)
        grid.rowconfigure(1, weight=1)
        self.master_status = self._section(
            grid,
            "Master 方案商品",
            0,
            0,
            [
                ("预览变化", self._preview_master),
                ("同步 Master", self._sync_master),
                ("打开品类项目", lambda: self.app.show_page("品类项目")),
            ],
        )
        self.md_status = self._section(
            grid,
            "MD 文案",
            0,
            1,
            [
                ("打开 MD", self._open_md),
                ("打开所在文件夹", self._open_md_folder),
                ("同步 MD", self._sync_md),
                ("创建/更新文案框架", self._init_outline),
            ],
        )
        self.folder_status = self._section(
            grid,
            "素材文件夹",
            1,
            0,
            [
                ("打开图片目录", lambda: self._open_project_path("image_root")),
                ("打开视频目录", lambda: self._open_project_path("video_root")),
                ("打开配音目录", lambda: self._open_project_path("voice_root")),
                ("扫描素材", self._sync_assets),
            ],
        )
        self.mapping_status = self._section(
            grid,
            "映射关系与缺口",
            1,
            1,
            [
                ("查看资产中心", lambda: self.app.show_page("资产中心")),
                ("导入旧项目用户/音色", self._import_legacy_accounts),
                ("导入屏幕挂灯资产", self._import_screen_light),
            ],
        )

        log_frame = ttk.LabelFrame(self, text="最近同步记录", padding=10)
        log_frame.grid(row=99, column=0, sticky="nsew", pady=(10, 0))
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)
        self.log_tree = ttk.Treeview(log_frame, columns=("时间", "类型", "状态", "说明"), show="headings", height=10)
        for col in ("时间", "类型", "状态", "说明"):
            self.log_tree.heading(col, text=col)
            width = 150 if col == "时间" else 110 if col in {"类型", "状态"} else 420
            self.log_tree.column(col, width=width, anchor="w", stretch=(col == "说明"))
        self.log_tree.grid(row=0, column=0, sticky="nsew")
        ybar = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_tree.yview)
        self.log_tree.configure(yscrollcommand=ybar.set)
        ybar.grid(row=0, column=1, sticky="ns")

    def _section(self, parent: ttk.Frame, title: str, row: int, column: int, buttons: list[tuple[str, Callable[[], None]]]) -> ttk.Label:
        frame = ttk.LabelFrame(parent, text=title, padding=10)
        frame.grid(row=row, column=column, sticky="nsew", padx=(0 if column == 0 else 8, 8 if column == 0 else 0), pady=(0, 8))
        frame.columnconfigure(0, weight=1)
        label = ttk.Label(frame, text="等待刷新", justify="left", anchor="nw", wraplength=480)
        label.grid(row=0, column=0, sticky="nsew")
        actions = ttk.Frame(frame)
        actions.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        for text, command in buttons:
            ttk.Button(actions, text=text, command=command).pack(side="left", padx=(0, 6), pady=2)
        frame.bind("<Configure>", lambda event, item=label: item.configure(wraplength=max(event.width - 28, 260)))
        return label

    def refresh(self) -> None:
        projects = self.repo.projects()
        values = [f"{item['id']} - {item['name']}" for item in projects]
        self.project_combo.configure(values=values)
        project = self.app.current_project()
        if not project and projects:
            self.app.current_project_id = projects[0]["id"]
            project = projects[0]
        if project:
            value = f"{project['id']} - {project['name']}"
            if self.project_var.get() != value:
                self.project_var.set(value)
        users = ["全部"] + [item["label"] for item in self.repo.accounts()]
        self.user_combo.configure(values=users)
        if self.user_var.get() not in users:
            self.user_var.set("全部")
        self._refresh_status()
        self._refresh_logs()

    def _select_project(self) -> None:
        value = self.project_var.get()
        if not value:
            return
        self.app.current_project_id = int(value.split(" - ", 1)[0])
        self.refresh()

    def _current_project_or_warn(self) -> dict[str, Any] | None:
        project = self.app.current_project()
        if not project:
            messagebox.showinfo("需要品类项目", "请先选择品类项目。")
            return None
        return project

    def _refresh_status(self) -> None:
        project = self.app.current_project()
        if not project:
            for label in (self.master_status, self.md_status, self.folder_status, self.mapping_status):
                label.configure(text="请先创建或选择品类项目。")
            return
        products = self.repo.products(project["id"], include_removed=False)
        blocks = self.repo.script_blocks(project["id"])
        assets = self.repo.asset_bindings(project["id"])
        intro_count = sum(1 for block in blocks if block["script_type"] == "intro")
        product_block_count = sum(1 for block in blocks if block["script_type"] == "product")
        price_count = sum(1 for block in blocks if block["script_type"] == "price_transition")
        asset_counts = {
            "image": sum(1 for item in assets if item["asset_type"] == "image" and item["status"] == "ready"),
            "video": sum(1 for item in assets if item["asset_type"] == "video" and item["status"] == "ready"),
            "voice": sum(1 for item in assets if item["asset_type"] == "voice" and item["status"] == "ready"),
        }
        issues = build_project_issue_summary(project, products, blocks, assets, self.repo.accounts(), selected_user=self.user_var.get())
        last_master = self._last_event(project["id"], "master_scheme_sync")
        last_md = self._last_event(project["id"], "markdown_sync")
        last_asset = self._last_event(project["id"], "asset_sync")
        self.master_status.configure(
            text=(
                f"方案：{project['scheme_name'] or '--'}\n"
                f"商品：{len(products)} 个\n"
                f"上次同步：{last_master or '未同步'}"
            )
        )
        self.md_status.configure(
            text=(
                f"MD：{compact_path(project['md_path'], 58) or '--'}\n"
                f"引言 {intro_count}，商品文案 {product_block_count}，价格过渡 {price_count}\n"
                f"上次同步：{last_md or '未同步'}"
            )
        )
        self.folder_status.configure(
            text=(
                f"图片：{compact_path(project['image_root'], 48)}\n"
                f"视频：{compact_path(project['video_root'], 48)}\n"
                f"配音：{compact_path(project['voice_root'], 48)}\n"
                f"已识别 图片 {asset_counts['image']} / 视频 {asset_counts['video']} / 配音 {asset_counts['voice']}\n"
                f"上次扫描：{last_asset or '未扫描'}"
            )
        )
        self.mapping_status.configure(
            text=(
                f"筛选用户：{self.user_var.get()}\n"
                f"缺文案 {len(issues['missing_copy'])}，缺图片 {len(issues['missing_image'])}，缺视频 {len(issues['missing_video'])}，"
                f"缺配音 {len(issues['missing_voice'])}，配音过期 {len(issues['expired_voice'])}\n"
                f"{format_issue_preview(issues, limit=3)}"
            )
        )

    def _refresh_logs(self) -> None:
        self.log_tree.delete(*self.log_tree.get_children())
        project = self.app.current_project()
        if not project:
            return
        rows = self.db.fetchall("SELECT * FROM sync_events WHERE project_id=? ORDER BY id DESC LIMIT 80", (project["id"],))
        for item in rows:
            self.log_tree.insert("", "end", values=(item["created_at"], item["event_type"], item["status"], item["message"]))

    def _last_event(self, project_id: int, event_type: str) -> str:
        row = self.db.fetchone(
            "SELECT created_at, message FROM sync_events WHERE project_id=? AND event_type=? ORDER BY id DESC LIMIT 1",
            (project_id, event_type),
        )
        return f"{row['created_at']} | {row['message']}" if row else ""

    def _preview_master(self) -> None:
        project = self._current_project_or_warn()
        if not project:
            return

        def work() -> dict[str, list[dict[str, Any]]]:
            return self.sync.sync_master_scheme(project["id"], apply_changes=False)

        def on_success(result: dict[str, list[dict[str, Any]]]) -> None:
            messagebox.showinfo("Master 变化预览", format_master_result(result))
            self.refresh()

        self.run_task("预览 Master 变化", work, on_success=on_success, success_message="Master 变化预览完成")

    def _sync_master(self) -> None:
        project = self._current_project_or_warn()
        if not project:
            return

        def work() -> dict[str, list[dict[str, Any]]]:
            return self.sync.sync_master_scheme(project["id"], apply_changes=True)

        def on_success(result: dict[str, list[dict[str, Any]]]) -> None:
            self.toast(f"Master 已同步：新增 {len(result['added'])}，更新 {len(result['updated'])}，移除 {len(result['removed'])}")
            self.refresh()

        self.run_task("同步 Master", work, on_success=on_success, success_message="Master 已同步", show_success_toast=False)

    def _sync_md(self) -> None:
        project = self._current_project_or_warn()
        if not project:
            return

        def work() -> dict[str, Any]:
            return self.sync.sync_markdown(project["id"])

        def on_success(result: dict[str, Any]) -> None:
            self.toast(f"MD 已同步：入库 {result['upserted']} 条，缺文案 {len(result['missing_copy'])} 个")
            self.refresh()

        self.run_task("同步 MD", work, on_success=on_success, success_message="MD 已同步", show_success_toast=False)

    def _sync_assets(self) -> None:
        project = self._current_project_or_warn()
        if not project:
            return

        def work() -> dict[str, int]:
            return self.sync.sync_assets(project["id"])

        def on_success(result: dict[str, int]) -> None:
            self.toast(f"素材扫描完成：图片 {result['image']}，视频 {result['video']}，配音 {result['voice']}，未识别 {result['unmatched']}")
            self.refresh()

        self.run_task("扫描素材", work, on_success=on_success, success_message="素材扫描完成", show_success_toast=False)

    def _sync_all(self) -> None:
        project = self._current_project_or_warn()
        if not project:
            return

        def work() -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any], dict[str, int]]:
            master = self.sync.sync_master_scheme(project["id"], apply_changes=True)
            md = self.sync.sync_markdown(project["id"])
            assets = self.sync.sync_assets(project["id"])
            return master, md, assets

        def on_success(result: tuple[dict[str, list[dict[str, Any]]], dict[str, Any], dict[str, int]]) -> None:
            master, md, assets = result
            self.toast(
                f"一键同步完成：Master 新增 {len(master['added'])}，MD 入库 {md['upserted']}，素材未识别 {assets['unmatched']}",
                duration=4500,
            )
            self.refresh()

        self.run_task("一键同步", work, on_success=on_success, success_message="一键同步完成", show_success_toast=False)

    def _init_outline(self) -> None:
        project = self._current_project_or_warn()
        if not project:
            return
        current = safe_text(project.get("md_path")) or str(self.outline.default_markdown_path(project["id"]))
        path = filedialog.asksaveasfilename(
            defaultextension=".md",
            filetypes=[("Markdown", "*.md"), ("All", "*.*")],
            initialdir=str(DEFAULT_MARKDOWN_ROOT),
            initialfile=Path(current).name,
        )
        if not path:
            return

        def work() -> tuple[dict[str, Any], dict[str, Any]]:
            result = self.outline.init_or_update_outline(project["id"], path)
            sync_result = self.sync.sync_markdown(project["id"])
            return result, sync_result

        def on_success(result: tuple[dict[str, Any], dict[str, Any]]) -> None:
            outline, sync_result = result
            self.toast(
                f"文案框架已更新：商品 {outline['total']} 个，新增 {len(outline['added'])}，入库 {sync_result['upserted']} 条",
                duration=4500,
            )
            self.refresh()

        self.run_task("创建文案框架", work, on_success=on_success, success_message="文案框架已更新", show_success_toast=False)

    def _open_project_path(self, key: str) -> None:
        project = self._current_project_or_warn()
        if project:
            open_path(project.get(key))

    def _open_md(self) -> None:
        project = self._current_project_or_warn()
        if project:
            open_path(project.get("md_path"))

    def _open_md_folder(self) -> None:
        project = self._current_project_or_warn()
        if project and project.get("md_path"):
            open_path(Path(project["md_path"]).parent)

    def _import_legacy_accounts(self) -> None:
        def work() -> tuple[int, int]:
            accounts = self.legacy_import.import_accounts()
            voices = self.legacy_import.import_voice_profiles()
            return accounts, voices

        def on_success(result: tuple[int, int]) -> None:
            accounts, voices = result
            self.toast(f"导入完成：用户 {accounts} 个，音色 {voices} 个")
            self.refresh()

        self.run_task("导入旧项目用户/音色", work, on_success=on_success, success_message="导入完成", show_success_toast=False)

    def _import_screen_light(self) -> None:
        def work() -> dict[str, Any]:
            return self.legacy_import.import_category_project(
                parent_category="数码",
                category="屏幕挂灯",
                md_path=Path(r"G:\WriteSpace\B站-文案脚本\10_b站文案\3.商品文案\数码-屏幕挂灯.md"),
            )

        def on_success(result: dict[str, Any]) -> None:
            self.app.set_current_project(result["project_id"])
            self.toast("屏幕挂灯已导入：商品、文案、素材映射已写入数据库", duration=4500)
            self.refresh()

        self.run_task("导入屏幕挂灯资产", work, on_success=on_success, success_message="导入完成", show_success_toast=False)


class AccountPage(TablePage):
    columns = ("用户名称", "账号标识", "音色标识", "音色名称", "素材身份", "结尾配音", "启用")

    def __init__(self, master, app: App):
        super().__init__(master, app)
        self.vars = {key: tk.StringVar() for key in ["label", "account_id", "voice_id", "voice_name", "media_identity", "closing_audio_path"]}
        form = ttk.LabelFrame(self, text="新增/更新用户", padding=10)
        form.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        form.columnconfigure(1, weight=1)
        labels = [
            ("用户名称", "label"),
            ("账号标识", "account_id"),
            ("音色标识", "voice_id"),
            ("音色名称", "voice_name"),
            ("素材身份", "media_identity"),
            ("结尾配音路径", "closing_audio_path"),
        ]
        for index, (label, key) in enumerate(labels):
            ttk.Label(form, text=label).grid(row=index // 2, column=(index % 2) * 2, sticky="w", padx=(0, 8), pady=4)
            ttk.Entry(form, textvariable=self.vars[key]).grid(row=index // 2, column=(index % 2) * 2 + 1, sticky="ew", padx=(0, 8), pady=4)
        ttk.Button(form, text="保存用户", command=self._save_account).grid(row=3, column=0, sticky="w", pady=6)
        ttk.Button(form, text="导入旧项目用户/音色", command=self._import_legacy_accounts).grid(row=3, column=1, sticky="w", pady=6)
        ttk.Label(form, text="说明：用户名称就是小燃、小博、小歪这类账号；音色标识用于生成对应配音。").grid(row=4, column=0, columnspan=4, sticky="w", pady=6)
        self._build_table()

    def _save_account(self) -> None:
        payload = {key: var.get().strip() for key, var in self.vars.items()}
        if not payload["label"]:
            messagebox.showwarning("缺少标签", "请填写用户标签，例如小燃。")
            return
        ts = now_iso()
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO accounts (label, account_id, voice_id, voice_name, media_identity, closing_audio_path, enabled, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
                ON CONFLICT(label) DO UPDATE SET
                    account_id=excluded.account_id,
                    voice_id=excluded.voice_id,
                    voice_name=excluded.voice_name,
                    media_identity=excluded.media_identity,
                    closing_audio_path=excluded.closing_audio_path,
                    updated_at=excluded.updated_at
                """,
                (payload["label"], payload["account_id"], payload["voice_id"], payload["voice_name"], payload["media_identity"], payload["closing_audio_path"], ts, ts),
            )
        self.refresh()
        self.toast("用户已保存")

    def _import_legacy_accounts(self) -> None:
        def work() -> tuple[int, int]:
            accounts = self.legacy_import.import_accounts()
            voices = self.legacy_import.import_voice_profiles()
            return accounts, voices

        def on_success(result: tuple[int, int]) -> None:
            accounts, voices = result
            self.toast(f"导入完成：用户 {accounts} 个，音色 {voices} 个")
            self.refresh()

        self.run_task("导入旧项目用户/音色", work, on_success=on_success, success_message="导入完成", show_success_toast=False)

    def refresh(self) -> None:
        rows = []
        for item in self.repo.accounts():
            rows.append((item["label"], item["account_id"], item["voice_id"], item["voice_name"], item["media_identity"], compact_path(item["closing_audio_path"], 40), "是" if item["enabled"] else "否"))
        self._set_rows(rows)




class WorkflowPage(BasePage):
    title = ""
    builder: Callable[..., list[str]]

    def __init__(self, master, app: App):
        super().__init__(master, app)
        self.mode_var = tk.StringVar(value="standard")
        self.project_var = tk.StringVar()
        self.account_var = tk.StringVar()
        self.uid_var = tk.StringVar()
        self.intro_var = tk.StringVar(value="1")
        self.intro_choice_var = tk.StringVar()
        self.spoken_md_var = tk.StringVar()
        self.intro_video_var = tk.StringVar()
        self.loaded_project_id: int | None = None

        project_row = ttk.Frame(self)
        project_row.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        project_row.columnconfigure(1, weight=1)
        ttk.Label(project_row, text="本次品类项目").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.project_combo = ttk.Combobox(project_row, textvariable=self.project_var, state="readonly")
        self.project_combo.grid(row=0, column=1, sticky="ew")
        self.project_combo.bind("<<ComboboxSelected>>", lambda _event: self._select_project())

        actions = ttk.Frame(self)
        actions.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(actions, text=self.primary_label()).pack(side="left")
        if isinstance(self, JianyingPage):
            self.account_input = ttk.Entry(actions, textvariable=self.account_var, width=24)
        else:
            self.account_input = ttk.Combobox(actions, textvariable=self.account_var, state="readonly", width=20)
        self.account_input.pack(side="left", padx=8)
        if not isinstance(self, JianyingPage):
            ttk.Label(actions, text=self.uid_label()).pack(side="left")
            ttk.Entry(actions, textvariable=self.uid_var, width=32).pack(side="left", padx=8)
        if isinstance(self, AssemblePage):
            self.mode_var.set("标准模式")
            ttk.Label(actions, text="组合方式").pack(side="left")
            ttk.Combobox(actions, textvariable=self.mode_var, values=["标准模式", "Top 模式"], state="readonly", width=10).pack(side="left", padx=8)
            ttk.Label(actions, text="引言").pack(side="left")
            self.intro_combo = ttk.Combobox(actions, textvariable=self.intro_choice_var, state="readonly", width=20)
            self.intro_combo.pack(side="left", padx=8)
            self.intro_combo.bind("<<ComboboxSelected>>", lambda _event: self._sync_intro_index())
            output_row = ttk.Frame(self)
            output_row.grid(row=2, column=0, sticky="ew", pady=(0, 8))
            output_row.columnconfigure(1, weight=1)
            ttk.Label(output_row, text="口播稿输出 MD（会覆盖全部内容）").grid(row=0, column=0, sticky="w", padx=(0, 8))
            ttk.Entry(output_row, textvariable=self.spoken_md_var).grid(row=0, column=1, sticky="ew", padx=(0, 8))
            ttk.Button(output_row, text="选", width=4, command=self._browse_spoken_md).grid(row=0, column=2, sticky="e")
            ttk.Label(
                output_row,
                text="Top UID 用逗号分隔，支持中文和英文逗号；引言编号按 MD 中“引言文案”从上到下排序。",
            ).grid(row=1, column=1, sticky="w", pady=(4, 0))
        if isinstance(self, JianyingPage):
            output_row = ttk.Frame(self)
            output_row.grid(row=2, column=0, sticky="ew", pady=(0, 8))
            output_row.columnconfigure(1, weight=1)
            ttk.Label(output_row, text="口播稿 MD").grid(row=0, column=0, sticky="w", padx=(0, 8))
            ttk.Entry(output_row, textvariable=self.spoken_md_var).grid(row=0, column=1, sticky="ew", padx=(0, 8))
            ttk.Button(output_row, text="选", width=4, command=self._browse_spoken_md).grid(row=0, column=2, sticky="e")
            ttk.Label(output_row, text="引言成片视频").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=(8, 0))
            ttk.Entry(output_row, textvariable=self.intro_video_var).grid(row=1, column=1, sticky="ew", padx=(0, 8), pady=(8, 0))
            ttk.Button(output_row, text="选", width=4, command=self._browse_intro_video).grid(row=1, column=2, sticky="e", pady=(8, 0))
            ttk.Label(
                output_row,
                text="选择后会先拼这段引言视频，再拼口播稿里的商品推荐部分；不会重复拼 manifest 里的引言条目。",
            ).grid(row=2, column=1, sticky="w", pady=(4, 0))
            template_row = ttk.Frame(self)
            template_row.grid(row=3, column=0, sticky="ew", pady=(0, 8))
            ttk.Label(template_row, text="口播用户").pack(side="left")
            self.jy_user_combo = ttk.Combobox(template_row, textvariable=self.jy_user_var, state="readonly", width=14)
            self.jy_user_combo.pack(side="left", padx=8)
            self.jy_user_combo.bind("<<ComboboxSelected>>", lambda _event: self._on_jy_user_changed())
            ttk.Label(template_row, text="展示模板").pack(side="left", padx=(12, 0))
            self.jy_template_combo = ttk.Combobox(template_row, textvariable=self.jy_template_var, state="readonly", width=18)
            self.jy_template_combo.pack(side="left", padx=8)
        ttk.Button(actions, text="高级：生成命令", command=self._build_command).pack(side="left", padx=8)
        ttk.Button(actions, text="预检查并执行", command=self._run_command).pack(side="left")
        self.log_text = tk.Text(self, height=28, state="disabled")
        self.log_text.grid(row=99, column=0, sticky="nsew")

    def refresh(self) -> None:
        project = self.app.current_project()
        projects = self.repo.projects()
        project_values = [f"{item['id']} - {item['name']}" for item in projects]
        self.project_combo.configure(values=project_values)
        if project:
            current_value = f"{project['id']} - {project['name']}"
            if self.project_var.get() != current_value:
                self.project_var.set(current_value)
            if self.loaded_project_id != project["id"]:
                self.spoken_md_var.set(safe_text(project.get("spoken_md_path")))
                self.loaded_project_id = project["id"]
        if isinstance(getattr(self, "account_input", None), ttk.Combobox):
            values = [item["label"] for item in self.repo.accounts()]
            self.account_input.configure(values=values)
            if values and not self.account_var.get():
                self.account_var.set(values[0])
        if project and not self.spoken_md_var.get().strip():
            self.spoken_md_var.set(safe_text(project.get("spoken_md_path")))
        if isinstance(self, AssemblePage):
            self._refresh_intro_choices(project)
        if isinstance(self, JianyingPage):
            users = [item["label"] for item in self.repo.accounts()]
            self.jy_user_combo.configure(values=users)
            if users and not self.jy_user_var.get():
                self.jy_user_var.set(users[0])
            self._refresh_jy_templates()
            if not self.account_var.get().strip():
                user = self.jy_user_var.get()
                if user:
                    self.account_var.set(f"完整-5月-{user}")

    def primary_label(self) -> str:
        if isinstance(self, JianyingPage):
            return "草稿名"
        if isinstance(self, VoicePage):
            return "配音用户"
        return "口播用户"

    def uid_label(self) -> str:
        if isinstance(self, AssemblePage):
            return "Top 商品UID（可不填）"
        if isinstance(self, VoicePage):
            return "商品UID（可不填）"
        return "预留"

    def _select_project(self) -> None:
        value = self.project_var.get()
        if not value:
            return
        project_id = int(value.split(" - ", 1)[0])
        self.app.set_current_project(project_id)

    def _refresh_intro_choices(self, project: dict[str, Any] | None) -> None:
        combo = getattr(self, "intro_combo", None)
        if combo is None:
            return
        choices: list[str] = []
        if project:
            intro_blocks = [block for block in self.repo.script_blocks(project["id"]) if block["script_type"] == "intro"]
            for index, block in enumerate(intro_blocks, start=1):
                choices.append(f"{index} - {safe_text(block.get('block_label')) or '引言'}")
        combo.configure(values=choices)
        if choices:
            current = self.intro_choice_var.get()
            if current not in choices:
                wanted = max(1, int(self.intro_var.get() or "1"))
                self.intro_choice_var.set(choices[min(wanted, len(choices)) - 1])
            self._sync_intro_index()
        else:
            self.intro_choice_var.set("")
            self.intro_var.set("1")

    def _sync_intro_index(self) -> None:
        match = re.match(r"\s*(\d+)", self.intro_choice_var.get())
        self.intro_var.set(match.group(1) if match else "1")

    def _on_jy_user_changed(self) -> None:
        self._refresh_jy_templates()
        if not self.account_var.get().strip():
            user = self.jy_user_var.get()
            if user:
                self.account_var.set(f"完整-5月-{user}")

    def _refresh_jy_templates(self) -> None:
        from .template_config import available_templates

        user = self.jy_user_var.get()
        templates = available_templates(user)
        self.jy_template_combo.configure(values=templates)
        if templates:
            current = self.jy_template_var.get()
            if current not in templates:
                self.jy_template_var.set(templates[0])
        else:
            self.jy_template_var.set("")

    def _command(self) -> list[str]:
        project = self.project_required()
        if not project:
            return []
        if isinstance(self, VoicePage):
            uids = parse_uid_list(self.uid_var.get())
            return self.workflow.build_voice_command(project["id"], account_label=self.account_var.get().strip(), uids=uids or None)
        if isinstance(self, AssemblePage):
            top_uids = parse_uid_list(self.uid_var.get())
            mode = "top" if self.mode_var.get() == "Top 模式" else "standard"
            return self.workflow.build_assembly_command(
                project["id"],
                mode=mode,
                top_uids=top_uids or None,
                account_label=self.account_var.get().strip(),
                intro_index=int(self.intro_var.get() or "1"),
                output_markdown_path=self._remember_spoken_md(project["id"]),
            )
        return self.workflow.build_jianying_command(
            project["id"],
            draft_name=self.account_var.get().strip(),
            spoken_markdown_path=self._remember_spoken_md(project["id"]),
            intro_video_path=self.intro_video_var.get().strip(),
            display_template=self.jy_template_var.get().strip(),
        )

    def _browse_spoken_md(self) -> None:
        project = self.app.current_project()
        default_name = safe_text(project.get("name")) if project else "口播稿"
        path = filedialog.asksaveasfilename(
            defaultextension=".md",
            filetypes=[("Markdown", "*.md"), ("All", "*.*")],
            initialdir=str(DEFAULT_SPOKEN_MD_ROOT),
            initialfile=f"{default_name or '口播稿'}.md",
        )
        if path:
            self.spoken_md_var.set(path.replace("/", "\\"))

    def _browse_intro_video(self) -> None:
        project = self.app.current_project()
        initial_dir = safe_text(project.get("video_root")) if project else ""
        path = filedialog.askopenfilename(
            filetypes=[("Video", "*.mp4 *.mov *.mkv *.avi *.webm"), ("All", "*.*")],
            initialdir=initial_dir or str(DEFAULT_VIDEO_ROOT),
        )
        if path:
            self.intro_video_var.set(path.replace("/", "\\"))

    def _remember_spoken_md(self, project_id: int) -> str:
        path = self.spoken_md_var.get().strip()
        if path:
            self.db.execute("UPDATE projects SET spoken_md_path=?, updated_at=? WHERE id=?", (path, now_iso(), project_id))
        return path

    def _build_command(self) -> None:
        try:
            cmd = self._command()
        except Exception as exc:
            messagebox.showerror("生成失败", str(exc))
            return
        self.log(" ".join(f'"{part}"' if " " in part else part for part in cmd))

    def _run_command(self) -> None:
        try:
            if not self._confirm_precheck():
                return
            cmd = self._command()
        except Exception as exc:
            messagebox.showerror("执行失败", str(exc))
            return
        progress_dialog = TaskProgressDialog(self, self._running_dialog_title(), self._running_dialog_message())
        progress_dialog.append("即将执行：")
        progress_dialog.append(" ".join(f'"{part}"' if " " in part else part for part in cmd))
        progress_dialog.append("")

        def work() -> Any:
            return self.workflow.run_command(cmd)

        def on_success(result: Any) -> None:
            self.log(result.stdout or "")
            if result.stderr:
                self.log(result.stderr)
            self.log(f"退出码：{result.returncode}")
            progress_dialog.append(result.stdout or "")
            if result.stderr:
                progress_dialog.append(result.stderr)
            progress_dialog.append(f"退出码：{result.returncode}")
            if result.returncode == 0:
                progress_dialog.finish("执行完成，可以关闭窗口。", kind="success")
                self.toast("执行完成")
            else:
                progress_dialog.finish(f"执行结束，退出码：{result.returncode}", kind="warning")
                self.toast(f"执行结束，退出码：{result.returncode}", kind="warning", duration=4500)

        def on_error(exc: Exception, tb: str) -> None:
            progress_dialog.append(tb or str(exc))
            progress_dialog.finish(f"执行失败：{exc}", kind="error")
            messagebox.showerror("执行失败", str(exc))

        started = self.run_task(
            "执行任务",
            work,
            on_success=on_success,
            on_error=on_error,
            success_message="任务执行完成",
            show_success_toast=False,
        )
        if not started:
            progress_dialog.destroy()

    def _running_dialog_title(self) -> str:
        if isinstance(self, JianyingPage):
            return "正在生成剪映草稿"
        if isinstance(self, VoicePage):
            return "正在生成配音"
        if isinstance(self, AssemblePage):
            return "正在组合口播稿"
        return "正在执行任务"

    def _running_dialog_message(self) -> str:
        if isinstance(self, JianyingPage):
            return "剪映草稿正在生成中，通常需要几分钟。窗口会在执行结束后显示结果。"
        if isinstance(self, VoicePage):
            return "配音任务正在执行中，请等待当前任务结束后再继续操作。"
        if isinstance(self, AssemblePage):
            return "口播稿正在组合中，请等待当前任务结束后再继续操作。"
        return "任务正在执行中，请等待当前任务结束后再继续操作。"

    def _confirm_precheck(self) -> bool:
        project = self.project_required()
        if not project:
            return False
        if isinstance(self, VoicePage):
            message, can_continue = self._voice_precheck(project)
            return show_precheck_dialog(self, "确认生成配音", message, can_continue=can_continue)
        if isinstance(self, JianyingPage):
            self._remember_spoken_md(project["id"])
            message, can_continue = self._jianying_precheck(project)
            return show_precheck_dialog(self, "生成剪映草稿预检查", message, can_continue=can_continue)
        return True

    def _voice_precheck(self, project: dict[str, Any]) -> tuple[str, bool]:
        account_label = self.account_var.get().strip()
        selected_uids = parse_uid_list(self.uid_var.get())
        products = {item["uid"]: item for item in self.repo.products(project["id"], include_removed=False)}
        blocks = self.repo.script_blocks(project["id"])
        assets = self.repo.asset_bindings(project["id"])
        selected = set(selected_uids)
        unknown_uids = [uid for uid in selected_uids if uid not in products]
        product_blocks = [
            block
            for block in blocks
            if block["script_type"] == "product" and (not selected or block["owner_uid"] in selected)
        ]
        shared_blocks = [] if selected else [block for block in blocks if block["script_type"] in {"intro", "price_transition"}]
        pending: list[str] = []
        skipped: list[str] = []
        blocked: list[str] = []
        for uid in selected_uids:
            if uid in products and not any(block["owner_uid"] == uid for block in product_blocks):
                blocked.append(f"{uid} {products[uid]['title']}：缺文案")
        for uid in unknown_uids:
            blocked.append(f"{uid}：当前品类项目中没有这个商品")
        for block in product_blocks:
            product = products.get(block["owner_uid"], {})
            display = f"{block['owner_uid']} {safe_text(product.get('title'))} / {block['block_label']}"
            state = voice_state(assets, uid=block["owner_uid"], account_label=account_label, hashes={block["text_hash"]})
            if state == "ready":
                skipped.append(f"{display}：已有配音")
            elif state == "expired":
                pending.append(f"{display}：配音过期，将重生成")
            else:
                pending.append(f"{display}：缺配音，将生成")
        for block in shared_blocks:
            if block["script_type"] == "intro":
                display = f"引言文案 / {block['block_label']}"
                state = voice_state(assets, uid="INTRO", account_label=account_label, hashes={block["text_hash"]})
            else:
                display = f"价格过渡 {block['price_range_label']} / {block['block_label']}"
                state = voice_state(
                    assets,
                    uid="PRICE_TRANSITION",
                    account_label=account_label,
                    hashes={block["text_hash"]},
                    block_label=block["price_range_label"],
                )
            if state == "ready":
                skipped.append(f"{display}：已有配音")
            elif state == "expired":
                pending.append(f"{display}：配音过期，将重生成")
            else:
                pending.append(f"{display}：缺配音，将生成")
        selected_text = "全部文案" if not selected_uids else "、".join(selected_uids)
        lines = [
            "本次配音生成预览",
            "",
            f"品类：{project['name']}",
            f"用户：{account_label or '未选择'}",
            f"范围：{selected_text}",
            "",
            "统计",
            f"- 待生成 / 重生成：{len(pending)} 条",
            f"- 已有配音跳过：{len(skipped)} 条",
            f"- 缺文案 / 不可处理：{len(blocked)} 条",
            "",
            "待生成明细",
            *preview_lines(pending),
            "",
            "已有跳过明细",
            *preview_lines(skipped),
            "",
            "缺失 / 不可处理",
            *preview_lines(blocked),
            "",
            "确认后会先执行底层脚本；已有配音由脚本继续跳过，缺失和过期会生成。",
        ]
        return "\n".join(lines), bool(account_label) and bool(pending or skipped or blocked)

    def _jianying_precheck(self, project: dict[str, Any]) -> tuple[str, bool]:
        path_text = self.spoken_md_var.get().strip() or safe_text(project.get("spoken_md_path"))
        if not path_text:
            return "还没有选择口播稿 MD。\n\n请先在“组合口播稿”生成口播稿和 manifest。", False
        spoken_path = Path(path_text)
        manifest = self.workflow.spoken_manifest_path(project["id"], spoken_path)
        intro_video_text = self.intro_video_var.get().strip()
        intro_video_path = Path(intro_video_text) if intro_video_text else None
        missing_manifest = not manifest.exists()
        products = self.repo.products(project["id"], include_removed=False)
        blocks = self.repo.script_blocks(project["id"])
        assets = self.repo.asset_bindings(project["id"])
        selected_user = "全部"
        missing_files: list[str] = []
        missing_product_videos: list[str] = []
        manifest_error = ""
        if manifest.exists():
            try:
                payload = json.loads(manifest.read_text(encoding="utf-8-sig"))
                selected_user = manifest_account_label(payload) or selected_user
                missing_product_videos = manifest_product_video_gaps(payload)
                for path in manifest_file_paths(payload):
                    if not Path(path).exists():
                        missing_files.append(path)
            except Exception as exc:
                manifest_error = str(exc)
        issues = build_project_issue_summary(project, products, blocks, assets, self.repo.accounts(), selected_user=selected_user)
        display_template = self.jy_template_var.get().strip()
        lines = [
            "生成剪映草稿预检查",
            "",
            f"品类：{project['name']}",
            f"口播稿：{spoken_path}",
            f"Manifest：{manifest}",
            f"口播用户：{selected_user}",
            f"展示模板：{display_template or '未选择'}",
            f"引言成片视频：{intro_video_path if intro_video_path else '未选择，将使用 manifest 内的引言配音'}",
            f"草稿输出：{DEFAULT_JIANYING_DRAFT_ROOT}",
            "",
            "阻塞问题",
        ]
        if missing_manifest:
            lines.append("- 缺 manifest：还没有组合口播稿，不能生成剪映草稿。")
        else:
            lines.append("- manifest 已找到")
        if manifest_error:
            lines.append(f"- manifest 读取失败：{manifest_error}")
        if intro_video_path is not None:
            if intro_video_path.exists():
                lines.append("- 引言成片视频已找到，生成时会过滤 manifest 里的引言条目")
            else:
                lines.append(f"- 引言成片视频不存在：{intro_video_path}")
        if missing_files:
            lines.append(f"- manifest 中有 {len(missing_files)} 个文件路径不存在")
            lines.extend(f"  {item}" for item in missing_files[:10])
            if len(missing_files) > 10:
                lines.append(f"  ... 其余 {len(missing_files) - 10} 个已省略")
        if missing_product_videos:
            lines.append(f"- 有 {len(missing_product_videos)} 个商品没有展示视频，将只显示商品图")
            lines.extend(f"  {item}" for item in missing_product_videos[:10])
            if len(missing_product_videos) > 10:
                lines.append(f"  ... 其余 {len(missing_product_videos) - 10} 个已省略")
        lines += [
            "",
            "数据库缺口",
            f"- 缺图片：{len(issues['missing_image'])}",
            f"- 缺视频：{len(issues['missing_video'])}",
            f"- 缺配音：{len(issues['missing_voice'])}",
            f"- 配音过期：{len(issues['expired_voice'])}",
            format_issue_preview(issues),
        ]
        return "\n".join(lines), not missing_manifest and not manifest_error and (intro_video_path is None or intro_video_path.exists())


class VoicePage(WorkflowPage):
    pass


class AssemblePage(WorkflowPage):
    pass


class JianyingPage(WorkflowPage):
    def __init__(self, master, app: App):
        self.jy_user_var = tk.StringVar()
        self.jy_template_var = tk.StringVar()
        super().__init__(master, app)


def parse_uid_list(value: str) -> list[str]:
    return [item.strip() for item in re.split(r"[,，]+", value or "") if item.strip()]


def open_path(path: str | Path | None) -> None:
    text = safe_text(path)
    if not text:
        messagebox.showinfo("打开失败", "当前没有配置路径。")
        return
    target = Path(text)
    if not target.exists():
        messagebox.showwarning("打开失败", f"路径不存在：\n{target}")
        return
    os.startfile(str(target))


def format_master_result(result: dict[str, list[dict[str, Any]]]) -> str:
    lines = [
        f"新增 {len(result['added'])}，更新 {len(result['updated'])}，移除 {len(result['removed'])}",
        "",
    ]
    for key, label in (("added", "新增"), ("updated", "更新"), ("removed", "移除")):
        items = result.get(key) or []
        lines.append(label)
        if not items:
            lines.append("无")
            continue
        for item in items[:20]:
            lines.append(f"- {item.get('uid', '')} {item.get('title', '')} {item.get('price_label', '')}".strip())
        if len(items) > 20:
            lines.append(f"... 其余 {len(items) - 20} 个已省略")
        lines.append("")
    return "\n".join(lines).strip()


def build_project_issue_summary(
    project: dict[str, Any],
    products: list[dict[str, Any]],
    blocks: list[dict[str, Any]],
    assets: list[dict[str, Any]],
    accounts: list[dict[str, Any]],
    *,
    selected_user: str = "全部",
) -> dict[str, list[str]]:
    product_blocks: dict[str, list[dict[str, Any]]] = {}
    product_hashes: dict[str, set[str]] = {}
    intro_hashes: set[str] = set()
    for block in blocks:
        if block["script_type"] == "product":
            product_blocks.setdefault(block["owner_uid"], []).append(block)
            product_hashes.setdefault(block["owner_uid"], set()).add(block["text_hash"])
        elif block["script_type"] == "intro":
            intro_hashes.add(block["text_hash"])

    labels = [item["label"] for item in accounts if selected_user == "全部" or item["label"] == selected_user]
    if not labels:
        labels = [selected_user] if selected_user != "全部" else []
    issues = {"missing_copy": [], "missing_image": [], "missing_video": [], "missing_voice": [], "expired_voice": []}
    for product in products:
        uid = product["uid"]
        title = product["title"]
        display = f"{uid} {title}"
        if uid not in product_blocks:
            issues["missing_copy"].append(display)
        if not has_ready_asset(assets, uid=uid, asset_type="image"):
            issues["missing_image"].append(display)
        if not has_ready_asset(assets, uid=uid, asset_type="video"):
            issues["missing_video"].append(display)
        if uid in product_blocks:
            for label in labels:
                state = voice_state(assets, uid=uid, account_label=label, hashes=product_hashes.get(uid, set()))
                if state == "missing":
                    issues["missing_voice"].append(f"{label} / {display}")
                elif state == "expired":
                    issues["expired_voice"].append(f"{label} / {display}")
    for label in labels:
        if intro_hashes:
            state = voice_state(assets, uid="INTRO", account_label=label, hashes=intro_hashes)
            if state == "missing":
                issues["missing_voice"].append(f"{label} / 引言文案")
            elif state == "expired":
                issues["expired_voice"].append(f"{label} / 引言文案")
    return issues


def has_ready_asset(assets: list[dict[str, Any]], *, uid: str, asset_type: str, account_label: str = "", block_label: str = "") -> bool:
    return any(
        asset["uid"] == uid
        and asset["asset_type"] == asset_type
        and asset["status"] == "ready"
        and (not account_label or asset["account_label"] == account_label or not asset["account_label"])
        and (not block_label or asset["block_label"] == block_label)
        for asset in assets
    )


def voice_state(assets: list[dict[str, Any]], *, uid: str, account_label: str, hashes: set[str], block_label: str = "") -> str:
    matching_uid = [
        asset
        for asset in assets
        if asset["uid"] == uid
        and asset["asset_type"] == "voice"
        and asset["status"] == "ready"
        and (not account_label or asset["account_label"] == account_label or not asset["account_label"])
        and (not block_label or asset["block_label"] == block_label)
    ]
    if not matching_uid:
        return "missing"
    if hashes and any(safe_text(asset.get("text_hash")) in hashes for asset in matching_uid):
        return "ready"
    if any(safe_text(asset.get("text_hash")) for asset in matching_uid):
        return "expired"
    return "ready"


def format_issue_preview(issues: dict[str, list[str]], limit: int = 8) -> str:
    parts = []
    for key, label in (
        ("missing_copy", "缺文案"),
        ("missing_image", "缺图片"),
        ("missing_video", "缺视频"),
        ("missing_voice", "缺配音"),
        ("expired_voice", "配音过期"),
    ):
        items = issues.get(key) or []
        if not items:
            continue
        shown = "；".join(items[:limit])
        suffix = f"；另有 {len(items) - limit} 个" if len(items) > limit else ""
        parts.append(f"{label}：{shown}{suffix}")
    return "\n".join(parts) if parts else "当前筛选下没有明显缺口。"


def preview_lines(items: list[str], limit: int = 18) -> list[str]:
    if not items:
        return ["无"]
    lines = [f"{index}. {item}" for index, item in enumerate(items[:limit], start=1)]
    if len(items) > limit:
        lines.append(f"... 其余 {len(items) - limit} 条已省略")
    return lines


def show_precheck_dialog(parent: tk.Widget, title: str, message: str, *, can_continue: bool = True) -> bool:
    dialog = tk.Toplevel(parent)
    dialog.title(title)
    dialog.geometry("780x620")
    dialog.minsize(620, 420)
    dialog.transient(parent.winfo_toplevel())
    dialog.grab_set()
    dialog.rowconfigure(1, weight=1)
    dialog.columnconfigure(0, weight=1)
    ttk.Label(dialog, text=title, font=("Microsoft YaHei UI", 14, "bold")).grid(row=0, column=0, sticky="w", padx=16, pady=(14, 8))
    text = tk.Text(dialog, wrap="word")
    text.grid(row=1, column=0, sticky="nsew", padx=16, pady=(0, 12))
    text.insert("1.0", message)
    text.configure(state="disabled")
    buttons = ttk.Frame(dialog)
    buttons.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 14))
    buttons.columnconfigure(0, weight=1)
    result = {"ok": False}

    def close(ok: bool) -> None:
        result["ok"] = ok
        dialog.destroy()

    ttk.Button(buttons, text="取消", command=lambda: close(False)).grid(row=0, column=1, padx=(0, 8))
    if can_continue:
        ttk.Button(buttons, text="确认继续", command=lambda: close(True)).grid(row=0, column=2)
    else:
        ttk.Button(buttons, text="知道了", command=lambda: close(False)).grid(row=0, column=2)
    dialog.protocol("WM_DELETE_WINDOW", lambda: close(False))
    dialog.wait_window()
    return result["ok"]


class TaskProgressDialog(tk.Toplevel):
    def __init__(self, parent: tk.Widget, title: str, message: str):
        super().__init__(parent)
        self.title(title)
        self.geometry("680x430")
        self.minsize(560, 340)
        self.transient(parent.winfo_toplevel())
        self.rowconfigure(3, weight=1)
        self.columnconfigure(0, weight=1)

        self.status_var = tk.StringVar(value=message)
        ttk.Label(self, text=title, font=("Microsoft YaHei UI", 14, "bold")).grid(
            row=0,
            column=0,
            sticky="w",
            padx=16,
            pady=(14, 6),
        )
        ttk.Label(self, textvariable=self.status_var, wraplength=620).grid(row=1, column=0, sticky="ew", padx=16)
        self.progress = ttk.Progressbar(self, mode="indeterminate")
        self.progress.grid(row=2, column=0, sticky="ew", padx=16, pady=(12, 10))
        self.progress.start(12)
        self.text = tk.Text(self, height=10, wrap="word", state="disabled")
        self.text.grid(row=3, column=0, sticky="nsew", padx=16, pady=(0, 12))
        buttons = ttk.Frame(self)
        buttons.grid(row=4, column=0, sticky="ew", padx=16, pady=(0, 14))
        buttons.columnconfigure(0, weight=1)
        self.close_button = ttk.Button(buttons, text="关闭", command=self.destroy)
        self.close_button.grid(row=0, column=1)
        self.close_button.state(["disabled"])
        self.protocol("WM_DELETE_WINDOW", self._ignore_close)
        self.lift()
        self.focus_set()

    def append(self, text: str) -> None:
        if not self.winfo_exists():
            return
        value = text.rstrip()
        if not value:
            return
        self.text.configure(state="normal")
        self.text.insert("end", value + "\n")
        self.text.see("end")
        self.text.configure(state="disabled")

    def finish(self, message: str, *, kind: str = "success") -> None:
        if not self.winfo_exists():
            return
        self.progress.stop()
        self.status_var.set(message)
        if kind == "error":
            self.bell()
        self.close_button.state(["!disabled"])
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.close_button.focus_set()

    def _ignore_close(self) -> None:
        self.bell()


def manifest_file_paths(value: Any) -> list[str]:
    suffixes = (".wav", ".mp3", ".png", ".jpg", ".jpeg", ".webp", ".mp4", ".mov", ".mkv", ".avi")
    paths: list[str] = []

    def walk(item: Any) -> None:
        if isinstance(item, dict):
            for child in item.values():
                walk(child)
        elif isinstance(item, list):
            for child in item:
                walk(child)
        elif isinstance(item, str):
            text = item.strip()
            if len(text) > 2 and text.lower().endswith(suffixes) and (":\\" in text or text.startswith("\\\\")):
                paths.append(text)

    walk(value)
    return list(dict.fromkeys(paths))


def manifest_product_video_gaps(value: Any) -> list[str]:
    if not isinstance(value, dict) or not isinstance(value.get("entries"), list):
        return []
    gaps: list[str] = []
    for entry in value["entries"]:
        if not isinstance(entry, dict) or safe_text(entry.get("type")) != "product":
            continue
        if safe_text(entry.get("video_path")) or safe_text(entry.get("display_video_path")):
            continue
        uid = safe_text(entry.get("product_uid"))
        name = safe_text(entry.get("product_name"))
        gaps.append(" ".join(part for part in [uid, name] if part))
    return gaps


def manifest_account_label(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    label = safe_text(value.get("account_label"))
    if label:
        return label
    entries = value.get("entries")
    if not isinstance(entries, list):
        return ""
    for entry in entries:
        if isinstance(entry, dict):
            label = safe_text(entry.get("account_label"))
            if label:
                return label
    return ""


PAGE_MAP = {
    "品类项目": ProjectPage,
    "文案中心": CopyPage,
    "资产中心": AssetPage,
    "同步中心": SyncPage,
    "用户管理": AccountPage,
    "生成配音": VoicePage,
    "组合口播稿": AssemblePage,
    "生成剪映草稿": JianyingPage,
}
