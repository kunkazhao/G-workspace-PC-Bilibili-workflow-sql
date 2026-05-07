from __future__ import annotations

import json
import os
import re
import threading
import tkinter as tk
import traceback
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable

import customtkinter as ctk

from .components import (
    AppCard,
    AppComboBox,
    AppEntry,
    AppLabel,
    AppTextbox,
    BasePage,
    DangerButton,
    FormRow,
    GhostButton,
    NavButton,
    PrimaryButton,
)
from .db import Database
from .legacy_import import LegacyImportService
from .master_data import MasterDataService, display_name
from .outline_service import OutlineService
from .repositories import Repository
from .settings import (
    DB_PATH,
    DEFAULT_IMAGE_ROOT,
    DEFAULT_JIANYING_DRAFT_ROOT,
    DEFAULT_MARKDOWN_ROOT,
    DEFAULT_SPOKEN_MD_ROOT,
    DEFAULT_VIDEO_ROOT,
    DEFAULT_VOICE_ROOT,
    INTERNAL_WORKSPACE_ROOT,
)
from .style_config import UIStyle
from .sync_service import SyncService
from .utils import compact_path, now_iso, safe_text
from .workflow_service import WorkflowService


ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")

UI_VERSION = "G-UI-2026-05-08-r4"


# ── 工具函数 ──


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
    lines = [f"新增 {len(result['added'])}，更新 {len(result['updated'])}，移除 {len(result['removed'])}", ""]
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
    issues: dict[str, list[str]] = {"missing_copy": [], "missing_image": [], "missing_video": [], "missing_voice": [], "expired_voice": []}
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
    dialog = ctk.CTkToplevel(parent)
    dialog.title(title)
    dialog.geometry("780x620")
    dialog.minsize(620, 420)
    dialog.transient(parent.winfo_toplevel())
    dialog.grab_set()
    dialog.rowconfigure(1, weight=1)
    dialog.columnconfigure(0, weight=1)
    ctk.CTkLabel(dialog, text=title, font=UIStyle.FONT_H2).grid(row=0, column=0, sticky="w", padx=UIStyle.PAD_LG, pady=(UIStyle.PAD_LG, UIStyle.PAD_SM))
    text = ctk.CTkTextbox(dialog, wrap="word")
    text.grid(row=1, column=0, sticky="nsew", padx=UIStyle.PAD_LG, pady=(0, UIStyle.PAD_MD))
    text.insert("1.0", message)
    text.configure(state="disabled")
    buttons = ctk.CTkFrame(dialog, fg_color="transparent")
    buttons.grid(row=2, column=0, sticky="ew", padx=UIStyle.PAD_LG, pady=(0, UIStyle.PAD_LG))
    buttons.columnconfigure(0, weight=1)
    result = {"ok": False}

    def close(ok: bool) -> None:
        result["ok"] = ok
        dialog.destroy()

    GhostButton(buttons, text="取消", command=lambda: close(False)).grid(row=0, column=1, padx=(0, UIStyle.PAD_SM))
    if can_continue:
        PrimaryButton(buttons, text="确认继续", command=lambda: close(True)).grid(row=0, column=2)
    else:
        GhostButton(buttons, text="知道了", command=lambda: close(False)).grid(row=0, column=2)
    dialog.protocol("WM_DELETE_WINDOW", lambda: close(False))
    dialog.wait_window()
    return result["ok"]


class TaskProgressDialog(ctk.CTkToplevel):
    def __init__(self, parent: tk.Widget, title: str, message: str):
        super().__init__(parent)
        self.title(title)
        self.geometry("680x430")
        self.minsize(560, 340)
        self.transient(parent.winfo_toplevel())
        self.rowconfigure(3, weight=1)
        self.columnconfigure(0, weight=1)

        self.status_var = ctk.StringVar(value=message)
        ctk.CTkLabel(self, text=title, font=UIStyle.FONT_H2).grid(
            row=0, column=0, sticky="w", padx=UIStyle.PAD_LG, pady=(UIStyle.PAD_LG, UIStyle.PAD_SM)
        )
        ctk.CTkLabel(self, textvariable=self.status_var, wraplength=620).grid(row=1, column=0, sticky="ew", padx=UIStyle.PAD_LG)
        self.progress = ctk.CTkProgressBar(self, mode="indeterminate")
        self.progress.grid(row=2, column=0, sticky="ew", padx=UIStyle.PAD_LG, pady=(UIStyle.PAD_MD, UIStyle.PAD_SM))
        self.progress.start()
        self.text = ctk.CTkTextbox(self, wrap="word")
        self.text.grid(row=3, column=0, sticky="nsew", padx=UIStyle.PAD_LG, pady=(0, UIStyle.PAD_MD))
        buttons = ctk.CTkFrame(self, fg_color="transparent")
        buttons.grid(row=4, column=0, sticky="ew", padx=UIStyle.PAD_LG, pady=(0, UIStyle.PAD_LG))
        buttons.columnconfigure(0, weight=1)
        self.close_button = GhostButton(buttons, text="关闭", command=self.destroy)
        self.close_button.grid(row=0, column=1)
        self.close_button.configure(state="disabled")
        self.protocol("WM_DELETE_WINDOW", lambda: None)
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
        self.close_button.configure(state="normal")
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.close_button.focus_set()


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


# ── 主应用 ──


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("B-Workflow SQL 资产工作台")
        self.geometry("1400x900")
        self.minsize(1180, 760)
        self.state("zoomed")

        self._busy = False
        self._toast_label: ctk.CTkLabel | None = None
        self._toast_after: str | None = None

        self.db = Database()
        self.repo = Repository(self.db)
        self.sync = SyncService(self.db)
        self.workflow = WorkflowService(self.db)
        self.outline = OutlineService(self.db)
        self.legacy_import = LegacyImportService(self.db)
        self.master_data = MasterDataService()

        self.current_project_id: int | None = self.db.latest_project_id()
        self.pages: dict[str, ctk.CTkFrame] = {}
        self.nav_buttons: dict[str, NavButton] = {}

        self._build_shell()
        self.show_page("品类项目")

    def _build_shell(self) -> None:
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # Sidebar
        sidebar = ctk.CTkFrame(self, fg_color=UIStyle.COLOR_SIDEBAR_BG, width=UIStyle.SIDEBAR_WIDTH, corner_radius=0)
        sidebar.grid(row=0, column=0, sticky="ns")
        sidebar.grid_propagate(False)

        title = ctk.CTkLabel(sidebar, text="B-Workflow SQL", font=UIStyle.FONT_H1, text_color=UIStyle.COLOR_PRIMARY)
        title.pack(pady=(UIStyle.PAD_XL, UIStyle.PAD_MD), padx=UIStyle.PAD_MD, anchor="w")

        nav_frame = ctk.CTkScrollableFrame(sidebar, fg_color="transparent")
        nav_frame.pack(fill="both", expand=True, padx=UIStyle.PAD_SM)

        groups = {
            "配置": ["品类项目", "文案中心", "资产中心", "同步中心", "用户管理"],
            "工作流": ["生成配音", "组合口播稿", "生成剪映草稿"],
        }
        for group, names in groups.items():
            ctk.CTkLabel(
                nav_frame, text=group,
                font=UIStyle.FONT_SMALL, text_color=UIStyle.COLOR_TEXT_DIM,
            ).pack(anchor="w", padx=UIStyle.PAD_SM, pady=(UIStyle.PAD_MD, UIStyle.PAD_XS))
            for name in names:
                btn = NavButton(nav_frame, text=name, command=lambda page=name: self.show_page(page))
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

    def show_page(self, name: str) -> None:
        for btn_name, btn in self.nav_buttons.items():
            btn.set_active(btn_name == name)
        for page in self.pages.values():
            page.grid_remove()
        if name not in self.pages:
            try:
                page_cls = PAGE_MAP[name]
                self.pages[name] = page_cls(self.content_frame, app=self)
                self.pages[name].grid(row=0, column=0, sticky="nsew")
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
        for page in self.pages.values():
            page.refresh()

    def set_status(self, text: str) -> None:
        self.status_var.set(text or "就绪")

    def toast(self, text: str, *, kind: str = "success", duration: int = 3000) -> None:
        colors = {
            "success": UIStyle.COLOR_SUCCESS,
            "info": UIStyle.COLOR_INFO,
            "warning": UIStyle.COLOR_WARNING,
            "error": UIStyle.COLOR_ERROR,
        }
        bg = colors.get(kind, UIStyle.COLOR_SUCCESS)
        if self._toast_after:
            self.after_cancel(self._toast_after)
            self._toast_after = None
        if self._toast_label:
            self._toast_label.destroy()
        self._toast_label = ctk.CTkLabel(
            self, text=text, fg_color=bg, text_color="white",
            corner_radius=UIStyle.RADIUS_MD, font=UIStyle.FONT_BODY,
            padx=UIStyle.PAD_LG, pady=UIStyle.PAD_SM,
        )
        self._toast_label.place(relx=1.0, y=UIStyle.PAD_XL, x=-UIStyle.PAD_XL, anchor="ne")
        self._toast_label.lift()
        self._toast_after = self.after(duration, self._toast_label.place_forget)

    def run_background(
        self, title: str, work: Callable[[], Any],
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


def _build_table(parent, columns: tuple[str, ...], row: int = 0) -> ttk.Treeview:
    """在 CTkFrame 内创建深色风格的 Treeview。"""
    style = ttk.Style()
    style.theme_use("clam")
    try:
        style.layout("CTreeview")
    except tk.TclError:
        style.layout("CTreeview", style.layout("Treeview"))
    style.configure("CTreeview", background=UIStyle.COLOR_TABLE_ROW, foreground=UIStyle.COLOR_TEXT_MAIN,
                     fieldbackground=UIStyle.COLOR_TABLE_ROW, font=UIStyle.FONT_TABLE,
                     rowheight=28)
    try:
        style.layout("CTreeview.Heading")
    except tk.TclError:
        try:
            style.layout("CTreeview.Heading", style.layout("Treeview.Heading"))
        except tk.TclError:
            pass
    style.configure("CTreeview.Heading", background=UIStyle.COLOR_TABLE_HEADER, foreground=UIStyle.COLOR_TEXT_MAIN,
                     font=UIStyle.FONT_TABLE, relief="flat")
    style.map("CTreeview", background=[("selected", UIStyle.COLOR_PRIMARY)],
              foreground=[("selected", "white")])
    style.map("CTreeview.Heading", background=[("active", UIStyle.COLOR_NAV_HOVER)])
    tree = ttk.Treeview(parent, columns=columns, show="headings", style="CTreeview")
    for col in columns:
        tree.heading(col, text=col)
        tree.column(col, width=100, anchor="w")
    tree.grid(row=row, column=0, sticky="nsew")
    parent.grid_rowconfigure(row, weight=1)
    parent.grid_columnconfigure(0, weight=1)
    ybar = ttk.Scrollbar(parent, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=ybar.set)
    ybar.grid(row=row, column=1, sticky="ns")
    return tree


def _set_tree_rows(tree: ttk.Treeview, rows: list[tuple[Any, ...]], tag_key: int | None = None) -> None:
    """向 Treeview 填充数据，可选按最后一列打 tag。"""
    tree.delete(*tree.get_children())
    for row in rows:
        tags = ("has_issues",) if tag_key is not None and row[tag_key] else ()
        tree.insert("", "end", values=row, tags=tags)


class ProjectPage(BasePage):
    def __init__(self, master, app: App):
        super().__init__(master, "品类项目", app)
        self.project_var = ctk.StringVar()
        self.workspace_var = ctk.StringVar()
        self.parent_category_var = ctk.StringVar()
        self.child_category_var = ctk.StringVar()
        self.scheme_var = ctk.StringVar()
        self.workspaces: list[dict[str, Any]] = []
        self.category_tree: list[dict[str, Any]] = []
        self.schemes: list[dict[str, Any]] = []
        self.fields: dict[str, ctk.StringVar] = {key: ctk.StringVar() for key in [
            "name", "workspace_id", "workspace_name",
            "category_parent_id", "category_parent_name",
            "category_id", "category_name",
            "scheme_id", "scheme_name",
            "md_path", "spoken_md_path",
            "image_root", "video_root", "voice_root", "output_root",
        ]}
        self._build()

    def _build(self) -> None:
        content = self.content

        # Project selector
        sel = ctk.CTkFrame(content, fg_color="transparent")
        sel.pack(fill="x", pady=(0, UIStyle.PAD_SM))
        ctk.CTkLabel(sel, text="当前项目", font=UIStyle.FONT_BODY, text_color=UIStyle.COLOR_TEXT_DIM).pack(side="left")
        self.project_combo = AppComboBox(sel, width=400, variable=self.project_var)
        self.project_combo.pack(side="left", padx=UIStyle.PAD_SM)
        self.project_combo.configure(command=self._select_project)
        PrimaryButton(sel, text="新建", width=80, command=self._new_project).pack(side="left", padx=UIStyle.PAD_XS)
        PrimaryButton(sel, text="保存", width=80, command=self._save_project).pack(side="left", padx=UIStyle.PAD_XS)

        # Master card
        card = AppCard(content, "从 Master 选择品类方案")
        f = ctk.CTkFrame(card, fg_color="transparent")
        f.columnconfigure(1, weight=1)
        f.columnconfigure(3, weight=1)
        r = 0
        ctk.CTkLabel(f, text="项目名称", font=UIStyle.FONT_BODY, text_color=UIStyle.COLOR_TEXT_DIM).grid(row=r, column=0, sticky="w", padx=(0, UIStyle.PAD_SM), pady=UIStyle.PAD_XS)
        AppEntry(f, textvariable=self.fields["name"]).grid(row=r, column=1, columnspan=3, sticky="ew", pady=UIStyle.PAD_XS)
        r += 1
        ctk.CTkLabel(f, text="Master 工作空间", font=UIStyle.FONT_BODY, text_color=UIStyle.COLOR_TEXT_DIM).grid(row=r, column=0, sticky="w", padx=(0, UIStyle.PAD_SM), pady=UIStyle.PAD_XS)
        ctk.CTkLabel(f, text="赵二（默认）", font=UIStyle.FONT_BODY).grid(row=r, column=1, sticky="w", pady=UIStyle.PAD_XS)
        GhostButton(f, text="刷新 Master", command=lambda: self._load_workspaces(force_refresh=True)).grid(row=r, column=2, columnspan=2, sticky="w", padx=UIStyle.PAD_SM, pady=UIStyle.PAD_XS)
        r += 1
        ctk.CTkLabel(f, text="一级品类", font=UIStyle.FONT_BODY, text_color=UIStyle.COLOR_TEXT_DIM).grid(row=r, column=0, sticky="w", padx=(0, UIStyle.PAD_SM), pady=UIStyle.PAD_XS)
        self.parent_combo = AppComboBox(f, width=300, variable=self.parent_category_var)
        self.parent_combo.grid(row=r, column=1, sticky="ew", padx=(0, UIStyle.PAD_MD), pady=UIStyle.PAD_XS)
        self.parent_combo.configure(command=self._on_parent_selected)
        ctk.CTkLabel(f, text="二级品类", font=UIStyle.FONT_BODY, text_color=UIStyle.COLOR_TEXT_DIM).grid(row=r, column=2, sticky="w", padx=(0, UIStyle.PAD_SM), pady=UIStyle.PAD_XS)
        self.child_combo = AppComboBox(f, width=300, variable=self.child_category_var)
        self.child_combo.grid(row=r, column=3, sticky="ew", pady=UIStyle.PAD_XS)
        self.child_combo.configure(command=self._on_child_selected)
        r += 1
        ctk.CTkLabel(f, text="Master 方案", font=UIStyle.FONT_BODY, text_color=UIStyle.COLOR_TEXT_DIM).grid(row=r, column=0, sticky="w", padx=(0, UIStyle.PAD_SM), pady=UIStyle.PAD_XS)
        self.scheme_combo = AppComboBox(f, width=400, variable=self.scheme_var)
        self.scheme_combo.grid(row=r, column=1, columnspan=3, sticky="ew", pady=UIStyle.PAD_XS)
        self.scheme_combo.configure(command=self._on_scheme_selected)
        card.add_content(f)

        # Paths card
        path_card = AppCard(content, "文案与素材来源")
        pf = ctk.CTkFrame(path_card, fg_color="transparent")
        pf.columnconfigure(1, weight=1)
        pf.columnconfigure(3, weight=1)
        labels = [("商品文案 MD", "md_path"), ("图片根目录", "image_root"), ("视频根目录", "video_root"), ("配音根目录", "voice_root")]
        for index, (label, key) in enumerate(labels):
            i = index // 2
            j = (index % 2) * 2
            ctk.CTkLabel(pf, text=label, font=UIStyle.FONT_BODY, text_color=UIStyle.COLOR_TEXT_DIM).grid(row=i, column=j, sticky="w", padx=(0, UIStyle.PAD_SM), pady=UIStyle.PAD_XS)
            AppEntry(pf, textvariable=self.fields[key]).grid(row=i, column=j + 1, sticky="ew", padx=(0, UIStyle.PAD_SM), pady=UIStyle.PAD_XS)
            GhostButton(pf, text="选", width=50, command=lambda item=key: self._browse(item)).grid(row=i, column=j + 1, sticky="e", padx=(0, UIStyle.PAD_SM))
        path_card.add_content(pf)

        # Actions
        act = ctk.CTkFrame(content, fg_color="transparent")
        act.pack(fill="x", pady=(0, UIStyle.PAD_MD))
        PrimaryButton(act, text="创建/更新文案框架", command=self._init_outline).pack(side="left", padx=(0, UIStyle.PAD_SM))
        ctk.CTkLabel(act, text="Master、MD、素材同步请到“同步中心”统一操作。", font=UIStyle.FONT_SMALL, text_color=UIStyle.COLOR_TEXT_DIM).pack(side="left", padx=UIStyle.PAD_SM)

        # Log
        self.log_text = AppTextbox(content, height=200)
        self.log_text.pack(fill="both", expand=True, pady=(UIStyle.PAD_SM, 0))

    def _browse(self, key: str) -> None:
        if key == "md_path":
            path = filedialog.askopenfilename(filetypes=[("Markdown", "*.md"), ("All", "*.*")], initialdir=str(DEFAULT_MARKDOWN_ROOT))
        else:
            path = filedialog.askdirectory()
        if path:
            self.fields[key].set(path.replace("/", "\\"))

    def _new_project(self) -> None:
        for var in self.fields.values():
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
        return {key: var.get().strip() for key, var in self.fields.items()}

    def _save_project(self) -> None:
        payload = self._payload()
        payload["id"] = self.app.current_project_id if self.project_var.get() else 0
        if not payload["name"]:
            messagebox.showwarning("缺少项目名", "请填写项目名。")
            return
        project_id = self.db.upsert_project(payload)
        self.app.set_current_project(project_id)
        self.log(f"已保存项目：{payload['name']}")
        self.toast("项目已保存")

    def _select_project(self, _=None) -> None:
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

    def log(self, text: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", text.rstrip() + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _init_outline(self) -> None:
        project = self.app.current_project()
        if not project:
            messagebox.showinfo("需要品类项目", "请先在“品类项目”中创建或选择项目。")
            return
        if not self.fields["md_path"].get().strip():
            self.fields["md_path"].set(str(self.outline.default_markdown_path(project["id"])))
        path = filedialog.asksaveasfilename(
            defaultextension=".md", filetypes=[("Markdown", "*.md"), ("All", "*.*")],
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
            self.log(f"文案框架已更新：商品 {result['total']} 个，新增 {len(result['added'])}，保留 {len(result['preserved'])}。")
            self.log(f"已同步 MD 到数据库：入库 {sync_result['upserted']} 条。")
            self.toast("文案框架已更新")

        self.app.run_background("创建文案框架", work, on_success=on_success, success_message="文案框架已更新", show_success_toast=False)

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

        def work() -> dict[str, Any]:
            workspaces = self.master_data.fetch_workspaces(force_refresh=force_refresh)
            workspace = next((w for w in workspaces if safe_text(w.get("name")) == "赵二" or safe_text(w.get("slug")) == "zhaoer"), workspaces[0] if workspaces else None)
            tree, source = [], ""
            if workspace:
                _w, tree, source = self.master_data.fetch_category_tree(safe_text(workspace.get("id")))
            return {"workspaces": workspaces, "workspace": workspace, "tree": tree, "source": source}

        def on_success(result: dict[str, Any]) -> None:
            self.workspaces = result["workspaces"]
            workspace = result["workspace"]
            if workspace:
                self.workspace_var.set(display_name(workspace))
                self._apply_category_tree(result["tree"], source=result["source"], keep_existing=bool(self.fields["category_name"].get().strip()))
            if not quiet:
                self.log(f"已读取 Master 工作空间，当前固定使用：{self.workspace_var.get() or '赵二'}。")

        def on_error(exc: Exception, _tb: str) -> None:
            if not quiet:
                messagebox.showerror("读取 Master 失败", str(exc))

        self.app.run_background("读取 Master", work, on_success=on_success, on_error=on_error,
                                on_done=lambda: setattr(self, "_workspaces_loading", False),
                                success_message=None if quiet else "Master 已刷新", silent=quiet)

    def _apply_category_tree(self, tree: list[dict[str, Any]], *, source: str, keep_existing: bool) -> None:
        self.category_tree = tree
        parent_names = [safe_text(p.get("name")) for p in tree]
        self.parent_combo.configure(values=parent_names)
        self.parent_category_var.set("")
        self.child_category_var.set("")
        self.scheme_var.set("")
        self.scheme_combo.configure(values=[])
        if parent_names:
            saved = self.fields["category_parent_name"].get().strip() if keep_existing else ""
            self.parent_category_var.set(saved if saved in parent_names else parent_names[0])
            self._on_parent_selected()
        self.log(f"已读取 Master 品类：{len(parent_names)} 个一级品类（来源：{source}）。")

    def _on_parent_selected(self, _=None) -> None:
        parent = self._selected_parent()
        if not parent:
            return
        self.fields["category_parent_id"].set(safe_text(parent.get("id")))
        self.fields["category_parent_name"].set(safe_text(parent.get("name")))
        children = parent.get("children") or []
        child_names = [safe_text(c.get("name")) for c in children if safe_text(c.get("name"))]
        self.child_combo.configure(values=child_names)
        self.child_category_var.set(child_names[0] if child_names else "")
        self.scheme_var.set("")
        self.scheme_combo.configure(values=[])
        if child_names:
            self._on_child_selected()

    def _on_child_selected(self, _=None) -> None:
        workspace = self._selected_workspace()
        child = self._selected_child()
        if not workspace or not child:
            return
        self.fields["category_id"].set(safe_text(child.get("id")))
        self.fields["category_name"].set(safe_text(child.get("name")))
        self.scheme_combo.configure(values=[])
        self.scheme_var.set("读取中...")

        def work() -> tuple[list[dict[str, Any]], str]:
            return self.master_data.fetch_schemes(workspace_id=safe_text(workspace.get("id")), category_id=safe_text(child.get("id")))

        def on_success(result: tuple[list[dict[str, Any]], str]) -> None:
            self.schemes, source = result
            names = [display_name(s, safe_text(s.get("id"))) for s in self.schemes]
            self.scheme_combo.configure(values=names)
            if names:
                self.scheme_var.set(names[0])
                self._on_scheme_selected()
            self.log(f"已读取“{safe_text(child.get('name'))}”方案：{len(names)} 个（来源：{source}）。")

        def on_error(exc: Exception, _tb: str) -> None:
            self.scheme_var.set("")
            messagebox.showerror("读取方案失败", str(exc))

        self.app.run_background("读取方案", work, on_success=on_success, on_error=on_error, success_message=None, silent=True)

    def _on_scheme_selected(self, _=None) -> None:
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

    def _selected_workspace(self) -> dict[str, Any] | None:
        name = self.workspace_var.get()
        for w in self.workspaces:
            if display_name(w) == name:
                return w
        return None

    def _selected_parent(self) -> dict[str, Any] | None:
        name = self.parent_category_var.get()
        for p in self.category_tree:
            if safe_text(p.get("name")) == name:
                return p
        return None

    def _selected_child(self) -> dict[str, Any] | None:
        parent = self._selected_parent()
        if not parent:
            return None
        name = self.child_category_var.get()
        for c in parent.get("children") or []:
            if safe_text(c.get("name")) == name:
                return c
        return None


class CopyPage(BasePage):
    def __init__(self, master, app: App):
        super().__init__(master, "文案中心", app)
        self.category_var = ctk.StringVar()
        self._body_map: dict[str, str] = {}

        top = ctk.CTkFrame(self.content, fg_color="transparent")
        top.pack(fill="x", pady=(0, UIStyle.PAD_MD))
        ctk.CTkLabel(top, text="品类", font=UIStyle.FONT_BODY, text_color=UIStyle.COLOR_TEXT_DIM).pack(side="left")
        self.category_combo = AppComboBox(top, width=200, variable=self.category_var)
        self.category_combo.pack(side="left", padx=UIStyle.PAD_SM)
        self.category_combo.configure(command=self._on_category_changed)
        ctk.CTkLabel(top, text="单击正文可查看完整内容。同步 MD 请到“同步中心”。", font=UIStyle.FONT_SMALL, text_color=UIStyle.COLOR_TEXT_DIM).pack(side="left", padx=UIStyle.PAD_LG)

        outer = ctk.CTkFrame(self.content, fg_color=UIStyle.COLOR_CARD_BG, corner_radius=UIStyle.RADIUS_LG)
        outer.pack(fill="both", expand=True)
        outer.grid_columnconfigure(0, weight=1)
        outer.grid_rowconfigure(0, weight=1)

        self.tree = _build_table(outer, CopyPageColumns, row=0)
        for col, width in COLUMN_WIDTHS.items():
            self.tree.column(col, width=width)
        self.tree.bind("<ButtonRelease-1>", self._on_body_click)

    def _on_category_changed(self, _=None) -> None:
        self.refresh()

    def _on_body_click(self, event: tk.Event) -> None:
        region = self.tree.identify_region(event.x, event.y)
        if region != "cell":
            return
        col_index = int(self.tree.identify_column(event.x).replace("#", "")) - 1
        if col_index != 5:
            return
        row_id = self.tree.identify_row(event.y)
        if not row_id:
            return
        full_body = self._body_map.get(row_id, "")
        if full_body:
            self._show_body_popup(full_body)

    def _show_body_popup(self, text: str) -> None:
        dialog = ctk.CTkToplevel(self)
        dialog.title("正文内容")
        dialog.geometry("700x500")
        dialog.minsize(500, 300)
        dialog.transient(self.winfo_toplevel())
        dialog.grab_set()
        txt = ctk.CTkTextbox(dialog, wrap="word", padx=UIStyle.PAD_LG, pady=UIStyle.PAD_LG, font=("Microsoft YaHei", 12))
        txt.pack(fill="both", expand=True)
        txt.insert("1.0", text)
        txt.configure(state="disabled")
        GhostButton(dialog, text="关闭", command=dialog.destroy).pack(pady=(0, UIStyle.PAD_MD))
        dialog.update_idletasks()
        x = dialog.winfo_screenwidth() // 2 - dialog.winfo_width() // 2
        y = dialog.winfo_screenheight() // 2 - dialog.winfo_height() // 2
        dialog.geometry(f"+{x}+{y}")

    def refresh(self) -> None:
        projects = self.repo.projects()
        if not projects:
            _set_tree_rows(self.tree, [])
            return
        categories = sorted({p["category_name"] for p in projects if p["category_name"]})
        self.category_combo.configure(values=categories)
        if self.category_var.get() not in categories:
            self.category_var.set(categories[0] if categories else "")
        selected = self.category_var.get()
        self._body_map.clear()
        self.tree.delete(*self.tree.get_children())
        block_order = {"intro": 0, "price_transition": 1, "product": 2}
        for proj in projects:
            if proj["category_name"] != selected:
                continue
            pmap = {item["uid"]: item["title"] for item in self.repo.products(proj["id"], include_removed=True)}
            cat = proj["category_name"] or ""
            blocks = list(self.repo.script_blocks(proj["id"]))
            blocks.sort(key=lambda b: (block_order.get(b["script_type"], 99), b.get("owner_uid", ""), b.get("price_range_label", ""), b.get("block_label", "")))
            for block in blocks:
                uid = block["owner_uid"] or ""
                pname = pmap.get(uid, "") if uid else ""
                owner = uid or block["price_range_label"] or ""
                tlabel = TYPE_LABELS.get(block["script_type"], block["script_type"])
                row = (cat, tlabel, owner, pname, block["block_label"], block["body"][:70])
                iid = self.tree.insert("", "end", values=row)
                self._body_map[iid] = block["body"]


CopyPageColumns = ("品类", "类型", "对象UID", "产品名称", "标签", "正文预览")


class AssetPage(BasePage):
    def __init__(self, master, app: App):
        super().__init__(master, "资产中心", app)
        self.category_var = ctk.StringVar(value="")
        self.status_var = ctk.StringVar(value="全部")
        self._default_user_selection_applied = False
        self._refreshing_user_list = False

        # Filters
        filters = ctk.CTkFrame(self.content, fg_color=UIStyle.COLOR_CARD_BG, corner_radius=UIStyle.RADIUS_LG)
        filters.pack(fill="x", pady=(0, UIStyle.PAD_SM))
        filters.columnconfigure(1, weight=0)
        filters.columnconfigure(3, weight=1)
        filters.columnconfigure(5, weight=0)

        ctk.CTkLabel(filters, text="品类", font=UIStyle.FONT_BODY, text_color=UIStyle.COLOR_TEXT_DIM).grid(
            row=0, column=0, sticky="w", padx=(UIStyle.PAD_LG, UIStyle.PAD_SM), pady=UIStyle.PAD_MD
        )
        self.category_combo = AppComboBox(filters, width=180, variable=self.category_var)
        self.category_combo.grid(row=0, column=1, sticky="w", pady=UIStyle.PAD_MD)
        self.category_combo.configure(command=lambda _=None: self.refresh())

        ctk.CTkLabel(filters, text="用户", font=UIStyle.FONT_BODY, text_color=UIStyle.COLOR_TEXT_DIM).grid(
            row=0, column=2, sticky="nw", padx=(UIStyle.PAD_LG, UIStyle.PAD_SM), pady=UIStyle.PAD_MD
        )
        user_box = ctk.CTkFrame(
            filters,
            fg_color=UIStyle.COLOR_INPUT_BG,
            border_color=UIStyle.COLOR_BORDER,
            border_width=1,
            corner_radius=UIStyle.RADIUS_MD,
        )
        user_box.grid(row=0, column=3, sticky="ew", padx=(0, UIStyle.PAD_LG), pady=UIStyle.PAD_MD)
        user_box.columnconfigure(0, weight=1)
        self.user_listbox = tk.Listbox(
            user_box,
            selectmode=tk.MULTIPLE,
            height=4,
            exportselection=False,
            activestyle="none",
            bg=UIStyle.COLOR_INPUT_BG,
            fg=UIStyle.COLOR_TEXT_MAIN,
            selectbackground=UIStyle.COLOR_PRIMARY,
            selectforeground="white",
            font=UIStyle.FONT_BODY,
            borderwidth=0,
            highlightthickness=0,
            relief="flat",
        )
        self.user_listbox.grid(row=0, column=0, sticky="ew", padx=UIStyle.PAD_SM, pady=(UIStyle.PAD_SM, UIStyle.PAD_XS))
        ctk.CTkLabel(
            user_box,
            text="可多选；默认显示小歪、小燃/小然",
            font=UIStyle.FONT_SMALL,
            text_color=UIStyle.COLOR_TEXT_DIM,
        ).grid(row=1, column=0, sticky="w", padx=UIStyle.PAD_SM, pady=(0, UIStyle.PAD_SM))
        self.user_listbox.bind("<<ListboxSelect>>", self._on_user_selection_changed)

        ctk.CTkLabel(filters, text="筛选", font=UIStyle.FONT_BODY, text_color=UIStyle.COLOR_TEXT_DIM).grid(
            row=0, column=4, sticky="w", padx=(0, UIStyle.PAD_SM), pady=UIStyle.PAD_MD
        )
        self.status_combo = AppComboBox(filters, width=140, variable=self.status_var, values=["全部", "缺文案", "缺图片", "缺视频", "缺配音", "配音过期"])
        self.status_combo.grid(row=0, column=5, sticky="w", padx=(0, UIStyle.PAD_LG), pady=UIStyle.PAD_MD)
        self.status_combo.configure(command=lambda _=None: self.refresh())

        self.summary_label = ctk.CTkLabel(self.content, text="", font=UIStyle.FONT_BODY, text_color=UIStyle.COLOR_TEXT_DIM)
        self.summary_label.pack(fill="x", pady=(0, UIStyle.PAD_SM))

        outer = ctk.CTkFrame(self.content, fg_color=UIStyle.COLOR_CARD_BG, corner_radius=UIStyle.RADIUS_LG)
        outer.pack(fill="both", expand=True)
        outer.grid_columnconfigure(0, weight=1)
        outer.grid_rowconfigure(0, weight=1)

        self.tree = _build_table(outer, AssetPageColumns, row=0)
        style = ttk.Style()
        style.configure("AssetIssue.Treeview", background=UIStyle.COLOR_ISSUE_BG, foreground=UIStyle.COLOR_TEXT_MAIN, fieldbackground=UIStyle.COLOR_ISSUE_BG)
        self.tree.tag_configure("has_issues", background=UIStyle.COLOR_ISSUE_BG)

    def refresh(self) -> None:
        projects = self.repo.projects()
        if not projects:
            _set_tree_rows(self.tree, [])
            return
        cats = ["全部"] + sorted({p["category_name"] for p in projects if p["category_name"]})
        self.category_combo.configure(values=cats)
        if self.category_var.get() not in cats:
            self.category_var.set("全部")
        self._refresh_user_choices()

        selected_cat = self.category_var.get()
        selected_users = [self.user_listbox.get(i) for i in self.user_listbox.curselection()]
        rows = []
        summary = {"copy": 0, "missing_copy": 0, "image": 0, "missing_image": 0, "video": 0, "missing_video": 0, "voice": 0, "missing_voice": 0}
        for proj in projects:
            if selected_cat != "全部" and proj["category_name"] != selected_cat:
                continue
            pr, ps = self._rows_for_project(proj, selected_users=selected_users)
            rows.extend(pr)
            for k, v in ps.items():
                summary[k] += v
        rows = [r for r in rows if self._row_matches_filter(r)]
        self.summary_label.configure(
            text=f"文案 {summary['copy']} / 缺 {summary['missing_copy']}  |  "
                 f"图片 {summary['image']} / 缺 {summary['missing_image']}  |  "
                 f"视频 {summary['video']} / 缺 {summary['missing_video']}  |  "
                 f"配音 {summary['voice']} / 缺 {summary['missing_voice']}"
        )
        self.tree.delete(*self.tree.get_children())
        for row in rows:
            tags = ("has_issues",) if row[-1] else ()
            self.tree.insert("", "end", values=row, tags=tags)

    def _on_user_selection_changed(self, _event: tk.Event | None = None) -> None:
        if self._refreshing_user_list:
            return
        self.refresh()

    def _refresh_user_choices(self) -> None:
        current = [self.user_listbox.get(i) for i in self.user_listbox.curselection()]
        labels = [item["label"] for item in self.repo.accounts()]
        if not self._default_user_selection_applied and not current:
            defaults = {"小歪", "小燃", "小然"}
            current = [label for label in labels if label in defaults]
            self._default_user_selection_applied = True

        self._refreshing_user_list = True
        try:
            self.user_listbox.delete(0, "end")
            for index, label in enumerate(labels):
                self.user_listbox.insert("end", label)
                if label in current:
                    self.user_listbox.selection_set(index)
        finally:
            self._refreshing_user_list = False

    def _rows_for_project(self, project: dict[str, Any], *, selected_users: list[str]) -> tuple[list[tuple[Any, ...]], dict[str, int]]:
        blocks = self.repo.script_blocks(project["id"])
        assets = self.repo.asset_bindings(project["id"])
        products = self.repo.products(project["id"], include_removed=True)
        accounts = self.repo.accounts()
        if selected_users:
            accounts = [a for a in accounts if a["label"] in selected_users]
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
        for account in accounts:
            rows.extend(self._shared_rows(project, account, assets, block_counts, block_hashes, summary))
            for product in products:
                rows.append(self._product_row(project, account, product, assets, block_counts, block_hashes, summary))
        return rows, summary

    def _shared_rows(self, project, account, assets, block_counts, block_hashes, summary):
        rows = []
        for uid, obj_label, copy_type, script_type, block_key, asset_block_label in [("INTRO", "引言文案", "引言文案", "intro", "INTRO", "")]:
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
            rows.append((project["category_name"], account["label"], obj_label, copy_type, copy_count, "--", "--", voice_count, "，".join(issues)))
        return rows

    def _product_row(self, project, account, product, assets, block_counts, block_hashes, summary):
        uid = product["uid"]
        copy_count = block_counts.get(("product", uid), 0)
        image_count = self._asset_count(assets, uid=uid, asset_type="image", account_label=account["label"])
        video_count = self._asset_count(assets, uid=uid, asset_type="video")
        voice_count = self._asset_count(assets, uid=uid, asset_type="voice", account_label=account["label"])
        issues = []
        for key, count, label in [("copy", copy_count, "缺文案"), ("image", image_count, "缺图片"), ("video", video_count, "缺视频"), ("voice", voice_count, "缺配音")]:
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

    def _asset_count(self, assets, *, uid, asset_type, account_label="", block_label="") -> int:
        return sum(1 for a in assets if a["uid"] == uid and a["asset_type"] == asset_type and a["status"] == "ready"
                   and (not account_label or a["account_label"] == account_label or not a["account_label"])
                   and (not block_label or a["block_label"] == block_label))

    def _has_expired_voice(self, assets, *, uid, account_label, hashes, block_label="") -> bool:
        if not hashes:
            return False
        for a in assets:
            if a["uid"] != uid or a["asset_type"] != "voice" or a["status"] != "ready":
                continue
            if account_label and a["account_label"] != account_label:
                continue
            if block_label and a["block_label"] != block_label:
                continue
            if safe_text(a.get("text_hash")) and safe_text(a.get("text_hash")) not in hashes:
                return True
        return False

    def _row_matches_filter(self, row: tuple[Any, ...]) -> bool:
        issue = str(row[-1] or "")
        v = self.status_var.get()
        return True if v == "全部" else v in issue


AssetPageColumns = ("品类", "用户", "对象", "文案类型", "文案", "图片", "视频", "配音", "问题")


class SyncPage(BasePage):
    def __init__(self, master, app: App):
        super().__init__(master, "同步中心", app)
        self.project_var = ctk.StringVar()
        self.user_var = ctk.StringVar(value="全部")
        self._build()

    def _build(self) -> None:
        top = ctk.CTkFrame(self.content, fg_color="transparent")
        top.pack(fill="x", pady=(0, UIStyle.PAD_MD))
        ctk.CTkLabel(top, text="本次品类项目", font=UIStyle.FONT_BODY, text_color=UIStyle.COLOR_TEXT_DIM).pack(side="left")
        self.project_combo = AppComboBox(top, width=350, variable=self.project_var)
        self.project_combo.pack(side="left", padx=UIStyle.PAD_SM)
        self.project_combo.configure(command=self._select_project)
        ctk.CTkLabel(top, text="用户", font=UIStyle.FONT_BODY, text_color=UIStyle.COLOR_TEXT_DIM).pack(side="left", padx=(UIStyle.PAD_MD, UIStyle.PAD_SM))
        self.user_combo = AppComboBox(top, width=140, variable=self.user_var)
        self.user_combo.pack(side="left", padx=UIStyle.PAD_SM)
        self.user_combo.configure(command=lambda _=None: self.refresh())
        GhostButton(top, text="刷新状态", command=self.refresh).pack(side="left", padx=UIStyle.PAD_SM)
        PrimaryButton(top, text="一键同步当前品类", command=self._sync_all).pack(side="left", padx=UIStyle.PAD_SM)
        GhostButton(top, text="打开数据库目录", command=lambda: open_path(self.db.path.parent)).pack(side="left", padx=UIStyle.PAD_SM)

        # Status cards grid
        grid = ctk.CTkFrame(self.content, fg_color="transparent")
        grid.pack(fill="x", pady=(0, UIStyle.PAD_MD))
        grid.columnconfigure(0, weight=1, uniform="sync")
        grid.columnconfigure(1, weight=1, uniform="sync")

        self.master_card = self._status_card(grid, "Master 方案商品", 0, 0, [("预览变化", self._preview_master), ("同步 Master", self._sync_master), ("打开品类项目", lambda: self.app.show_page("品类项目"))])
        self.md_card = self._status_card(grid, "MD 文案", 0, 1, [("打开 MD", self._open_md), ("打开所在文件夹", self._open_md_folder), ("同步 MD", self._sync_md), ("创建/更新文案框架", self._init_outline)])
        self.folder_card = self._status_card(grid, "素材文件夹", 1, 0, [("打开图片目录", lambda: self._open_path("image_root")), ("打开视频目录", lambda: self._open_path("video_root")), ("打开配音目录", lambda: self._open_path("voice_root")), ("扫描素材", self._sync_assets)])
        self.mapping_card = self._status_card(grid, "映射关系与缺口", 1, 1, [("查看资产中心", lambda: self.app.show_page("资产中心")), ("导入旧项目用户/音色", self._import_legacy), ("导入屏幕挂灯资产", self._import_screen_light)])

        # Sync log
        log_card = AppCard(self.content, "最近同步记录")
        outer = ctk.CTkFrame(self.content, fg_color=UIStyle.COLOR_CARD_BG, corner_radius=UIStyle.RADIUS_LG)
        outer.pack(fill="both", expand=True)
        outer.grid_columnconfigure(0, weight=1)
        outer.grid_rowconfigure(0, weight=1)
        self.log_tree = _build_table(outer, ("时间", "类型", "状态", "说明"), row=0)
        self.log_tree.column("时间", width=160)
        self.log_tree.column("类型", width=110)
        self.log_tree.column("状态", width=80)
        self.log_tree.column("说明", width=500)

    def _status_card(self, parent, title: str, row: int, col: int, buttons: list[tuple[str, Callable]]) -> ctk.CTkLabel:
        card = ctk.CTkFrame(parent, fg_color=UIStyle.COLOR_CARD_BG, corner_radius=UIStyle.RADIUS_LG)
        card.grid(row=row, column=col, sticky="nsew", padx=(0, UIStyle.PAD_SM) if col == 0 else (UIStyle.PAD_SM, 0), pady=(0, UIStyle.PAD_SM))
        ctk.CTkLabel(card, text=title, font=UIStyle.FONT_H3, text_color=UIStyle.COLOR_TEXT_MAIN).pack(anchor="w", padx=UIStyle.PAD_MD, pady=(UIStyle.PAD_MD, UIStyle.PAD_XS))
        label = ctk.CTkLabel(card, text="等待刷新", justify="left", anchor="nw", font=UIStyle.FONT_SMALL, text_color=UIStyle.COLOR_TEXT_DIM)
        label.pack(fill="x", padx=UIStyle.PAD_MD, pady=(0, UIStyle.PAD_SM))
        btn_frame = ctk.CTkFrame(card, fg_color="transparent")
        btn_frame.pack(fill="x", padx=UIStyle.PAD_MD, pady=(0, UIStyle.PAD_MD))
        for text, cmd in buttons:
            GhostButton(btn_frame, text=text, command=cmd, height=30).pack(side="left", padx=(0, UIStyle.PAD_XS), pady=2)
        return label

    def refresh(self) -> None:
        projects = self.repo.projects()
        vals = [f"{p['id']} - {p['name']}" for p in projects]
        self.project_combo.configure(values=vals)
        project = self.app.current_project()
        if not project and projects:
            self.app.current_project_id = projects[0]["id"]
            project = projects[0]
        if project:
            self.project_var.set(f"{project['id']} - {project['name']}")
        users = ["全部"] + [a["label"] for a in self.repo.accounts()]
        self.user_combo.configure(values=users)
        if self.user_var.get() not in users:
            self.user_var.set("全部")
        self._refresh_status()
        self._refresh_logs()

    def _select_project(self, _=None) -> None:
        v = self.project_var.get()
        if not v:
            return
        self.app.current_project_id = int(v.split(" - ", 1)[0])
        self.refresh()

    def _current_project_or_warn(self) -> dict[str, Any] | None:
        p = self.app.current_project()
        if not p:
            messagebox.showinfo("需要品类项目", "请先选择品类项目。")
        return p

    def _refresh_status(self) -> None:
        project = self.app.current_project()
        if not project:
            for lbl in (self.master_card, self.md_card, self.folder_card, self.mapping_card):
                lbl.configure(text="请先创建或选择品类项目。")
            return
        products = self.repo.products(project["id"], include_removed=False)
        blocks = self.repo.script_blocks(project["id"])
        assets = self.repo.asset_bindings(project["id"])
        intro_count = sum(1 for b in blocks if b["script_type"] == "intro")
        product_block_count = sum(1 for b in blocks if b["script_type"] == "product")
        price_count = sum(1 for b in blocks if b["script_type"] == "price_transition")
        asset_counts = {
            "image": sum(1 for a in assets if a["asset_type"] == "image" and a["status"] == "ready"),
            "video": sum(1 for a in assets if a["asset_type"] == "video" and a["status"] == "ready"),
            "voice": sum(1 for a in assets if a["asset_type"] == "voice" and a["status"] == "ready"),
        }
        issues = build_project_issue_summary(project, products, blocks, assets, self.repo.accounts(), selected_user=self.user_var.get())
        last_master = self._last_event(project["id"], "master_scheme_sync")
        last_md = self._last_event(project["id"], "markdown_sync")
        last_asset = self._last_event(project["id"], "asset_sync")
        self.master_card.configure(text=f"方案：{project['scheme_name'] or '--'}\n商品：{len(products)} 个\n上次同步：{last_master or '未同步'}")
        self.md_card.configure(text=f"MD：{compact_path(project['md_path'], 58) or '--'}\n引言 {intro_count}，商品文案 {product_block_count}，价格过渡 {price_count}\n上次同步：{last_md or '未同步'}")
        self.folder_card.configure(text=f"图片：{compact_path(project['image_root'], 48)}\n视频：{compact_path(project['video_root'], 48)}\n配音：{compact_path(project['voice_root'], 48)}\n已识别 图片 {asset_counts['image']} / 视频 {asset_counts['video']} / 配音 {asset_counts['voice']}\n上次扫描：{last_asset or '未扫描'}")
        self.mapping_card.configure(text=f"筛选用户：{self.user_var.get()}\n缺文案 {len(issues['missing_copy'])}，缺图片 {len(issues['missing_image'])}，缺视频 {len(issues['missing_video'])}，缺配音 {len(issues['missing_voice'])}，配音过期 {len(issues['expired_voice'])}\n{format_issue_preview(issues, limit=3)}")

    def _refresh_logs(self) -> None:
        self.log_tree.delete(*self.log_tree.get_children())
        project = self.app.current_project()
        if not project:
            return
        for item in self.db.fetchall("SELECT * FROM sync_events WHERE project_id=? ORDER BY id DESC LIMIT 80", (project["id"],)):
            self.log_tree.insert("", "end", values=(item["created_at"], item["event_type"], item["status"], item["message"]))

    def _last_event(self, pid: int, event_type: str) -> str:
        row = self.db.fetchone("SELECT created_at, message FROM sync_events WHERE project_id=? AND event_type=? ORDER BY id DESC LIMIT 1", (pid, event_type))
        return f"{row['created_at']} | {row['message']}" if row else ""

    def _preview_master(self) -> None:
        project = self._current_project_or_warn()
        if not project:
            return
        self.app.run_background("预览 Master 变化", lambda: self.sync.sync_master_scheme(project["id"], apply_changes=False),
                                on_success=lambda r: (messagebox.showinfo("Master 变化预览", format_master_result(r)), self.refresh()),
                                success_message="Master 变化预览完成")

    def _sync_master(self) -> None:
        project = self._current_project_or_warn()
        if not project:
            return
        self.app.run_background("同步 Master", lambda: self.sync.sync_master_scheme(project["id"], apply_changes=True),
                                on_success=lambda r: (self.toast(f"Master 已同步：新增 {len(r['added'])}，更新 {len(r['updated'])}，移除 {len(r['removed'])}"), self.refresh()),
                                show_success_toast=False)

    def _sync_md(self) -> None:
        project = self._current_project_or_warn()
        if not project:
            return
        self.app.run_background("同步 MD", lambda: self.sync.sync_markdown(project["id"]),
                                on_success=lambda r: (self.toast(f"MD 已同步：入库 {r['upserted']} 条，缺文案 {len(r['missing_copy'])} 个"), self.refresh()),
                                show_success_toast=False)

    def _sync_assets(self) -> None:
        project = self._current_project_or_warn()
        if not project:
            return
        self.app.run_background("扫描素材", lambda: self.sync.sync_assets(project["id"]),
                                on_success=lambda r: (self.toast(f"素材扫描完成：图片 {r['image']}，视频 {r['video']}，配音 {r['voice']}，未识别 {r['unmatched']}"), self.refresh()),
                                show_success_toast=False)

    def _sync_all(self) -> None:
        project = self._current_project_or_warn()
        if not project:
            return
        self.app.run_background("一键同步",
                                lambda: (self.sync.sync_master_scheme(project["id"], apply_changes=True), self.sync.sync_markdown(project["id"]), self.sync.sync_assets(project["id"])),
                                on_success=lambda r: (self.toast(f"一键同步完成", duration=4500), self.refresh()), show_success_toast=False)

    def _init_outline(self) -> None:
        project = self._current_project_or_warn()
        if not project:
            return
        current = safe_text(project.get("md_path")) or str(self.outline.default_markdown_path(project["id"]))
        path = filedialog.asksaveasfilename(defaultextension=".md", filetypes=[("Markdown", "*.md"), ("All", "*.*")], initialdir=str(DEFAULT_MARKDOWN_ROOT), initialfile=Path(current).name)
        if not path:
            return
        self.app.run_background("创建文案框架",
                                lambda: (self.outline.init_or_update_outline(project["id"], path), self.sync.sync_markdown(project["id"])),
                                on_success=lambda r: (self.toast(f"文案框架已更新", duration=4500), self.refresh()), show_success_toast=False)

    def _open_path(self, key: str) -> None:
        p = self._current_project_or_warn()
        if p:
            open_path(p.get(key))

    def _open_md(self) -> None:
        p = self._current_project_or_warn()
        if p:
            open_path(p.get("md_path"))

    def _open_md_folder(self) -> None:
        p = self._current_project_or_warn()
        if p and p.get("md_path"):
            open_path(Path(p["md_path"]).parent)

    def _import_legacy(self) -> None:
        self.app.run_background("导入旧项目用户/音色",
                                lambda: (self.legacy_import.import_accounts(), self.legacy_import.import_voice_profiles()),
                                on_success=lambda r: (self.toast("导入完成"), self.refresh()), show_success_toast=False)

    def _import_screen_light(self) -> None:
        self.app.run_background("导入屏幕挂灯资产",
                                lambda: self.legacy_import.import_category_project(
                                    parent_category="数码", category="屏幕挂灯",
                                    md_path=Path(r"G:\WriteSpace\B站-文案脚本\10_b站文案\3.商品文案\数码-屏幕挂灯.md")),
                                on_success=lambda r: (self.app.set_current_project(r["project_id"]), self.toast("屏幕挂灯已导入", duration=4500), self.refresh()),
                                show_success_toast=False)


class AccountPage(BasePage):
    def __init__(self, master, app: App):
        super().__init__(master, "用户管理", app)
        self.vars = {key: ctk.StringVar() for key in ["label", "account_id", "voice_id", "voice_name", "media_identity", "closing_audio_path"]}

        card = AppCard(self.content, "新增/更新用户")
        f = ctk.CTkFrame(card, fg_color="transparent")
        f.columnconfigure(1, weight=1)
        f.columnconfigure(3, weight=1)
        labels = [("用户名称", "label"), ("账号标识", "account_id"), ("音色标识", "voice_id"), ("音色名称", "voice_name"), ("素材身份", "media_identity"), ("结尾配音路径", "closing_audio_path")]
        for idx, (label, key) in enumerate(labels):
            r = idx // 2
            c = (idx % 2) * 2
            ctk.CTkLabel(f, text=label, font=UIStyle.FONT_BODY, text_color=UIStyle.COLOR_TEXT_DIM).grid(row=r, column=c, sticky="w", padx=(0, UIStyle.PAD_SM), pady=UIStyle.PAD_XS)
            AppEntry(f, textvariable=self.vars[key]).grid(row=r, column=c + 1, sticky="ew", padx=(0, UIStyle.PAD_MD), pady=UIStyle.PAD_XS)
        PrimaryButton(f, text="保存用户", command=self._save_account).grid(row=3, column=0, sticky="w", pady=UIStyle.PAD_SM)
        GhostButton(f, text="导入旧项目用户/音色", command=self._import_legacy).grid(row=3, column=1, sticky="w", pady=UIStyle.PAD_SM)
        ctk.CTkLabel(f, text="说明：用户名称就是小燃、小博、小歪这类账号；音色标识用于生成对应配音。",
                     font=UIStyle.FONT_SMALL, text_color=UIStyle.COLOR_TEXT_DIM).grid(row=4, column=0, columnspan=4, sticky="w", pady=UIStyle.PAD_SM)
        card.add_content(f)

        outer = ctk.CTkFrame(self.content, fg_color=UIStyle.COLOR_CARD_BG, corner_radius=UIStyle.RADIUS_LG)
        outer.pack(fill="both", expand=True)
        outer.grid_columnconfigure(0, weight=1)
        outer.grid_rowconfigure(0, weight=1)
        self.tree = _build_table(outer, ("用户名称", "账号标识", "音色标识", "音色名称", "素材身份", "结尾配音", "启用"), row=0)

    def _save_account(self) -> None:
        payload = {k: v.get().strip() for k, v in self.vars.items()}
        if not payload["label"]:
            messagebox.showwarning("缺少标签", "请填写用户标签，例如小燃。")
            return
        ts = now_iso()
        with self.db.connect() as conn:
            conn.execute("""
                INSERT INTO accounts (label, account_id, voice_id, voice_name, media_identity, closing_audio_path, enabled, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
                ON CONFLICT(label) DO UPDATE SET
                    account_id=excluded.account_id, voice_id=excluded.voice_id, voice_name=excluded.voice_name,
                    media_identity=excluded.media_identity, closing_audio_path=excluded.closing_audio_path, updated_at=excluded.updated_at
            """, (payload["label"], payload["account_id"], payload["voice_id"], payload["voice_name"], payload["media_identity"], payload["closing_audio_path"], ts, ts))
        self.refresh()
        self.toast("用户已保存")

    def _import_legacy(self) -> None:
        self.app.run_background("导入旧项目用户/音色",
                                lambda: (self.legacy_import.import_accounts(), self.legacy_import.import_voice_profiles()),
                                on_success=lambda r: (self.toast("导入完成"), self.refresh()), show_success_toast=False)

    def refresh(self) -> None:
        rows = [(a["label"], a["account_id"], a["voice_id"], a["voice_name"], a["media_identity"], compact_path(a["closing_audio_path"], 40), "是" if a["enabled"] else "否") for a in self.repo.accounts()]
        _set_tree_rows(self.tree, rows)


class WorkflowPage(BasePage):
    def __init__(self, master, app: App, title: str):
        super().__init__(master, title, app)
        self.mode_var = ctk.StringVar(value="standard")
        self.project_var = ctk.StringVar()
        self.account_var = ctk.StringVar()
        self.uid_var = ctk.StringVar()
        self.intro_var = ctk.StringVar(value="1")
        self.intro_choice_var = ctk.StringVar()
        self.spoken_md_var = ctk.StringVar()
        self.intro_video_var = ctk.StringVar()
        self.loaded_project_id: int | None = None

        # Project selector
        sel = ctk.CTkFrame(self.content, fg_color=UIStyle.COLOR_CARD_BG, corner_radius=UIStyle.RADIUS_LG)
        sel.pack(fill="x", pady=(0, UIStyle.PAD_SM))
        sel.columnconfigure(1, weight=1)
        ctk.CTkLabel(sel, text="本次品类项目", font=UIStyle.FONT_BODY, text_color=UIStyle.COLOR_TEXT_DIM).grid(
            row=0, column=0, sticky="w", padx=(UIStyle.PAD_LG, UIStyle.PAD_SM), pady=UIStyle.PAD_MD
        )
        self.project_combo = AppComboBox(sel, width=400, variable=self.project_var)
        self.project_combo.grid(row=0, column=1, sticky="ew", padx=(0, UIStyle.PAD_LG), pady=UIStyle.PAD_MD)
        self.project_combo.configure(command=self._select_project)

        # 子页面的表单控件应打包到此 frame 中（在 log 之前）
        self.form_area = ctk.CTkFrame(self.content, fg_color="transparent")
        self.form_area.pack(fill="x")

        self.log_text = AppTextbox(self.content, height=200)
        self.log_text.pack(fill="both", expand=True, pady=(UIStyle.PAD_SM, 0))

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
                project["id"], mode=mode, top_uids=top_uids or None,
                account_label=self.account_var.get().strip(), intro_index=int(self.intro_var.get() or "1"),
                output_markdown_path=self._remember_spoken_md(project["id"]),
                display_template=self.template_var.get().strip() if hasattr(self, "template_var") else "",
            )
        return self.workflow.build_jianying_command(
            project["id"], draft_name=self.account_var.get().strip(),
            spoken_markdown_path=self._remember_spoken_md(project["id"]),
            intro_video_path=self.intro_video_var.get().strip(),
        )

    def _browse_spoken_md(self) -> None:
        p = self.app.current_project()
        default_name = safe_text(p.get("name")) if p else "口播稿"
        path = filedialog.asksaveasfilename(defaultextension=".md", filetypes=[("Markdown", "*.md"), ("All", "*.*")],
                                            initialdir=str(DEFAULT_SPOKEN_MD_ROOT), initialfile=f"{default_name or '口播稿'}.md")
        if path:
            self.spoken_md_var.set(path.replace("/", "\\"))

    def _browse_intro_video(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("Video", "*.mp4 *.mov *.mkv *.avi *.webm"), ("All", "*.*")], initialdir=r"G:\2026项目-b站")
        if path:
            self.intro_video_var.set(path.replace("/", "\\"))

    def _remember_spoken_md(self, project_id: int) -> str:
        path = self.spoken_md_var.get().strip()
        if path:
            self.db.execute("UPDATE projects SET spoken_md_path=?, updated_at=? WHERE id=?", (path, now_iso(), project_id))
        return path

    def project_required(self) -> dict[str, Any] | None:
        p = self.app.current_project()
        if not p:
            messagebox.showinfo("需要品类项目", "请先在“品类项目”中创建或选择项目。")
        return p

    def log(self, text: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", text.rstrip() + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _refresh_intro_choices(self, project: dict[str, Any] | None) -> None:
        combo = getattr(self, "intro_combo", None)
        if combo is None:
            return
        choices: list[str] = []
        if project:
            for idx, b in enumerate(self.repo.script_blocks(project["id"]), start=1):
                if b["script_type"] == "intro":
                    choices.append(f"{idx} - {safe_text(b.get('block_label')) or '引言'}")
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

    def _build_command(self) -> None:
        try:
            cmd = self._command()
        except Exception as exc:
            messagebox.showerror("生成失败", str(exc))
            return
        self.log(" ".join(f'"{p}"' if " " in p else p for p in cmd))

    def _run_command(self) -> None:
        try:
            cmd = self._command()
        except Exception as exc:
            messagebox.showerror("执行失败", str(exc))
            return
        progress_dialog = TaskProgressDialog(self, self._running_dialog_title(), self._running_dialog_message())
        progress_dialog.append("即将执行：")
        progress_dialog.append(" ".join(f'"{p}"' if " " in p else p for p in cmd))
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

        self.app.run_background("执行任务", work, on_success=on_success, on_error=on_error, show_success_toast=False)

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
            message, can_continue = self._jianying_precheck(project)
            return show_precheck_dialog(self, "生成剪映草稿预检查", message, can_continue=can_continue)
        return True

    def _voice_precheck(self, project: dict[str, Any]) -> tuple[str, bool]:
        account_label = self.account_var.get().strip()
        selected_uids = parse_uid_list(self.uid_var.get())
        products = {a["uid"]: a for a in self.repo.products(project["id"], include_removed=False)}
        blocks = self.repo.script_blocks(project["id"])
        assets = self.repo.asset_bindings(project["id"])
        selected = set(selected_uids)
        unknown = [u for u in selected_uids if u not in products]
        product_blocks = [b for b in blocks if b["script_type"] == "product" and (not selected or b["owner_uid"] in selected)]
        shared_blocks = [] if selected else [b for b in blocks if b["script_type"] in {"intro", "price_transition"}]
        pending, skipped, blocked = [], [], []
        for uid in selected_uids:
            if uid in products and not any(b["owner_uid"] == uid for b in product_blocks):
                blocked.append(f"{uid} {products[uid]['title']}：缺文案")
        for uid in unknown:
            blocked.append(f"{uid}：当前品类项目中没有这个商品")
        for b in product_blocks:
            prod = products.get(b["owner_uid"], {})
            display = f"{b['owner_uid']} {safe_text(prod.get('title'))} / {b['block_label']}"
            state = voice_state(assets, uid=b["owner_uid"], account_label=account_label, hashes={b["text_hash"]})
            (pending if state != "ready" else skipped).append(f"{display}：{'配音过期，将重生成' if state == 'expired' else '缺配音，将生成'}" if state != "ready" else f"{display}：已有配音")
        for b in shared_blocks:
            uid = "INTRO" if b["script_type"] == "intro" else "PRICE_TRANSITION"
            display = f"{'引言文案' if uid == 'INTRO' else f'价格过渡 {b["price_range_label"]}'} / {b['block_label']}"
            state = voice_state(assets, uid=uid, account_label=account_label, hashes={b["text_hash"]}, block_label=b.get("price_range_label", ""))
            (pending if state != "ready" else skipped).append(f"{display}：{'配音过期，将重生成' if state == 'expired' else '缺配音，将生成'}" if state != "ready" else f"{display}：已有配音")
        selected_text = "全部文案" if not selected_uids else "、".join(selected_uids)
        lines = [
            "本次配音生成预览", "",
            f"品类：{project['name']}", f"用户：{account_label or '未选择'}", f"范围：{selected_text}", "",
            "统计", f"- 待生成 / 重生成：{len(pending)} 条", f"- 已有配音跳过：{len(skipped)} 条", f"- 缺文案 / 不可处理：{len(blocked)} 条", "",
            "待生成明细", *preview_lines(pending), "", "已有跳过明细", *preview_lines(skipped), "",
            "缺失 / 不可处理", *preview_lines(blocked), "",
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
        missing_files, missing_product_videos = [], []
        manifest_error = ""
        selected_user = "全部"
        if manifest.exists():
            try:
                payload = json.loads(manifest.read_text(encoding="utf-8-sig"))
                selected_user = manifest_account_label(payload) or selected_user
                missing_product_videos = manifest_product_video_gaps(payload)
                for p in manifest_file_paths(payload):
                    if not Path(p).exists():
                        missing_files.append(p)
            except Exception as exc:
                manifest_error = str(exc)
        issues = build_project_issue_summary(project, products, blocks, assets, self.repo.accounts(), selected_user=selected_user)
        lines = [
            "生成剪映草稿预检查", "",
            f"品类：{project['name']}", f"口播稿：{spoken_path}", f"Manifest：{manifest}",
            f"口播用户：{selected_user}",
            f"引言成片视频：{intro_video_path if intro_video_path else '未选择，将使用 manifest 内的引言配音'}",
            f"草稿输出：{DEFAULT_JIANYING_DRAFT_ROOT}", "", "阻塞问题",
        ]
        if missing_manifest:
            lines.append("- 缺 manifest：还没有组合口播稿，不能生成剪映草稿。")
        else:
            lines.append("- manifest 已找到")
        if manifest_error:
            lines.append(f"- manifest 读取失败：{manifest_error}")
        if intro_video_path is not None:
            lines.append(f"- 引言成片视频{'已找到' if intro_video_path.exists() else f'不存在：{intro_video_path}'}")
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
        lines += ["", "数据库缺口",
                  f"- 缺图片：{len(issues['missing_image'])}", f"- 缺视频：{len(issues['missing_video'])}",
                  f"- 缺配音：{len(issues['missing_voice'])}", f"- 配音过期：{len(issues['expired_voice'])}",
                  format_issue_preview(issues)]
        return "\n".join(lines), not missing_manifest and not manifest_error and (intro_video_path is None or intro_video_path.exists())

    def refresh(self) -> None:
        project = self.app.current_project()
        projects = self.repo.projects()
        vals = [f"{p['id']} - {p['name']}" for p in projects]
        self.project_combo.configure(values=vals)
        if project:
            self.project_var.set(f"{project['id']} - {project['name']}")
            if self.loaded_project_id != project["id"]:
                self.spoken_md_var.set(safe_text(project.get("spoken_md_path")))
                self.loaded_project_id = project["id"]
        users = [a["label"] for a in self.repo.accounts()]
        account_input = getattr(self, "account_input", None)
        if account_input is not None:
            account_input.configure(values=users)
            if users and self.account_var.get() not in users:
                self.account_var.set(users[0])
        if isinstance(self, AssemblePage):
            self._refresh_intro_choices(project)
            self.asm_user_combo.configure(values=users)
            if users and not self.asm_user_var.get():
                self.asm_user_var.set(users[0])
                self._on_asm_user_changed()
        if isinstance(self, JianyingPage):
            if not self.account_var.get().strip():
                try:
                    md_text = self.spoken_md_var.get().strip()
                    if md_text and project:
                        mf = self.workflow.spoken_manifest_path(project["id"], md_text)
                        if mf.exists():
                            p = json.loads(mf.read_text(encoding="utf-8-sig"))
                            label = p.get("account_label", "") if isinstance(p, dict) else ""
                            if label:
                                self.account_var.set(f"完整-5月-{label}")
                except Exception:
                    pass
        if project and not self.spoken_md_var.get().strip():
            self.spoken_md_var.set(safe_text(project.get("spoken_md_path")))

    def _select_project(self, _=None) -> None:
        v = self.project_var.get()
        if not v:
            return
        pid = int(v.split(" - ", 1)[0])
        self.app.set_current_project(pid)


class VoicePage(WorkflowPage):
    def __init__(self, master, app: App):
        super().__init__(master, app, "生成配音")
        form = ctk.CTkFrame(self.form_area, fg_color=UIStyle.COLOR_CARD_BG, corner_radius=UIStyle.RADIUS_LG)
        form.pack(fill="x", pady=(0, UIStyle.PAD_SM))
        form.columnconfigure(1, weight=0)
        form.columnconfigure(3, weight=1)

        ctk.CTkLabel(form, text="配音用户", font=UIStyle.FONT_BODY, text_color=UIStyle.COLOR_TEXT_DIM).grid(
            row=0, column=0, sticky="w", padx=(UIStyle.PAD_LG, UIStyle.PAD_SM), pady=(UIStyle.PAD_LG, UIStyle.PAD_SM)
        )
        self.account_input = AppComboBox(form, width=180, variable=self.account_var)
        self.account_input.grid(row=0, column=1, sticky="w", pady=(UIStyle.PAD_LG, UIStyle.PAD_SM))

        ctk.CTkLabel(form, text="商品UID（可不填）", font=UIStyle.FONT_BODY, text_color=UIStyle.COLOR_TEXT_DIM).grid(
            row=0, column=2, sticky="w", padx=(UIStyle.PAD_LG, UIStyle.PAD_SM), pady=(UIStyle.PAD_LG, UIStyle.PAD_SM)
        )
        AppEntry(form, textvariable=self.uid_var).grid(
            row=0, column=3, sticky="ew", padx=(0, UIStyle.PAD_LG), pady=(UIStyle.PAD_LG, UIStyle.PAD_SM)
        )

        ctk.CTkLabel(
            form,
            text="留空会处理当前品类下全部文案；多个 UID 用中文或英文逗号分隔。",
            font=UIStyle.FONT_SMALL,
            text_color=UIStyle.COLOR_TEXT_DIM,
        ).grid(row=1, column=3, sticky="w", padx=(0, UIStyle.PAD_LG), pady=(0, UIStyle.PAD_MD))

        actions = ctk.CTkFrame(form, fg_color="transparent")
        actions.grid(row=2, column=0, columnspan=4, sticky="ew", padx=UIStyle.PAD_LG, pady=(0, UIStyle.PAD_LG))
        actions.columnconfigure(0, weight=1)
        PrimaryButton(actions, text="预检查并执行", command=self._run_command).grid(row=0, column=1, sticky="e")


class AssemblePage(WorkflowPage):
    def __init__(self, master, app: App):
        self.asm_user_var = ctk.StringVar()
        self.template_var = ctk.StringVar()
        super().__init__(master, app, "组合口播稿")
        form = ctk.CTkFrame(self.form_area, fg_color=UIStyle.COLOR_CARD_BG, corner_radius=UIStyle.RADIUS_LG)
        form.pack(fill="x", pady=(0, UIStyle.PAD_SM))
        form.columnconfigure(1, weight=1)
        form.columnconfigure(3, weight=1)
        form.columnconfigure(5, weight=1)

        ctk.CTkLabel(form, text="口播用户", font=UIStyle.FONT_BODY, text_color=UIStyle.COLOR_TEXT_DIM).grid(
            row=0, column=0, sticky="w", padx=(UIStyle.PAD_LG, UIStyle.PAD_SM), pady=(UIStyle.PAD_LG, UIStyle.PAD_XS)
        )
        self.account_input = AppComboBox(form, variable=self.account_var)
        self.account_input.grid(row=0, column=1, sticky="ew", padx=(0, UIStyle.PAD_LG), pady=(UIStyle.PAD_LG, UIStyle.PAD_XS))

        ctk.CTkLabel(form, text="组合方式", font=UIStyle.FONT_BODY, text_color=UIStyle.COLOR_TEXT_DIM).grid(
            row=0, column=2, sticky="w", padx=(0, UIStyle.PAD_SM), pady=(UIStyle.PAD_LG, UIStyle.PAD_XS)
        )
        mode_combo = AppComboBox(form, variable=self.mode_var, values=["标准模式", "Top 模式"])
        mode_combo.grid(row=0, column=3, sticky="ew", padx=(0, UIStyle.PAD_LG), pady=(UIStyle.PAD_LG, UIStyle.PAD_XS))
        self.mode_var.set("标准模式")

        ctk.CTkLabel(form, text="引言", font=UIStyle.FONT_BODY, text_color=UIStyle.COLOR_TEXT_DIM).grid(
            row=0, column=4, sticky="w", padx=(0, UIStyle.PAD_SM), pady=(UIStyle.PAD_LG, UIStyle.PAD_XS)
        )
        self.intro_combo = AppComboBox(form, variable=self.intro_choice_var)
        self.intro_combo.grid(row=0, column=5, sticky="ew", padx=(0, UIStyle.PAD_LG), pady=(UIStyle.PAD_LG, UIStyle.PAD_XS))
        self.intro_combo.configure(command=lambda _=None: self._sync_intro_index())

        ctk.CTkLabel(form, text="Top 商品UID（可不填）", font=UIStyle.FONT_BODY, text_color=UIStyle.COLOR_TEXT_DIM).grid(
            row=1, column=0, sticky="w", padx=(UIStyle.PAD_LG, UIStyle.PAD_SM), pady=UIStyle.PAD_XS
        )
        AppEntry(form, textvariable=self.uid_var).grid(row=1, column=1, sticky="ew", padx=(0, UIStyle.PAD_LG), pady=UIStyle.PAD_XS)

        ctk.CTkLabel(form, text="展示用户", font=UIStyle.FONT_BODY, text_color=UIStyle.COLOR_TEXT_DIM).grid(
            row=1, column=2, sticky="w", padx=(0, UIStyle.PAD_SM), pady=UIStyle.PAD_XS
        )
        self.asm_user_combo = AppComboBox(form, variable=self.asm_user_var)
        self.asm_user_combo.grid(row=1, column=3, sticky="ew", padx=(0, UIStyle.PAD_LG), pady=UIStyle.PAD_XS)
        self.asm_user_combo.configure(command=self._on_asm_user_changed)

        ctk.CTkLabel(form, text="展示模板", font=UIStyle.FONT_BODY, text_color=UIStyle.COLOR_TEXT_DIM).grid(
            row=1, column=4, sticky="w", padx=(0, UIStyle.PAD_SM), pady=UIStyle.PAD_XS
        )
        self.asm_template_combo = AppComboBox(form, variable=self.template_var)
        self.asm_template_combo.grid(row=1, column=5, sticky="ew", padx=(0, UIStyle.PAD_LG), pady=UIStyle.PAD_XS)

        ctk.CTkLabel(form, text="口播稿输出 MD", font=UIStyle.FONT_BODY, text_color=UIStyle.COLOR_TEXT_DIM).grid(
            row=2, column=0, sticky="w", padx=(UIStyle.PAD_LG, UIStyle.PAD_SM), pady=(UIStyle.PAD_XS, 0)
        )
        AppEntry(form, textvariable=self.spoken_md_var).grid(row=2, column=1, columnspan=4, sticky="ew", padx=(0, UIStyle.PAD_SM), pady=(UIStyle.PAD_XS, 0))
        GhostButton(form, text="选", width=52, command=self._browse_spoken_md).grid(row=2, column=5, sticky="e", padx=(0, UIStyle.PAD_LG), pady=(UIStyle.PAD_XS, 0))
        ctk.CTkLabel(
            form,
            text="Top UID 用逗号分隔，支持中文和英文逗号；引言编号按 MD 中“引言文案”从上到下排序。",
            font=UIStyle.FONT_SMALL,
            text_color=UIStyle.COLOR_TEXT_DIM,
        ).grid(row=3, column=1, columnspan=5, sticky="w", padx=(0, UIStyle.PAD_LG), pady=(UIStyle.PAD_XS, UIStyle.PAD_MD))

        actions = ctk.CTkFrame(form, fg_color="transparent")
        actions.grid(row=4, column=0, columnspan=6, sticky="ew", padx=UIStyle.PAD_LG, pady=(0, UIStyle.PAD_LG))
        actions.columnconfigure(0, weight=1)
        PrimaryButton(actions, text="预检查并执行", command=self._run_command).grid(row=0, column=1, sticky="e")

    def _on_asm_user_changed(self, _=None) -> None:
        from .template_config import available_templates
        templates = available_templates(self.asm_user_var.get())
        self.asm_template_combo.configure(values=templates)
        if templates:
            self.template_var.set(templates[0])
        else:
            self.template_var.set("")


class JianyingPage(WorkflowPage):
    def __init__(self, master, app: App):
        super().__init__(master, app, "生成剪映草稿")

        form = ctk.CTkFrame(self.form_area, fg_color=UIStyle.COLOR_CARD_BG, corner_radius=UIStyle.RADIUS_LG)
        form.pack(fill="x", pady=(0, UIStyle.PAD_LG))
        inner = ctk.CTkFrame(form, fg_color="transparent")
        inner.pack(fill="x", padx=UIStyle.PAD_LG, pady=UIStyle.PAD_LG)
        inner.columnconfigure(1, weight=1)

        r = 0
        ctk.CTkLabel(inner, text="草稿名", font=UIStyle.FONT_BODY, text_color=UIStyle.COLOR_TEXT_DIM).grid(row=r, column=0, sticky="w", padx=(0, UIStyle.PAD_SM), pady=UIStyle.PAD_XS)
        self.account_entry = AppEntry(inner, textvariable=self.account_var)
        self.account_entry.grid(row=r, column=1, sticky="ew", pady=UIStyle.PAD_XS)

        r += 1
        ctk.CTkLabel(inner, text="口播稿 MD", font=UIStyle.FONT_BODY, text_color=UIStyle.COLOR_TEXT_DIM).grid(row=r, column=0, sticky="w", padx=(0, UIStyle.PAD_SM), pady=UIStyle.PAD_XS)
        AppEntry(inner, textvariable=self.spoken_md_var).grid(row=r, column=1, sticky="ew", padx=(0, UIStyle.PAD_SM), pady=UIStyle.PAD_XS)
        GhostButton(inner, text="选", width=50, command=self._browse_spoken_md).grid(row=r, column=2, pady=UIStyle.PAD_XS)

        r += 1
        ctk.CTkLabel(inner, text="引言成片视频", font=UIStyle.FONT_BODY, text_color=UIStyle.COLOR_TEXT_DIM).grid(row=r, column=0, sticky="w", padx=(0, UIStyle.PAD_SM), pady=UIStyle.PAD_XS)
        AppEntry(inner, textvariable=self.intro_video_var).grid(row=r, column=1, sticky="ew", padx=(0, UIStyle.PAD_SM), pady=UIStyle.PAD_XS)
        GhostButton(inner, text="选", width=50, command=self._browse_intro_video).grid(row=r, column=2, pady=UIStyle.PAD_XS)

        r += 1
        ctk.CTkLabel(inner, text="选择后会先拼这段引言视频，再拼口播稿里的商品推荐部分；不会重复拼 manifest 里的引言条目。",
                     font=UIStyle.FONT_SMALL, text_color=UIStyle.COLOR_TEXT_DIM).grid(row=r, column=0, columnspan=3, sticky="w", pady=(UIStyle.PAD_XS, UIStyle.PAD_SM))

        act = ctk.CTkFrame(self.form_area, fg_color="transparent")
        act.pack(fill="x", pady=(0, UIStyle.PAD_MD))
        PrimaryButton(act, text="    预检查并执行    ", command=self._run_command).pack()


PAGE_MAP: dict[str, type] = {
    "品类项目": ProjectPage,
    "文案中心": CopyPage,
    "资产中心": AssetPage,
    "同步中心": SyncPage,
    "用户管理": AccountPage,
    "生成配音": VoicePage,
    "组合口播稿": AssemblePage,
    "生成剪映草稿": JianyingPage,
}
