from __future__ import annotations

from pathlib import Path
from typing import Any

from .db import Database
from .md_parser import ProductDoc, parse_markdown_text
from .repositories import Repository
from .settings import DEFAULT_MARKDOWN_ROOT
from .utils import safe_text


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
            "## 视频信息",
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

        lines += ["## 价格过渡文案", "", "### 0-100元", "", "", "### 100-200元", "", ""]

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
