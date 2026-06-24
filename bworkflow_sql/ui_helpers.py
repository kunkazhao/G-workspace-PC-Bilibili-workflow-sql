from __future__ import annotations

import os
import re
import tkinter as tk
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any

import customtkinter as ctk

from .asset_paths import project_category_folder, voice_user_dir
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
from .settings import (
    DEFAULT_SPOKEN_MD_ROOT,
)
from .style_config import UIStyle
from .sync_service import AUDIO_SUFFIXES
from .template_config import image_set_for_template
from .utils import compact_path, safe_text, text_hash
from .workflow_service import (
    VOICE_PROVIDER_INDEXTTS,
    account_voice_id_for_provider,
)


@dataclass
class DialogSection:
    title: str
    step: str = ""
    tone: str = "default"
    rows: list[tuple[str, str]] = field(default_factory=list)
    items: list[str] = field(default_factory=list)
    helper: str = ""


@dataclass
class VoiceTaskDraft:
    project_id: int
    project_name: str
    account_label: str
    target_text: str = ""
    output_dir: str = ""
    voice_provider: str = VOICE_PROVIDER_INDEXTTS

    @property
    def display_target(self) -> str:
        return self.target_text.strip() or "全部文案"


def project_selector_value(project: dict[str, Any] | None) -> str:
    if not project:
        return ""
    return safe_text(project.get("name"))


def project_id_from_selector_value(value: str) -> int | None:
    text = safe_text(value)
    if not text:
        return None
    head = text.split(" - ", 1)[0].strip()
    try:
        return int(head)
    except ValueError:
        return None


def project_name_exists(projects: list[dict[str, Any]], name: str, *, exclude_project_id: int | None = None) -> bool:
    target = safe_text(name).casefold()
    if not target:
        return False
    for project in projects:
        if exclude_project_id is not None and int(project.get("id") or 0) == int(exclude_project_id):
            continue
        if safe_text(project.get("name")).casefold() == target:
            return True
    return False


def account_labels_for_voice_provider(accounts: list[dict[str, Any]], provider: str) -> list[str]:
    labels: list[str] = []
    for account in accounts:
        label = safe_text(account.get("label"))
        if not label or not int(account.get("enabled") or 0):
            continue
        if account_voice_id_for_provider(account, provider):
            labels.append(label)
    return labels


_TREEVIEW_STYLE_READY = False


def configure_treeview_style(master: tk.Misc | None = None, *, force: bool = False) -> None:
    global _TREEVIEW_STYLE_READY
    if _TREEVIEW_STYLE_READY and not force:
        return
    style = ttk.Style(master)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    try:
        style.layout("CTreeview")
    except tk.TclError:
        style.layout("CTreeview", style.layout("Treeview"))
    style.configure(
        "CTreeview",
        background=UIStyle.COLOR_TABLE_ROW,
        foreground=UIStyle.COLOR_TEXT_MAIN,
        fieldbackground=UIStyle.COLOR_TABLE_ROW,
        font=UIStyle.FONT_TABLE,
        rowheight=28,
    )
    try:
        style.layout("CTreeview.Heading")
    except tk.TclError:
        try:
            style.layout("CTreeview.Heading", style.layout("Treeview.Heading"))
        except tk.TclError:
            pass
    style.configure(
        "CTreeview.Heading",
        background=UIStyle.COLOR_TABLE_HEADER,
        foreground=UIStyle.COLOR_TEXT_MAIN,
        font=UIStyle.FONT_TABLE,
        relief="flat",
    )
    style.map(
        "CTreeview",
        background=[("selected", UIStyle.COLOR_TABLE_SELECTED)],
        foreground=[("selected", UIStyle.COLOR_TEXT_MAIN)],
    )
    style.map("CTreeview.Heading", background=[("active", UIStyle.COLOR_NAV_HOVER)])
    _TREEVIEW_STYLE_READY = True


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
    image_template: str = "",
) -> dict[str, list[str]]:
    return build_project_gap_details(
        project,
        products,
        blocks,
        assets,
        accounts,
        selected_user=selected_user,
        image_template=image_template,
    )


def selected_account_labels(accounts: list[dict[str, Any]], selected_user: str) -> list[str]:
    labels = [safe_text(item.get("label")) for item in accounts if safe_text(item.get("label"))]
    if selected_user == "全部":
        return labels
    return [selected_user] if selected_user else []


def voice_block_uid(block: dict[str, Any]) -> str:
    script_type = safe_text(block.get("script_type"))
    if script_type == "intro":
        return "INTRO"
    if script_type == "price_transition":
        return "PRICE_TRANSITION"
    return safe_text(block.get("owner_uid"))


def voice_block_match_label(block: dict[str, Any]) -> str:
    if safe_text(block.get("script_type")) == "price_transition":
        return safe_text(block.get("price_range_label"))
    return safe_text(block.get("block_label"))


def voice_block_display(block: dict[str, Any], products_by_uid: dict[str, dict[str, Any]] | None = None) -> str:
    products_by_uid = products_by_uid or {}
    script_type = safe_text(block.get("script_type"))
    block_label = safe_text(block.get("block_label")) or "正文"
    if script_type == "intro":
        return f"引言 {block_label}"
    if script_type == "price_transition":
        price = safe_text(block.get("price_range_label")) or "未分组"
        return f"价格过渡 {price} / {block_label}"
    uid = safe_text(block.get("owner_uid"))
    title = safe_text((products_by_uid.get(uid) or {}).get("title"))
    return " ".join(part for part in [uid, title, block_label] if part)


def collect_voice_status(
    blocks: list[dict[str, Any]],
    assets: list[dict[str, Any]],
    accounts: list[dict[str, Any]],
    products_by_uid: dict[str, dict[str, Any]],
    *,
    selected_user: str,
) -> dict[str, Any]:
    labels = selected_account_labels(accounts, selected_user)
    rows: list[dict[str, str]] = []
    for account_label in labels:
        for block in blocks:
            uid = voice_block_uid(block)
            if not uid:
                continue
            display = voice_block_display(block, products_by_uid)
            state = voice_state(
                assets,
                uid=uid,
                account_label=account_label,
                hashes={safe_text(block.get("text_hash"))},
                block_label=voice_block_match_label(block),
            )
            rows.append(
                {
                    "account_label": account_label,
                    "uid": uid,
                    "display": display,
                    "block_label": voice_block_match_label(block),
                    "script_id": safe_text(block.get("script_id")),
                    "script_type": safe_text(block.get("script_type")),
                    "script_block_id": str(block.get("id") or ""),
                    "state": state,
                }
            )
    total = len(rows)
    missing = [row for row in rows if row["state"] == "missing"]
    missing_file = [row for row in rows if row["state"] == "missing_file"]
    expired = [row for row in rows if row["state"] == "expired"]
    ready = total - len(missing) - len(missing_file) - len(expired)
    return {
        "total": total,
        "ready": ready,
        "missing": missing,
        "missing_file": missing_file,
        "expired": expired,
        "rows": rows,
    }


def voice_inventory_stats(
    blocks: list[dict[str, Any]],
    assets: list[dict[str, Any]],
    *,
    account_label: str,
    directory: str | Path,
) -> dict[str, int]:
    blocks_by_id = {int(block.get("id") or 0): block for block in blocks if int(block.get("id") or 0)}
    valid_paths_by_block: dict[int, set[str]] = {}
    for asset in assets:
        if safe_text(asset.get("asset_type")) != "voice" or safe_text(asset.get("status")) != "ready":
            continue
        if account_label and account_label != "全部" and safe_text(asset.get("account_label")) != account_label:
            continue
        block_id = int(asset.get("script_block_id") or 0)
        block = blocks_by_id.get(block_id)
        if not block or safe_text(asset.get("text_hash")) != safe_text(block.get("text_hash")):
            continue
        path_text = safe_text(asset.get("path"))
        if not path_text or not Path(path_text).exists():
            continue
        valid_paths_by_block.setdefault(block_id, set()).add(str(Path(path_text)))

    valid_paths = {path for paths in valid_paths_by_block.values() for path in paths}
    duplicate_files = sum(max(0, len(paths) - 1) for paths in valid_paths_by_block.values())
    folder = Path(directory) if safe_text(directory) else None
    directory_paths = {
        str(path)
        for path in folder.rglob("*")
        if folder and folder.exists() and path.is_file() and path.suffix.casefold() in AUDIO_SUFFIXES
    } if folder else set()
    return {
        "valid_files": len(valid_paths),
        "duplicate_files": duplicate_files,
        "directory_files": len(directory_paths),
        "untracked_files": len(directory_paths - valid_paths),
    }


def voice_generation_targets_from_rows(rows: list[dict[str, str]]) -> list[str]:
    product_uids: list[str] = []
    script_ids: list[str] = []
    seen_products: set[str] = set()
    seen_scripts: set[str] = set()
    for row in rows:
        uid = safe_text(row.get("uid"))
        script_id = safe_text(row.get("script_id"))
        script_type = safe_text(row.get("script_type"))
        if script_type == "product":
            if uid and uid not in seen_products:
                product_uids.append(uid)
                seen_products.add(uid)
            continue
        if script_id and script_id not in seen_scripts:
            script_ids.append(script_id)
            seen_scripts.add(script_id)
    return product_uids + script_ids


def voice_row_choice_label(row: dict[str, Any]) -> str:
    state_label = {"missing": "缺配音", "missing_file": "文件丢失", "expired": "配音过期", "ready": "已就绪"}.get(
        safe_text(row.get("state")),
        safe_text(row.get("state")),
    )
    return " / ".join(
        part
        for part in [
            safe_text(row.get("account_label")),
            safe_text(row.get("display")),
            state_label,
        ]
        if part
    )


def build_project_gap_details(
    project: dict[str, Any],
    products: list[dict[str, Any]],
    blocks: list[dict[str, Any]],
    assets: list[dict[str, Any]],
    accounts: list[dict[str, Any]],
    *,
    selected_user: str = "全部",
    image_template: str = "",
) -> dict[str, list[str]]:
    product_blocks: dict[str, list[dict[str, Any]]] = {}
    for block in blocks:
        if block["script_type"] == "product":
            product_blocks.setdefault(block["owner_uid"], []).append(block)
    issues: dict[str, list[str]] = {"missing_copy": [], "missing_image": [], "missing_video": [], "missing_voice": [], "expired_voice": []}
    image_account = "" if selected_user == "全部" else selected_user
    image_template_suffix = image_set_for_template(image_template)
    products_by_uid = {safe_text(item.get("uid")): item for item in products}
    for product in products:
        uid = product["uid"]
        title = product["title"]
        display = f"{uid} {title}"
        if uid not in product_blocks:
            issues["missing_copy"].append(display)
        if not has_ready_asset(
            assets,
            uid=uid,
            asset_type="image",
            account_label=image_account,
            path_contains=image_template_suffix,
            allow_global_account=not bool(image_template_suffix),
        ):
            issues["missing_image"].append(display)
        if not has_ready_asset(assets, uid=uid, asset_type="video"):
            issues["missing_video"].append(display)
    voice_status = collect_voice_status(
        blocks,
        assets,
        accounts,
        products_by_uid,
        selected_user=selected_user,
    )
    for row in voice_status["missing"] + (voice_status.get("missing_file") or []):
        issues["missing_voice"].append(f"{row['account_label']} / {row['display']}")
    for row in voice_status["expired"]:
        issues["expired_voice"].append(f"{row['account_label']} / {row['display']}")
    return issues


def has_ready_asset(
    assets: list[dict[str, Any]],
    *,
    uid: str,
    asset_type: str,
    account_label: str = "",
    block_label: str = "",
    path_contains: str = "",
    allow_global_account: bool = True,
) -> bool:
    return any(
        asset["uid"] == uid
        and asset["asset_type"] == asset_type
        and asset["status"] == "ready"
        and safe_text(asset.get("path"))
        and Path(safe_text(asset.get("path"))).is_file()
        and (not account_label or asset["account_label"] == account_label or (allow_global_account and not asset["account_label"]))
        and (not block_label or asset["block_label"] == block_label)
        and (not path_contains or path_contains in safe_text(asset.get("path")))
        for asset in assets
    )


def voice_state(assets: list[dict[str, Any]], *, uid: str, account_label: str, hashes: set[str], block_label: str = "") -> str:
    def path_available(asset: dict[str, Any]) -> bool:
        if "path" not in asset:
            return True
        path_text = safe_text(asset.get("path"))
        return bool(path_text and Path(path_text).exists())

    matching_uid = [
        asset
        for asset in assets
        if asset["uid"] == uid
        and asset["asset_type"] == "voice"
        and asset["status"] == "ready"
        and (not account_label or asset["account_label"] == account_label)
        and (not block_label or asset["block_label"] == block_label)
    ]
    if not matching_uid:
        return "missing"
    if hashes:
        # 有文案 hash 可对比：必须 hash 匹配才算 ready
        if any(safe_text(asset.get("text_hash")) in hashes and path_available(asset) for asset in matching_uid):
            return "ready"
        # 存在 ready 记录且对应文件仍在，但 hash 都不匹配文案 → 过期
        if any(safe_text(asset.get("text_hash")) and path_available(asset) for asset in matching_uid):
            return "expired"
        return "missing_file"
    # 没有文案 hash 可对比（旧数据），有 ready 记录就算可用
    return "ready" if any(path_available(asset) for asset in matching_uid) else "missing_file"


def split_missing_voice_rows_by_removed_assets(
    missing_rows: list[dict[str, str]],
    removed_items: list[dict[str, Any]],
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    removed_by_script: set[tuple[str, str]] = set()
    removed_by_block: set[tuple[str, str, str]] = set()
    for item in removed_items:
        if safe_text(item.get("asset_type")) != "voice":
            continue
        account = safe_text(item.get("account_label"))
        script_block_id = safe_text(item.get("script_block_id"))
        uid = safe_text(item.get("uid"))
        block_label = safe_text(item.get("block_label"))
        if account and script_block_id:
            removed_by_script.add((account, script_block_id))
        if account and uid:
            removed_by_block.add((account, uid, block_label))

    still_missing: list[dict[str, str]] = []
    missing_file: list[dict[str, str]] = []
    for row in missing_rows:
        account = safe_text(row.get("account_label"))
        script_block_id = safe_text(row.get("script_block_id"))
        block_key = (account, safe_text(row.get("uid")), safe_text(row.get("block_label")))
        if (account, script_block_id) in removed_by_script or block_key in removed_by_block:
            moved = dict(row)
            moved["state"] = "missing_file"
            missing_file.append(moved)
        else:
            still_missing.append(row)
    return still_missing, missing_file


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


def asset_folder_paths(project: dict[str, Any], assets: list[dict[str, Any]], selected_user: str, image_template: str = "") -> dict[str, str]:
    category_hint = project_category_folder(project)
    image_template_suffix = image_set_for_template(image_template)
    image_dir = _asset_common_dir(
        assets,
        asset_type="image",
        selected_user=selected_user,
        fallback=safe_text(project.get("image_root")),
        category_hint=category_hint,
    )
    if selected_user != "全部" and image_template_suffix:
        image_root = safe_text(project.get("image_root"))
        if image_root and category_hint:
            image_dir = str(Path(image_root) / category_hint / selected_user / image_template_suffix)
    video_root = safe_text(project.get("video_root"))
    video_dir = str(Path(video_root) / category_hint) if video_root and category_hint else video_root
    voice_root = safe_text(project.get("voice_root"))
    voice_dir = ""
    if selected_user != "全部" and voice_root:
        voice_expected = voice_user_dir(voice_root, project, selected_user)
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


def safe_file_component(value: str, fallback: str = "未命名") -> str:
    text = safe_text(value).strip()
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", text)
    text = re.sub(r"\s+", " ", text).strip(" .")
    return text or fallback


def is_valid_windows_filename(value: str) -> bool:
    text = safe_text(value).strip()
    return bool(text and Path(text).name == text and not re.search(r'[<>:"/\\|?*\x00-\x1f]', text))


def default_spoken_markdown_path(project: dict[str, Any], account_label: str = "") -> Path:
    project_name = safe_file_component(
        safe_text(project.get("name")) or safe_text(project.get("category_name")),
        "品类名称",
    )
    user_label = safe_file_component(account_label, "用户")
    return DEFAULT_SPOKEN_MD_ROOT / project_name / f"6月-{user_label}.md"


def is_default_spoken_markdown_path(path_text: str) -> bool:
    text = safe_text(path_text).strip().casefold()
    if not text:
        return True
    return text.startswith(str(DEFAULT_SPOKEN_MD_ROOT).casefold())


def account_label_from_spoken_path(path_text: str) -> str:
    stem = Path(path_text).stem if safe_text(path_text) else ""
    match = re.match(r"^6月-(?P<label>.+)$", stem)
    return safe_text(match.group("label")) if match else ""


def default_jianying_draft_name(project: dict[str, Any], account_label: str = "") -> str:
    category = safe_file_component(
        safe_text(project.get("category_name")) or safe_text(project.get("name")),
        "品类",
    )
    user = safe_file_component(account_label, "用户")
    return f"完整-{category}-{user}"


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
    parent = getattr(dialog, "_parent_toplevel", None)
    if parent is None:
        master = getattr(dialog, "master", None)
        parent = master.winfo_toplevel() if master is not None else None

    try:
        if parent is not None and parent.winfo_exists():
            parent.update_idletasks()
            parent_width = parent.winfo_width()
            parent_height = parent.winfo_height()
            if parent_width > 1 and parent_height > 1:
                x = parent.winfo_rootx() + (parent_width - dialog.winfo_width()) // 2
                y = parent.winfo_rooty() + (parent_height - dialog.winfo_height()) // 2
                dialog.geometry(f"+{max(0, x)}+{max(0, y)}")
                return
    except tk.TclError:
        pass

    x = dialog.winfo_screenwidth() // 2 - dialog.winfo_width() // 2
    y = dialog.winfo_screenheight() // 2 - dialog.winfo_height() // 2
    dialog.geometry(f"+{max(0, x)}+{max(0, y)}")


def _restore_window(win: tk.Misc) -> None:
    """将窗口恢复到前台并获取焦点（修复 Windows 任务栏图标点击无响应问题）。"""
    try:
        win.deiconify()       # 如果被最小化则恢复
        win.lift()            # 提升 Z 序
        win.focus_force()     # 强制获取焦点
    except Exception:
        pass


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

    if not can_continue and confirm_text == dismiss_text:
        GhostButton(buttons, text=dismiss_text, command=lambda: close(False), width=140).grid(row=0, column=2)
    else:
        GhostButton(buttons, text=dismiss_text, command=lambda: close(False)).grid(row=0, column=1, padx=(0, UIStyle.PAD_SM))
        if can_continue:
            PrimaryButton(buttons, text=confirm_text, command=lambda: close(True)).grid(row=0, column=2)
        else:
            blocked_text = confirm_text if confirm_text != "确认继续" else "修正后再继续"
            ctk.CTkLabel(
                buttons,
                text="存在阻塞项，请先按检查结果修正。",
                font=UIStyle.FONT_SMALL,
                text_color=UIStyle.COLOR_TEXT_DIM,
            ).grid(row=0, column=0, sticky="w")
            PrimaryButton(buttons, text=blocked_text, state="disabled").grid(row=0, column=2)
    dialog.protocol("WM_DELETE_WINDOW", lambda: close(False))
    _center_dialog(dialog)
    dialog.wait_window()
    return result["ok"]


def show_action_sections_dialog(
    parent: tk.Widget,
    title: str,
    subtitle: str,
    sections: list[DialogSection],
    *,
    action_text: str,
    action_enabled: bool,
    secondary_action_text: str = "",
    secondary_action_enabled: bool = False,
    close_text: str = "关闭",
) -> str:
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
    for section in sections:
        card = _build_dialog_section(body, section)
        card.pack(fill="x", pady=(0, UIStyle.PAD_MD))

    buttons = ctk.CTkFrame(dialog, fg_color="transparent")
    buttons.grid(row=1, column=0, sticky="ew", padx=UIStyle.PAD_XL, pady=(0, UIStyle.PAD_XL))
    buttons.columnconfigure(0, weight=1)
    result = {"action": "close"}

    def close(action: str) -> None:
        result["action"] = action
        dialog.destroy()

    column = 1
    if secondary_action_text:
        secondary_button = GhostButton(buttons, text=secondary_action_text, command=lambda: close("secondary"), width=140)
        secondary_button.grid(row=0, column=column, padx=(0, UIStyle.PAD_SM))
        if not secondary_action_enabled:
            secondary_button.configure(state="disabled")
        column += 1
    action_button = PrimaryButton(buttons, text=action_text, command=lambda: close("action"))
    action_button.grid(row=0, column=column, padx=(0, UIStyle.PAD_SM))
    if not action_enabled:
        action_button.configure(state="disabled")
    GhostButton(buttons, text=close_text, command=lambda: close("close"), width=140).grid(row=0, column=column + 1)
    dialog.protocol("WM_DELETE_WINDOW", lambda: close("close"))
    _center_dialog(dialog)
    dialog.wait_window()
    return result["action"]


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
    _center_dialog(dialog)




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


def manifest_display_template(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    template = safe_text(value.get("display_template"))
    if template:
        return template
    account_label = manifest_account_label(value)
    for entry in manifest_entries(value):
        template = safe_text(entry.get("display_template"))
        if template:
            return template
        image_path = safe_text(entry.get("image_path"))
        if not image_path:
            continue
        for part in reversed(Path(image_path).parts[:-1]):
            if re.fullmatch(r"模板\d+", part):
                return f"{account_label}-{part}" if account_label else part
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


def entry_asset_lines(entries: list[dict[str, Any]], *, include_intro: bool = True) -> list[str]:
    lines: list[str] = []
    for entry in entries:
        section = safe_text(entry.get("section"))
        entry_type = safe_text(entry.get("type"))
        if not include_intro and section == "intro":
            continue
        label = " ".join(
            part
            for part in [
                f"#{entry.get('order_index') or entry.get('index')}" if entry.get("order_index") or entry.get("index") else "",
                TYPE_LABELS.get(section) or entry_type,
                safe_text(entry.get("product_uid")),
                safe_text(entry.get("product_name")),
                safe_text(entry.get("source_label")),
            ]
            if part
        )
        label = label or "未命名段落"
        lines.append(f"{label}｜配音：{safe_text(entry.get('audio_path')) or '未匹配'}")
        if entry_type == "product":
            lines.append(f"{label}｜图片：{safe_text(entry.get('image_path')) or '未匹配'}")
            video_path = safe_text(entry.get("display_video_path")) or safe_text(entry.get("video_path"))
            lines.append(f"{label}｜视频：{video_path or '未匹配'}")
    return lines


def entry_asset_issue_lines(entries: list[dict[str, Any]], *, include_intro: bool = True) -> list[str]:
    lines: list[str] = []

    def append_issue(label: str, asset_label: str, path: str) -> None:
        if not path:
            lines.append(f"{label}｜{asset_label}：未匹配")
        elif not Path(path).exists():
            lines.append(f"{label}｜{asset_label}路径不存在：{path}")

    for entry in entries:
        section = safe_text(entry.get("section"))
        entry_type = safe_text(entry.get("type"))
        if not include_intro and section == "intro":
            continue
        label = " ".join(
            part
            for part in [
                f"#{entry.get('order_index') or entry.get('index')}" if entry.get("order_index") or entry.get("index") else "",
                TYPE_LABELS.get(section) or entry_type,
                safe_text(entry.get("product_uid")),
                safe_text(entry.get("product_name")),
                safe_text(entry.get("source_label")),
            ]
            if part
        )
        label = label or "未命名段落"
        append_issue(label, "配音", safe_text(entry.get("audio_path")))
        if entry_type == "product":
            append_issue(label, "图片", safe_text(entry.get("image_path")))
            video_path = safe_text(entry.get("display_video_path")) or safe_text(entry.get("video_path"))
            append_issue(label, "视频", video_path)
    return lines


COLUMN_WIDTHS = {
    "品类": 80,
    "类型": 80,
    "对象UID": 80,
    "产品名称": 110,
    "标签": 80,
    "正文预览": 400,
}


# ── 主应用 ──




def _build_table(parent, columns: tuple[str, ...], row: int = 0) -> ttk.Treeview:
    """在 CTkFrame 内创建深色风格的 Treeview。"""
    configure_treeview_style(parent)
    tree = ttk.Treeview(parent, columns=columns, show="headings", style="CTreeview")
    for col in columns:
        tree.heading(col, text=col)
        tree.column(col, width=100, anchor="w")
    tree.tag_configure("has_issues", background=UIStyle.COLOR_ISSUE_BG, foreground=UIStyle.COLOR_TEXT_MAIN)
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


