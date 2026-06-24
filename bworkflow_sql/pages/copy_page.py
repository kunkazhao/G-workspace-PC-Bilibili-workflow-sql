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
        PrimaryButton(top, text="写入文案", width=110, command=self._open_copy_writer).pack(side="right")
        ctk.CTkLabel(top, text="单击正文可查看完整内容。同步 MD 请到“同步中心”。", font=UIStyle.FONT_SMALL, text_color=UIStyle.COLOR_TEXT_DIM).pack(side="left", padx=UIStyle.PAD_LG)

        outer = ctk.CTkFrame(self.content, fg_color=UIStyle.COLOR_CARD_BG, corner_radius=UIStyle.RADIUS_LG)
        outer.pack(fill="both", expand=True)
        outer.grid_columnconfigure(0, weight=1)
        outer.grid_rowconfigure(0, weight=1)

        self.tree = _build_table(outer, CopyPageColumns, row=0, empty_text="暂无文案数据。请先同步 Master 方案并导入 MD 文档。")
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
        txt = ctk.CTkTextbox(dialog, wrap="word", padx=UIStyle.PAD_LG, pady=UIStyle.PAD_LG, font=UIStyle.FONT_SMALL)
        txt.pack(fill="both", expand=True)
        txt.insert("1.0", text)
        txt.configure(state="disabled")
        GhostButton(dialog, text="关闭", command=dialog.destroy).pack(pady=(0, UIStyle.PAD_MD))
        _center_dialog(dialog)

    def _open_copy_writer(self) -> None:
        project = self.app.current_project()
        if not project:
            self.toast("请先在“品类项目”中创建或选择项目。", kind="warning")
            return
        path_var = ctk.StringVar(value=safe_text(project.get("md_path")) or str(self.outline.default_markdown_path(project["id"])))
        dialog = ctk.CTkToplevel(self)
        dialog.title("写入文案")
        dialog.geometry("960x720")
        dialog.minsize(760, 560)
        dialog.transient(self.winfo_toplevel())
        dialog.grab_set()
        dialog.rowconfigure(2, weight=1)
        dialog.columnconfigure(1, weight=1)

        ctk.CTkLabel(dialog, text="文案 MD 路径", font=UIStyle.FONT_BODY, text_color=UIStyle.COLOR_TEXT_DIM).grid(
            row=0, column=0, sticky="w", padx=(UIStyle.PAD_LG, UIStyle.PAD_SM), pady=(UIStyle.PAD_LG, UIStyle.PAD_SM)
        )
        AppEntry(dialog, textvariable=path_var).grid(
            row=0, column=1, sticky="ew", padx=(0, UIStyle.PAD_SM), pady=(UIStyle.PAD_LG, UIStyle.PAD_SM)
        )
        GhostButton(dialog, text="选择", width=70, command=lambda: self._browse_copy_writer_path(path_var)).grid(
            row=0, column=2, sticky="e", padx=(0, UIStyle.PAD_LG), pady=(UIStyle.PAD_LG, UIStyle.PAD_SM)
        )
        ctk.CTkLabel(dialog, text="粘贴格式：商品UID: XLB006，下一行开始写正文；多个商品连续粘贴。", font=UIStyle.FONT_SMALL, text_color=UIStyle.COLOR_TEXT_DIM).grid(
            row=1, column=1, columnspan=2, sticky="w", padx=(0, UIStyle.PAD_LG), pady=(0, UIStyle.PAD_SM)
        )
        text = ctk.CTkTextbox(dialog, wrap="word", font=UIStyle.FONT_SMALL)
        text.grid(row=2, column=0, columnspan=3, sticky="nsew", padx=UIStyle.PAD_LG, pady=(0, UIStyle.PAD_MD))

        buttons = ctk.CTkFrame(dialog, fg_color="transparent")
        buttons.grid(row=3, column=0, columnspan=3, sticky="ew", padx=UIStyle.PAD_LG, pady=(0, UIStyle.PAD_LG))
        buttons.columnconfigure(0, weight=1)
        GhostButton(buttons, text="取消", command=dialog.destroy).grid(row=0, column=1, padx=(0, UIStyle.PAD_SM))
        PrimaryButton(buttons, text="预览并写入", command=lambda: self._preview_and_write_copy(dialog, path_var.get(), text.get("1.0", "end"))).grid(row=0, column=2)
        _center_dialog(dialog)

    def _browse_copy_writer_path(self, path_var: ctk.StringVar) -> None:
        current = path_var.get().strip()
        initialdir = str(Path(current).parent) if current else str(DEFAULT_MARKDOWN_ROOT)
        path = filedialog.askopenfilename(
            filetypes=[("Markdown", "*.md"), ("All", "*.*")],
            initialdir=initialdir,
            parent=self,
        )
        if path:
            path_var.set(path.replace("/", "\\"))

    def _preview_and_write_copy(self, dialog: ctk.CTkToplevel, path_text: str, pasted_text: str) -> None:
        project = self.app.current_project()
        if not project:
            messagebox.showinfo("需要品类项目", "请先在“品类项目”中创建或选择项目。", parent=dialog)
            return
        path_text = path_text.strip()
        if not path_text:
            messagebox.showwarning("缺少 MD 路径", "请选择要写入的文案 MD。", parent=dialog)
            return
        if not Path(path_text).exists():
            messagebox.showwarning("MD 文件不存在", f"路径不存在：\n{path_text}", parent=dialog)
            return
        if not pasted_text.strip():
            messagebox.showwarning("缺少文案", "请先粘贴要写入的文案。", parent=dialog)
            return
        products = self.repo.products(project["id"], include_removed=False)
        try:
            preview = preview_copy_write(path_text, pasted_text, products)
        except Exception as exc:
            messagebox.showerror("解析失败", str(exc), parent=dialog)
            return

        matched_items = [f"{item['uid']} -> {item['label']}：{item['body'][:42]}" for item in preview["matched"]]
        blocked = (
            [f"{uid}：当前品类项目中没有这个商品" for uid in preview["missing_product"]]
            + [f"{uid}：MD 中没有找到对应商品标题" for uid in preview["missing_heading"]]
            + [f"{uid}：输入中重复，已跳过后续重复段落" for uid in preview["duplicate_input"]]
        )
        sections = [
            DialogSection(
                title="写入目标",
                step="1",
                tone="primary",
                rows=[
                    ("项目", safe_text(project.get("name"))),
                    ("MD 路径", path_text),
                    ("解析到 UID", f"{len(preview['blocks'])} 个"),
                    ("可写入", f"{len(preview['matched'])} 个"),
                    ("跳过 / 阻塞", f"{len(blocked)} 个"),
                ],
                helper="确认后会把可写入的文案追加到对应商品标题下，并同步 MD 入库。",
            ),
            DialogSection(
                title="将写入的文案",
                step="2",
                tone="success" if preview["matched"] else "warning",
                items=preview_lines(matched_items),
            ),
            DialogSection(
                title="跳过与阻塞",
                step="3",
                tone="warning" if blocked else "success",
                items=preview_lines(blocked),
                helper="" if blocked else "当前没有发现跳过项。",
            ),
        ]
        if not show_precheck_dialog(
            dialog,
            "确认写入文案",
            "请核对本次解析结果，确认无误后再写入 MD。",
            sections,
            can_continue=bool(preview["matched"]),
            confirm_text="确认写入",
        ):
            return

        def work() -> tuple[dict[str, Any], dict[str, Any]]:
            result = write_copy_blocks_to_markdown(path_text, pasted_text, products)
            if safe_text(project.get("md_path")) != str(Path(path_text)):
                self.db.execute("UPDATE projects SET md_path=?, updated_at=datetime('now') WHERE id=?", (str(Path(path_text)), project["id"]))
            sync_result = self.sync.sync_markdown(project["id"])
            return result, sync_result

        def on_success(payload: tuple[dict[str, Any], dict[str, Any]]) -> None:
            result, sync_result = payload
            if dialog.winfo_exists():
                dialog.destroy()
            self.toast(f"文案已写入：{len(result['written'])} 条；入库 {sync_result['upserted']} 条")
            self.refresh()

        self.app.run_background("写入文案", work, on_success=on_success, show_success_toast=False)

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
            pmap = {item["uid"]: item["title"] for item in self.repo.products(proj["id"], include_removed=False)}
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
