from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable

from .db import Database
from .repositories import Repository
from .settings import DEFAULT_IMAGE_ROOT, DEFAULT_MARKDOWN_ROOT, DEFAULT_OUTPUT_ROOT, DEFAULT_VIDEO_ROOT, DEFAULT_VOICE_ROOT
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
        self.fields: dict[str, tk.StringVar] = {key: tk.StringVar() for key in [
            "name",
            "workspace_id",
            "workspace_name",
            "category_parent_name",
            "category_id",
            "category_name",
            "scheme_id",
            "scheme_name",
            "md_path",
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

        form = ttk.LabelFrame(self, text="品类项目配置", padding=12)
        form.grid(row=1, column=0, sticky="ew")
        form.columnconfigure(1, weight=1)
        form.columnconfigure(3, weight=1)
        labels = [
            ("项目名", "name"),
            ("Workspace ID", "workspace_id"),
            ("Workspace 名称", "workspace_name"),
            ("一级品类", "category_parent_name"),
            ("二级品类 ID", "category_id"),
            ("二级品类", "category_name"),
            ("方案 ID", "scheme_id"),
            ("方案名称", "scheme_name"),
            ("MD 文档", "md_path"),
            ("图片根目录", "image_root"),
            ("视频根目录", "video_root"),
            ("配音根目录", "voice_root"),
            ("输出目录", "output_root"),
        ]
        for index, (label, key) in enumerate(labels):
            row = index // 2
            col = (index % 2) * 2
            ttk.Label(form, text=label).grid(row=row, column=col, sticky="w", padx=(0, 8), pady=5)
            entry = ttk.Entry(form, textvariable=self.fields[key])
            entry.grid(row=row, column=col + 1, sticky="ew", padx=(0, 12), pady=5)
            if key.endswith("_path") or key.endswith("_root"):
                ttk.Button(form, text="选", width=4, command=lambda item=key: self._browse(item)).grid(row=row, column=col + 1, sticky="e", padx=(0, 12))

        actions = ttk.Frame(self)
        actions.grid(row=2, column=0, sticky="ew", pady=12)
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
        self.fields["image_root"].set(str(DEFAULT_IMAGE_ROOT))
        self.fields["video_root"].set(str(DEFAULT_VIDEO_ROOT))
        self.fields["voice_root"].set(str(DEFAULT_VOICE_ROOT))
        self.fields["output_root"].set(str(DEFAULT_OUTPUT_ROOT))
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
    columns = ("标签", "Account ID", "音色 ID", "音色名", "图片身份", "结尾音频", "启用")

    def __init__(self, master, app: App):
        super().__init__(master, app)
        self.vars = {key: tk.StringVar() for key in ["label", "account_id", "voice_id", "voice_name", "media_identity", "closing_audio_path"]}
        form = ttk.LabelFrame(self, text="新增/更新用户", padding=10)
        form.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        form.columnconfigure(1, weight=1)
        keys = list(self.vars)
        for index, key in enumerate(keys):
            ttk.Label(form, text=key).grid(row=index // 2, column=(index % 2) * 2, sticky="w", padx=(0, 8), pady=4)
            ttk.Entry(form, textvariable=self.vars[key]).grid(row=index // 2, column=(index % 2) * 2 + 1, sticky="ew", padx=(0, 8), pady=4)
        ttk.Button(form, text="保存用户", command=self._save_account).grid(row=3, column=0, sticky="w", pady=6)
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
        ttk.Label(self, text="V2 规则：数据库是主账本；MD 是文案编辑格式；素材文件夹保存真实文件。").grid(row=1, column=0, sticky="w", pady=4)


class WorkflowPage(BasePage):
    title = ""
    builder: Callable[..., list[str]]

    def __init__(self, master, app: App):
        super().__init__(master, app)
        self.mode_var = tk.StringVar(value="standard")
        self.account_var = tk.StringVar()
        self.uid_var = tk.StringVar()
        self.intro_var = tk.StringVar(value="1")
        actions = ttk.Frame(self)
        actions.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(actions, text=self.primary_label()).pack(side="left")
        ttk.Entry(actions, textvariable=self.account_var, width=22).pack(side="left", padx=8)
        ttk.Label(actions, text=self.uid_label()).pack(side="left")
        ttk.Entry(actions, textvariable=self.uid_var, width=30).pack(side="left", padx=8)
        ttk.Label(actions, text="模式").pack(side="left")
        ttk.Combobox(actions, textvariable=self.mode_var, values=["standard", "top"], width=10).pack(side="left", padx=8)
        if isinstance(self, AssemblePage):
            ttk.Label(actions, text="引言编号").pack(side="left")
            ttk.Entry(actions, textvariable=self.intro_var, width=5).pack(side="left", padx=8)
        ttk.Button(actions, text="生成命令", command=self._build_command).pack(side="left", padx=8)
        ttk.Button(actions, text="执行", command=self._run_command).pack(side="left")
        self.log_text = tk.Text(self, height=28, state="disabled")
        self.log_text.grid(row=99, column=0, sticky="nsew")

    def primary_label(self) -> str:
        if isinstance(self, JianyingPage):
            return "草稿名"
        return "账号标签"

    def uid_label(self) -> str:
        if isinstance(self, AssemblePage):
            return "Top UID"
        if isinstance(self, VoicePage):
            return "商品 UID"
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
            return self.workflow.build_assembly_command(
                project["id"],
                mode=self.mode_var.get(),
                top_uids=top_uids or None,
                account_label=self.account_var.get().strip(),
                intro_index=int(self.intro_var.get() or "1"),
            )
        return self.workflow.build_jianying_command(project["id"], draft_name=self.account_var.get().strip())

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
