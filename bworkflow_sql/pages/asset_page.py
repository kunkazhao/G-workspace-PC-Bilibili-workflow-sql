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


class AssetPage(BasePage):
    def __init__(self, master, app: App):
        super().__init__(master, "资产中心", app)
        self.category_var = ctk.StringVar(value="全部")
        self.status_var = ctk.StringVar(value="全部")
        self._default_user_selection_applied = False
        self._default_category_applied = False
        self._refreshing_user_list = False
        self.user_vars: dict[str, ctk.BooleanVar] = {}
        self.user_checks: dict[str, ctk.CTkCheckBox] = {}
        self.stat_value_labels: dict[str, ctk.CTkLabel] = {}
        self.stat_hint_labels: dict[str, ctk.CTkLabel] = {}

        filters = ctk.CTkFrame(
            self.content,
            fg_color=UIStyle.COLOR_CARD_BG,
            corner_radius=UIStyle.RADIUS_LG,
            border_width=1,
            border_color=UIStyle.COLOR_BORDER,
        )
        filters.pack(fill="x", pady=(0, UIStyle.PAD_MD))
        filters.grid_columnconfigure(1, weight=0)
        filters.grid_columnconfigure(3, weight=0)

        ctk.CTkLabel(filters, text="用户", font=UIStyle.FONT_BODY, text_color=UIStyle.COLOR_TEXT_DIM).grid(
            row=0, column=0, sticky="w", padx=(UIStyle.PAD_LG, UIStyle.PAD_SM), pady=(UIStyle.PAD_LG, UIStyle.PAD_XS)
        )
        self.user_checks_frame = ctk.CTkFrame(filters, fg_color="transparent")
        self.user_checks_frame.grid(row=0, column=1, columnspan=3, sticky="w", padx=(0, UIStyle.PAD_LG), pady=(UIStyle.PAD_LG, UIStyle.PAD_SM))

        ctk.CTkLabel(filters, text="品类", font=UIStyle.FONT_BODY, text_color=UIStyle.COLOR_TEXT_DIM).grid(
            row=1, column=0, sticky="w", padx=(UIStyle.PAD_LG, UIStyle.PAD_SM), pady=(0, UIStyle.PAD_LG)
        )
        self.category_combo = AppComboBox(filters, width=180, variable=self.category_var)
        self.category_combo.grid(row=1, column=1, sticky="w", padx=(0, UIStyle.PAD_LG), pady=(0, UIStyle.PAD_LG))
        self.category_combo.configure(command=lambda _=None: self.refresh())

        ctk.CTkLabel(filters, text="筛选", font=UIStyle.FONT_BODY, text_color=UIStyle.COLOR_TEXT_DIM).grid(
            row=1, column=2, sticky="w", padx=(0, UIStyle.PAD_SM), pady=(0, UIStyle.PAD_LG)
        )
        self.status_combo = AppComboBox(filters, width=160, variable=self.status_var, values=["全部", "缺文案", "缺图片", "缺视频", "缺配音", "配音过期"])
        self.status_combo.grid(row=1, column=3, sticky="w", padx=(0, UIStyle.PAD_LG), pady=(0, UIStyle.PAD_LG))
        self.status_combo.configure(command=lambda _=None: self.refresh())

        stats = ctk.CTkFrame(
            self.content,
            fg_color=UIStyle.COLOR_CARD_BG,
            corner_radius=UIStyle.RADIUS_LG,
            border_width=1,
            border_color=UIStyle.COLOR_BORDER,
        )
        stats.pack(fill="x", pady=(0, UIStyle.PAD_MD))
        for column in range(5):
            stats.grid_columnconfigure(column, weight=1)
        stat_specs = [
            ("copy", "文案", UIStyle.COLOR_INFO),
            ("image", "图片", UIStyle.COLOR_SUCCESS),
            ("video", "视频", UIStyle.COLOR_ASSET_VIDEO),
            ("voice", "配音", UIStyle.COLOR_WARNING),
            ("issue", "问题", UIStyle.COLOR_ERROR),
        ]
        for column, (key, title, accent) in enumerate(stat_specs):
            card = ctk.CTkFrame(stats, fg_color=UIStyle.COLOR_SURFACE_SOFT, corner_radius=UIStyle.RADIUS_MD)
            card.grid(row=0, column=column, sticky="ew", padx=(UIStyle.PAD_LG if column == 0 else 0, UIStyle.PAD_LG), pady=UIStyle.PAD_LG)
            bar = ctk.CTkFrame(card, fg_color=accent, height=4, corner_radius=999)
            bar.pack(fill="x", padx=UIStyle.PAD_LG, pady=(UIStyle.PAD_MD, UIStyle.PAD_SM))
            bar.pack_propagate(False)
            ctk.CTkLabel(card, text=title, font=UIStyle.FONT_SMALL, text_color=UIStyle.COLOR_TEXT_DIM).pack(anchor="center")
            value = ctk.CTkLabel(card, text="0", font=UIStyle.FONT_STAT, text_color=accent)
            value.pack(anchor="center", pady=(2, UIStyle.PAD_MD))
            self.stat_value_labels[key] = value
            self.stat_hint_labels[key] = ctk.CTkLabel(card, text="", font=UIStyle.FONT_SMALL, text_color=UIStyle.COLOR_TEXT_DIM)

        outer = ctk.CTkFrame(
            self.content,
            fg_color=UIStyle.COLOR_CARD_BG,
            corner_radius=UIStyle.RADIUS_LG,
            border_width=1,
            border_color=UIStyle.COLOR_BORDER,
        )
        outer.pack(fill="both", expand=True)
        outer.grid_columnconfigure(0, weight=1)
        outer.grid_rowconfigure(0, weight=1)

        self.tree = _build_table(outer, AssetPageColumns, row=0)
        self._configure_asset_tree()
        self.empty_state = ctk.CTkLabel(
            outer, text="", font=UIStyle.FONT_BODY,
            text_color=UIStyle.COLOR_TEXT_DIM, justify="center", wraplength=480,
        )

    def refresh(self) -> None:
        projects = self.repo.projects()
        if not projects:
            _set_tree_rows(self.tree, [])
            self.empty_state.configure(text="还没有品类项目。先到「品类项目」新建或选择一个，资产清单会自动出现。")
            self.empty_state.place(relx=0.5, rely=0.5, anchor="center")
            self.empty_state.lift()
            return
        cats = ["全部"] + sorted({p["category_name"] for p in projects if p["category_name"]})
        self.category_combo.configure(values=cats)
        if not self._default_category_applied:
            self.category_var.set("键盘" if "键盘" in cats else cats[0])
            self._default_category_applied = True
        elif self.category_var.get() not in cats:
            self.category_var.set("键盘" if "键盘" in cats else cats[0])
        if self.status_var.get() not in ["全部", "缺文案", "缺图片", "缺视频", "缺配音", "配音过期"]:
            self.status_var.set("全部")
        self._refresh_user_choices()

        selected_cat = self.category_var.get()
        selected_users = self._selected_users()
        rows: list[tuple[Any, ...]] = []
        summary = {"copy": 0, "image_paths": set(), "video_paths": set(), "voice_paths": set(), "issue": 0}
        for proj in projects:
            if selected_cat != "全部" and proj["category_name"] != selected_cat:
                continue
            project_rows, project_summary = self._rows_for_project(proj, selected_users=selected_users)
            rows.extend(project_rows)
            summary["copy"] += project_summary["copy"]
            summary["image_paths"].update(project_summary["image_paths"])
            summary["video_paths"].update(project_summary["video_paths"])
            summary["voice_paths"].update(project_summary["voice_paths"])
            summary["issue"] += project_summary["issue"]
        rows = [row for row in rows if self._row_matches_filter(row)]
        self._update_stat_cards(summary, rows)

        self.tree.delete(*self.tree.get_children())
        if rows:
            self.empty_state.place_forget()
        else:
            self.empty_state.configure(text="当前用户 / 品类 / 筛选条件下没有匹配的数据，换个筛选条件试试。")
            self.empty_state.place(relx=0.5, rely=0.5, anchor="center")
            self.empty_state.lift()
        for index, row in enumerate(rows):
            issue_text = str(row[-1] or "").strip()
            parity = "odd" if index % 2 else "even"
            tags = [parity]
            if issue_text and issue_text != "—":
                tags.append(f"{parity}_issue")
            self.tree.insert("", "end", values=row, tags=tuple(tags))

    def _refresh_user_choices(self) -> None:
        current = self._selected_users()
        labels = [item["label"] for item in self.repo.accounts()]
        if not self._default_user_selection_applied and not current:
            defaults = {"小歪", "小燃", "小然"}
            current = [label for label in labels if label in defaults]
            self._default_user_selection_applied = True

        self._refreshing_user_list = True
        try:
            for widget in self.user_checks.values():
                widget.destroy()
            self.user_checks.clear()
            old_vars = self.user_vars
            self.user_vars = {}
            for index, label in enumerate(labels):
                var = old_vars.get(label) or ctk.BooleanVar(value=label in current)
                self.user_vars[label] = var
                check = ctk.CTkCheckBox(
                    self.user_checks_frame,
                    text=label,
                    variable=var,
                    checkbox_width=16,
                    checkbox_height=16,
                    corner_radius=4,
                    border_width=1,
                    fg_color=UIStyle.COLOR_PRIMARY,
                    hover_color=UIStyle.COLOR_PRIMARY_HOVER,
                    border_color=UIStyle.COLOR_BORDER,
                    text_color=UIStyle.COLOR_TEXT_MAIN,
                    font=UIStyle.FONT_BODY,
                    command=self.refresh,
                )
                check.grid(row=0, column=index, sticky="w", padx=(0, UIStyle.PAD_MD), pady=0)
                self.user_checks[label] = check
        finally:
            self._refreshing_user_list = False

    def _rows_for_project(self, project: dict[str, Any], *, selected_users: list[str]) -> tuple[list[tuple[Any, ...]], dict[str, Any]]:
        blocks = self.repo.script_blocks(project["id"])
        assets = self.repo.asset_bindings(project["id"])
        products = self.repo.products(project["id"], include_removed=False)
        accounts = self.repo.accounts()
        if selected_users:
            accounts = [account for account in accounts if account["label"] in selected_users]
        if not accounts:
            return [], {"copy": 0, "image_paths": set(), "video_paths": set(), "voice_paths": set(), "issue": 0}

        rows: list[tuple[Any, ...]] = []
        summary = {"copy": 0, "image_paths": set(), "video_paths": set(), "voice_paths": set(), "issue": 0}
        for account in accounts:
            detail_rows, issue_count = self._script_block_rows(project, account, products, blocks, assets)
            rows.extend(detail_rows)
            summary["copy"] += len(detail_rows)
            summary["issue"] += issue_count
            summary["image_paths"].update(
                safe_text(asset.get("path"))
                for asset in assets
                if asset["asset_type"] == "image"
                and asset["status"] == "ready"
                and safe_text(asset.get("account_label")) == account["label"]
                and safe_text(asset.get("path"))
            )
            summary["voice_paths"].update(
                safe_text(asset.get("path"))
                for asset in assets
                if asset["asset_type"] == "voice"
                and asset["status"] == "ready"
                and safe_text(asset.get("account_label")) == account["label"]
                and safe_text(asset.get("path"))
            )
        summary["video_paths"].update(
            safe_text(asset.get("path"))
            for asset in assets
            if asset["asset_type"] == "video" and asset["status"] == "ready" and safe_text(asset.get("path"))
        )
        return rows, summary

    def _script_block_rows(self, project, account, products, blocks, assets):
        rows = []
        issue_count = 0
        products_by_uid = {product["uid"]: product for product in products}
        ordered_blocks = sorted(
            blocks,
            key=lambda block: (
                0 if block["script_type"] == "product" else 1 if block["script_type"] == "intro" else 2,
                safe_text(block.get("owner_uid")),
                safe_text(block.get("price_range_label")),
                safe_text(block.get("block_label")),
            ),
        )
        for block in ordered_blocks:
            script_type = block["script_type"]
            uid = safe_text(block.get("owner_uid"))
            script_id = safe_text(block.get("script_id")) or f"script-{block['id']}"
            if script_type == "product":
                product = products_by_uid.get(uid, {})
                obj = f"{safe_text(product.get('price_label'))} / {uid} / {safe_text(product.get('title'))} / {script_id}"
                voice_uid = uid
                block_label = safe_text(block.get("block_label"))
                image_count = self._asset_count(assets, uid=uid, asset_type="image", account_label=account["label"])
                video_count = self._asset_count(assets, uid=uid, asset_type="video")
                copy_type = "商品文案"
                issues = []
                if image_count == 0:
                    issues.append("缺图片")
                if video_count == 0:
                    issues.append("缺视频")
            elif script_type == "intro":
                obj = f"引言 / {safe_text(block.get('block_label'))} / {script_id}"
                voice_uid = "INTRO"
                block_label = safe_text(block.get("block_label"))
                image_count = "—"
                video_count = "—"
                copy_type = "引言文案"
                issues = []
            elif script_type == "price_transition":
                obj = f"价格过渡 / {safe_text(block.get('price_range_label'))} / {safe_text(block.get('block_label'))} / {script_id}"
                voice_uid = "PRICE_TRANSITION"
                block_label = safe_text(block.get("price_range_label"))
                image_count = "—"
                video_count = "—"
                copy_type = "价格过渡"
                issues = []
            else:
                continue
            state = voice_state(
                assets,
                uid=voice_uid,
                account_label=account["label"],
                hashes={safe_text(block.get("text_hash"))},
                block_label=block_label,
            )
            if state == "expired":
                issues.append("配音过期")
            elif state != "ready":
                issues.append("缺配音")
            issue = "，".join(issues) if issues else "—"
            if issues:
                issue_count += 1
            voice_count = 1 if state == "ready" else 0
            rows.append((project["category_name"], account["label"], obj, copy_type, "1", str(image_count), str(video_count), str(voice_count), issue))
        return rows, issue_count

    def _asset_count(self, assets, *, uid, asset_type, account_label="", block_label="") -> int:
        return sum(
            1
            for asset in assets
            if asset["uid"] == uid
            and asset["asset_type"] == asset_type
            and asset["status"] == "ready"
            and (not account_label or asset["account_label"] == account_label or not asset["account_label"])
            and (not block_label or asset["block_label"] == block_label)
        )

    def _row_matches_filter(self, row: tuple[Any, ...]) -> bool:
        issue = str(row[-1] or "")
        selected = self.status_var.get()
        return True if selected == "全部" else selected in issue

    def _selected_users(self) -> list[str]:
        return [label for label, var in self.user_vars.items() if bool(var.get())]

    def _update_stat_cards(self, summary: dict[str, Any], rows: list[tuple[Any, ...]]) -> None:
        self.stat_value_labels["copy"].configure(text=str(len(rows)))
        self.stat_value_labels["image"].configure(text=str(len(summary["image_paths"])))
        self.stat_value_labels["video"].configure(text=str(len(summary["video_paths"])))
        self.stat_value_labels["voice"].configure(text=str(len(summary["voice_paths"])))
        self.stat_value_labels["issue"].configure(text=str(sum(1 for row in rows if str(row[-1]).strip() and str(row[-1]).strip() != "—")))
        for label in self.stat_hint_labels.values():
            label.configure(text="")

    def _configure_asset_tree(self) -> None:
        style = ttk.Style()
        style.configure(
            "Asset.CTreeview",
            rowheight=38,
            background=UIStyle.COLOR_ASSET_TABLE_ROW,
            foreground=UIStyle.COLOR_ASSET_TABLE_TEXT,
            fieldbackground=UIStyle.COLOR_ASSET_TABLE_ROW,
            borderwidth=0,
            relief="flat",
            font=UIStyle.FONT_TABLE,
            bordercolor=UIStyle.COLOR_BORDER,
            lightcolor=UIStyle.COLOR_BORDER,
            darkcolor=UIStyle.COLOR_BORDER,
        )
        style.configure(
            "Asset.CTreeview.Heading",
            background=UIStyle.COLOR_ASSET_TABLE_HEADER,
            foreground=UIStyle.COLOR_ASSET_TABLE_HEADING_TEXT,
            borderwidth=0,
            relief="flat",
            font=UIStyle.FONT_LABEL_STRONG,
            bordercolor=UIStyle.COLOR_BORDER,
            lightcolor=UIStyle.COLOR_BORDER,
            darkcolor=UIStyle.COLOR_BORDER,
        )
        style.map("Asset.CTreeview", background=[("selected", UIStyle.COLOR_TABLE_SELECTED)], foreground=[("selected", UIStyle.COLOR_TEXT_MAIN)])
        style.map("Asset.CTreeview.Heading", background=[("active", UIStyle.COLOR_TABLE_ACTIVE)], foreground=[("active", UIStyle.COLOR_TEXT_MAIN)])
        self.tree.configure(style="Asset.CTreeview")
        self.tree.tag_configure("even", background=UIStyle.COLOR_ASSET_TABLE_ROW, foreground=UIStyle.COLOR_ASSET_TABLE_TEXT)
        self.tree.tag_configure("odd", background=UIStyle.COLOR_ASSET_TABLE_ROW_ALT, foreground=UIStyle.COLOR_ASSET_TABLE_TEXT)
        self.tree.tag_configure("even_issue", background=UIStyle.COLOR_ASSET_ISSUE_ROW, foreground=UIStyle.COLOR_ASSET_ISSUE_TEXT)
        self.tree.tag_configure("odd_issue", background=UIStyle.COLOR_ASSET_ISSUE_ROW_ALT, foreground=UIStyle.COLOR_ASSET_ISSUE_TEXT)
        widths = {
            "品类": 88,
            "用户": 84,
            "对象": 430,
            "文案类型": 122,
            "文案": 54,
            "图片": 54,
            "视频": 54,
            "配音": 54,
            "问题": 240,
        }
        for column, width in widths.items():
            anchor = "center" if column in {"文案", "图片", "视频", "配音"} else "w"
            self.tree.column(column, width=width, minwidth=width, anchor=anchor, stretch=column in {"对象", "问题"})
        self.tree.configure(selectmode="browse")
        try:
            self.tree.configure(padding=0)
        except tk.TclError:
            pass
        self.tree["show"] = "headings"


AssetPageColumns = ("品类", "用户", "对象", "文案类型", "文案", "图片", "视频", "配音", "问题")
