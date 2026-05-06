from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable

from .db import Database
from .master_data import MasterDataService, display_name
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
        self.title("B-Workflow SQL 资产工作台")
        self.geometry("1400x900")
        self.minsize(1180, 760)
        self.db = Database()
        self.repo = Repository(self.db)
        self.sync = SyncService(self.db)
        self.workflow = WorkflowService(self.db)
        self.master_data = MasterDataService()
        self.current_project_id: int | None = self.db.latest_project_id()
        self.pages: dict[str, BasePage] = {}
        self.nav_buttons: dict[str, ttk.Button] = {}
        self._build_shell()
        self.show_page("品类项目")

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
            "配置": ["品类项目", "文案中心", "资产中心", "同步中心", "用户管理", "设置"],
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


class BasePage(ttk.Frame):
    def __init__(self, master, app: App):
        super().__init__(master)
        self.app = app
        self.db = app.db
        self.repo = app.repo
        self.sync = app.sync
        self.workflow = app.workflow
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
        ttk.Button(actions, text="预览 Master 方案变化", command=self._preview_master).pack(side="left", padx=(0, 8))
        ttk.Button(actions, text="同步 Master 方案商品", command=self._sync_master).pack(side="left", padx=(0, 8))
        ttk.Button(actions, text="同步 MD 文案", command=self._sync_md).pack(side="left")
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
        try:
            self.workspaces = self.master_data.fetch_workspaces(force_refresh=force_refresh)
        except Exception as exc:
            if not quiet:
                messagebox.showerror("读取 Master 失败", str(exc))
            return
        workspace = self._default_workspace()
        if workspace:
            self.workspace_var.set(display_name(workspace))
            self.workspace_label.configure(text=f"{display_name(workspace)}（默认）")
            self.fields["workspace_id"].set(safe_text(workspace.get("id")))
            self.fields["workspace_name"].set(display_name(workspace))
            self._load_category_tree(workspace, keep_existing=bool(self.fields["category_name"].get().strip()))
        if not quiet:
            self.log(f"已读取 Master 工作空间，当前固定使用：{self.workspace_var.get() or '赵二'}。")

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
        try:
            _workspace, tree, source = self.master_data.fetch_category_tree(safe_text(workspace.get("id")))
        except Exception as exc:
            messagebox.showerror("读取品类失败", str(exc))
            return
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
        try:
            self.schemes, source = self.master_data.fetch_schemes(workspace_id=safe_text(workspace.get("id")), category_id=safe_text(child.get("id")))
        except Exception as exc:
            messagebox.showerror("读取方案失败", str(exc))
            return
        scheme_names = [display_name(item, safe_text(item.get("id"))) for item in self.schemes]
        self.scheme_combo.configure(values=scheme_names)
        saved_scheme = self.fields["scheme_name"].get().strip() if keep_existing else ""
        self.scheme_var.set(saved_scheme if saved_scheme in scheme_names else (scheme_names[0] if scheme_names else ""))
        if scheme_names:
            self._on_scheme_selected()
        self.log(f"已读取“{safe_text(child.get('name'))}”方案：{len(scheme_names)} 个（来源：{source}）。")

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

    def _build_table(self) -> None:
        self.tree = ttk.Treeview(self, columns=self.columns, show="headings")
        for column in self.columns:
            self.tree.heading(column, text=column)
            self.tree.column(column, width=140, anchor="w")
        self.tree.grid(row=1, column=0, sticky="nsew")
        self.rowconfigure(1, weight=1)
        ybar = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=ybar.set)
        ybar.grid(row=1, column=1, sticky="ns")

    def _set_rows(self, rows: list[tuple[Any, ...]]) -> None:
        self.tree.delete(*self.tree.get_children())
        for row in rows:
            self.tree.insert("", "end", values=row)


class CopyPage(TablePage):
    columns = ("类型", "对象", "标签", "正文预览", "Hash")

    def __init__(self, master, app: App):
        super().__init__(master, app)
        actions = ttk.Frame(self)
        actions.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Button(actions, text="同步 MD 文案到数据库", command=self._sync_md).pack(side="left")
        self._build_table()

    def _sync_md(self) -> None:
        project = self.project_required()
        if not project:
            return
        try:
            self.sync.sync_markdown(project["id"])
        except Exception as exc:
            messagebox.showerror("同步失败", str(exc))
            return
        self.refresh()

    def refresh(self) -> None:
        project = self.app.current_project()
        if not project:
            self._set_rows([])
            return
        rows = []
        for block in self.repo.script_blocks(project["id"]):
            owner = block["owner_uid"] or block["price_range_label"] or "项目"
            rows.append((block["script_type"], owner, block["block_label"], block["body"][:70], block["text_hash"][:10]))
        self._set_rows(rows)


class AssetPage(TablePage):
    columns = ("UID", "商品", "文案", "图片", "视频", "配音", "问题")

    def __init__(self, master, app: App):
        super().__init__(master, app)
        actions = ttk.Frame(self)
        actions.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Button(actions, text="同步素材文件夹", command=self._sync_assets).pack(side="left")
        self._build_table()

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
        project = self.app.current_project()
        if not project:
            self._set_rows([])
            return
        blocks = self.repo.script_blocks(project["id"])
        assets = self.repo.asset_bindings(project["id"])
        by_uid: dict[str, dict[str, int]] = {}
        for asset in assets:
            uid = asset["uid"]
            by_uid.setdefault(uid, {"image": 0, "video": 0, "voice": 0})
            if asset["status"] == "ready" and asset["asset_type"] in by_uid[uid]:
                by_uid[uid][asset["asset_type"]] += 1
        copy_uids = {block["owner_uid"] for block in blocks if block["script_type"] == "product" and block["owner_uid"]}
        rows = []
        for product in self.repo.products(project["id"], include_removed=True):
            uid = product["uid"]
            stats = by_uid.get(uid, {})
            issues = []
            if uid not in copy_uids:
                issues.append("缺文案")
            for kind, label in [("image", "缺图片"), ("video", "缺视频"), ("voice", "缺配音")]:
                if not stats.get(kind):
                    issues.append(label)
            if int(product["removed_from_master"]):
                issues.append("已从 Master 移除")
            rows.append((uid, product["title"], "有" if uid in copy_uids else "缺", stats.get("image", 0), stats.get("video", 0), stats.get("voice", 0), "，".join(issues)))
        self._set_rows(rows)


class SyncPage(TablePage):
    columns = ("时间", "类型", "状态", "说明")

    def __init__(self, master, app: App):
        super().__init__(master, app)
        self._build_table()

    def refresh(self) -> None:
        project = self.app.current_project()
        if not project:
            self._set_rows([])
            return
        rows = [
            (item["created_at"], item["event_type"], item["status"], item["message"])
            for item in self.db.fetchall("SELECT * FROM sync_events WHERE project_id=? ORDER BY id DESC LIMIT 200", (project["id"],))
        ]
        self._set_rows(rows)


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
        ttk.Label(form, text="说明：用户名称就是小燃、小博、小歪这类账号；音色标识用于生成对应配音。").grid(row=3, column=1, columnspan=3, sticky="w", pady=6)
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

    def refresh(self) -> None:
        rows = []
        for item in self.repo.accounts():
            rows.append((item["label"], item["account_id"], item["voice_id"], item["voice_name"], item["media_identity"], compact_path(item["closing_audio_path"], 40), "是" if item["enabled"] else "否"))
        self._set_rows(rows)


class SettingsPage(BasePage):
    def __init__(self, master, app: App):
        super().__init__(master, app)
        ttk.Label(self, text=f"数据库：{self.db.path}").grid(row=0, column=0, sticky="w", pady=4)
        ttk.Label(self, text=f"软件中间文件：{INTERNAL_WORKSPACE_ROOT}").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Label(self, text=f"剪映草稿固定目录：{DEFAULT_JIANYING_DRAFT_ROOT}").grid(row=2, column=0, sticky="w", pady=4)
        ttk.Label(self, text="V2 规则：数据库是主账本；MD 是文案编辑格式；素材文件夹保存真实文件。").grid(row=3, column=0, sticky="w", pady=4)


class WorkflowPage(BasePage):
    title = ""
    builder: Callable[..., list[str]]

    def __init__(self, master, app: App):
        super().__init__(master, app)
        self.mode_var = tk.StringVar(value="standard")
        self.account_var = tk.StringVar()
        self.uid_var = tk.StringVar()
        self.intro_var = tk.StringVar(value="1")
        self.spoken_md_var = tk.StringVar()
        actions = ttk.Frame(self)
        actions.grid(row=0, column=0, sticky="ew", pady=(0, 8))
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
            ttk.Label(actions, text="引言编号").pack(side="left")
            ttk.Entry(actions, textvariable=self.intro_var, width=5).pack(side="left", padx=8)
            output_row = ttk.Frame(self)
            output_row.grid(row=1, column=0, sticky="ew", pady=(0, 8))
            output_row.columnconfigure(1, weight=1)
            ttk.Label(output_row, text="口播稿输出 MD（会覆盖全部内容）").grid(row=0, column=0, sticky="w", padx=(0, 8))
            ttk.Entry(output_row, textvariable=self.spoken_md_var).grid(row=0, column=1, sticky="ew", padx=(0, 8))
            ttk.Button(output_row, text="选", width=4, command=self._browse_spoken_md).grid(row=0, column=2, sticky="e")
        if isinstance(self, JianyingPage):
            output_row = ttk.Frame(self)
            output_row.grid(row=1, column=0, sticky="ew", pady=(0, 8))
            output_row.columnconfigure(1, weight=1)
            ttk.Label(output_row, text="口播稿 MD").grid(row=0, column=0, sticky="w", padx=(0, 8))
            ttk.Entry(output_row, textvariable=self.spoken_md_var).grid(row=0, column=1, sticky="ew", padx=(0, 8))
            ttk.Button(output_row, text="选", width=4, command=self._browse_spoken_md).grid(row=0, column=2, sticky="e")
        ttk.Button(actions, text="生成命令", command=self._build_command).pack(side="left", padx=8)
        ttk.Button(actions, text="执行", command=self._run_command).pack(side="left")
        self.log_text = tk.Text(self, height=28, state="disabled")
        self.log_text.grid(row=99, column=0, sticky="nsew")

    def refresh(self) -> None:
        project = self.app.current_project()
        if isinstance(getattr(self, "account_input", None), ttk.Combobox):
            values = [item["label"] for item in self.repo.accounts()]
            self.account_input.configure(values=values)
            if values and not self.account_var.get():
                self.account_var.set(values[0])
        if project and not self.spoken_md_var.get().strip():
            self.spoken_md_var.set(safe_text(project.get("spoken_md_path")))

    def primary_label(self) -> str:
        if isinstance(self, JianyingPage):
            return "草稿名"
        if isinstance(self, VoicePage):
            return "配音用户"
        return "口播用户"

    def uid_label(self) -> str:
        if isinstance(self, AssemblePage):
            return "Top 商品UID"
        if isinstance(self, VoicePage):
            return "商品UID（可不填）"
        return "预留"

    def _command(self) -> list[str]:
        project = self.project_required()
        if not project:
            return []
        if isinstance(self, VoicePage):
            uids = [item.strip() for item in self.uid_var.get().split(",") if item.strip()]
            return self.workflow.build_voice_command(project["id"], account_label=self.account_var.get().strip(), uids=uids or None)
        if isinstance(self, AssemblePage):
            top_uids = [item.strip() for item in self.uid_var.get().split(",") if item.strip()]
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
            cmd = self._command()
            result = self.workflow.run_command(cmd)
        except Exception as exc:
            messagebox.showerror("执行失败", str(exc))
            return
        self.log(result.stdout or "")
        if result.stderr:
            self.log(result.stderr)
        self.log(f"退出码：{result.returncode}")


class VoicePage(WorkflowPage):
    pass


class AssemblePage(WorkflowPage):
    pass


class JianyingPage(WorkflowPage):
    pass


PAGE_MAP = {
    "品类项目": ProjectPage,
    "文案中心": CopyPage,
    "资产中心": AssetPage,
    "同步中心": SyncPage,
    "用户管理": AccountPage,
    "设置": SettingsPage,
    "生成配音": VoicePage,
    "组合口播稿": AssemblePage,
    "生成剪映草稿": JianyingPage,
}
