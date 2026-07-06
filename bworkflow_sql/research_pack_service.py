from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .db import Database
from .repositories import Repository
from .settings import DEFAULT_RESEARCH_PACK_ROOT
from .utils import safe_text


class ResearchPackService:
    """Create the reusable evidence-pack skeleton used before product-copy drafting."""

    def __init__(self, db: Database):
        self.db = db
        self.repo = Repository(db)

    def default_pack_path(self, project_id: int) -> Path:
        project = self.repo.project(project_id)
        if not project:
            raise ValueError(f"project does not exist: {project_id}")
        category = _project_category_name(project)
        scheme = safe_text(project.get("scheme_name")) or "主方案"
        return DEFAULT_RESEARCH_PACK_ROOT / category / f"{scheme}.md"

    def init_or_update_pack(self, project_id: int, target_path: str | Path | None = None) -> dict[str, Any]:
        project = self.repo.project(project_id)
        if not project:
            raise ValueError(f"project does not exist: {project_id}")
        products = self.repo.products(project_id, include_removed=False)
        if not products:
            raise ValueError("current project has no products; sync Master scheme products first.")

        target = Path(target_path) if target_path else self.default_pack_path(project_id)
        existing_text = target.read_text(encoding="utf-8-sig") if target.exists() else ""
        existing_blocks = _split_product_blocks(existing_text)

        category = _project_category_name(project)
        scheme = safe_text(project.get("scheme_name")) or "主方案"
        lines: list[str] = [
            "---",
            f"project_id: {project_id}",
            f"category: {category}",
            f"category_id: {safe_text(project.get('category_id'))}",
            f"scheme: {scheme}",
            f"scheme_id: {safe_text(project.get('scheme_id'))}",
            "stage: research_pack",
            "---",
            "",
            f"# {category}｜{scheme}｜资料采集包",
            "",
            "用途：联网写商品文案前的证据底稿。这里只放可核实资料、来源链接和不确定项，不写正式口播文案。",
            "",
        ]

        added: list[dict[str, Any]] = []
        preserved: list[dict[str, Any]] = []
        for product in products:
            uid = safe_text(product.get("uid"))
            if uid in existing_blocks:
                preserved.append(product)
                lines.extend(existing_blocks[uid])
                lines.append("")
                continue
            added.append(product)
            lines.extend(_render_product_block(product))
            lines.append("")

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
        return {
            "target_path": str(target),
            "added": added,
            "preserved": preserved,
            "total": len(products),
        }


def _project_category_name(project: dict[str, Any]) -> str:
    parent = safe_text(project.get("category_parent_name"))
    child = safe_text(project.get("category_name"))
    if parent and child:
        return f"{parent}-{child}"
    return safe_text(project.get("name")) or "未命名品类"


def _render_product_block(product: dict[str, Any]) -> list[str]:
    uid = safe_text(product.get("uid"))
    title = safe_text(product.get("title"))
    price = safe_text(product.get("price_label"))
    card = _load_product_card(product)
    slots = card.get("slots") if isinstance(card.get("slots"), list) else []
    lines = [
        f"## {uid}｜{title}",
        "",
        f"- 当前价格：{price}",
        "",
        "### 本地商品卡参数（只用于写后校对）",
    ]
    if slots:
        for slot in slots:
            if not isinstance(slot, dict):
                continue
            label = safe_text(slot.get("label"))
            value = safe_text(slot.get("value"))
            if label or value:
                lines.append(f"- {label}：{value}")
    else:
        lines.append("- 暂无")
    lines += [
        "",
        "### 联网可确认参数",
        "- 发声单元：",
        "- 输出功率：",
        "- 连接方式：",
        "- 供电方式：",
        "- 其他功能：",
        "",
        "### 来源",
        "- 来源1：",
        "- 来源2：",
        "",
        "### 写作可用判断",
        "- 适合怎么讲：",
        "- 不确定/不要写：",
    ]
    return lines


def _load_product_card(product: dict[str, Any]) -> dict[str, Any]:
    raw = safe_text(product.get("product_card_json"))
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _split_product_blocks(text: str) -> dict[str, list[str]]:
    blocks: dict[str, list[str]] = {}
    current_uid = ""
    current_lines: list[str] = []

    def flush() -> None:
        nonlocal current_uid, current_lines
        if current_uid:
            blocks[current_uid] = current_lines
        current_uid = ""
        current_lines = []

    for raw in text.splitlines():
        if raw.startswith("## ") and "｜" in raw:
            candidate = raw[3:].split("｜", 1)[0].strip()
            if candidate:
                flush()
                current_uid = candidate
                current_lines = [raw]
                continue
        if current_uid:
            current_lines.append(raw)
    flush()
    return blocks
