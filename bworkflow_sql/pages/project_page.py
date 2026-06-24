from __future__ import annotations

import json
import os
import re
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable

import customtkinter as ctk

from ..asset_paths import project_category_folder, voice_user_dir
from ..components import (
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
from ..copy_writer import preview_copy_write, write_copy_blocks_to_markdown
from ..db import Database
from ..legacy_import import LegacyImportService
from ..master_data import MasterDataService, display_name
from ..master_service import MasterServiceManager, is_master_connection_error
from ..outline_service import OutlineService
from ..repositories import Repository
from ..settings import (
    CUTME_OUTPUT_ROOT,
    CUTME_ROOT,
    DB_PATH,
    DEFAULT_IMAGE_ROOT,
    DEFAULT_JIANYING_DRAFT_ROOT,
    DEFAULT_MARKDOWN_ROOT,
    DEFAULT_SPOKEN_MD_ROOT,
    DEFAULT_STANDALONE_VOICE_ROOT,
    DEFAULT_VIDEO_ROOT,
    DEFAULT_VOICE_ROOT,
    INTERNAL_WORKSPACE_ROOT,
)
from ..template_config import available_templates, image_set_for_template
from ..style_config import UIStyle
from ..sync_service import AUDIO_SUFFIXES, SyncService
from ..utils import compact_path, now_iso, safe_text, text_hash
from ..workflow_service import (
    DEFAULT_SUBTITLE_ASR_BEAM_SIZE,
    DEFAULT_SUBTITLE_ASR_MODEL,
    DEFAULT_SUBTITLE_ASR_WORKERS,
    VOICE_PROVIDER_INDEXTTS,
    VOICE_PROVIDER_MINIMAX,
    WorkflowRunResult,
    WorkflowService,
    account_voice_id_for_provider,
    markdown_file_to_voice_text,
    normalize_voice_provider,
    subtitle_entry_label,
    subtitle_manifest_entries,
    voice_provider_label,
)
from ..dialogs import TaskProgressDialog
from ..ui_helpers import (
    DialogSection,
    ProjectEditorState,
    VoiceTaskDraft,
    COLUMN_WIDTHS,
    TYPE_LABELS,
    _build_table,
    _set_tree_rows,
    _center_dialog,
    _restore_window,
    account_label_from_spoken_path,
    account_labels_for_voice_provider,
    asset_folder_paths,
    build_project_gap_details,
    build_project_issue_summary,
    collect_voice_status,
    confirm_project_markdown_path,
    configure_treeview_style,
    default_jianying_draft_name,
    default_spoken_markdown_path,
    entry_asset_issue_lines,
    entry_asset_lines,
    format_issue_preview,
    has_ready_asset,
    is_default_spoken_markdown_path,
    is_valid_windows_filename,
    manifest_account_label,
    manifest_display_template,
    manifest_entries,
    manifest_file_paths,
    manifest_missing_assets,
    manifest_product_video_gaps,
    normalized_name,
    open_path,
    parse_uid_list,
    parse_voice_targets,
    preview_lines,
    project_name_exists,
    project_selector_value,
    safe_file_component,
    selected_account_labels,
    show_action_sections_dialog,
    show_confirmation_dialog,
    show_precheck_dialog,
    show_text_dialog,
    split_missing_voice_rows_by_removed_assets,
    voice_block_display,
    voice_block_match_label,
    voice_block_uid,
    voice_generation_targets_from_rows,
    voice_inventory_stats,
    voice_row_choice_label,
    voice_state,
)

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from ..ui import App


class ProjectPageDialog(BasePage):
    def __init__(self, master, app: App):
        super().__init__(master, "品类项目", app)
        self.project_var = app.project_selector_var
        self.workspaces: list[dict[str, Any]] = []
        self.category_tree: list[dict[str, Any]] = []
        self.schemes: list[dict[str, Any]] = []
        self._editor_state: ProjectEditorState | None = None
        self.fields: dict[str, ctk.StringVar] = {key: ctk.StringVar() for key in [
            "name", "workspace_id", "workspace_name",
            "category_parent_id", "category_parent_name",
            "category_id", "category_name",
            "scheme_id", "scheme_name",
            "md_path", "spoken_md_path",
            "image_root", "video_root", "voice_root", "output_root",
        ]}
        self.display_labels: dict[str, ctk.StringVar] = {}
        self.display_label_widgets: dict[str, ctk.CTkLabel] = {}
        self._build()

    def _build(self) -> None:
        content = self.content
        selector = ctk.CTkFrame(content, fg_color="transparent")
        selector.pack(fill="x", pady=(0, UIStyle.PAD_SM))
        ctk.CTkLabel(selector, text="当前项目", font=UIStyle.FONT_BODY, text_color=UIStyle.COLOR_TEXT_DIM).pack(side="left")
        self.project_combo = AppComboBox(selector, width=400, variable=self.project_var)
        self.project_combo.pack(side="left", padx=UIStyle.PAD_SM)
        self.app.register_project_selector(self.project_combo)
        self.project_combo.configure(command=self._select_project)
        PrimaryButton(selector, text="新建", width=80, command=self._new_project).pack(side="left", padx=UIStyle.PAD_XS)
        PrimaryButton(selector, text="编辑", width=80, command=self._edit_project).pack(side="left", padx=UIStyle.PAD_XS)

        card = AppCard(content, "当前项目配置")
        summary = ctk.CTkFrame(card, fg_color="transparent")
        summary.columnconfigure(1, weight=1)
        summary.columnconfigure(3, weight=1)
        rows = [
            ("项目名称", "name"),
            ("Master 工作空间", "workspace_name"),
            ("一级品类", "category_parent_name"),
            ("二级品类", "category_name"),
            ("Master 方案", "scheme_name"),
            ("商品文案 MD", "md_path"),
            ("图片根目录", "image_root"),
            ("视频根目录", "video_root"),
            ("配音根目录", "voice_root"),
        ]
        for index, (label, key) in enumerate(rows):
            row = index // 2
            column = (index % 2) * 2
            self._add_summary_row(summary, row=row, column=column, label=label, key=key)
        card.add_content(summary)

        action_row = ctk.CTkFrame(content, fg_color="transparent")
        action_row.pack(fill="x", pady=(0, UIStyle.PAD_MD))
        PrimaryButton(action_row, text="创建/更新文案框架", command=self._init_outline).pack(side="left", padx=(0, UIStyle.PAD_SM))
        GhostButton(action_row, text="刷新 Master", command=self._refresh_master_for_current, width=96).pack(side="left", padx=(0, UIStyle.PAD_SM))
        ctk.CTkLabel(
            action_row,
            text="Master、MD、素材同步请到“同步中心”统一操作。",
            font=UIStyle.FONT_SMALL,
            text_color=UIStyle.COLOR_TEXT_DIM,
        ).pack(side="left", padx=UIStyle.PAD_SM)

        ctk.CTkLabel(
            content, text="操作日志", font=UIStyle.FONT_H3,
            text_color=UIStyle.COLOR_TEXT_DIM, anchor="w",
        ).pack(anchor="w", pady=(UIStyle.PAD_SM, UIStyle.PAD_XS))
        self.log_text = AppTextbox(content, height=150)
        self.log_text.pack(fill="x", expand=False)

    def _add_summary_row(self, parent: ctk.CTkFrame, *, row: int, column: int, label: str, key: str) -> None:
        ctk.CTkLabel(parent, text=label, font=UIStyle.FONT_BODY, text_color=UIStyle.COLOR_TEXT_DIM).grid(
            row=row, column=column, sticky="nw", padx=(0, UIStyle.PAD_SM), pady=UIStyle.PAD_XS
        )
        value = ctk.StringVar(value="未配置")
        self.display_labels[key] = value
        value_label = ctk.CTkLabel(
            parent,
            textvariable=value,
            font=UIStyle.FONT_BODY,
            text_color=UIStyle.COLOR_FIELD_EMPTY_TEXT,
            justify="left",
            anchor="w",
            wraplength=520,
        )
        value_label.grid(row=row, column=column + 1, sticky="ew", padx=(0, UIStyle.PAD_MD), pady=UIStyle.PAD_XS)
        self.display_label_widgets[key] = value_label

    def _browse(self, fields: dict[str, ctk.StringVar], key: str) -> None:
        if key == "md_path":
            path = filedialog.askopenfilename(filetypes=[("Markdown", "*.md"), ("All", "*.*")], initialdir=str(DEFAULT_MARKDOWN_ROOT))
        else:
            path = filedialog.askdirectory()
        if path:
            fields[key].set(path.replace("/", "\\"))

    def _new_project(self) -> None:
        self._open_project_dialog("new")

    def _edit_project(self) -> None:
        project = self.app.current_project()
        if not project:
            self.toast("请先选择一个品类项目。", kind="warning")
            return
        self._open_project_dialog("edit", project)

    def _payload(self, fields: dict[str, ctk.StringVar] | None = None) -> dict[str, Any]:
        target_fields = fields or self.fields
        return {key: var.get().strip() for key, var in target_fields.items()}

    def _open_project_dialog(self, mode: str, project: dict[str, Any] | None = None) -> None:
        if self._editor_state and self._editor_state.dialog.winfo_exists():
            self._editor_state.dialog.lift()
            self._editor_state.dialog.focus_set()
            return
        dialog = ctk.CTkToplevel(self)
        dialog.title("新建品类项目" if mode == "new" else "编辑品类项目")
        dialog.geometry("1120x720")
        dialog.minsize(960, 640)
        dialog.transient(self.winfo_toplevel())
        dialog.grab_set()
        dialog.rowconfigure(0, weight=1)
        dialog.columnconfigure(0, weight=1)
        state = ProjectEditorState(
            dialog=dialog,
            mode=mode,
            project_id=int(project["id"]) if project else 0,
            fields={key: ctk.StringVar() for key in self.fields},
            workspace_var=ctk.StringVar(),
            parent_category_var=ctk.StringVar(),
            child_category_var=ctk.StringVar(),
            scheme_var=ctk.StringVar(),
        )
        self._editor_state = state
        self._reset_editor_fields(state, project)
        self._build_project_dialog(state)
        self._hydrate_editor_master_state(state)
        dialog.protocol("WM_DELETE_WINDOW", self._close_project_dialog)
        _center_dialog(dialog)
        dialog.lift()
        dialog.focus_set()

    def _build_project_dialog(self, state: ProjectEditorState) -> None:
        shell = ctk.CTkFrame(state.dialog, fg_color="transparent")
        shell.grid(row=0, column=0, sticky="nsew", padx=UIStyle.PAD_XL, pady=UIStyle.PAD_XL)

        card = AppCard(shell, "从 Master 选择品类方案")
        form = ctk.CTkFrame(card, fg_color="transparent")
        form.columnconfigure(1, weight=1)
        form.columnconfigure(3, weight=1)
        row = 0
        ctk.CTkLabel(form, text="项目名称", font=UIStyle.FONT_BODY, text_color=UIStyle.COLOR_TEXT_DIM).grid(row=row, column=0, sticky="w", padx=(0, UIStyle.PAD_SM), pady=UIStyle.PAD_XS)
        AppEntry(form, textvariable=state.fields["name"]).grid(row=row, column=1, columnspan=3, sticky="ew", pady=UIStyle.PAD_XS)
        row += 1
        ctk.CTkLabel(form, text="Master 工作空间", font=UIStyle.FONT_BODY, text_color=UIStyle.COLOR_TEXT_DIM).grid(row=row, column=0, sticky="w", padx=(0, UIStyle.PAD_SM), pady=UIStyle.PAD_XS)
        ctk.CTkLabel(form, textvariable=state.workspace_var, font=UIStyle.FONT_BODY).grid(row=row, column=1, sticky="w", pady=UIStyle.PAD_XS)
        GhostButton(form, text="刷新 Master", command=lambda: self._load_workspaces(force_refresh=True, editor_state=state)).grid(
            row=row, column=2, columnspan=2, sticky="w", padx=UIStyle.PAD_SM, pady=UIStyle.PAD_XS
        )
        row += 1
        ctk.CTkLabel(form, text="一级品类", font=UIStyle.FONT_BODY, text_color=UIStyle.COLOR_TEXT_DIM).grid(row=row, column=0, sticky="w", padx=(0, UIStyle.PAD_SM), pady=UIStyle.PAD_XS)
        state.parent_combo = AppComboBox(form, width=300, variable=state.parent_category_var)
        state.parent_combo.grid(row=row, column=1, sticky="ew", padx=(0, UIStyle.PAD_MD), pady=UIStyle.PAD_XS)
        state.parent_combo.configure(command=lambda _=None: self._editor_on_parent_selected(state))
        ctk.CTkLabel(form, text="二级品类", font=UIStyle.FONT_BODY, text_color=UIStyle.COLOR_TEXT_DIM).grid(row=row, column=2, sticky="w", padx=(0, UIStyle.PAD_SM), pady=UIStyle.PAD_XS)
        state.child_combo = AppComboBox(form, width=300, variable=state.child_category_var)
        state.child_combo.grid(row=row, column=3, sticky="ew", pady=UIStyle.PAD_XS)
        state.child_combo.configure(command=lambda _=None: self._editor_on_child_selected(state))
        row += 1
        ctk.CTkLabel(form, text="Master 方案", font=UIStyle.FONT_BODY, text_color=UIStyle.COLOR_TEXT_DIM).grid(row=row, column=0, sticky="w", padx=(0, UIStyle.PAD_SM), pady=UIStyle.PAD_XS)
        state.scheme_combo = AppComboBox(form, width=400, variable=state.scheme_var)
        state.scheme_combo.grid(row=row, column=1, columnspan=3, sticky="ew", pady=UIStyle.PAD_XS)
        state.scheme_combo.configure(command=lambda _=None: self._editor_on_scheme_selected(state))
        card.add_content(form)

        path_card = AppCard(shell, "文案与素材来源")
        paths = ctk.CTkFrame(path_card, fg_color="transparent")
        paths.columnconfigure(1, weight=1)
        paths.columnconfigure(3, weight=1)
        labels = [("商品文案 MD", "md_path"), ("图片根目录", "image_root"), ("视频根目录", "video_root"), ("配音根目录", "voice_root")]
        for index, (label, key) in enumerate(labels):
            path_row = index // 2
            path_column = (index % 2) * 2
            ctk.CTkLabel(paths, text=label, font=UIStyle.FONT_BODY, text_color=UIStyle.COLOR_TEXT_DIM).grid(row=path_row, column=path_column, sticky="w", padx=(0, UIStyle.PAD_SM), pady=UIStyle.PAD_XS)
            AppEntry(paths, textvariable=state.fields[key]).grid(row=path_row, column=path_column + 1, sticky="ew", padx=(0, UIStyle.PAD_SM), pady=UIStyle.PAD_XS)
            GhostButton(paths, text="选", width=50, command=lambda item=key: self._browse(state.fields, item)).grid(row=path_row, column=path_column + 1, sticky="e", padx=(0, UIStyle.PAD_SM))
        path_card.add_content(paths)

        buttons = ctk.CTkFrame(shell, fg_color="transparent")
        buttons.pack(fill="x", pady=(0, UIStyle.PAD_SM))
        buttons.columnconfigure(0, weight=1)
        GhostButton(buttons, text="取消", command=self._close_project_dialog).grid(row=0, column=1, padx=(0, UIStyle.PAD_SM))
        PrimaryButton(buttons, text="保存新项目" if state.mode == "new" else "保存修改", command=lambda: self._save_project(state)).grid(row=0, column=2)

    def _close_project_dialog(self) -> None:
        if not self._editor_state:
            return
        dialog = self._editor_state.dialog
        self._editor_state = None
        if dialog.winfo_exists():
            dialog.destroy()

    def _reset_editor_fields(self, state: ProjectEditorState, project: dict[str, Any] | None) -> None:
        for key, var in state.fields.items():
            var.set(safe_text(project.get(key)) if project else "")
        if not project:
            state.fields["image_root"].set(str(DEFAULT_IMAGE_ROOT))
            state.fields["video_root"].set(str(DEFAULT_VIDEO_ROOT))
            state.fields["voice_root"].set(str(DEFAULT_VOICE_ROOT))
            state.fields["output_root"].set(str(INTERNAL_WORKSPACE_ROOT))

    def _hydrate_editor_master_state(self, state: ProjectEditorState) -> None:
        workspace = self._default_workspace()
        if workspace:
            state.workspace_var.set(display_name(workspace))
            state.fields["workspace_id"].set(safe_text(workspace.get("id")))
            state.fields["workspace_name"].set(display_name(workspace))
        else:
            state.workspace_var.set("赵二（默认）")
        if self.category_tree:
            self._apply_category_tree_to_editor(state, self.category_tree, source="当前缓存", keep_existing=bool(state.fields["category_name"].get().strip()))
        elif not getattr(self, "_workspaces_loading", False):
            self._load_workspaces(force_refresh=False, quiet=True, editor_state=state)

    def _save_project(self, state: ProjectEditorState) -> None:
        payload = self._payload(state.fields)
        payload["id"] = state.project_id
        if not payload["name"]:
            messagebox.showwarning("缺少项目名", "请填写项目名。")
            return
        if project_name_exists(self.repo.projects(), payload["name"], exclude_project_id=state.project_id or None):
            messagebox.showwarning("项目已存在", f"项目“{payload['name']}”已经存在，请换一个名称。")
            return
        if payload.get("md_path") and not confirm_project_markdown_path(self, payload, payload["md_path"]):
            return

        def work() -> tuple[int, dict[str, Any] | None, str]:
            project_id = self.db.upsert_project(payload)
            sync_result = None
            sync_error = ""
            if payload.get("scheme_id"):
                try:
                    sync_result = self.sync.sync_master_scheme(project_id, apply_changes=True)
                except Exception as exc:
                    sync_error = str(exc)
            return project_id, sync_result, sync_error

        def on_success(result: tuple[int, dict[str, Any] | None, str]) -> None:
            project_id, sync_result, sync_error = result
            self._close_project_dialog()
            self.app.set_current_project(project_id)
            self.refresh()
            self.log(f"已保存项目：{payload['name']}")
            if sync_result:
                self.log(f"Master 方案已同步：新增 {len(sync_result['added'])}，更新 {len(sync_result['updated'])}，移除 {len(sync_result['removed'])}")
            if sync_error:
                self.log(f"Master 方案同步失败：{sync_error}")
                messagebox.showwarning("项目已保存，Master 同步失败", sync_error)
                self.toast("项目已保存，Master 同步失败", kind="warning")
                return
            self.toast("项目已保存")

        self.app.run_background("保存品类项目", work, on_success=on_success, show_success_toast=False)

    def _select_project(self, _=None) -> None:
        value = self.project_var.get()
        if not value:
            return
        project_id = self.app.project_id_for_selector_value(value)
        if project_id is None:
            return
        self.app.set_current_project(project_id)
        self._fill(project_id)

    def _fill(self, project_id: int) -> None:
        project = self.repo.project(project_id)
        if not project:
            return
        for key, var in self.fields.items():
            var.set(safe_text(project.get(key)))
        self.project_var.set(project_selector_value(project))
        for key, var in self.display_labels.items():
            value = safe_text(project.get(key))
            var.set(value or "未配置")
            widget = self.display_label_widgets.get(key)
            if widget is not None:
                widget.configure(text_color=UIStyle.COLOR_TEXT_MAIN if value else UIStyle.COLOR_FIELD_EMPTY_TEXT)

    def log(self, text: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", text.rstrip() + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _init_outline(self) -> None:
        project = self.app.current_project()
        if not project:
            self.toast("请先在“品类项目”中创建或选择项目。", kind="warning")
            return
        md_path = self.fields["md_path"].get().strip() or str(self.outline.default_markdown_path(project["id"]))
        md_file = Path(md_path)
        initialdir = str(md_file.parent if md_file.parent.exists() else DEFAULT_MARKDOWN_ROOT)
        dialog_options = {
            "defaultextension": ".md",
            "filetypes": [("Markdown", "*.md"), ("All", "*.*")],
            "initialdir": initialdir,
            "initialfile": md_file.name,
        }
        if md_file.exists():
            path = filedialog.askopenfilename(title="选择要更新的 MD 文档", **dialog_options)
        else:
            path = filedialog.asksaveasfilename(title="创建新的 MD 文档", **dialog_options)
        if not path:
            return
        if not confirm_project_markdown_path(self, project, path):
            return

        def work() -> tuple[dict[str, Any] | None, dict[str, Any], dict[str, Any]]:
            master_result = self.sync.sync_master_scheme(project["id"], apply_changes=True) if safe_text(project.get("scheme_id")) else None
            result = self.outline.init_or_update_outline(project["id"], path)
            sync_result = self.sync.sync_markdown(project["id"])
            return master_result, result, sync_result

        def on_success(payload: tuple[dict[str, Any] | None, dict[str, Any], dict[str, Any]]) -> None:
            master_result, result, sync_result = payload
            self.fields["md_path"].set(result["target_path"])
            self.display_labels["md_path"].set(result["target_path"])
            if master_result:
                self.log(f"Master 已刷新：新增 {len(master_result['added'])}，更新 {len(master_result['updated'])}，移除 {len(master_result['removed'])}。")
            self.log(f"文案框架已更新：商品 {result['total']} 个，新增 {len(result['added'])}，保留 {len(result['preserved'])}。")
            self.log(f"已同步 MD 到数据库：入库 {sync_result['upserted']} 条。")
            self.toast("文案框架已更新")

        self.app.run_background("创建文案框架", work, on_success=on_success, success_message="文案框架已更新", show_success_toast=False)

    def _refresh_master_for_current(self) -> None:
        project = self.app.current_project()
        if not project:
            self.toast("请先在“品类项目”中创建或选择项目。", kind="warning")
            return
        if not safe_text(project.get("scheme_id")):
            self.toast("当前项目还没有绑定 Master 方案。", kind="warning")
            return

        def on_success(result: dict[str, Any]) -> None:
            self.refresh()
            self.log(f"Master 已刷新：新增 {len(result['added'])}，更新 {len(result['updated'])}，移除 {len(result['removed'])}。")
            self.toast("Master 已刷新")

        self.app.run_background(
            "刷新 Master",
            lambda: self.sync.sync_master_scheme(project["id"], apply_changes=True),
            on_success=on_success,
            show_success_toast=False,
        )

    def refresh(self) -> None:
        projects = self.repo.projects()
        self.app.sync_project_selectors()
        if self.app.current_project_id:
            self._fill(self.app.current_project_id)
        elif projects:
            self.app.set_current_project(int(projects[0]["id"]))
            self._fill(projects[0]["id"])
        else:
            self.project_var.set("")
            for key, var in self.fields.items():
                var.set("")
            for var in self.display_labels.values():
                var.set("未配置")
            for widget in self.display_label_widgets.values():
                widget.configure(text_color=UIStyle.COLOR_FIELD_EMPTY_TEXT)
        if not self.workspaces:
            self._load_workspaces(force_refresh=False, quiet=True)

    def _load_workspaces(self, *, force_refresh: bool = False, quiet: bool = False, editor_state: ProjectEditorState | None = None) -> None:
        if getattr(self, "_workspaces_loading", False):
            return
        self._workspaces_loading = True

        def work() -> dict[str, Any]:
            workspaces = self.master_data.fetch_workspaces(force_refresh=force_refresh)
            workspace = next((w for w in workspaces if safe_text(w.get("name")) == "赵二" or safe_text(w.get("slug")) == "zhaoer"), workspaces[0] if workspaces else None)
            tree, source = [], ""
            if workspace:
                _w, tree, source = self.master_data.fetch_category_tree(safe_text(workspace.get("id")), force_refresh=force_refresh)
            return {"workspaces": workspaces, "workspace": workspace, "tree": tree, "source": source}

        def on_success(result: dict[str, Any]) -> None:
            self.workspaces = result["workspaces"]
            self.category_tree = result["tree"]
            workspace = result["workspace"]
            if editor_state and self._editor_alive(editor_state):
                if workspace:
                    editor_state.workspace_var.set(display_name(workspace))
                    editor_state.fields["workspace_id"].set(safe_text(workspace.get("id")))
                    editor_state.fields["workspace_name"].set(display_name(workspace))
                self._apply_category_tree_to_editor(
                    editor_state,
                    result["tree"],
                    source=result["source"],
                    keep_existing=bool(editor_state.fields["category_name"].get().strip()),
                )
            if not quiet:
                self.log(f"已读取 Master 工作空间，当前固定使用：{display_name(workspace) if workspace else '赵二'}。")

        def on_error(exc: Exception, _tb: str) -> None:
            if not quiet:
                messagebox.showerror("读取 Master 失败", str(exc))

        self.app.run_background(
            "读取 Master",
            work,
            on_success=on_success,
            on_error=on_error,
            on_done=lambda: setattr(self, "_workspaces_loading", False),
            success_message=None if quiet else "Master 已刷新",
            silent=quiet,
        )

    def _editor_alive(self, state: ProjectEditorState) -> bool:
        return bool(state and state.dialog and state.dialog.winfo_exists())

    def _default_workspace(self) -> dict[str, Any] | None:
        if not self.workspaces:
            return None
        for workspace in self.workspaces:
            if safe_text(workspace.get("name")) == "赵二" or safe_text(workspace.get("slug")) == "zhaoer":
                return workspace
        return self.workspaces[0]

    def _choose_from_options(self, parent: tk.Widget, *, title: str, options: list[str], current: str = "") -> str | None:
        if not options:
            return None
        dialog = ctk.CTkToplevel(parent)
        dialog.title(title)
        dialog.geometry("560x640")
        dialog.minsize(420, 420)
        dialog.transient(parent.winfo_toplevel())
        dialog.grab_set()
        dialog.rowconfigure(1, weight=1)
        dialog.columnconfigure(0, weight=1)
        result = {"value": None}

        ctk.CTkLabel(dialog, text=title, font=UIStyle.FONT_H2).grid(
            row=0, column=0, sticky="w", padx=UIStyle.PAD_LG, pady=(UIStyle.PAD_LG, UIStyle.PAD_SM)
        )
        body = ctk.CTkScrollableFrame(dialog, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew", padx=UIStyle.PAD_LG, pady=(0, UIStyle.PAD_MD))

        def choose(value: str) -> None:
            result["value"] = value
            dialog.destroy()

        for option in options:
            button_cls = PrimaryButton if option == current else GhostButton
            button_cls(body, text=option, command=lambda value=option: choose(value)).pack(fill="x", pady=(0, UIStyle.PAD_XS))

        footer = ctk.CTkFrame(dialog, fg_color="transparent")
        footer.grid(row=2, column=0, sticky="ew", padx=UIStyle.PAD_LG, pady=(0, UIStyle.PAD_LG))
        footer.columnconfigure(0, weight=1)
        GhostButton(footer, text="取消", command=dialog.destroy).grid(row=0, column=1)
        _center_dialog(dialog)
        dialog.wait_window()
        return result["value"]

    def _choose_parent(self, state: ProjectEditorState) -> None:
        options = [safe_text(parent.get("name")) for parent in self.category_tree if safe_text(parent.get("name"))]
        chosen = self._choose_from_options(state.dialog, title="选择一级品类", options=options, current=state.parent_category_var.get())
        if not chosen:
            return
        state.parent_category_var.set(chosen)
        self._editor_on_parent_selected(state)

    def _choose_child(self, state: ProjectEditorState) -> None:
        parent = self._selected_parent(state)
        if not parent:
            messagebox.showinfo("需要一级品类", "请先选择一级品类。", parent=state.dialog)
            return
        options = [safe_text(child.get("name")) for child in parent.get("children") or [] if safe_text(child.get("name"))]
        chosen = self._choose_from_options(state.dialog, title="选择二级品类", options=options, current=state.child_category_var.get())
        if not chosen:
            return
        state.child_category_var.set(chosen)
        self._editor_on_child_selected(state)

    def _choose_scheme(self, state: ProjectEditorState) -> None:
        if not self.schemes:
            messagebox.showinfo("需要方案列表", "请先选择二级品类并等待方案加载完成。", parent=state.dialog)
            return
        options = [display_name(scheme, safe_text(scheme.get("id"))) for scheme in self.schemes]
        chosen = self._choose_from_options(state.dialog, title="选择 Master 方案", options=options, current=state.scheme_var.get())
        if not chosen:
            return
        state.scheme_var.set(chosen)
        self._editor_on_scheme_selected(state)

    def _apply_category_tree_to_editor(self, state: ProjectEditorState, tree: list[dict[str, Any]], *, source: str, keep_existing: bool) -> None:
        if not self._editor_alive(state):
            return
        parent_names = [safe_text(parent.get("name")) for parent in tree]
        if state.parent_combo is not None:
            state.parent_combo.configure(values=parent_names)
        state.parent_category_var.set("")
        state.child_category_var.set("")
        state.scheme_var.set("")
        if state.child_combo is not None:
            state.child_combo.configure(values=[])
        if state.scheme_combo is not None:
            state.scheme_combo.configure(values=[])
        if parent_names:
            saved = state.fields["category_parent_name"].get().strip() if keep_existing else ""
            saved_child = state.fields["category_name"].get().strip() if keep_existing else ""
            saved_scheme = state.fields["scheme_name"].get().strip() if keep_existing else ""
            state.parent_category_var.set(saved if saved in parent_names else parent_names[0])
            self._editor_on_parent_selected(state, preferred_child=saved_child, preferred_scheme=saved_scheme)
        self.log(f"已读取 Master 品类：{len(parent_names)} 个一级品类（来源：{source}）。")

    def _editor_on_parent_selected(self, state: ProjectEditorState, _=None, *, preferred_child: str = "", preferred_scheme: str = "") -> None:
        parent = self._selected_parent(state)
        if not parent:
            return
        state.fields["category_parent_id"].set(safe_text(parent.get("id")))
        state.fields["category_parent_name"].set(safe_text(parent.get("name")))
        children = parent.get("children") or []
        child_names = [safe_text(child.get("name")) for child in children if safe_text(child.get("name"))]
        if state.child_combo is not None:
            state.child_combo.configure(values=child_names)
        state.child_category_var.set(preferred_child if preferred_child in child_names else (child_names[0] if child_names else ""))
        state.scheme_var.set("")
        if state.scheme_combo is not None:
            state.scheme_combo.configure(values=[])
        if child_names:
            self._editor_on_child_selected(state, preferred_scheme=preferred_scheme)

    def _editor_on_child_selected(self, state: ProjectEditorState, _=None, *, preferred_scheme: str = "") -> None:
        workspace = self._selected_workspace(state)
        child = self._selected_child(state)
        if not workspace or not child:
            return
        state.fields["category_id"].set(safe_text(child.get("id")))
        state.fields["category_name"].set(safe_text(child.get("name")))
        if state.scheme_combo is not None:
            state.scheme_combo.configure(values=[])
        state.scheme_var.set("读取中...")

        def work() -> tuple[list[dict[str, Any]], str]:
            return self.master_data.fetch_schemes(workspace_id=safe_text(workspace.get("id")), category_id=safe_text(child.get("id")))

        def on_success(result: tuple[list[dict[str, Any]], str]) -> None:
            if not self._editor_alive(state):
                return
            self.schemes, source = result
            names = [display_name(scheme, safe_text(scheme.get("id"))) for scheme in self.schemes]
            if state.scheme_combo is not None:
                state.scheme_combo.configure(values=names)
            if names:
                state.scheme_var.set(preferred_scheme if preferred_scheme in names else names[0])
                self._editor_on_scheme_selected(state)
            self.log(f"已读取“{safe_text(child.get('name'))}”方案：{len(names)} 个（来源：{source}）。")

        def on_error(exc: Exception, _tb: str) -> None:
            if not self._editor_alive(state):
                return
            state.scheme_var.set("")
            messagebox.showerror("读取方案失败", str(exc))

        self.app.run_background("读取方案", work, on_success=on_success, on_error=on_error, success_message=None, silent=True)

    def _editor_on_scheme_selected(self, state: ProjectEditorState, _=None) -> None:
        name = state.scheme_var.get()
        for scheme in self.schemes:
            if display_name(scheme, safe_text(scheme.get("id"))) != name:
                continue
            state.fields["scheme_id"].set(safe_text(scheme.get("id")))
            state.fields["scheme_name"].set(name)
            if not state.fields["name"].get().strip():
                parent = state.fields["category_parent_name"].get().strip()
                child = state.fields["category_name"].get().strip()
                state.fields["name"].set(f"{parent}-{child}" if parent and child else name)
            return

    def _selected_workspace(self, state: ProjectEditorState) -> dict[str, Any] | None:
        name = state.workspace_var.get()
        for workspace in self.workspaces:
            if display_name(workspace) == name:
                return workspace
        return None

    def _selected_parent(self, state: ProjectEditorState) -> dict[str, Any] | None:
        name = state.parent_category_var.get()
        for parent in self.category_tree:
            if safe_text(parent.get("name")) == name:
                return parent
        return None

    def _selected_child(self, state: ProjectEditorState) -> dict[str, Any] | None:
        parent = self._selected_parent(state)
        if not parent:
            return None
        name = state.child_category_var.get()
        for child in parent.get("children") or []:
            if safe_text(child.get("name")) == name:
                return child
        return None
