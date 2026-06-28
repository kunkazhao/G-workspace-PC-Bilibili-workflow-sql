from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .db import Database
from .md_parser import ProductDoc, parse_markdown_text
from .repositories import Repository
from .settings import DEFAULT_MARKDOWN_ROOT
from .utils import safe_text

logger = logging.getLogger(__name__)

# 价格段缺省值，仅在无法从 Master scheme 读到价格区间时兜底。
# 真源是 Master scheme.blue_link_price_ranges，正常不会走到这里。
DEFAULT_PRICE_RANGES: list[dict[str, Any]] = [
    {"min": None, "max": 100},
    {"min": 100, "max": 300},
    {"min": 300, "max": 500},
    {"min": 500, "max": None},
]


class OutlineService:
    def __init__(self, db: Database):
        self.db = db
        self.repo = Repository(db)

    def default_markdown_path(self, project_id: int) -> Path:
        project = self.repo.project(project_id)
        if not project:
            raise ValueError("请先选择品类项目。")
        parent = safe_text(project.get("category_parent_name"))
        child = safe_text(project.get("category_name"))
        filename = f"{parent}-{child}.md" if parent and child else f"{project['name']}.md"
        return DEFAULT_MARKDOWN_ROOT / filename

    def fetch_scheme_price_ranges(self, project: dict[str, Any]) -> list[dict[str, Any]]:
        """从 Master scheme 读取价格段（真源）。

        优先用 script_generation_config.transitions（已从 blue_link_price_ranges 派生），
        其次直接用 blue_link_price_ranges。读取失败时回退到 DEFAULT_PRICE_RANGES，
        保证建文档流程不因网络问题中断。
        """
        from .master_data import MasterDataService

        workspace_id = safe_text(project.get("workspace_id"))
        scheme_id = safe_text(project.get("scheme_id"))
        if not workspace_id or not scheme_id:
            logger.warning("项目缺少 workspace_id/scheme_id，价格段使用缺省值")
            return [dict(item) for item in DEFAULT_PRICE_RANGES]
        try:
            summary = MasterDataService().fetch_scheme_summary(
                workspace_id=workspace_id, scheme_id=scheme_id, force_refresh=True
            )
        except Exception as exc:  # noqa: BLE001 — 网络/接口异常都兜底，不阻断建文档
            logger.warning("读取 Master 价格段失败，使用缺省值：%s", exc)
            return [dict(item) for item in DEFAULT_PRICE_RANGES]

        config = summary.get("script_generation_config")
        rows: list[Any] = []
        if isinstance(config, dict) and isinstance(config.get("transitions"), list):
            rows = config["transitions"]
        if not rows and isinstance(summary.get("blue_link_price_ranges"), list):
            rows = summary["blue_link_price_ranges"]

        ranges = [
            {"min": r.get("min"), "max": r.get("max")}
            for r in rows
            if isinstance(r, dict)
        ]
        return ranges or [dict(item) for item in DEFAULT_PRICE_RANGES]

    def init_or_update_outline(self, project_id: int, target_path: str | Path | None = None) -> dict[str, Any]:
        project = self.repo.project(project_id)
        if not project:
            raise ValueError("请先选择品类项目。")
        products = self.repo.products(project_id, include_removed=False)
        if not products:
            raise ValueError("当前品类项目还没有商品，请先同步 Master 方案商品。")
        target = Path(target_path) if target_path else self.default_markdown_path(project_id)
        if path_looks_mismatched(project, target):
            raise ValueError(
                "商品文案 MD 文件名和当前项目名不一致，已停止更新，避免覆盖错误项目路径。\n"
                f"当前项目：{safe_text(project.get('name'))}\n"
                f"目标文件：{target}"
            )

        existing_text = target.read_text(encoding="utf-8-sig") if target.exists() else ""
        parsed = parse_markdown_text(existing_text) if existing_text.strip() else None
        existing_products = {item.uid: item for item in parsed.products} if parsed else {}
        active_uids = {safe_text(item.get("uid")) for item in products}

        added: list[dict[str, Any]] = []
        preserved: list[dict[str, Any]] = []
        lines: list[str] = [
            "---",
            f"primary_category: {safe_text(project.get('category_parent_name'))}",
            f"primary_category_id: {safe_text(project.get('category_parent_id'))}",
            f"category: {safe_text(project.get('category_name'))}",
            f"category_id: {safe_text(project.get('category_id'))}",
            f"scheme: {safe_text(project.get('scheme_name'))}",
            f"scheme_id: {safe_text(project.get('scheme_id'))}",
            "---",
            "",
        ]
        lines += ["## 引言文案", ""]
        if parsed and parsed.intro_scripts:
            for block in parsed.intro_scripts:
                lines += [f"### {block.label or '版本一'}", block.body.strip(), ""]
        else:
            lines += ["### 版本一", "", ""]

        lines += ["## 商品文案", ""]
        for product in products:
            uid = product["uid"]
            existing = existing_products.get(uid)
            if existing:
                preserved.append(product)
            else:
                added.append(product)
            lines += [f"### {format_product_heading(product)}", ""]
            if existing:
                lines.extend(render_product_body(existing))
            else:
                lines += ["#### 正文", ""]
            lines.append("")

        archive_title = "已移出 Master 的商品文案"
        removed_products = [item for uid, item in existing_products.items() if uid not in active_uids]
        archived_lines = parsed.extra_sections.get(archive_title, []) if parsed else []
        if archived_lines or removed_products:
            lines += [f"## {archive_title}", ""]
            if archived_lines:
                lines.extend(archived_lines)
                lines.append("")
            for product in removed_products:
                lines += [f"### {product.price_label or '未定价'}-{product.uid}-{product.title}", ""]
                lines.extend(render_product_body(product))
                lines.append("")

        scheme_ranges = self.fetch_scheme_price_ranges(project)
        lines += render_price_transitions(parsed.price_transitions if parsed else [], scheme_ranges)
        if parsed:
            for title, section_lines in parsed.extra_sections.items():
                if title in {"视频信息", archive_title}:
                    continue
                lines += render_section(title, section_lines)

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
        self.db.execute("UPDATE projects SET md_path=?, updated_at=datetime('now') WHERE id=?", (str(target), project_id))
        self.db.log_event(
            project_id,
            "outline_init",
            "success",
            f"文案框架已更新：新增 {len(added)}，保留 {len(preserved)}，目标 {target}",
        )
        return {
            "target_path": str(target),
            "added": added,
            "preserved": preserved,
            "total": len(products),
        }


def path_looks_mismatched(project: dict[str, Any], path: Path) -> bool:
    project_name = safe_text(project.get("name")).replace(" ", "").casefold()
    target_name = path.stem.replace(" ", "").casefold()
    return bool(project_name and target_name and project_name != target_name)


def format_product_heading(product: dict[str, Any]) -> str:
    return f"{format_price_label(product.get('price_label'))}-{safe_text(product.get('uid'))}-{safe_text(product.get('title'))}"


def format_price_label(value: Any) -> str:
    raw = safe_text(value).replace("¥", "").replace("￥", "").strip()
    if not raw:
        return "未定价"
    if raw.endswith("元") and not raw.endswith(".0元"):
        return raw
    raw = raw[:-1].strip() if raw.endswith("元") else raw
    try:
        number = float(raw)
    except ValueError:
        return raw or "未定价"
    if number.is_integer():
        return f"{int(number)}元"
    return f"{number:.2f}".rstrip("0").rstrip(".") + "元"


def render_product_body(product: ProductDoc) -> list[str]:
    lines: list[str] = []
    for script in product.scripts:
        lines += [f"#### {script.label}", script.body, ""]
    if product.image_path:
        lines.append(f"图片：{product.image_path}")
    if product.video_path:
        lines.append(f"视频：{product.video_path}")
    return lines


def render_price_transitions(transitions: list[Any], scheme_ranges: list[dict[str, Any]] | None = None) -> list[str]:
    lines: list[str] = ["## 价格过渡文案", ""]
    if transitions:
        # MD 里已有价格过渡文案，原样保留，不覆盖。
        for price in transitions:
            lines += [f"### {price.label}", ""]
            for script in price.scripts:
                lines += [f"#### {script.label}", script.body, ""]
        return lines
    # 新建：价格段从 Master scheme 派生，不再硬编码。
    ranges = scheme_ranges or [dict(item) for item in DEFAULT_PRICE_RANGES]
    for r in ranges:
        label = format_price_range_label(r.get("min"), r.get("max"))
        lines += [f"### {label}", "", "#### 正文1", "", ""]
    return lines


def format_price_range_label(range_min: Any, range_max: Any) -> str:
    """把 {min, max} 价格区间格式化为标签，与 Master 前端一致：
    {min}元以上 / {max}元以下 / {min}-{max}元。"""
    has_min = range_min not in (None, "")
    has_max = range_max not in (None, "")
    if has_min and not has_max:
        return f"{_fmt_price_num(range_min)}元以上"
    if not has_min and has_max:
        return f"{_fmt_price_num(range_max)}元以下"
    if has_min and has_max:
        return f"{_fmt_price_num(range_min)}-{_fmt_price_num(range_max)}元"
    return "全部价位"


def _fmt_price_num(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return safe_text(value)
    if number.is_integer():
        return str(int(number))
    return f"{number:.2f}".rstrip("0").rstrip(".")


def render_section(title: str, section_lines: list[str]) -> list[str]:
    lines = [f"## {title}", ""]
    if section_lines:
        lines.extend(section_lines)
        lines.append("")
    return lines
