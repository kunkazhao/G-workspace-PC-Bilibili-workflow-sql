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


class SyncStatusCard(ctk.CTkFrame):
    def __init__(self, master, title: str, buttons: list[tuple[str, Callable]], *, min_height: int):
        super().__init__(
            master,
            fg_color=UIStyle.COLOR_CARD_BG,
            corner_radius=UIStyle.RADIUS_LG,
            border_width=1,
            border_color=UIStyle.COLOR_BORDER,
        )
        self.grid_propagate(False)
        self.configure(height=min_height)

        self.title_label = ctk.CTkLabel(
            self,
            text=title,
            font=UIStyle.FONT_H2,
            text_color=UIStyle.COLOR_TEXT_MAIN,
            anchor="w",
        )
        self.title_label.pack(anchor="w", padx=UIStyle.PAD_XL, pady=(UIStyle.PAD_XL, UIStyle.PAD_MD))

        self.body_label = ctk.CTkLabel(
            self,
            text="等待刷新",
            justify="left",
            anchor="nw",
            font=UIStyle.FONT_BODY,
            text_color=UIStyle.COLOR_TEXT_DIM,
            wraplength=520,
        )
        self.body_label.pack(fill="x", padx=UIStyle.PAD_XL, pady=(0, UIStyle.PAD_MD))

        self.asset_rows_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.metric_frame = ctk.CTkFrame(self, fg_color="transparent")

        self.button_frame = ctk.CTkFrame(self, fg_color="transparent")
        if buttons:
            self.button_frame.pack(fill="x", padx=UIStyle.PAD_XL, pady=(0, UIStyle.PAD_XL))
            for index, (text, cmd) in enumerate(buttons):
                btn_cls = PrimaryButton if index == 0 else GhostButton
                btn_cls(self.button_frame, text=text, command=cmd, height=36).pack(side="left", padx=(0, UIStyle.PAD_SM), pady=2)

    def set_body(self, text: str) -> None:
        self.body_label.configure(text=text)

    def set_asset_rows(self, rows: list) -> None:
        """rows 每项格式：
        - 标准: (label, path, open_cmd, sync_cmd)
        - 配音: (label, path, open_cmd, None, voice_check_cmd)
        - 带匹配数: (label, path, open_cmd, sync_cmd, None, matched_count) 或 (label, path, open_cmd, None, voice_check_cmd, matched_count)
        """
        for child in self.asset_rows_frame.winfo_children():
            child.destroy()
        if rows:
            if not self.asset_rows_frame.winfo_ismapped():
                self.asset_rows_frame.pack(fill="x", padx=UIStyle.PAD_LG, pady=(0, UIStyle.PAD_SM))
            if self.body_label.winfo_ismapped():
                self.body_label.pack_forget()
        else:
            if self.asset_rows_frame.winfo_ismapped():
                self.asset_rows_frame.pack_forget()
            need_body = (self.body_label.cget("text") != "等待刷新" and (not self.metric_frame.winfo_ismapped()))
            if need_body and not self.body_label.winfo_ismapped():
                self.body_label.pack(fill="x", padx=UIStyle.PAD_XL, pady=(0, UIStyle.PAD_SM))
        for item in rows:
            label, path, open_cmd, sync_cmd, *extra = item
            voice_check_cmd = extra[0] if len(extra) > 0 else None
            matched_count = extra[1] if len(extra) > 1 else None
            row = ctk.CTkFrame(self.asset_rows_frame, fg_color="transparent")
            row.pack(fill="x", pady=(0, UIStyle.PAD_MD))
            ctk.CTkLabel(row, text=label, width=34, font=UIStyle.FONT_BODY, text_color=UIStyle.COLOR_TEXT_MAIN, anchor="w").pack(side="left")
            path_box = ctk.CTkFrame(row, fg_color=UIStyle.COLOR_INPUT_BG, corner_radius=UIStyle.RADIUS_MD, border_width=1, border_color=UIStyle.COLOR_BORDER)
            path_box.pack(side="left", fill="x", expand=True, padx=(0, UIStyle.PAD_SM))
            ctk.CTkLabel(path_box, text=compact_path(path, 52) or "--", font=UIStyle.FONT_BODY, text_color=UIStyle.COLOR_TEXT_DIM, anchor="w").pack(fill="x", padx=UIStyle.PAD_SM, pady=UIStyle.PAD_SM)
            if matched_count is not None:
                if isinstance(matched_count, str):
                    stat_text = matched_count
                    stat_color = UIStyle.COLOR_PRIMARY if "0/" not in matched_count and "无" not in matched_count else UIStyle.COLOR_TEXT_DIM
                else:
                    unit = {"图片": "张", "视频": "个", "配音": "个"}.get(label, "个")
                    stat_text = f"已匹配 {matched_count}{unit}" if matched_count else "无匹配"
                    stat_color = UIStyle.COLOR_PRIMARY if matched_count else UIStyle.COLOR_TEXT_DIM
                ctk.CTkLabel(row, text=stat_text, font=UIStyle.FONT_BODY, text_color=stat_color, anchor="e").pack(side="left", padx=(UIStyle.PAD_SM, UIStyle.PAD_SM))
            GhostButton(row, text="打开目录", command=open_cmd, height=36, width=84).pack(side="left", padx=(0, UIStyle.PAD_SM))
            if voice_check_cmd:
                PrimaryButton(row, text="检查配音", command=voice_check_cmd, height=36, width=84).pack(side="left")
            else:
                PrimaryButton(row, text="同步素材", command=sync_cmd, height=36, width=84).pack(side="left")

    def set_metrics(self, items: list[tuple[str, int]], *, warn_labels: set[str] | None = None) -> None:
        for child in self.metric_frame.winfo_children():
            child.destroy()
        if items:
            if not self.metric_frame.winfo_ismapped():
                self.metric_frame.pack(fill="x", padx=UIStyle.PAD_XL, pady=(0, UIStyle.PAD_MD))
        else:
            if self.metric_frame.winfo_ismapped():
                self.metric_frame.pack_forget()
            return
        warn_labels = warn_labels or set()
        for label, value in items:
            chip = ctk.CTkFrame(
                self.metric_frame,
                fg_color=UIStyle.COLOR_SURFACE_SOFT,
                corner_radius=UIStyle.RADIUS_MD,
                border_width=1,
                border_color=UIStyle.COLOR_BORDER,
            )
            chip.pack(side="left", padx=(0, UIStyle.PAD_SM), pady=3)
            ctk.CTkLabel(chip, text=label, font=UIStyle.FONT_BODY, text_color=UIStyle.COLOR_TEXT_DIM).pack(side="left", padx=(UIStyle.PAD_SM, UIStyle.PAD_XS), pady=UIStyle.PAD_XS)
            value_color = UIStyle.COLOR_PRIMARY if label in warn_labels and value else UIStyle.COLOR_TEXT_MAIN
            ctk.CTkLabel(chip, text=str(value), font=UIStyle.FONT_BODY, text_color=value_color).pack(side="left", padx=(0, UIStyle.PAD_SM), pady=UIStyle.PAD_XS)


class SyncPage(BasePage):
    def __init__(self, master, app: App):
        super().__init__(master, "同步中心", app)
        self.project_var = app.project_selector_var
        self.user_var = ctk.StringVar(value="小燃")
        self.template_var = ctk.StringVar(value="")
        self.asset_paths: dict[str, str] = {}
        self._build()

    def _build(self) -> None:
        top = ctk.CTkFrame(self.content, fg_color="transparent")
        top.pack(fill="x", pady=(0, UIStyle.PAD_SM))
        ctk.CTkLabel(top, text="本次同步项目", font=UIStyle.FONT_SMALL, text_color=UIStyle.COLOR_TEXT_DIM).pack(side="left", padx=(0, UIStyle.PAD_SM))
        self.project_combo = AppComboBox(top, width=250, variable=self.project_var)
        self.project_combo.pack(side="left", padx=(0, UIStyle.PAD_MD))
        self.app.register_project_selector(self.project_combo)
        self.project_combo.configure(command=self._select_project)
        ctk.CTkLabel(top, text="用户", font=UIStyle.FONT_SMALL, text_color=UIStyle.COLOR_TEXT_DIM).pack(side="left", padx=(0, UIStyle.PAD_SM))
        self.user_combo = AppComboBox(top, width=92, variable=self.user_var)
        self.user_combo.pack(side="left", padx=(0, UIStyle.PAD_MD))
        self.user_combo.configure(command=lambda _=None: self._on_user_changed())
        ctk.CTkLabel(top, text="模板", font=UIStyle.FONT_SMALL, text_color=UIStyle.COLOR_TEXT_DIM).pack(side="left", padx=(0, UIStyle.PAD_SM))
        self.template_combo = AppComboBox(top, width=128, variable=self.template_var)
        self.template_combo.pack(side="left", padx=(0, UIStyle.PAD_MD))
        self.template_combo.configure(command=lambda _=None: self.refresh())
        GhostButton(top, text="刷新状态", command=self.refresh, width=92).pack(side="left", padx=(0, UIStyle.PAD_SM))
        PrimaryButton(top, text="一键同步当前品类", command=self._sync_all, width=132).pack(side="left", padx=(0, UIStyle.PAD_SM))

        # Status cards grid (填充中间区域)
        grid = ctk.CTkFrame(self.content, fg_color="transparent")
        grid.pack(fill="both", expand=True, pady=(0, UIStyle.PAD_SM))
        grid.columnconfigure(0, weight=1, uniform="sync")
        grid.columnconfigure(1, weight=1, uniform="sync")
        grid.rowconfigure(0, weight=0)
        grid.rowconfigure(1, weight=0)

        self.master_card = self._status_card(grid, "Master 方案商品", 0, 0, [("同步 Master", self._sync_master)])
        self.md_card = self._status_card(grid, "MD 文案", 0, 1, [("同步 MD", self._sync_md), ("打开所在文件夹", self._open_md_folder)])
        self.folder_card = self._status_card(grid, "素材文件夹", 1, 0, [], min_height=236)
        self.mapping_card = self._status_card(grid, "映射关系与缺口", 1, 1, [("查看全部缺口", self._show_all_gaps)], min_height=236)

        # Sync log (默认折叠，置于底部)
        self._log_expanded = False
        self._log_header = ctk.CTkButton(
            self.content,
            text="▶ 最近同步记录",
            font=UIStyle.FONT_H2,
            fg_color=UIStyle.COLOR_CARD_BG,
            text_color=UIStyle.COLOR_TEXT_MAIN,
            hover_color=UIStyle.COLOR_NAV_HOVER,
            anchor="w",
            corner_radius=UIStyle.RADIUS_LG,
            border_width=1,
            border_color=UIStyle.COLOR_BORDER,
            command=self._toggle_log,
        )
        self._log_header.pack(side="bottom", fill="x", pady=(0, UIStyle.PAD_SM))

        self._log_body = ctk.CTkFrame(self.content, fg_color=UIStyle.COLOR_CARD_BG, corner_radius=UIStyle.RADIUS_LG, border_width=1, border_color=UIStyle.COLOR_BORDER)
        self._log_body.grid_columnconfigure(0, weight=1)
        self._log_body.grid_rowconfigure(0, weight=1)
        self.log_tree = _build_table(self._log_body, ("时间", "类型", "状态", "说明"), row=0)
        self.log_tree.column("时间", width=160)
        self.log_tree.column("类型", width=110)
        self.log_tree.column("状态", width=80)
        self.log_tree.column("说明", width=500)

    def _toggle_log(self) -> None:
        self._log_expanded = not self._log_expanded
        if self._log_expanded:
            self._log_body.pack(side="bottom", fill="both", expand=True, pady=(0, UIStyle.PAD_SM))
            self._log_header.configure(text="▼ 最近同步记录")
        else:
            self._log_body.pack_forget()
            self._log_header.configure(text="▶ 最近同步记录")

    def _status_card(self, parent, title: str, row: int, col: int, buttons: list[tuple[str, Callable]], *, min_height: int | None = None) -> SyncStatusCard:
        card = SyncStatusCard(parent, title, buttons, min_height=min_height or (180 if row == 0 else 270))
        card.grid(row=row, column=col, sticky="nsew", padx=(0, UIStyle.PAD_MD) if col == 0 else (UIStyle.PAD_MD, 0), pady=(0, UIStyle.PAD_MD))
        return card

    def refresh(self) -> None:
        projects = self.repo.projects()
        self.app.sync_project_selectors()
        project = self.app.current_project()
        if not project and projects:
            self.app.current_project_id = int(projects[0]["id"])
            self.app.sync_project_selectors()
            project = projects[0]
        users = ["全部"] + [a["label"] for a in self.repo.accounts()]
        self.user_combo.configure(values=users)
        if self.user_var.get() not in users:
            self.user_var.set("全部")
        self._refresh_template_options()
        self._refresh_status()
        self._refresh_logs()

    def _on_user_changed(self) -> None:
        self._refresh_template_options()
        self.refresh()

    def _refresh_template_options(self) -> None:
        selected_user = self.user_var.get().strip()
        templates = available_templates(selected_user) if selected_user and selected_user != "全部" else []
        values = templates or ["全部"]
        self.template_combo.configure(values=values)
        if self.template_var.get() not in values:
            self.template_var.set(values[0])

    def _selected_image_template(self) -> str:
        value = self.template_var.get().strip()
        return "" if value == "全部" else value

    def _select_project(self, _=None) -> None:
        v = self.project_var.get()
        if not v:
            return
        project_id = self.app.project_id_for_selector_value(v)
        if project_id is not None:
            self.app.set_current_project(project_id)

    def _current_project_or_warn(self) -> dict[str, Any] | None:
        p = self.app.current_project()
        if not p:
            self.toast("请先选择品类项目。", kind="warning")
        return p

    def _refresh_status(self) -> None:
        project = self.app.current_project()
        if not project:
            for card in (self.master_card, self.md_card, self.folder_card, self.mapping_card):
                card.set_body("请先创建或选择品类项目。")
                card.set_asset_rows([])
                card.set_metrics([])
            return
        products = self.repo.products(project["id"], include_removed=False)
        blocks = self.repo.script_blocks(project["id"])
        assets = self.repo.asset_bindings(project["id"])
        intro_count = sum(1 for b in blocks if b["script_type"] == "intro")
        product_block_count = sum(1 for b in blocks if b["script_type"] == "product")
        price_count = sum(1 for b in blocks if b["script_type"] == "price_transition")
        selected_user = self.user_var.get().strip()
        selected_template = self._selected_image_template()
        image_template_suffix = image_set_for_template(selected_template)
        asset_counts = {
            "image": sum(1 for a in assets if a["asset_type"] == "image" and a["status"] == "ready"
                         and safe_text(a.get("path")) and Path(safe_text(a.get("path"))).is_file()
                         and (selected_user == "全部" or a["account_label"] == selected_user or (not image_template_suffix and not a["account_label"]))
                         and (not image_template_suffix or image_template_suffix in safe_text(a.get("path")))),
            "video": sum(1 for a in assets if a["asset_type"] == "video" and a["status"] == "ready"
                         and safe_text(a.get("path")) and Path(safe_text(a.get("path"))).is_file()),
            "voice": sum(1 for a in assets if a["asset_type"] == "voice" and a["status"] == "ready"
                         and safe_text(a.get("path")) and Path(safe_text(a.get("path"))).is_file()
                         and (selected_user == "全部" or a["account_label"] == selected_user or not a["account_label"])),
        }
        issues = build_project_issue_summary(project, products, blocks, assets, self.repo.accounts(), selected_user=self.user_var.get(), image_template=selected_template)
        voice_status = collect_voice_status(
            blocks,
            assets,
            self.repo.accounts(),
            {safe_text(item.get("uid")): item for item in products},
            selected_user=selected_user,
        )
        self.asset_paths = asset_folder_paths(project, assets, self.user_var.get(), selected_template)
        voice_inventory = voice_inventory_stats(
            blocks,
            assets,
            account_label=selected_user,
            directory=self.asset_paths.get("voice", ""),
        )
        voice_stat = (
            f"覆盖 {voice_status['ready']}/{voice_status['total']}；"
            f"有效文件 {voice_inventory['valid_files']}"
        )
        if voice_inventory["duplicate_files"]:
            voice_stat += f"（重复 {voice_inventory['duplicate_files']}）"
        voice_stat += f"；目录共 {voice_inventory['directory_files']}"
        self.master_card.set_asset_rows([])
        self.master_card.set_body(f"方案：{project['scheme_name'] or '--'}\n商品：{len(products)} 个")
        self.master_card.set_metrics([])
        self.md_card.set_asset_rows([])
        self.md_card.set_body(f"MD：{compact_path(project['md_path'], 58) or '--'}\n引言 {intro_count}，商品文案 {product_block_count}，价格过渡 {price_count}")
        self.md_card.set_metrics([])
        self.folder_card.set_body("")
        self.folder_card.set_asset_rows(
            [
                ("图片", self.asset_paths.get("image", ""), lambda: self._open_asset_path("image"), lambda: self._sync_asset_type("image"), None, asset_counts["image"]),
                ("视频", self.asset_paths.get("video", ""), lambda: self._open_asset_path("video"), lambda: self._sync_asset_type("video"), None, asset_counts["video"]),
                ("配音", self.asset_paths.get("voice", ""), lambda: self._open_asset_path("voice"), None, self._check_voice_status, voice_stat),
            ]
        )
        self.folder_card.set_metrics([])
        self.mapping_card.set_asset_rows([])
        template_label = selected_template or "全部"
        self.mapping_card.set_body(f"筛选用户：{self.user_var.get()}｜图片模板：{template_label}\n{format_issue_preview(issues, limit=3)}")
        self.mapping_card.set_metrics(
            [
                ("缺文案", len(issues["missing_copy"])),
                ("缺图片", len(issues["missing_image"])),
                ("缺视频", len(issues["missing_video"])),
                ("缺配音", len(issues["missing_voice"])),
                ("配音过期", len(issues["expired_voice"])),
            ],
            warn_labels={"缺文案", "缺图片", "缺视频", "缺配音", "配音过期"},
        )

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

    def _sync_master(self) -> None:
        project = self._current_project_or_warn()
        if not project:
            return
        self.app.run_background("预览 Master 变化",
            lambda: self.sync.sync_master_scheme(project["id"], apply_changes=False),
            on_success=lambda r: self._confirm_and_sync_master(project["id"], r),
            on_error=lambda exc, tb: self._handle_master_sync_error(project["id"], exc, tb),
            success_message="")

    def _handle_master_sync_error(self, project_id: int, exc: Exception, _tb: str) -> None:
        if not is_master_connection_error(exc):
            messagebox.showerror("预览 Master 变化失败", str(exc), parent=self)
            return
        sections = [
            DialogSection(
                title="Master 服务未启动",
                step="1",
                tone="warning",
                rows=[
                    ("接口地址", self.app.master_service.api_base_url),
                    ("服务项目", str(self.app.master_service.service_root)),
                ],
                items=["当前无法连接 Master 方案接口。", "确认后会启动本地 Master 后端服务，并自动重试本次同步。"],
            )
        ]
        if not show_confirmation_dialog(
            self,
            "Master 接口不可用",
            "同步 Master 需要本地 Master 后端服务。",
            sections,
            confirm_text="启动服务并重试",
        ):
            self.toast("已取消同步 Master。", kind="warning")
            return

        def work():
            self.app.master_service.ensure_running()
            return self.sync.sync_master_scheme(project_id, apply_changes=False)

        self.app.run_background(
            "启动 Master 服务",
            work,
            on_success=lambda r: self._confirm_and_sync_master(project_id, r),
            on_error=lambda retry_exc, _retry_tb: messagebox.showerror("Master 服务启动失败", str(retry_exc), parent=self),
            success_message="",
            show_success_toast=False,
        )

    def _confirm_and_sync_master(self, pid: int, preview: dict[str, Any]) -> None:
        sections = [
            DialogSection(
                title="变更统计",
                step="1",
                tone="primary",
                rows=[
                    ("新增", f"{len(preview['added'])} 个"),
                    ("更新", f"{len(preview['updated'])} 个"),
                    ("移除", f"{len(preview['removed'])} 个"),
                ],
            ),
            DialogSection(title="新增项目", step="2", tone="success", items=preview_lines([f"{item.get('uid', '')} {item.get('title', '')} {item.get('price_label', '')}".strip() for item in preview.get("added") or []])),
            DialogSection(title="更新项目", step="3", tone="info", items=preview_lines([f"{item.get('uid', '')} {item.get('title', '')} {item.get('price_label', '')}".strip() for item in preview.get("updated") or []])),
            DialogSection(title="移除项目", step="4", tone="warning", items=preview_lines([f"{item.get('uid', '')} {item.get('title', '')} {item.get('price_label', '')}".strip() for item in preview.get("removed") or []])),
        ]
        if not show_confirmation_dialog(self, "确认同步 Master", "请核对本次 Master 同步变更，确认无误后再继续。", sections, confirm_text="确认同步"):
            return
        self.app.run_background("同步 Master",
            lambda: self.sync.sync_master_scheme(pid, apply_changes=True),
            on_success=lambda r: (self.toast(f"Master 已同步：新增 {len(r['added'])}，更新 {len(r['updated'])}，移除 {len(r['removed'])}"), self.refresh()),
            show_success_toast=False)

    def _sync_md(self) -> None:
        project = self._current_project_or_warn()
        if not project:
            return
        from .md_parser import parse_markdown_file
        try:
            md_path = safe_text(project.get("md_path"))
            if not md_path or not Path(md_path).exists():
                self.toast("当前项目没有绑定可读取的 MD 文档。", kind="warning")
                return
            parsed = parse_markdown_file(md_path)
            products = self.repo.products(project["id"], include_removed=False)
            blocks = self.repo.script_blocks(project["id"])
            md_uids = {item.uid for item in parsed.products}
            matched = sum(1 for p in products if p["uid"] in md_uids)
            missing = len(products) - matched
            extra_md = [item for item in parsed.products if item.uid not in {p["uid"] for p in products}]

            # 计算文案块变化
            existing_keys = {(b["script_type"], b["owner_uid"], b["price_range_label"], b["block_label"]): b for b in blocks}
            md_added, md_updated, md_same = [], [], []
            for p in parsed.products:
                uid = p.uid
                for script in p.scripts:
                    label = script.label or "正文"
                    key = ("product", uid, "", label)
                    old = existing_keys.get(key)
                    if old is None:
                        md_added.append(f"{uid} / {label}")
                    elif old["text_hash"] != text_hash(script.body):
                        md_updated.append(f"{uid} / {label}")
                    else:
                        md_same.append(True)
            for script in parsed.intro_scripts:
                label = script.label or "引言"
                key = ("intro", "", "", label)
                old = existing_keys.get(key)
                if old is None:
                    md_added.append(f"引言 {label}")
                elif old["text_hash"] != text_hash(script.body):
                    md_updated.append(f"引言 {label}")
            for pt in parsed.price_transitions:
                for script in pt.scripts:
                    label = script.label or "正文"
                    key = ("price_transition", "", pt.label, label)
                    old = existing_keys.get(key)
                    if old is None:
                        md_added.append(f"价格过渡 {pt.label} / {label}")
                    elif old["text_hash"] != text_hash(script.body):
                        md_updated.append(f"价格过渡 {pt.label} / {label}")

            sections = [
                DialogSection(
                    title="MD 解析结果",
                    step="1",
                    tone="primary",
                    rows=[
                        ("引言文案", f"{len(parsed.intro_scripts)} 段"),
                        ("商品文案", f"{len(parsed.products)} 个"),
                        ("已匹配商品文案", f"{matched} 个"),
                        ("缺文案商品", f"{missing} 个"),
                        ("MD 额外商品", f"{len(extra_md)} 个（在 MD 中但不在当前项目商品列表）"),
                    ],
                    helper="确认后会将 MD 中的文案块同步入库。",
                ),
            ]
            if md_added:
                sections.append(DialogSection(title="新增文案块", step="2", tone="success", items=md_added[:20]))
            if md_updated:
                sections.append(DialogSection(title="变更文案块", step="3", tone="info", items=md_updated[:20]))
            if not show_confirmation_dialog(self, "确认同步 MD", "请核对本次 MD 变化内容，确认无误后再继续。", sections, confirm_text="确认同步"):
                return
        except Exception as e:
            sections = [
                DialogSection(
                    title="解析异常",
                    step="1",
                    tone="warning",
                    items=[str(e)],
                    helper="如果继续，系统仍会尝试执行同步。",
                )
            ]
            if not show_confirmation_dialog(self, "MD 解析异常", "解析 MD 时遇到异常。你仍然可以尝试继续同步。", sections, confirm_text="仍然同步"):
                return
        self.app.run_background("同步 MD",
            lambda: self.sync.sync_markdown(project["id"]),
            on_success=lambda r: (self.toast(f"MD 已同步：入库 {r['upserted']} 条，缺文案 {len(r['missing_copy'])} 个"), self.refresh()),
            show_success_toast=False)

    def _sync_assets(self) -> None:
        project = self._current_project_or_warn()
        if not project:
            return
        def task():
            img = self.sync.sync_assets(project["id"], asset_type="image")
            vid = self.sync.sync_assets(project["id"], asset_type="video")
            merged = {"image": img["image"], "video": vid["video"], "unmatched": img["unmatched"] + vid["unmatched"], "voice": 0}
            for key in ("matched_items", "added_items", "removed_items", "current_items", "unmatched_items"):
                merged[key] = (img.get(key) or []) + (vid.get(key) or [])
            merged["scanned_roots"] = {**(img.get("scanned_roots") or {}), **(vid.get("scanned_roots") or {})}
            return merged
        self.app.run_background("扫描素材", task,
                                on_success=lambda r: self._finish_asset_sync("全部", r),
                                show_success_toast=False)

    def _sync_asset_type(self, asset_type: str) -> None:
        project = self._current_project_or_warn()
        if not project:
            return
        labels = {"image": "图片", "video": "视频", "voice": "配音"}
        path = self.asset_paths.get(asset_type) or safe_text(project.get(f"{asset_type}_root"))
        label = labels.get(asset_type, "素材")
        selected_user = self.user_var.get().strip()
        selected_template = self._selected_image_template() if asset_type == "image" else ""
        path_filter = image_set_for_template(selected_template)
        self.app.run_background(
            f"同步{label}素材",
            lambda: self.sync.sync_assets(project["id"], asset_type=asset_type, root_override=path),
            on_success=lambda r: self._finish_asset_sync(label, r, focus_type=asset_type, account_filter=selected_user, path_filter=path_filter),
            show_success_toast=False,
        )

    def _check_voice_status(self) -> None:
        project = self._current_project_or_warn()
        if not project:
            return
        account_label = self.user_var.get().strip()
        if not account_label or account_label == "全部":
            account_label = "小燃"
        try:
            self.sync.sync_markdown(project["id"])
            voice_root = self.asset_paths.get("voice") or safe_text(project.get("voice_root"))
            voice_sync_result = self.sync.sync_assets(project["id"], asset_type="voice", root_override=voice_root or None)
        except Exception as exc:
            messagebox.showerror("配音检查失败", f"配音检查前同步当前 MD 或配音目录失败：{exc}")
            return
        project = self.repo.project(project["id"]) or project
        blocks = self.repo.script_blocks(project["id"])
        assets = self.repo.asset_bindings(project["id"])
        products = {safe_text(item.get("uid")): item for item in self.repo.products(project["id"], include_removed=False)}
        voice_status = collect_voice_status(blocks, assets, self.repo.accounts(), products, selected_user=account_label)
        missing_voice, removed_file_voice = split_missing_voice_rows_by_removed_assets(
            voice_status["missing"],
            voice_sync_result.get("removed_items") or [],
        )
        missing_file_voice = (voice_status.get("missing_file") or []) + removed_file_voice
        expired_voice = voice_status["expired"]
        ready_count = voice_status["total"] - len(missing_voice) - len(missing_file_voice) - len(expired_voice)
        voice_inventory = voice_inventory_stats(
            blocks,
            assets,
            account_label=account_label,
            directory=voice_root or "",
        )
        has_inventory_warning = bool(voice_inventory["duplicate_files"] or voice_inventory["untracked_files"])
        self._refresh_status()
        sections = [
            DialogSection(
                title="配音检查结果",
                step="1",
                tone="warning" if (missing_voice or missing_file_voice or expired_voice or has_inventory_warning) else "success",
                rows=[
                    ("筛选用户", account_label),
                    ("配音块总数", str(voice_status["total"])),
                    ("已就绪", str(ready_count)),
                    ("文件丢失", str(len(missing_file_voice))),
                    ("缺配音", str(len(missing_voice))),
                    ("文案过期", str(len(expired_voice))),
                    ("有效音频文件", str(voice_inventory["valid_files"])),
                    ("其中重复文件", str(voice_inventory["duplicate_files"])),
                    ("目录音频文件", str(voice_inventory["directory_files"])),
                    ("未采用或旧文件", str(voice_inventory["untracked_files"])),
                ],
                helper="检查结果按活动文案块判断是否齐全；一个文案块存在多份音频时仍只计为一个已就绪块。",
            )
        ]
        step_index = 2
        if missing_file_voice:
            sections.append(
                DialogSection(
                    title="文件丢失列表",
                    step=str(step_index),
                    tone="warning",
                    items=[item["display"] for item in missing_file_voice],
                    helper="这些文案块原本有配音记录，但对应 wav/mp3 文件已不存在，已同步为失效记录。",
                )
            )
            step_index += 1
        if missing_voice:
            sections.append(
                DialogSection(
                    title="缺配音列表",
                    step=str(step_index),
                    tone="warning",
                    items=[item["display"] for item in missing_voice],
                    helper="这些文案块没有找到当前用户可用的配音文件。",
                )
            )
            step_index += 1
        if expired_voice:
            sections.append(
                DialogSection(
                    title="文案过期列表",
                    step=str(step_index),
                    tone="warning",
                    items=[item["display"] for item in expired_voice],
                    helper="这些文案块已有配音文件，但文本 hash 已不一致，需要重新生成。",
                )
            )
        if not missing_voice and not missing_file_voice and not expired_voice:
            if has_inventory_warning:
                sections[0].helper = (
                    "当前文案块已全部覆盖，但目录中仍有重复有效文件或未采用的旧文件；"
                    "它们不影响配音完整性，但不应显示为完全无异常。"
                )
            else:
                sections[0].helper = "当前用户没有缺配音、过期配音、重复文件或未采用的旧文件。"
        action = show_action_sections_dialog(
            self,
            "配音检查结果",
            "按文案块核对当前用户的配音状态。",
            sections,
            action_text="立即配音",
            action_enabled=bool(missing_voice or missing_file_voice or expired_voice),
            secondary_action_text="手动映射音频",
            secondary_action_enabled=bool(missing_voice or missing_file_voice or expired_voice),
            close_text="关闭",
        )
        if action == "action":
            self._open_voice_generation_for_missing(project["id"], account_label, missing_file_voice + missing_voice + expired_voice)
        elif action == "secondary":
            self._open_manual_voice_binding_dialog(project["id"], account_label, missing_file_voice + missing_voice + expired_voice)

    def _open_voice_generation_for_missing(self, project_id: int, account_label: str, missing_voice: list[dict[str, str]]) -> None:
        targets = voice_generation_targets_from_rows(missing_voice)
        if not targets:
            self.toast("没有可自动填充的缺配音目标。", kind="warning")
            return
        self.app.set_current_project(project_id)
        self.app.show_page("生成配音")
        page = self.app.pages.get("生成配音")
        if not isinstance(page, VoicePage):
            self.toast("无法打开生成配音页面。", kind="error")
            return
        page.account_var.set(account_label)
        page.uid_var.set("，".join(targets))
        page.extra_voice_tasks.clear()
        page._render_voice_task_list()
        page._update_voice_output_dir(force=True)
        page.log(f"已从配音检查填入缺配音目标：{'，'.join(targets)}")
        self.toast(f"已填入 {len(targets)} 个缺配音目标")

    def _open_manual_voice_binding_dialog(self, project_id: int, account_label: str, rows: list[dict[str, str]]) -> None:
        if not rows:
            self.toast("当前没有可手动映射的配音缺口。", kind="warning")
            return
        choice_rows = [row for row in rows if safe_text(row.get("script_block_id"))]
        if not choice_rows:
            self.toast("缺口里没有可定位的文案块，请先同步 MD。", kind="warning")
            return
        dialog = ctk.CTkToplevel(self)
        dialog.title("手动映射配音")
        dialog.geometry("860x360")
        dialog.minsize(760, 320)
        dialog.transient(self.winfo_toplevel())
        dialog.grab_set()
        dialog.columnconfigure(1, weight=1)
        ctk.CTkLabel(dialog, text="选择文案块", font=UIStyle.FONT_BODY, text_color=UIStyle.COLOR_TEXT_DIM).grid(
            row=0, column=0, sticky="w", padx=UIStyle.PAD_XL, pady=(UIStyle.PAD_XL, UIStyle.PAD_SM)
        )
        choices = [voice_row_choice_label(row) for row in choice_rows]
        choice_var = ctk.StringVar(value=choices[0])
        combo = AppComboBox(dialog, variable=choice_var, values=choices, width=620)
        combo.grid(row=0, column=1, sticky="ew", padx=(0, UIStyle.PAD_XL), pady=(UIStyle.PAD_XL, UIStyle.PAD_SM))
        ctk.CTkLabel(dialog, text="本地音频", font=UIStyle.FONT_BODY, text_color=UIStyle.COLOR_TEXT_DIM).grid(
            row=1, column=0, sticky="w", padx=UIStyle.PAD_XL, pady=UIStyle.PAD_SM
        )
        path_var = ctk.StringVar(value="")
        path_entry = AppEntry(dialog, textvariable=path_var)
        path_entry.grid(row=1, column=1, sticky="ew", padx=(0, UIStyle.PAD_XL), pady=UIStyle.PAD_SM)

        def browse_audio() -> None:
            initial = self.asset_paths.get("voice") or safe_text((self.app.current_project() or {}).get("voice_root")) or str(DEFAULT_VOICE_ROOT)
            selected = filedialog.askopenfilename(
                parent=dialog,
                title="选择要映射的本地配音文件",
                initialdir=initial,
                filetypes=[("Audio", "*.wav *.mp3 *.m4a *.aac *.flac *.ogg"), ("All", "*.*")],
            )
            if selected:
                path_var.set(selected)

        def bind_selected() -> None:
            selected_path = safe_text(path_var.get())
            if not selected_path:
                messagebox.showwarning("缺少音频文件", "请先选择一个本地配音文件。", parent=dialog)
                return
            index = choices.index(choice_var.get()) if choice_var.get() in choices else 0
            row = choice_rows[index]
            try:
                result = self.sync.manual_bind_voice_asset(
                    project_id,
                    script_block_id=int(row["script_block_id"]),
                    account_label=account_label,
                    path=selected_path,
                )
            except Exception as exc:
                messagebox.showerror("手动映射失败", str(exc), parent=dialog)
                return
            dialog.destroy()
            self.toast(f"已手动映射配音：{result['title']}")
            self.refresh()

        GhostButton(dialog, text="选择文件", command=browse_audio, width=100).grid(row=2, column=1, sticky="w", padx=(0, UIStyle.PAD_XL), pady=(UIStyle.PAD_SM, UIStyle.PAD_MD))
        helper = "会把所选音频直接绑定到当前文案块，并用当前文案 hash 标记为已就绪。"
        ctk.CTkLabel(dialog, text=helper, font=UIStyle.FONT_SMALL, text_color=UIStyle.COLOR_TEXT_DIM, anchor="w").grid(
            row=3, column=0, columnspan=2, sticky="ew", padx=UIStyle.PAD_XL, pady=(0, UIStyle.PAD_MD)
        )
        buttons = ctk.CTkFrame(dialog, fg_color="transparent")
        buttons.grid(row=4, column=0, columnspan=2, sticky="ew", padx=UIStyle.PAD_XL, pady=(0, UIStyle.PAD_XL))
        buttons.columnconfigure(0, weight=1)
        GhostButton(buttons, text="取消", command=dialog.destroy, width=100).grid(row=0, column=1, padx=(0, UIStyle.PAD_SM))
        PrimaryButton(buttons, text="确认映射", command=bind_selected, width=120).grid(row=0, column=2)
        _center_dialog(dialog)
        dialog.wait_window()

    def _show_all_gaps(self) -> None:
        project = self._current_project_or_warn()
        if not project:
            return
        products = self.repo.products(project["id"], include_removed=False)
        blocks = self.repo.script_blocks(project["id"])
        assets = self.repo.asset_bindings(project["id"])
        selected_user = self.user_var.get().strip()
        selected_template = self._selected_image_template()
        issues = build_project_gap_details(
            project,
            products,
            blocks,
            assets,
            self.repo.accounts(),
            selected_user=selected_user,
            image_template=selected_template,
        )
        labels = [
            ("missing_copy", "缺文案"),
            ("missing_image", "缺图片"),
            ("missing_video", "缺视频"),
            ("missing_voice", "缺配音"),
            ("expired_voice", "配音过期"),
        ]
        sections = [
            DialogSection(
                title="缺口汇总",
                step="1",
                tone="warning" if any(issues.get(key) for key, _label in labels) else "success",
                rows=[
                    ("项目", safe_text(project.get("name"))),
                    ("筛选用户", selected_user or "全部"),
                    ("缺文案", str(len(issues.get("missing_copy") or []))),
                    ("缺图片", str(len(issues.get("missing_image") or []))),
                    ("缺视频", str(len(issues.get("missing_video") or []))),
                    ("缺配音", str(len(issues.get("missing_voice") or []))),
                    ("配音过期", str(len(issues.get("expired_voice") or []))),
                ],
            )
        ]
        step = 2
        for key, label in labels:
            items = issues.get(key) or []
            if not items:
                continue
            target = {
                "missing_copy": "商品文案 MD",
                "missing_image": "图片目录",
                "missing_video": "视频目录",
                "missing_voice": "配音目录",
                "expired_voice": "重新生成配音",
            }.get(key, "")
            sections.append(
                DialogSection(
                    title=label,
                    step=str(step),
                    tone="warning",
                    items=items,
                    helper=f"补齐位置：{target}" if target else "",
                )
            )
            step += 1
        if step == 2:
            sections[0].helper = "当前项目和筛选用户下没有明显素材与文案缺口。"
        show_precheck_dialog(
            self,
            "全部缺口明细",
            "完整列出当前项目和筛选用户下的所有素材与文案缺口。",
            sections,
            can_continue=False,
            confirm_text="关闭",
            dismiss_text="关闭",
        )

    def _finish_asset_sync(self, label: str, result: dict[str, Any], *, focus_type: str = "", account_filter: str = "", path_filter: str = "") -> None:
        if focus_type:
            count_text = f"{label} {result.get(focus_type, 0)}"
        else:
            count_text = f"图片 {result.get('image', 0)}，视频 {result.get('video', 0)}，配音 {result.get('voice', 0)}"
        self.toast(f"{label}素材同步完成：{count_text}，缺素材 {result.get('unmatched', 0)}")
        self.refresh()
        self._show_asset_sync_result(label, result, focus_type=focus_type, account_filter=account_filter, path_filter=path_filter)

    def _show_asset_sync_result(self, label: str, result: dict[str, Any], *, focus_type: str = "", account_filter: str = "", path_filter: str = "") -> None:
        type_labels = {"image": "图片", "video": "视频", "voice": "配音"}
        matched_items = result.get("matched_items") or []
        added_items = result["added_items"] if "added_items" in result else matched_items
        removed_items = result["removed_items"] if "removed_items" in result else []
        current_items = result["current_items"] if "current_items" in result else matched_items
        unmatched_items = result.get("unmatched_items") or []
        if focus_type:
            matched_items = [item for item in matched_items if item.get("asset_type") == focus_type]
            added_items = [item for item in added_items if item.get("asset_type") == focus_type]
            removed_items = [item for item in removed_items if item.get("asset_type") == focus_type]
            current_items = [item for item in current_items if item.get("asset_type") == focus_type]
            unmatched_items = [item for item in unmatched_items if item.get("asset_type") == focus_type]
        if account_filter and account_filter != "全部":
            current_items = [item for item in current_items if safe_text(item.get("account_label")) == account_filter or not item.get("account_label")]
        if path_filter:
            current_items = [item for item in current_items if path_filter in safe_text(item.get("path"))]

        def item_line(item: dict[str, Any], prefix: str = "") -> str:
            uid = safe_text(item.get("uid"))
            title = safe_text(item.get("title"))
            acct = safe_text(item.get("account_label")) or "全局"
            block = safe_text(item.get("block_label"))
            atype = type_labels.get(item.get("asset_type"), item.get("asset_type"))
            middle = " ".join(part for part in [uid, title] if part).strip()
            suffix = f" / {acct}" + (f" / {block}" if block else "")
            return f"{prefix}[{atype}] {middle}{suffix}".strip()

        changed_lines = [item_line(item, "+ ") for item in added_items]
        changed_lines.extend(item_line(item, "- ") for item in removed_items)

        sections = [
            DialogSection(
                title="扫描结果",
                step="1",
                tone="success" if not unmatched_items else "warning",
                rows=[
                    ("匹配成功", f"图片 {result.get('image', 0)}，视频 {result.get('video', 0)}，配音 {result.get('voice', 0)}"),
                    ("扫描目录", "; ".join(safe_text(p) for p in (result.get("scanned_roots") or {}).values())),
                    ("本次新增", str(len(added_items))),
                    ("本次减少", str(len(removed_items))),
                    ("当前总览", str(len(current_items))),
                    ("缺素材商品", str(len(unmatched_items))),
                ],
            ),
            DialogSection(
                title="新匹配的素材",
                step="2",
                tone="success" if changed_lines else "info",
                items=preview_lines(changed_lines, limit=40) if changed_lines else [],
                helper="这里只显示本次同步相比同步前新增或减少的素材；本次无变化则不展开正常素材。",
            ),
        ]
        if unmatched_items:
            sections.append(
                DialogSection(
                    title="缺素材商品",
                    step="4",
                    tone="warning",
                    items=preview_lines([item_line(item) for item in unmatched_items], limit=40),
                    helper="以下商品的该类型素材尚未找到，可以到对应文件夹下检查文件是否存在。",
                ),
            )

        dialog = ctk.CTkToplevel(self)
        dialog.title(f"{label}素材同步结果")
        dialog.geometry("1080x720")
        dialog.minsize(900, 620)
        dialog.transient(self.winfo_toplevel())
        dialog.grab_set()
        dialog.rowconfigure(0, weight=1)
        dialog.columnconfigure(0, weight=1)

        body = ctk.CTkScrollableFrame(dialog, fg_color="transparent")
        body.grid(row=0, column=0, sticky="nsew", padx=UIStyle.PAD_XL, pady=(UIStyle.PAD_XL, UIStyle.PAD_MD))
        for section in sections:
            card = _build_dialog_section(body, section)
            card.pack(fill="x", pady=(0, UIStyle.PAD_MD))

        buttons = ctk.CTkFrame(dialog, fg_color="transparent")
        buttons.grid(row=1, column=0, sticky="ew", padx=UIStyle.PAD_XL, pady=(0, UIStyle.PAD_XL))
        buttons.columnconfigure(0, weight=1)
        GhostButton(buttons, text="关闭", command=dialog.destroy).grid(row=0, column=1)
        _center_dialog(dialog)
        dialog.lift()
        dialog.focus_set()

    def _sync_all(self) -> None:
        project = self._current_project_or_warn()
        if not project:
            return
        if not show_confirmation_dialog(
            self,
            "确认一键同步当前品类",
            "一键同步会更新当前项目的商品、文案和素材状态，请确认后继续。",
            [
                DialogSection(
                    title="执行步骤",
                    step="1",
                    tone="primary",
                    items=[
                        "从 Master 方案刷新当前品类商品列表。",
                        "读取绑定的 MD 文案并更新文案块。",
                        "扫描图片和视频素材并刷新映射。",
                    ],
                )
            ],
            confirm_text="确认同步",
        ):
            return
        def sync_all_task():
            self.sync.sync_master_scheme(project["id"], apply_changes=True)
            self.sync.sync_markdown(project["id"])
            image_path = self.asset_paths.get("image") or safe_text(project.get("image_root"))
            self.sync.sync_assets(project["id"], asset_type="image", root_override=image_path)
            self.sync.sync_assets(project["id"], asset_type="video")
            return {}
        self.app.run_background("一键同步", sync_all_task,
                                on_success=lambda r: (self.toast(f"一键同步完成", duration=4500), self.refresh()), show_success_toast=False)

    def _open_path(self, key: str) -> None:
        p = self._current_project_or_warn()
        if p:
            open_path(p.get(key))

    def _open_asset_path(self, asset_type: str) -> None:
        path = self.asset_paths.get(asset_type)
        if path:
            open_path(path)
            return
        p = self._current_project_or_warn()
        if p:
            root_key = {"image": "image_root", "video": "video_root", "voice": "voice_root"}.get(asset_type, "")
            open_path(p.get(root_key))

    def _open_md_folder(self) -> None:
        p = self._current_project_or_warn()
        if p and p.get("md_path"):
            open_path(Path(p["md_path"]).parent)
