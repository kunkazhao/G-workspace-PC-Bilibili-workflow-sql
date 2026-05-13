from __future__ import annotations

import json
import os
import re
import threading
import tkinter as tk
import traceback
from dataclasses import dataclass, field
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

UI_VERSION = "G-UI-2026-05-10-sync-redesign"


@dataclass
class DialogSection:
    title: str
    step: str = ""
    tone: str = "default"
    rows: list[tuple[str, str]] = field(default_factory=list)
    items: list[str] = field(default_factory=list)
    helper: str = ""


@dataclass
class ProjectEditorState:
    dialog: ctk.CTkToplevel
    mode: str
    project_id: int
    fields: dict[str, ctk.StringVar]
    workspace_var: ctk.StringVar
    parent_category_var: ctk.StringVar
    child_category_var: ctk.StringVar
    scheme_var: ctk.StringVar
    parent_combo: AppComboBox | None = None
    child_combo: AppComboBox | None = None
    scheme_combo: AppComboBox | None = None


# ── 工具函数 ──


def parse_uid_list(value: str) -> list[str]:
    return [item.strip() for item in re.split(r"[,，]+", value or "") if item.strip()]


def parse_voice_targets(value: str) -> tuple[list[str], list[str]]:
    tokens = parse_uid_list(value)
    script_ids = [item for item in tokens if ":" in item]
    uids = [item for item in tokens if ":" not in item]
    return uids, script_ids


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
    image_account = "" if selected_user == "全部" else selected_user
    for product in products:
        uid = product["uid"]
        title = product["title"]
        display = f"{uid} {title}"
        if uid not in product_blocks:
            issues["missing_copy"].append(display)
        if not has_ready_asset(assets, uid=uid, asset_type="image", account_label=image_account):
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
    if hashes:
        # 有文案 hash 可对比：必须 hash 匹配才算 ready
        if any(safe_text(asset.get("text_hash")) in hashes for asset in matching_uid):
            return "ready"
        if any(not safe_text(asset.get("text_hash")) for asset in matching_uid):
            return "ready"
        # 存在 ready 记录但 hash 都不匹配文案 → 过期
        if any(safe_text(asset.get("text_hash")) for asset in matching_uid):
            return "expired"
    # 没有文案 hash 可对比（旧数据），有 ready 记录就算可用
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


def _path_common_dir(paths: list[str]) -> str:
    valid = [safe_text(path) for path in paths if safe_text(path)]
    if not valid:
        return ""
    try:
        return str(Path(os.path.commonpath(valid)))
    except ValueError:
        return str(Path(valid[0]).parent)


def _asset_common_dir(
    assets: list[dict[str, Any]],
    *,
    asset_type: str,
    selected_user: str = "全部",
    fallback: str = "",
    category_hint: str = "",
) -> str:
    scoped_by_user = asset_type in {"image", "voice"} and selected_user != "全部"

    def display_dir(path_text: str) -> str:
        path = Path(path_text)
        if scoped_by_user:
            parts = path.parts
            if selected_user in parts:
                return str(Path(*parts[: parts.index(selected_user) + 1]))
        return str(path)

    filtered = [
        safe_text(asset.get("path"))
        for asset in assets
        if asset.get("asset_type") == asset_type
        and asset.get("status") == "ready"
        and safe_text(asset.get("path"))
        and (not scoped_by_user or safe_text(asset.get("account_label")) == selected_user)
    ]
    base = Path(fallback) if fallback else Path()
    if scoped_by_user and category_hint:
        expected = base / category_hint / selected_user
        if expected.exists() or any(str(Path(path)).casefold().startswith(str(expected).casefold()) for path in filtered):
            return str(expected)
    if scoped_by_user:
        expected = base / selected_user
        if expected.exists() or any(str(Path(path)).casefold().startswith(str(expected).casefold()) for path in filtered):
            return str(expected)
    common = _path_common_dir([display_dir(path) for path in filtered])
    if common:
        return common
    if scoped_by_user and category_hint:
        return str(base / category_hint / selected_user)
    if scoped_by_user:
        return str(base / selected_user)
    if category_hint:
        return str(base / category_hint)
    return safe_text(fallback)


def asset_folder_paths(project: dict[str, Any], assets: list[dict[str, Any]], selected_user: str) -> dict[str, str]:
    category_hint = safe_text(project.get("name"))
    category_name = safe_text(project.get("category_name")) or category_hint.split("-", 1)[-1]
    image_dir = _asset_common_dir(
        assets,
        asset_type="image",
        selected_user=selected_user,
        fallback=safe_text(project.get("image_root")),
        category_hint=category_hint,
    )
    video_root = safe_text(project.get("video_root"))
    video_dir = str(Path(video_root) / category_hint) if video_root and category_hint else video_root
    voice_root = safe_text(project.get("voice_root"))
    voice_dir = ""
    if selected_user != "全部" and voice_root:
        voice_expected = Path(voice_root) / f"{selected_user}-{category_name}"
        if voice_expected.exists():
            voice_dir = str(voice_expected)
    if not voice_dir:
        voice_dir = _asset_common_dir(
            assets,
            asset_type="voice",
            selected_user=selected_user,
            fallback=voice_root,
            category_hint=category_hint,
        )
    if selected_user == "全部" and not voice_dir:
        voice_dir = safe_text(project.get("voice_root"))
    return {"image": image_dir, "video": video_dir, "voice": voice_dir}


def preview_lines(items: list[str], limit: int = 18) -> list[str]:
    if not items:
        return ["无"]
    lines = [f"{index}. {item}" for index, item in enumerate(items[:limit], start=1)]
    if len(items) > limit:
        lines.append(f"... 其余 {len(items) - limit} 条已省略")
    return lines


def normalized_name(value: str | Path | None) -> str:
    text = Path(value).stem if isinstance(value, Path) else safe_text(value)
    text = re.sub(r"\s+", "", text)
    return text.casefold()


def confirm_project_markdown_path(parent: tk.Widget, project: dict[str, Any], path: str | Path) -> bool:
    project_name = safe_text(project.get("name"))
    target_name = Path(path).stem
    if normalized_name(project_name) == normalized_name(target_name):
        return True
    return show_confirmation_dialog(
        parent,
        "确认商品文案路径",
        "当前选择的商品文案文件名和项目名不一致。",
        [
            DialogSection(
                title="路径确认",
                step="1",
                tone="warning",
                rows=[
                    ("当前项目", project_name or "未命名项目"),
                    ("目标文件", str(path)),
                ],
                helper="如果继续，项目会绑定到这个 MD 文件，并用它覆盖数据库里的文案块。",
            )
        ],
        confirm_text="确认继续",
    )


def _center_dialog(dialog: ctk.CTkToplevel) -> None:
    dialog.update_idletasks()
    x = dialog.winfo_screenwidth() // 2 - dialog.winfo_width() // 2
    y = dialog.winfo_screenheight() // 2 - dialog.winfo_height() // 2
    dialog.geometry(f"+{x}+{y}")


def _dialog_tone_colors(tone: str) -> tuple[str, str]:
    palette = {
        "default": (UIStyle.COLOR_INFO, UIStyle.COLOR_BORDER),
        "info": (UIStyle.COLOR_INFO, UIStyle.COLOR_BORDER),
        "success": (UIStyle.COLOR_SUCCESS, UIStyle.COLOR_BORDER),
        "warning": (UIStyle.COLOR_WARNING, UIStyle.COLOR_BORDER),
        "error": (UIStyle.COLOR_ERROR, UIStyle.COLOR_BORDER),
        "primary": (UIStyle.COLOR_PRIMARY, UIStyle.COLOR_BORDER),
    }
    return palette.get(tone, palette["default"])


def _build_dialog_section(parent: tk.Widget, section: DialogSection) -> ctk.CTkFrame:
    accent, border = _dialog_tone_colors(section.tone)
    card = ctk.CTkFrame(
        parent,
        fg_color=UIStyle.COLOR_CARD_BG,
        corner_radius=UIStyle.RADIUS_LG,
        border_width=1,
        border_color=border,
    )
    card.grid_columnconfigure(1, weight=1)
    header = ctk.CTkFrame(card, fg_color="transparent")
    header.grid(row=0, column=0, columnspan=2, sticky="ew", padx=UIStyle.PAD_LG, pady=(UIStyle.PAD_LG, UIStyle.PAD_MD))
    badge_text = section.step or "•"
    badge = ctk.CTkLabel(
        header,
        text=badge_text,
        width=34,
        height=34,
        corner_radius=10,
        fg_color=accent,
        text_color=UIStyle.COLOR_TEXT_MAIN,
        font=UIStyle.FONT_H3,
    )
    badge.pack(side="left")
    ctk.CTkLabel(header, text=section.title, font=UIStyle.FONT_H2).pack(side="left", padx=(UIStyle.PAD_MD, 0))

    row_index = 1
    for label, value in section.rows:
        if not safe_text(value):
            continue
        ctk.CTkLabel(card, text=f"{label}：", font=UIStyle.FONT_BODY, text_color=UIStyle.COLOR_TEXT_DIM).grid(
            row=row_index, column=0, sticky="nw", padx=(UIStyle.PAD_LG, UIStyle.PAD_SM), pady=(0, UIStyle.PAD_SM)
        )
        ctk.CTkLabel(
            card,
            text=value,
            font=UIStyle.FONT_BODY,
            justify="left",
            anchor="w",
            wraplength=920,
        ).grid(row=row_index, column=1, sticky="nw", padx=(0, UIStyle.PAD_LG), pady=(0, UIStyle.PAD_SM))
        row_index += 1

    for item in section.items:
        ctk.CTkLabel(
            card,
            text=f"• {item}",
            font=UIStyle.FONT_BODY,
            justify="left",
            anchor="w",
            wraplength=980,
        ).grid(row=row_index, column=0, columnspan=2, sticky="w", padx=UIStyle.PAD_LG, pady=(0, UIStyle.PAD_SM))
        row_index += 1

    if section.helper:
        ctk.CTkLabel(
            card,
            text=section.helper,
            font=UIStyle.FONT_SMALL,
            text_color=UIStyle.COLOR_TEXT_DIM,
            justify="left",
            anchor="w",
            wraplength=980,
        ).grid(row=row_index, column=0, columnspan=2, sticky="w", padx=UIStyle.PAD_LG, pady=(0, UIStyle.PAD_LG))
    else:
        ctk.CTkLabel(card, text="", height=1).grid(row=row_index, column=0, pady=(0, UIStyle.PAD_XS))
    return card


def show_precheck_dialog(
    parent: tk.Widget,
    title: str,
    subtitle: str,
    sections: list[DialogSection],
    *,
    can_continue: bool = True,
    confirm_text: str = "确认继续",
    dismiss_text: str = "取消",
) -> bool:
    dialog = ctk.CTkToplevel(parent)
    dialog.title(title)
    dialog.geometry("1180x860")
    dialog.minsize(920, 680)
    dialog.transient(parent.winfo_toplevel())
    dialog.grab_set()
    dialog.rowconfigure(0, weight=1)
    dialog.columnconfigure(0, weight=1)
    body = ctk.CTkScrollableFrame(dialog, fg_color="transparent")
    body.grid(row=0, column=0, sticky="nsew", padx=UIStyle.PAD_XL, pady=(UIStyle.PAD_XL, UIStyle.PAD_MD))
    for index, section in enumerate(sections):
        card = _build_dialog_section(body, section)
        card.pack(fill="x", pady=(0, UIStyle.PAD_MD))

    buttons = ctk.CTkFrame(dialog, fg_color="transparent")
    buttons.grid(row=1, column=0, sticky="ew", padx=UIStyle.PAD_XL, pady=(0, UIStyle.PAD_XL))
    buttons.columnconfigure(0, weight=1)
    result = {"ok": False}

    def close(ok: bool) -> None:
        result["ok"] = ok
        dialog.destroy()

    GhostButton(buttons, text=dismiss_text, command=lambda: close(False)).grid(row=0, column=1, padx=(0, UIStyle.PAD_SM))
    if can_continue:
        PrimaryButton(buttons, text=confirm_text, command=lambda: close(True)).grid(row=0, column=2)
    else:
        GhostButton(buttons, text=confirm_text, command=lambda: close(False)).grid(row=0, column=2)
    dialog.protocol("WM_DELETE_WINDOW", lambda: close(False))
    _center_dialog(dialog)
    dialog.wait_window()
    return result["ok"]


def show_confirmation_dialog(
    parent: tk.Widget,
    title: str,
    subtitle: str,
    sections: list[DialogSection],
    *,
    confirm_text: str = "确认继续",
    dismiss_text: str = "取消",
) -> bool:
    return show_precheck_dialog(
        parent,
        title,
        subtitle,
        sections,
        can_continue=True,
        confirm_text=confirm_text,
        dismiss_text=dismiss_text,
    )


def show_text_dialog(parent: tk.Widget, title: str, message: str) -> None:
    dialog = ctk.CTkToplevel(parent)
    dialog.title(title)
    dialog.geometry("860x560")
    dialog.minsize(680, 420)
    dialog.transient(parent.winfo_toplevel())
    dialog.rowconfigure(1, weight=1)
    dialog.columnconfigure(0, weight=1)
    ctk.CTkLabel(dialog, text=title, font=UIStyle.FONT_H2).grid(
        row=0, column=0, sticky="w", padx=UIStyle.PAD_LG, pady=(UIStyle.PAD_LG, UIStyle.PAD_SM)
    )
    text = ctk.CTkTextbox(dialog, wrap="none")
    text.grid(row=1, column=0, sticky="nsew", padx=UIStyle.PAD_LG, pady=(0, UIStyle.PAD_MD))
    text.insert("1.0", message)
    text.configure(state="disabled")
    buttons = ctk.CTkFrame(dialog, fg_color="transparent")
    buttons.grid(row=2, column=0, sticky="ew", padx=UIStyle.PAD_LG, pady=(0, UIStyle.PAD_LG))
    buttons.columnconfigure(0, weight=1)
    GhostButton(buttons, text="关闭", command=dialog.destroy).grid(row=0, column=1)
    dialog.lift()
    dialog.focus_set()


class TaskProgressDialog(ctk.CTkToplevel):
    def __init__(self, parent: tk.Widget, title: str, message: str):
        super().__init__(parent)
        self.title(title)
        self.geometry("1040x700")
        self.minsize(820, 560)
        self.transient(parent.winfo_toplevel())
        self.rowconfigure(1, weight=1)
        self.columnconfigure(0, weight=1)

        self.hero_title_var = ctk.StringVar(value=title)
        self.status_var = ctk.StringVar(value=message)
        self.detail_var = ctk.StringVar(value="")

        shell = ctk.CTkFrame(self, fg_color="transparent")
        shell.grid(row=0, column=0, sticky="nsew", padx=UIStyle.PAD_XL, pady=UIStyle.PAD_XL)
        shell.rowconfigure(1, weight=1)
        shell.columnconfigure(0, weight=1)

        hero = ctk.CTkFrame(shell, fg_color="transparent")
        hero.grid(row=0, column=0, sticky="ew", pady=(UIStyle.PAD_XL, UIStyle.PAD_LG))
        self.icon_label = ctk.CTkLabel(
            hero,
            text="✂",
            font=("Microsoft YaHei", 72, "bold"),
            text_color=UIStyle.COLOR_INFO,
        )
        self.icon_label.pack(pady=(0, UIStyle.PAD_MD))
        ctk.CTkLabel(hero, textvariable=self.hero_title_var, font=("Microsoft YaHei", 30, "bold")).pack()
        ctk.CTkLabel(
            hero,
            textvariable=self.status_var,
            font=UIStyle.FONT_BODY,
            text_color=UIStyle.COLOR_TEXT_DIM,
            justify="center",
            wraplength=860,
        ).pack(pady=(UIStyle.PAD_MD, UIStyle.PAD_SM))
        self.detail_label = ctk.CTkLabel(
            hero,
            textvariable=self.detail_var,
            font=UIStyle.FONT_H3,
            text_color=UIStyle.COLOR_TEXT_MAIN,
        )
        self.detail_label.pack()

        self.progress = ctk.CTkProgressBar(hero, mode="indeterminate", height=12, corner_radius=999)
        self.progress.pack(fill="x", padx=140, pady=(UIStyle.PAD_LG, 0))
        self.progress.start()

        log_card = ctk.CTkFrame(shell, fg_color=UIStyle.COLOR_CARD_BG, corner_radius=UIStyle.RADIUS_LG, border_width=1, border_color=UIStyle.COLOR_BORDER)
        log_card.grid(row=1, column=0, sticky="nsew")
        log_card.rowconfigure(1, weight=1)
        log_card.columnconfigure(0, weight=1)
        ctk.CTkLabel(log_card, text="执行日志", font=UIStyle.FONT_H3).grid(
            row=0, column=0, sticky="w", padx=UIStyle.PAD_LG, pady=(UIStyle.PAD_LG, UIStyle.PAD_SM)
        )
        self.text = ctk.CTkTextbox(log_card, wrap="word")
        self.text.grid(row=1, column=0, sticky="nsew", padx=UIStyle.PAD_LG, pady=(0, UIStyle.PAD_LG))
        buttons = ctk.CTkFrame(shell, fg_color="transparent")
        buttons.grid(row=2, column=0, sticky="ew", pady=(UIStyle.PAD_LG, 0))
        buttons.columnconfigure(0, weight=1)
        self.close_button = GhostButton(buttons, text="关闭", command=self.destroy)
        self.close_button.grid(row=0, column=1)
        self.close_button.configure(state="disabled")
        self.protocol("WM_DELETE_WINDOW", lambda: None)
        self.lift()
        self.focus_set()
        _center_dialog(self)

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

    def finish(self, message: str, *, kind: str = "success", headline: str | None = None, detail: str = "") -> None:
        if not self.winfo_exists():
            return
        self.progress.stop()
        self.progress.pack_forget()
        palette = {
            "success": (UIStyle.COLOR_SUCCESS, "✓"),
            "warning": (UIStyle.COLOR_WARNING, "!"),
            "error": (UIStyle.COLOR_ERROR, "×"),
            "info": (UIStyle.COLOR_INFO, "i"),
        }
        color, icon = palette.get(kind, palette["success"])
        self.icon_label.configure(text=icon, text_color=color)
        if headline:
            self.hero_title_var.set(headline)
        self.status_var.set(message)
        self.detail_var.set(detail)
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


def manifest_entries(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, dict) or not isinstance(value.get("entries"), list):
        return []
    return [entry for entry in value["entries"] if isinstance(entry, dict)]


def manifest_missing_assets(value: Any) -> dict[str, list[str]]:
    missing: dict[str, list[str]] = {"audio": [], "image": [], "video": []}
    for entry in manifest_entries(value):
        entry_type = safe_text(entry.get("type"))
        label = " ".join(
            part
            for part in [
                f"#{entry.get('order_index') or entry.get('index')}" if entry.get("order_index") or entry.get("index") else "",
                entry_type,
                safe_text(entry.get("product_uid")),
                safe_text(entry.get("product_name")),
                safe_text(entry.get("source_label")),
            ]
            if part
        )
        for key, field in (("audio", "audio_path"), ("image", "image_path"), ("video", "video_path")):
            if key in {"image", "video"} and entry_type != "product":
                continue
            path = safe_text(entry.get(field))
            if not path:
                missing[key].append(f"{label}：路径为空")
            elif not Path(path).exists():
                missing[key].append(f"{label}：{path}")
    return missing


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
        self.after(50, lambda: self.state("zoomed"))

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

        title = ctk.CTkLabel(sidebar, text="B-Workflow SQL", font=("Microsoft YaHei", 13, "bold"), text_color=UIStyle.COLOR_PRIMARY)
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
        payload["id"] = int(self.project_var.get().split(" - ", 1)[0]) if self.project_var.get() else 0
        if not payload["name"]:
            messagebox.showwarning("缺少项目名", "请填写项目名。")
            return
        if payload.get("md_path") and not confirm_project_markdown_path(self, payload, payload["md_path"]):
            return
        project_id = self.db.upsert_project(payload)
        self.app.set_current_project(project_id)
        self.log(f"已保存项目：{payload['name']}")
        # 如果有方案 ID，自动同步 Master 方案商品
        if payload.get("scheme_id"):
            self.log("正在同步 Master 方案商品...")
            try:
                result = self.sync.sync_master_scheme(project_id, apply_changes=True)
                self.log(f"Master 方案已同步：新增 {len(result['added'])}，更新 {len(result['updated'])}，移除 {len(result['removed'])}")
            except Exception as exc:
                self.log(f"Master 方案同步失败：{exc}")
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
        if not confirm_project_markdown_path(self, project, path):
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
                _w, tree, source = self.master_data.fetch_category_tree(safe_text(workspace.get("id")), force_refresh=force_refresh)
            return {"workspaces": workspaces, "workspace": workspace, "tree": tree, "source": source}

        def on_success(result: dict[str, Any]) -> None:
            self.workspaces = result["workspaces"]
            workspace = result["workspace"]
            if workspace:
                self.workspace_var.set(display_name(workspace))
                self.fields["workspace_id"].set(safe_text(workspace.get("id")))
                self.fields["workspace_name"].set(display_name(workspace))
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
            saved_child = self.fields["category_name"].get().strip() if keep_existing else ""
            saved_scheme = self.fields["scheme_name"].get().strip() if keep_existing else ""
            self.parent_category_var.set(saved if saved in parent_names else parent_names[0])
            self._on_parent_selected(preferred_child=saved_child, preferred_scheme=saved_scheme)
        self.log(f"已读取 Master 品类：{len(parent_names)} 个一级品类（来源：{source}）。")

    def _on_parent_selected(self, _=None, *, preferred_child: str = "", preferred_scheme: str = "") -> None:
        parent = self._selected_parent()
        if not parent:
            return
        self.fields["category_parent_id"].set(safe_text(parent.get("id")))
        self.fields["category_parent_name"].set(safe_text(parent.get("name")))
        children = parent.get("children") or []
        child_names = [safe_text(c.get("name")) for c in children if safe_text(c.get("name"))]
        self.child_combo.configure(values=child_names)
        self.child_category_var.set(preferred_child if preferred_child in child_names else (child_names[0] if child_names else ""))
        self.scheme_var.set("")
        self.scheme_combo.configure(values=[])
        if child_names:
            self._on_child_selected(preferred_scheme=preferred_scheme)

    def _on_child_selected(self, _=None, *, preferred_scheme: str = "") -> None:
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
                self.scheme_var.set(preferred_scheme if preferred_scheme in names else names[0])
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


class ProjectPageDialog(BasePage):
    def __init__(self, master, app: App):
        super().__init__(master, "品类项目", app)
        self.project_var = ctk.StringVar()
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
        self._build()

    def _build(self) -> None:
        content = self.content
        selector = ctk.CTkFrame(content, fg_color="transparent")
        selector.pack(fill="x", pady=(0, UIStyle.PAD_SM))
        ctk.CTkLabel(selector, text="当前项目", font=UIStyle.FONT_BODY, text_color=UIStyle.COLOR_TEXT_DIM).pack(side="left")
        self.project_combo = AppComboBox(selector, width=400, variable=self.project_var)
        self.project_combo.pack(side="left", padx=UIStyle.PAD_SM)
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
        ctk.CTkLabel(
            action_row,
            text="Master、MD、素材同步请到“同步中心”统一操作。",
            font=UIStyle.FONT_SMALL,
            text_color=UIStyle.COLOR_TEXT_DIM,
        ).pack(side="left", padx=UIStyle.PAD_SM)

        self.log_text = AppTextbox(content, height=200)
        self.log_text.pack(fill="both", expand=True, pady=(UIStyle.PAD_SM, 0))

    def _add_summary_row(self, parent: ctk.CTkFrame, *, row: int, column: int, label: str, key: str) -> None:
        ctk.CTkLabel(parent, text=label, font=UIStyle.FONT_BODY, text_color=UIStyle.COLOR_TEXT_DIM).grid(
            row=row, column=column, sticky="nw", padx=(0, UIStyle.PAD_SM), pady=UIStyle.PAD_XS
        )
        value = ctk.StringVar(value="未配置")
        self.display_labels[key] = value
        ctk.CTkLabel(
            parent,
            textvariable=value,
            font=UIStyle.FONT_BODY,
            justify="left",
            anchor="w",
            wraplength=520,
        ).grid(row=row, column=column + 1, sticky="ew", padx=(0, UIStyle.PAD_MD), pady=UIStyle.PAD_XS)

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
            messagebox.showinfo("需要品类项目", "请先选择一个品类项目。")
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
        if payload.get("md_path") and not confirm_project_markdown_path(self, payload, payload["md_path"]):
            return

        def work() -> tuple[int, dict[str, Any] | None]:
            project_id = self.db.upsert_project(payload)
            sync_result = self.sync.sync_master_scheme(project_id, apply_changes=True) if payload.get("scheme_id") else None
            return project_id, sync_result

        def on_success(result: tuple[int, dict[str, Any] | None]) -> None:
            project_id, sync_result = result
            self._close_project_dialog()
            self.app.set_current_project(project_id)
            self.refresh()
            self.log(f"已保存项目：{payload['name']}")
            if sync_result:
                self.log(f"Master 方案已同步：新增 {len(sync_result['added'])}，更新 {len(sync_result['updated'])}，移除 {len(sync_result['removed'])}")
            self.toast("项目已保存")

        self.app.run_background("保存品类项目", work, on_success=on_success, show_success_toast=False)

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
        for key, var in self.display_labels.items():
            value = safe_text(project.get(key))
            var.set(value or "未配置")

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
        md_path = self.fields["md_path"].get().strip() or str(self.outline.default_markdown_path(project["id"]))
        path = filedialog.asksaveasfilename(
            defaultextension=".md", filetypes=[("Markdown", "*.md"), ("All", "*.*")],
            initialdir=str(DEFAULT_MARKDOWN_ROOT),
            initialfile=Path(md_path).name,
        )
        if not path:
            return
        if not confirm_project_markdown_path(self, project, path):
            return

        def work() -> tuple[dict[str, Any], dict[str, Any]]:
            result = self.outline.init_or_update_outline(project["id"], path)
            sync_result = self.sync.sync_markdown(project["id"])
            return result, sync_result

        def on_success(payload: tuple[dict[str, Any], dict[str, Any]]) -> None:
            result, sync_result = payload
            self.fields["md_path"].set(result["target_path"])
            self.display_labels["md_path"].set(result["target_path"])
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
        else:
            self.project_var.set("")
            for key, var in self.fields.items():
                var.set("")
            for var in self.display_labels.values():
                var.set("未配置")
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
        state.parent_category_var.set("")
        state.child_category_var.set("")
        state.scheme_var.set("")
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
        state.child_category_var.set(preferred_child if preferred_child in child_names else (child_names[0] if child_names else ""))
        state.scheme_var.set("")
        if child_names:
            self._editor_on_child_selected(state, preferred_scheme=preferred_scheme)

    def _editor_on_child_selected(self, state: ProjectEditorState, _=None, *, preferred_scheme: str = "") -> None:
        workspace = self._selected_workspace(state)
        child = self._selected_child(state)
        if not workspace or not child:
            return
        state.fields["category_id"].set(safe_text(child.get("id")))
        state.fields["category_name"].set(safe_text(child.get("name")))
        state.scheme_var.set("读取中...")

        def work() -> tuple[list[dict[str, Any]], str]:
            return self.master_data.fetch_schemes(workspace_id=safe_text(workspace.get("id")), category_id=safe_text(child.get("id")))

        def on_success(result: tuple[list[dict[str, Any]], str]) -> None:
            if not self._editor_alive(state):
                return
            self.schemes, source = result
            names = [display_name(scheme, safe_text(scheme.get("id"))) for scheme in self.schemes]
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
            ("video", "视频", "#8B5CF6"),
            ("voice", "配音", UIStyle.COLOR_WARNING),
            ("issue", "问题", UIStyle.COLOR_ERROR),
        ]
        for column, (key, title, _accent) in enumerate(stat_specs):
            card = ctk.CTkFrame(stats, fg_color=UIStyle.COLOR_SURFACE_SOFT, corner_radius=UIStyle.RADIUS_MD)
            card.grid(row=0, column=column, sticky="ew", padx=(UIStyle.PAD_LG if column == 0 else 0, UIStyle.PAD_LG), pady=UIStyle.PAD_LG)
            ctk.CTkLabel(card, text=title, font=("Microsoft YaHei", 14, "bold"), text_color=UIStyle.COLOR_TEXT_MAIN).pack(
                anchor="center", pady=(UIStyle.PAD_MD, 2)
            )
            value = ctk.CTkLabel(card, text="0", font=("Microsoft YaHei", 24, "bold"), text_color=UIStyle.COLOR_TEXT_MAIN)
            value.pack(anchor="center", pady=(0, UIStyle.PAD_MD))
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

    def refresh(self) -> None:
        projects = self.repo.projects()
        if not projects:
            _set_tree_rows(self.tree, [])
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
            "CTreeview",
            rowheight=38,
            background="#131D2B",
            foreground="#E8EEF8",
            fieldbackground="#131D2B",
            borderwidth=0,
            relief="flat",
            font=UIStyle.FONT_TABLE,
            bordercolor=UIStyle.COLOR_BORDER,
            lightcolor=UIStyle.COLOR_BORDER,
            darkcolor=UIStyle.COLOR_BORDER,
        )
        style.configure(
            "CTreeview.Heading",
            background="#111927",
            foreground="#D6DFEC",
            borderwidth=0,
            relief="flat",
            font=("Microsoft YaHei", 12, "bold"),
            bordercolor=UIStyle.COLOR_BORDER,
            lightcolor=UIStyle.COLOR_BORDER,
            darkcolor=UIStyle.COLOR_BORDER,
        )
        style.map("CTreeview", background=[("selected", "#233247")], foreground=[("selected", "#F7FAFF")])
        style.map("CTreeview.Heading", background=[("active", "#162233")], foreground=[("active", "#F7FAFF")])
        self.tree.tag_configure("even", background="#131D2B", foreground="#E8EEF8")
        self.tree.tag_configure("odd", background="#162233", foreground="#E8EEF8")
        self.tree.tag_configure("even_issue", background="#3A2426", foreground="#F7D7D9")
        self.tree.tag_configure("odd_issue", background="#43292C", foreground="#F7D7D9")
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
            font=("Microsoft YaHei", 15, "bold"),
            text_color=UIStyle.COLOR_TEXT_MAIN,
            anchor="w",
        )
        self.title_label.pack(anchor="w", padx=UIStyle.PAD_LG, pady=(UIStyle.PAD_LG, UIStyle.PAD_SM))

        self.body_label = ctk.CTkLabel(
            self,
            text="等待刷新",
            justify="left",
            anchor="nw",
            font=UIStyle.FONT_BODY,
            text_color=UIStyle.COLOR_TEXT_DIM,
            wraplength=520,
        )
        self.body_label.pack(fill="x", padx=UIStyle.PAD_LG, pady=(0, UIStyle.PAD_SM))

        self.asset_rows_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.metric_frame = ctk.CTkFrame(self, fg_color="transparent")

        self.button_frame = ctk.CTkFrame(self, fg_color="transparent")
        if buttons:
            self.button_frame.pack(fill="x", padx=UIStyle.PAD_LG, pady=(0, UIStyle.PAD_LG))
            for text, cmd in buttons:
                GhostButton(self.button_frame, text=text, command=cmd, height=32).pack(side="left", padx=(0, UIStyle.PAD_SM), pady=2)

    def set_body(self, text: str) -> None:
        self.body_label.configure(text=text)

    def set_asset_rows(self, rows: list) -> None:
        """rows: (label, path, open_cmd, sync_cmd) 或 (label, path, open_cmd, sync_cmd, voice_check_cmd)"""
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
                self.body_label.pack(fill="x", padx=UIStyle.PAD_LG, pady=(0, UIStyle.PAD_SM))
        for item in rows:
            label, path, open_cmd, sync_cmd, *extra = item
            voice_check_cmd = extra[0] if extra else None
            row = ctk.CTkFrame(self.asset_rows_frame, fg_color="transparent")
            row.pack(fill="x", pady=(0, UIStyle.PAD_SM))
            ctk.CTkLabel(row, text=label, width=34, font=UIStyle.FONT_BODY, text_color=UIStyle.COLOR_TEXT_MAIN, anchor="w").pack(side="left")
            path_box = ctk.CTkFrame(row, fg_color=UIStyle.COLOR_INPUT_BG, corner_radius=UIStyle.RADIUS_MD, border_width=1, border_color=UIStyle.COLOR_BORDER)
            path_box.pack(side="left", fill="x", expand=True, padx=(0, UIStyle.PAD_SM))
            ctk.CTkLabel(path_box, text=compact_path(path, 52) or "--", font=UIStyle.FONT_SMALL, text_color=UIStyle.COLOR_TEXT_DIM, anchor="w").pack(fill="x", padx=UIStyle.PAD_SM, pady=UIStyle.PAD_SM)
            GhostButton(row, text="打开目录", command=open_cmd, height=34, width=78).pack(side="left", padx=(0, UIStyle.PAD_SM))
            if voice_check_cmd:
                GhostButton(row, text="检查配音", command=voice_check_cmd, height=34, width=78).pack(side="left")
            else:
                GhostButton(row, text="同步素材", command=sync_cmd, height=34, width=78).pack(side="left")

    def set_metrics(self, items: list[tuple[str, int]], *, warn_labels: set[str] | None = None) -> None:
        for child in self.metric_frame.winfo_children():
            child.destroy()
        if items:
            if not self.metric_frame.winfo_ismapped():
                self.metric_frame.pack(fill="x", padx=UIStyle.PAD_LG, pady=(0, UIStyle.PAD_SM))
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
            chip.pack(side="left", padx=(0, UIStyle.PAD_SM), pady=2)
            ctk.CTkLabel(chip, text=label, font=UIStyle.FONT_SMALL, text_color=UIStyle.COLOR_TEXT_DIM).pack(side="left", padx=(UIStyle.PAD_SM, UIStyle.PAD_XS), pady=UIStyle.PAD_XS)
            value_color = UIStyle.COLOR_PRIMARY if label in warn_labels and value else UIStyle.COLOR_TEXT_MAIN
            ctk.CTkLabel(chip, text=str(value), font=UIStyle.FONT_SMALL, text_color=value_color).pack(side="left", padx=(0, UIStyle.PAD_SM), pady=UIStyle.PAD_XS)


class SyncPage(BasePage):
    def __init__(self, master, app: App):
        super().__init__(master, "同步中心", app)
        self.project_var = ctk.StringVar()
        self.user_var = ctk.StringVar(value="小燃")
        self.asset_paths: dict[str, str] = {}
        self._build()

    def _build(self) -> None:
        top = ctk.CTkFrame(self.content, fg_color="transparent")
        top.pack(fill="x", pady=(0, UIStyle.PAD_SM))
        ctk.CTkLabel(top, text="本次同步项目", font=UIStyle.FONT_SMALL, text_color=UIStyle.COLOR_TEXT_DIM).pack(side="left", padx=(0, UIStyle.PAD_SM))
        self.project_combo = AppComboBox(top, width=250, variable=self.project_var)
        self.project_combo.pack(side="left", padx=(0, UIStyle.PAD_MD))
        self.project_combo.configure(command=self._select_project)
        ctk.CTkLabel(top, text="用户", font=UIStyle.FONT_SMALL, text_color=UIStyle.COLOR_TEXT_DIM).pack(side="left", padx=(0, UIStyle.PAD_SM))
        self.user_combo = AppComboBox(top, width=92, variable=self.user_var)
        self.user_combo.pack(side="left", padx=(0, UIStyle.PAD_MD))
        self.user_combo.configure(command=lambda _=None: self.refresh())
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
        self.md_card = self._status_card(grid, "MD 文案", 0, 1, [("打开所在文件夹", self._open_md_folder), ("同步 MD", self._sync_md)])
        self.folder_card = self._status_card(grid, "素材文件夹", 1, 0, [], min_height=236)
        self.mapping_card = self._status_card(grid, "映射关系与缺口", 1, 1, [], min_height=236)

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
        card = SyncStatusCard(parent, title, buttons, min_height=min_height or (144 if row == 0 else 236))
        card.grid(row=row, column=col, sticky="nsew", padx=(0, UIStyle.PAD_MD) if col == 0 else (UIStyle.PAD_MD, 0), pady=(0, UIStyle.PAD_MD))
        return card

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
        asset_counts = {
            "image": sum(1 for a in assets if a["asset_type"] == "image" and a["status"] == "ready"),
            "video": sum(1 for a in assets if a["asset_type"] == "video" and a["status"] == "ready"),
            "voice": sum(1 for a in assets if a["asset_type"] == "voice" and a["status"] == "ready"),
        }
        issues = build_project_issue_summary(project, products, blocks, assets, self.repo.accounts(), selected_user=self.user_var.get())
        self.master_card.set_asset_rows([])
        self.master_card.set_body(f"方案：{project['scheme_name'] or '--'}\n商品：{len(products)} 个")
        self.master_card.set_metrics([])
        self.md_card.set_asset_rows([])
        self.md_card.set_body(f"MD：{compact_path(project['md_path'], 58) or '--'}\n引言 {intro_count}，商品文案 {product_block_count}，价格过渡 {price_count}")
        self.md_card.set_metrics([])
        self.asset_paths = asset_folder_paths(project, assets, self.user_var.get())
        self.folder_card.set_body("")
        self.folder_card.set_asset_rows(
            [
                ("图片", self.asset_paths.get("image", ""), lambda: self._open_asset_path("image"), lambda: self._sync_asset_type("image")),
                ("视频", self.asset_paths.get("video", ""), lambda: self._open_asset_path("video"), lambda: self._sync_asset_type("video")),
                ("配音", self.asset_paths.get("voice", ""), lambda: self._open_asset_path("voice"), None, self._check_voice_status),
            ]
        )
        self.folder_card.set_metrics([("图片", asset_counts["image"]), ("视频", asset_counts["video"]), ("配音", asset_counts["voice"]), ("素材总数", sum(asset_counts.values()))])
        self.mapping_card.set_asset_rows([])
        self.mapping_card.set_body(f"筛选用户：{self.user_var.get()}\n{format_issue_preview(issues, limit=3)}")
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
            success_message="")

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
        # 先解析 MD，预览变化内容
        from .md_parser import parse_markdown_file
        try:
            md_path = safe_text(project.get("md_path"))
            if not md_path or not Path(md_path).exists():
                messagebox.showwarning("MD 文件不存在", "当前项目没有绑定可读取的 MD 文档。", parent=self)
                return
            parsed = parse_markdown_file(md_path)
            products = self.repo.products(project["id"], include_removed=False)
            md_uids = {item.uid for item in parsed.products}
            matched = sum(1 for p in products if p["uid"] in md_uids)
            missing = len(products) - matched
            sections = [
                DialogSection(
                    title="解析结果",
                    step="1",
                    tone="primary",
                    rows=[
                        ("引言文案", f"{len(parsed.intro_scripts)} 段"),
                        ("商品文案", f"{len(parsed.products)} 个"),
                        ("已有匹配商品", f"{matched} 个"),
                        ("缺文案商品", f"{missing} 个"),
                    ],
                    helper="确认后会将 MD 中的文案块同步入库。",
                )
            ]
            if not show_confirmation_dialog(self, "确认同步 MD", "请核对本次 MD 解析结果，确认无误后再继续。", sections, confirm_text="确认同步"):
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
            return {"image": img["image"], "video": vid["video"], "unmatched": img["unmatched"] + vid["unmatched"], "voice": 0}
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
        self.app.run_background(
            f"同步{label}素材",
            lambda: self.sync.sync_assets(project["id"], asset_type=asset_type, root_override=path),
            on_success=lambda r: self._finish_asset_sync(label, r, focus_type=asset_type),
            show_success_toast=False,
        )

    def _check_voice_status(self) -> None:
        project = self._current_project_or_warn()
        if not project:
            return
        account_label = self.user_var.get().strip()
        if not account_label or account_label == "全部":
            account_label = "小燃"
        blocks = self.repo.script_blocks(project["id"])
        assets = self.repo.asset_bindings(project["id"])
        intro_blocks = [b for b in blocks if b["script_type"] == "intro"]
        product_blocks = [b for b in blocks if b["script_type"] == "product"]
        price_blocks = [b for b in blocks if b["script_type"] == "price_transition"]

        missing_voice = []
        expired_voice = []

        for block in intro_blocks:
            state = voice_state(assets, uid="INTRO", account_label=account_label,
                                hashes={safe_text(block.get("text_hash"))},
                                block_label=safe_text(block.get("block_label")))
            label = f"引言 {safe_text(block.get('block_label'))}"
            if state == "missing":
                missing_voice.append(label)
            elif state == "expired":
                expired_voice.append(label)

        for block in product_blocks:
            uid = safe_text(block.get("owner_uid"))
            state = voice_state(assets, uid=uid, account_label=account_label,
                                hashes={safe_text(block.get("text_hash"))})
            label = f"{uid} {safe_text(block.get('block_label'))}"
            if state == "missing":
                missing_voice.append(label)
            elif state == "expired":
                expired_voice.append(label)

        for block in price_blocks:
            state = voice_state(assets, uid="PRICE_TRANSITION", account_label=account_label,
                                hashes={safe_text(block.get("text_hash"))},
                                block_label=safe_text(block.get("price_range_label")))
            label = f"价格过渡 {safe_text(block.get('price_range_label'))}"
            if state == "missing":
                missing_voice.append(label)
            elif state == "expired":
                expired_voice.append(label)

        all_blocks = len(intro_blocks) + len(product_blocks) + len(price_blocks)
        ready_count = all_blocks - len(missing_voice) - len(expired_voice)
        lines = [
            f"配音检查结果（用户：{account_label}）",
            "",
            f"配音块总数：{all_blocks}",
            f"已就绪：{ready_count}",
            f"缺配音：{len(missing_voice)}",
            f"配音过期：{len(expired_voice)}",
        ]
        if missing_voice:
            lines += ["", "缺配音列表"]
            for item in missing_voice[:15]:
                lines.append(f"  - {item}")
            if len(missing_voice) > 15:
                lines.append(f"  ... 另有 {len(missing_voice) - 15} 个")
        if expired_voice:
            lines += ["", "配音过期列表"]
            for item in expired_voice[:15]:
                lines.append(f"  - {item}")
            if len(expired_voice) > 15:
                lines.append(f"  ... 另有 {len(expired_voice) - 15} 个")
        show_text_dialog(self, "配音检查结果", "\n".join(lines))

    def _finish_asset_sync(self, label: str, result: dict[str, Any], *, focus_type: str = "") -> None:
        if focus_type:
            count_text = f"{label} {result.get(focus_type, 0)}"
        else:
            count_text = f"图片 {result.get('image', 0)}，视频 {result.get('video', 0)}，配音 {result.get('voice', 0)}"
        self.toast(f"{label}素材同步完成：{count_text}，缺素材 {result.get('unmatched', 0)}")
        self.refresh()
        show_text_dialog(self, f"{label}素材同步结果", self._format_asset_sync_result(label, result, focus_type=focus_type))

    def _format_asset_sync_result(self, label: str, result: dict[str, Any], *, focus_type: str = "") -> str:
        type_labels = {"image": "图片", "video": "视频", "voice": "配音"}
        lines = [
            f"{label}素材同步结果",
            "",
            f"匹配成功：图片 {result.get('image', 0)}，视频 {result.get('video', 0)}，配音 {result.get('voice', 0)}",
            f"缺素材商品：{result.get('unmatched', 0)}",
        ]
        scanned_roots = result.get("scanned_roots") or {}
        if scanned_roots:
            lines += ["", "扫描目录"]
            for key, path in scanned_roots.items():
                lines.append(f"- {type_labels.get(key, key)}：{path}")
        matched_items = result.get("matched_items") or []
        if focus_type:
            matched_items = [item for item in matched_items if item.get("asset_type") == focus_type]
        lines += ["", f"匹配成功明细（{len(matched_items)}）"]
        if matched_items:
            for index, item in enumerate(matched_items, start=1):
                account = safe_text(item.get("account_label")) or "全局"
                title = safe_text(item.get("title"))
                title_part = f" {title}" if title else ""
                lines.append(f"{index}. [{type_labels.get(item.get('asset_type'), item.get('asset_type'))}] {item.get('uid')}{title_part} / {account}")
                lines.append(f"   {item.get('path')}")
        else:
            lines.append("无")
        unmatched_items = result.get("unmatched_items") or []
        if focus_type:
            unmatched_items = [item for item in unmatched_items if item.get("asset_type") == focus_type]
        lines += ["", f"缺素材商品（{len(unmatched_items)}）"]
        if unmatched_items:
            for index, item in enumerate(unmatched_items, start=1):
                uid = safe_text(item.get("uid"))
                title = safe_text(item.get("title"))
                asset_name = type_labels.get(item.get("asset_type"), item.get("asset_type"))
                lines.append(f"{index}. [{asset_name}] {uid} {title}".strip())
                message = safe_text(item.get("message"))
                if message:
                    lines.append(f"   {message}")
        else:
            lines.append("无")
        return "\n".join(lines)

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
            self.sync.sync_assets(project["id"], asset_type="image")
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
            uids, script_ids = parse_voice_targets(self.uid_var.get())
            return self.workflow.build_voice_command(
                project["id"],
                account_label=self.account_var.get().strip(),
                uids=uids or None,
                script_ids=script_ids or None,
            )
        if isinstance(self, AssemblePage):
            top_uids = parse_uid_list(self.uid_var.get())
            mode = "top" if self.mode_var.get().strip().startswith("Top") else "standard"
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
        if isinstance(self, VoicePage):
            self._run_voice_command()
            return
        if not self._confirm_precheck():
            return
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
                progress_dialog.finish(
                    self._success_dialog_message(),
                    kind="success",
                    headline=self._success_dialog_headline(),
                    detail=self._success_dialog_detail(),
                )
                self.toast("执行完成")
            else:
                progress_dialog.finish(
                    f"执行结束，退出码：{result.returncode}",
                    kind="warning",
                    headline="执行结束",
                )
                self.toast(f"执行结束，退出码：{result.returncode}", kind="warning", duration=4500)

        def on_error(exc: Exception, tb: str) -> None:
            progress_dialog.append(tb or str(exc))
            progress_dialog.finish(f"执行失败：{exc}", kind="error")
            messagebox.showerror("执行失败", str(exc))

        self.app.run_background("执行任务", work, on_success=on_success, on_error=on_error, show_success_toast=False)

    def _run_voice_command(self) -> None:
        project = self.project_required()
        if not project:
            return
        if not self._confirm_precheck():
            return

        account_label = self.account_var.get().strip()
        uids, script_ids = parse_voice_targets(self.uid_var.get())
        total_jobs, existing_jobs, pending_jobs = self.workflow.voice_generation_counts(
            project["id"],
            account_label=account_label,
            uids=uids or None,
            script_ids=script_ids or None,
        )
        if pending_jobs == 0:
            self.toast("所有文案已有 OK 配音，无需生成。", kind="info", duration=3000)
            return

        # 检查 TTS 服务状态，弹窗询问
        service_ok = self.workflow.is_tts_service_running(timeout=0.8)
        if service_ok:
            if not show_confirmation_dialog(
                self,
                "配音服务已就绪",
                "检测到本地配音服务正在运行。",
                [DialogSection(title="服务状态", step="1", tone="success", items=["本地配音服务已在运行，可以直接继续生成配音。"])],
                confirm_text="继续生成",
            ):
                self.toast("已取消本次配音生成。", kind="warning")
                return
        else:
            if not show_confirmation_dialog(
                self,
                "配音服务未启动",
                "检测到本地配音服务尚未启动。",
                [DialogSection(title="服务状态", step="1", tone="warning", items=["生成配音前需要先启动并预热服务。", "确认后系统会自动启动服务并继续执行。"])],
                confirm_text="启动并继续",
            ):
                self.toast("已取消本次配音生成。", kind="warning")
                return

        progress_dialog = TaskProgressDialog(self, "正在生成配音", "正在准备配音任务...")
        progress_dialog.append("配音参数：")
        progress_dialog.append(f"品类项目：{project['name']}")
        progress_dialog.append(f"配音用户：{account_label}")
        target_text = "全部文案" if not uids and not script_ids else "、".join(uids + script_ids)
        progress_dialog.append(f"生成范围：{target_text}")
        progress_dialog.append(f"本次文案：{total_jobs} 条；已有跳过：{existing_jobs} 条；待生成：{pending_jobs} 条")
        progress_dialog.append("")

        def append_progress(message: str) -> None:
            msg = safe_text(message)
            if msg.startswith("[服务检查]"):
                progress_dialog.status_var.set("正在检查并预热配音服务...")
            elif msg.startswith("[音色注册]"):
                progress_dialog.status_var.set("正在确认音色配置...")
            elif msg.startswith("[生成 "):
                progress_dialog.status_var.set("正在生成配音中...")
            elif msg.startswith("[成功]"):
                progress_dialog.status_var.set("正在写入并确认音频文件...")
            elif msg.startswith("[失败]"):
                progress_dialog.status_var.set("配音中存在失败条目，正在继续后续任务...")
            progress_dialog.append(msg)

        def progress_hook(message: str) -> None:
            self.after(0, lambda m=message: append_progress(m))

        def work() -> Any:
            return self.workflow.generate_voice(
                project["id"],
                account_label=account_label,
                uids=uids or None,
                script_ids=script_ids or None,
                start_service_if_needed=True,
                progress_hook=progress_hook,
            )

        def close_service() -> None:
            if not self.workflow.is_tts_service_running(timeout=0.8):
                return
            killed = self.workflow.shutdown_tts_service()
            if killed > 0:
                messagebox.showinfo("配音服务已关闭", f"配音已完成，已自动关闭配音服务（{killed} 个进程）。")

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
                progress_dialog.finish(
                    "本次配音已经完成，生成结果已写入目标目录。",
                    kind="success",
                    headline="配音生成完成",
                    detail=f"配音用户：{account_label}",
                )
                self.toast("配音完成")
            else:
                progress_dialog.finish(
                    f"配音结束，退出码：{result.returncode}",
                    kind="warning",
                    headline="配音执行结束",
                )
                self.toast(f"配音结束，退出码：{result.returncode}", kind="warning", duration=4500)
            close_service()

        def on_error(exc: Exception, tb: str) -> None:
            progress_dialog.append(tb or str(exc))
            progress_dialog.finish(f"执行失败：{exc}", kind="error")
            messagebox.showerror("执行失败", str(exc))
            close_service()

        self.app.run_background("生成配音", work, on_success=on_success, on_error=on_error, show_success_toast=False)

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
            return "通常需要几分钟。窗口会在执行结束后显示结果。"
        if isinstance(self, VoicePage):
            return "正在准备配音任务与服务状态，请等待当前任务结束后再继续操作。"
        if isinstance(self, AssemblePage):
            return "正在组合口播稿内容，请等待当前任务结束后再继续操作。"
        return "任务正在执行中，请等待当前任务结束后再继续操作。"

    def _success_dialog_headline(self) -> str:
        if isinstance(self, JianyingPage):
            return "剪映草稿生成成功"
        if isinstance(self, AssemblePage):
            return "口播稿组合完成"
        return "执行完成"

    def _success_dialog_message(self) -> str:
        if isinstance(self, JianyingPage):
            return "草稿已经写入输出目录，现在可以去剪映里打开。"
        if isinstance(self, AssemblePage):
            return "口播稿与 manifest 已生成完成，可以继续后续流程。"
        return "任务已经完成，可以关闭窗口。"

    def _success_dialog_detail(self) -> str:
        if isinstance(self, JianyingPage):
            draft_name = self.account_var.get().strip() or safe_text(self.project_required().get("name"))
            return f"草稿名称：{draft_name}"
        if isinstance(self, AssemblePage):
            output_path = self.spoken_md_var.get().strip()
            return f"输出文件：{output_path}" if output_path else ""
        return ""

    def _confirm_precheck(self) -> bool:
        project = self.project_required()
        if not project:
            return False
        if isinstance(self, VoicePage):
            sections, can_continue = self._voice_precheck(project)
            return show_precheck_dialog(
                self,
                "生成配音预检查",
                "请核对本次配音范围、已有配音状态与阻塞项，确认无误后再继续生成。",
                sections,
                can_continue=can_continue,
            )
        if isinstance(self, JianyingPage):
            sections, can_continue = self._jianying_precheck(project)
            return show_precheck_dialog(
                self,
                "生成剪映草稿预检查",
                "请核对以下配置信息，确认无误后再生成草稿。",
                sections,
                can_continue=can_continue,
            )
        if isinstance(self, AssemblePage):
            sections, can_continue = self._assembly_precheck(project)
            return show_precheck_dialog(
                self,
                "组合口播稿预检查",
                "请核对组合范围、素材缺口与阻塞问题，确认无误后再继续生成。",
                sections,
                can_continue=can_continue,
            )
        return True

    def _voice_precheck(self, project: dict[str, Any]) -> tuple[list[DialogSection], bool]:
        account_label = self.account_var.get().strip()
        selected_uids, selected_script_ids = parse_voice_targets(self.uid_var.get())
        products = {a["uid"]: a for a in self.repo.products(project["id"], include_removed=False)}
        blocks = self.repo.script_blocks(project["id"])
        assets = self.repo.asset_bindings(project["id"])
        selected = set(selected_uids)
        selected_scripts = {item.casefold() for item in selected_script_ids}
        unknown = [u for u in selected_uids if u not in products]
        unknown_scripts = [
            script_id
            for script_id in selected_script_ids
            if script_id.casefold() not in {safe_text(block.get("script_id")).casefold() for block in blocks}
        ]
        product_blocks = [
            b for b in blocks
            if b["script_type"] == "product"
            and (not selected or b["owner_uid"] in selected)
            and (not selected_scripts or safe_text(b.get("script_id")).casefold() in selected_scripts)
        ]
        shared_blocks = [
            b for b in blocks
            if b["script_type"] in {"intro", "price_transition"}
            and not selected
            and (not selected_scripts or safe_text(b.get("script_id")).casefold() in selected_scripts)
        ]
        pending, skipped, blocked = [], [], []
        for uid in selected_uids:
            if uid in products and not any(b["owner_uid"] == uid for b in product_blocks):
                blocked.append(f"{uid} {products[uid]['title']}：缺文案")
        for uid in unknown:
            blocked.append(f"{uid}：当前品类项目中没有这个商品")
        for script_id in unknown_scripts:
            blocked.append(f"{script_id}：当前品类项目中没有这个文案版本 ID")
        for b in product_blocks:
            prod = products.get(b["owner_uid"], {})
            display = f"{safe_text(b.get('script_id'))} / {b['owner_uid']} {safe_text(prod.get('title'))} / {b['block_label']}"
            state = voice_state(assets, uid=b["owner_uid"], account_label=account_label, hashes={b["text_hash"]}, block_label=safe_text(b.get("block_label")))
            (pending if state != "ready" else skipped).append(f"{display}：{'配音过期，将重生成' if state == 'expired' else '缺配音，将生成'}" if state != "ready" else f"{display}：已有配音")
        for b in shared_blocks:
            uid = "INTRO" if b["script_type"] == "intro" else "PRICE_TRANSITION"
            label = safe_text(b.get("block_label")) if uid == "INTRO" else safe_text(b.get("price_range_label"))
            kind_label = "引言文案" if uid == "INTRO" else f"价格过渡 {safe_text(b.get('price_range_label'))}"
            display = f"{safe_text(b.get('script_id'))} / {kind_label} / {b['block_label']}"
            state = voice_state(assets, uid=uid, account_label=account_label, hashes={b["text_hash"]}, block_label=label)
            (pending if state != "ready" else skipped).append(f"{display}：{'配音过期，将重生成' if state == 'expired' else '缺配音，将生成'}" if state != "ready" else f"{display}：已有配音")
        selected_text = "全部文案" if not selected_uids and not selected_script_ids else "、".join(selected_uids + selected_script_ids)
        sections = [
            DialogSection(
                title="项目信息",
                step="1",
                tone="primary",
                rows=[
                    ("项目", project["name"]),
                    ("配音用户", account_label or "未选择"),
                    ("生成范围", selected_text),
                ],
            ),
            DialogSection(
                title="执行统计",
                step="2",
                tone="success",
                rows=[
                    ("待生成 / 重生成", f"{len(pending)} 条"),
                    ("已有配音跳过", f"{len(skipped)} 条"),
                    ("缺文案 / 不可处理", f"{len(blocked)} 条"),
                ],
                helper="确认后会先执行底层脚本；已有配音由脚本继续跳过，缺失和过期会重新生成。",
            ),
            DialogSection(
                title="待生成明细",
                step="3",
                tone="info",
                items=preview_lines(pending),
            ),
            DialogSection(
                title="已有跳过",
                step="4",
                tone="info",
                items=preview_lines(skipped),
            ),
            DialogSection(
                title="阻塞与缺口",
                step="5",
                tone="warning" if blocked else "success",
                items=preview_lines(blocked),
                helper="" if blocked else "当前没有发现阻塞项，可以继续生成配音。",
            ),
        ]
        return sections, bool(account_label) and bool(pending or skipped or blocked)

    def _assembly_precheck(self, project: dict[str, Any]) -> tuple[list[DialogSection], bool]:
        blocks = self.repo.script_blocks(project["id"])
        assets = self.repo.asset_bindings(project["id"])
        top_uids = parse_uid_list(self.uid_var.get())
        account_label = self.account_var.get().strip()
        mode = "top" if self.mode_var.get().strip().startswith("Top") else "standard"
        products = self.workflow._ordered_products(project["id"], mode=mode, top_uids=top_uids, product_uids=[])
        product_blocks = [block for block in blocks if block["script_type"] == "product"]
        intro_blocks = [block for block in blocks if block["script_type"] == "intro"]
        price_blocks = [block for block in blocks if block["script_type"] == "price_transition"]
        product_block_uids = {safe_text(block.get("owner_uid")).casefold() for block in product_blocks}
        missing_top = [uid for uid in top_uids if uid.casefold() not in product_block_uids]
        product_blocks_by_uid: dict[str, list[dict[str, Any]]] = {}
        for block in product_blocks:
            product_blocks_by_uid.setdefault(safe_text(block.get("owner_uid")), []).append(block)
        top_set = {uid.casefold() for uid in top_uids}
        ordered_blocks: list[tuple[dict[str, Any], dict[str, Any], bool]] = []
        used_price_labels: set[str] = set()
        used_price_blocks: list[dict[str, Any]] = []
        for product in products:
            uid = safe_text(product.get("uid"))
            is_top_product = uid.casefold() in top_set
            if not is_top_product:
                price_block = self.workflow._matching_price_block_for_assets(product, price_blocks, assets, account_label=account_label)
                if price_block:
                    price_key = safe_text(price_block.get("price_range_label")) or str(price_block["id"])
                    if price_key not in used_price_labels:
                        used_price_labels.add(price_key)
                        used_price_blocks.append(price_block)
            versions = product_blocks_by_uid.get(uid, [])
            if versions:
                block = self.workflow._choose_voice_ready_block(versions, assets, uid=uid, account_label=account_label) or versions[0]
                ordered_blocks.append((block, product, is_top_product))
        top_product_blocks = [item for item in ordered_blocks if item[2]]
        other_product_blocks = [item for item in ordered_blocks if not item[2]]
        selected_products = [product for product in products if safe_text(product.get("uid")) in product_blocks_by_uid]
        expected_blocks = min(1, len(intro_blocks)) + len(used_price_blocks) + len(ordered_blocks)
        missing_voice = []
        missing_image = []
        missing_video = []
        for block, product, _is_top_product in ordered_blocks:
            uid = safe_text(block.get("owner_uid"))
            label = f"{uid} {safe_text(product.get('title'))}".strip()
            if voice_state(assets, uid=uid, account_label=account_label, hashes={safe_text(block.get("text_hash"))}) != "ready":
                missing_voice.append(label)
            if not has_ready_asset(assets, uid=uid, asset_type="image"):
                missing_image.append(label)
            if not has_ready_asset(assets, uid=uid, asset_type="video"):
                missing_video.append(label)
        selected_intro = []
        if intro_blocks:
            intro_index = max(1, int(self.intro_var.get() or "1"))
            selected_intro = [intro_blocks[min(intro_index, len(intro_blocks)) - 1]]
        for block in selected_intro:
            if voice_state(
                assets,
                uid="INTRO",
                account_label=account_label,
                hashes={safe_text(block.get("text_hash"))},
                block_label=safe_text(block.get("block_label")),
            ) != "ready":
                missing_voice.append("引言文案")
        for block in used_price_blocks:
            if voice_state(
                assets,
                uid="PRICE_TRANSITION",
                account_label=account_label,
                hashes={safe_text(block.get("text_hash"))},
                block_label=safe_text(block.get("price_range_label")),
            ) != "ready":
                missing_voice.append(f"价格过渡 {safe_text(block.get('price_range_label'))}")
        output_path = self._remember_spoken_md(project["id"]) if self.spoken_md_var.get().strip() else safe_text(project.get("spoken_md_path"))
        top_titles = [
            f"{safe_text(product.get('uid'))} {safe_text(product.get('title'))}".strip()
            for _block, product, _is_top in top_product_blocks
        ]
        other_preview = [
            f"{safe_text(product.get('uid'))} {safe_text(product.get('title'))}".strip()
            for _block, product, _is_top in other_product_blocks[:5]
        ]
        blockers: list[str] = []
        if not output_path:
            blockers.append("还没有选择口播稿输出 MD。")
        if not account_label:
            blockers.append("还没有选择口播用户。")
        if not blocks:
            blockers.append("当前项目还没有同步到任何文案块。请先到“同步中心”同步商品文案 MD。")
        if top_uids and not top_product_blocks:
            blockers.append("填写的 Top UID 没有匹配到任何商品文案。")
        if missing_top:
            blockers.append(f"这些 Top UID 没有对应文案：{'、'.join(missing_top)}")
        hit_items: list[str] = []
        if top_titles:
            hit_items.append("Top 优先：" + "；".join(top_titles[:6]))
        if other_preview:
            suffix = f"；另有 {len(other_product_blocks) - len(other_preview)} 条" if len(other_product_blocks) > len(other_preview) else ""
            hit_items.append("其余继续组合：" + "；".join(other_preview) + suffix)
        gap_items: list[str] = []
        if missing_voice:
            gap_items.append("缺配音：" + "；".join(missing_voice[:5]))
        if missing_image:
            gap_items.append("缺图片：" + "；".join(missing_image[:5]))
        if missing_video:
            gap_items.append("缺视频：" + "；".join(missing_video[:5]))
        sections = [
            DialogSection(
                title="项目信息",
                step="1",
                tone="primary",
                rows=[
                    ("项目", project["name"]),
                    ("用户", account_label or "未选择"),
                    ("模式", self.mode_var.get()),
                    ("Top 商品", "、".join(top_uids) if top_uids else "未填写，将使用全部商品"),
                    ("输出 MD", output_path or "未选择"),
                ],
            ),
            DialogSection(
                title="组合范围",
                step="2",
                tone="success",
                rows=[
                    ("预计段落", f"约 {expected_blocks + 1} 段（引言 {len(selected_intro)}，价格过渡 {len(used_price_blocks)}，商品文案 {len(ordered_blocks)}，结尾 1）"),
                    ("商品范围", f"共 {len(selected_products)} 个；Top 命中文案 {len(top_product_blocks)} 条；其他商品文案 {len(other_product_blocks)} 条"),
                    ("素材缺口", f"缺配音 {len(missing_voice)}，缺图片 {len(missing_image)}，缺视频 {len(missing_video)}"),
                ],
                items=hit_items or ["无"],
            ),
            DialogSection(
                title="缺口示例",
                step="3",
                tone="warning" if gap_items else "success",
                items=gap_items or ["当前没有明显素材缺口。"],
            ),
            DialogSection(
                title="阻塞问题",
                step="4",
                tone="warning" if blockers else "success",
                items=blockers or ["可以继续组合口播稿。"],
            ),
        ]
        return sections, not blockers

    def _jianying_precheck(self, project: dict[str, Any]) -> tuple[list[DialogSection], bool]:
        path_text = self.spoken_md_var.get().strip() or safe_text(project.get("spoken_md_path"))
        if not path_text:
            return [
                DialogSection(
                    title="阻塞问题",
                    step="1",
                    tone="error",
                    items=["还没有选择口播稿 MD。", "请先在“组合口播稿”生成口播稿和 manifest。"],
                )
            ], False
        spoken_path = Path(path_text)
        manifest = self.workflow.spoken_manifest_path(project["id"], spoken_path)
        intro_video_text = self.intro_video_var.get().strip()
        intro_video_path = Path(intro_video_text) if intro_video_text else None
        missing_manifest = not manifest.exists()
        bg_dir = Path(r"G:\2026项目-b站\素材-剪辑\1-背景图")
        bg_images = list(bg_dir.glob("*")) if bg_dir.exists() else []
        bg_image_suffixes = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
        has_bg = any(p.is_file() and p.suffix.casefold() in bg_image_suffixes for p in bg_images)
        products = self.repo.products(project["id"], include_removed=False)
        blocks = self.repo.script_blocks(project["id"])
        assets = self.repo.asset_bindings(project["id"])
        missing_files, missing_product_videos = [], []
        manifest_error = ""
        selected_user = "全部"
        payload: dict[str, Any] = {}
        entries: list[dict[str, Any]] = []
        missing_by_type: dict[str, list[str]] = {"audio": [], "image": [], "video": []}
        if manifest.exists():
            try:
                payload = json.loads(manifest.read_text(encoding="utf-8-sig"))
                entries = manifest_entries(payload)
                selected_user = manifest_account_label(payload) or selected_user
                missing_product_videos = manifest_product_video_gaps(payload)
                missing_by_type = manifest_missing_assets(payload)
                for p in manifest_file_paths(payload):
                    if not Path(p).exists():
                        missing_files.append(p)
            except Exception as exc:
                manifest_error = str(exc)
        issues = build_project_issue_summary(project, products, blocks, assets, self.repo.accounts(), selected_user=selected_user)
        entry_counts = {
            "transition": sum(1 for item in entries if safe_text(item.get("type")) == "transition"),
            "product": sum(1 for item in entries if safe_text(item.get("type")) == "product"),
            "closing": sum(1 for item in entries if safe_text(item.get("type")) == "closing"),
        }
        product_names = [
            " ".join(part for part in [safe_text(item.get("product_uid")), safe_text(item.get("product_name"))] if part)
            for item in entries
            if safe_text(item.get("type")) == "product"
        ]
        result_items: list[str] = []
        if not has_bg:
            result_items.append("背景图：缺失，G:\\2026项目-b站\\素材-剪辑\\1-背景图 目录下没有可用图片。")
        if missing_manifest:
            result_items.append("manifest：缺失，还没有组合口播稿，不能生成剪映草稿。")
        else:
            result_items.append("manifest：已找到")
        if manifest_error:
            result_items.append(f"manifest 读取失败：{manifest_error}")
        if intro_video_path is not None:
            result_items.append(f"引言成片视频：{'已找到' if intro_video_path.exists() else f'不存在：{intro_video_path}'}")
        if missing_files:
            result_items.append(f"manifest 中有 {len(missing_files)} 个文件路径不存在")
        if missing_by_type["audio"]:
            result_items.append(f"manifest 中有 {len(missing_by_type['audio'])} 条音频路径缺失")
        if missing_by_type["image"]:
            result_items.append(f"manifest 中有 {len(missing_by_type['image'])} 条图片路径缺失；会尝试用数据库素材或兜底图处理")
        if missing_by_type["video"]:
            result_items.append(f"manifest 中有 {len(missing_by_type['video'])} 条视频路径缺失；商品展示视频缺失时会用商品图兜底")
        if missing_product_videos:
            result_items.append(f"{len(missing_product_videos)} 个商品没有展示视频，将用商品图兜底")
        missing_examples: list[str] = []
        for label, items in (
            ("缺音频", missing_by_type["audio"]),
            ("缺图片", missing_by_type["image"]),
            ("缺视频", missing_by_type["video"]),
        ):
            if items:
                missing_examples.append(f"{label}：{items[0]}")
                if len(missing_examples) >= 6:
                    break
        if missing_product_videos:
            missing_examples.extend(missing_product_videos[: max(0, 6 - len(missing_examples))])
        sections = [
            DialogSection(
                title="项目信息",
                step="1",
                tone="primary",
                rows=[
                    ("项目", f"{project['name']} / 用户：{selected_user}"),
                    ("口播稿", str(spoken_path)),
                    ("草稿名", self.account_var.get().strip() or "未填写"),
                    ("草稿输出", DEFAULT_JIANYING_DRAFT_ROOT),
                ],
            ),
            DialogSection(
                title="素材使用",
                step="2",
                tone="success",
                rows=[
                    ("使用包", f"{len(entries)} 条 manifest（商品 {entry_counts['product']}，过渡/引言 {entry_counts['transition']}，结尾 {entry_counts['closing']}）"),
                    ("商品示例", "；".join(product_names[:6]) if product_names else "无"),
                    ("引言视频", str(intro_video_path) if intro_video_path else "未选择，将使用 manifest 内的引言配音"),
                    ("缺失文件", f"音频 {len(missing_by_type['audio'])}，图片 {len(missing_by_type['image'])}，视频 {len(missing_by_type['video'])}"),
                ],
                items=missing_examples or ["当前没有可见的缺失文件示例。"],
            ),
            DialogSection(
                title="检查结果",
                step="3",
                tone="warning" if (missing_manifest or manifest_error or missing_by_type['audio']) else "success",
                items=result_items,
            ),
            DialogSection(
                title="数据库缺口",
                step="4",
                tone="info",
                rows=[
                    ("缺图片", str(len(issues["missing_image"]))),
                    ("缺视频", str(len(issues["missing_video"]))),
                    ("缺配音", str(len(issues["missing_voice"]))),
                    ("配音过期", str(len(issues["expired_voice"]))),
                ],
            ),
        ]
        can_continue = (
            not missing_manifest
            and not manifest_error
            and not missing_by_type["audio"]
            and has_bg
            and (intro_video_path is None or intro_video_path.exists())
        )
        return sections, can_continue

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

        ctk.CTkLabel(form, text="商品UID / 文案ID（可不填）", font=UIStyle.FONT_BODY, text_color=UIStyle.COLOR_TEXT_DIM).grid(
            row=0, column=2, sticky="w", padx=(UIStyle.PAD_LG, UIStyle.PAD_SM), pady=(UIStyle.PAD_LG, UIStyle.PAD_SM)
        )
        AppEntry(form, textvariable=self.uid_var).grid(
            row=0, column=3, sticky="ew", padx=(0, UIStyle.PAD_LG), pady=(UIStyle.PAD_LG, UIStyle.PAD_SM)
        )

        ctk.CTkLabel(
            form,
            text="留空处理全部文案；填商品 UID 会处理该商品全部版本；填 script_id 只处理指定文案版本，多个值用逗号分隔。",
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
    "品类项目": ProjectPageDialog,
    "文案中心": CopyPage,
    "资产中心": AssetPage,
    "同步中心": SyncPage,
    "用户管理": AccountPage,
    "生成配音": VoicePage,
    "组合口播稿": AssemblePage,
    "生成剪映草稿": JianyingPage,
}
