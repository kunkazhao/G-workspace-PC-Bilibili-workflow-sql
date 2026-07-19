from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

from .db import Database
from .markdown_paths import project_asset_markdown_path
from .md_parser import ParsedMarkdown, parse_markdown_text
from .repositories import Repository
from .utils import safe_text


@dataclass(frozen=True)
class ProductCopyLintRule:
    rule_id: str
    category: str
    pattern: re.Pattern[str]
    message: str
    suggestion: str


@dataclass(frozen=True)
class ProductCopyLintFinding:
    rule_id: str
    category: str
    match: str
    message: str
    suggestion: str
    line: int
    column: int
    excerpt: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


INTERNAL_ROLE_PATTERN = re.compile(
    r"主推(?:款|商品)?|重点款|普通款|低佣(?:款)?|选品池|重点标记|佣金|featured",
    re.IGNORECASE,
)
PRICE_TIER_PATTERN = re.compile(
    r"(?:"
    r"(?:\d+(?:\.\d+)?|[零〇一二三四五六七八九十百千万两]+)\s*"
    r"(?:元)?\s*(?:到|至|[-—–~～])\s*"
    r"(?:\d+(?:\.\d+)?|[零〇一二三四五六七八九十百千万两]+)\s*"
    r"(?:元)?|"
    r"百元|千元|万元|入门|中端|高端"
    r")\s*(?:价格|价位)?档(?:位)?"
)
SOURCE_PAGE_PATTERN = re.compile(r"商品页|详情页|品牌页|网页|页面|官网")
SOURCE_ATTRIBUTION_PATTERN = re.compile(
    r"官方(?:宣称|表示|标注|显示|写(?:着|明)?)|"
    r"资料(?:显示|表明|写明)|"
    r"(?:网上|网友|买家|用户)(?:的)?反馈|"
    r"据(?:测评|资料|页面)|"
    r"(?:我)?(?:查到|搜到|搜索到)"
)


PRODUCT_COPY_LINT_RULES: tuple[ProductCopyLintRule, ...] = (
    ProductCopyLintRule(
        rule_id="internal_role_label",
        category="internal_role_leak",
        pattern=INTERNAL_ROLE_PATTERN,
        message="商品口播暴露了内部选品角色或运营标签。",
        suggestion="删除主推、重点款、佣金等内部身份词，直接说明适用场景和购买理由。",
    ),
    ProductCopyLintRule(
        rule_id="internal_price_tier",
        category="internal_role_leak",
        pattern=PRICE_TIER_PATTERN,
        message="商品口播把价格区间写成了内部档位标签。",
        suggestion="改成观众预算表达，例如“预算在一百到两百元”。",
    ),
    ProductCopyLintRule(
        rule_id="source_page_reference",
        category="source_process_leak",
        pattern=SOURCE_PAGE_PATTERN,
        message="商品口播暴露了页面或官网等资料采集路径。",
        suggestion="删除来源过程，直接给出已经核实的参数、使用影响和购买判断。",
    ),
    ProductCopyLintRule(
        rule_id="source_attribution_phrase",
        category="source_process_leak",
        pattern=SOURCE_ATTRIBUTION_PATTERN,
        message="商品口播使用了资料归因或搜索过程措辞。",
        suggestion="把来源措辞留在资料采集包，正式口播只保留核实后的事实和判断。",
    ),
)


def lint_product_copy(text: str) -> list[ProductCopyLintFinding]:
    body = safe_text(text)
    findings: list[ProductCopyLintFinding] = []
    seen: set[tuple[str, int, int]] = set()
    for rule in PRODUCT_COPY_LINT_RULES:
        for match in rule.pattern.finditer(body):
            key = (rule.rule_id, match.start(), match.end())
            if key in seen:
                continue
            seen.add(key)
            line_start = body.rfind("\n", 0, match.start()) + 1
            line_end = body.find("\n", match.end())
            if line_end < 0:
                line_end = len(body)
            findings.append(
                ProductCopyLintFinding(
                    rule_id=rule.rule_id,
                    category=rule.category,
                    match=match.group(0),
                    message=rule.message,
                    suggestion=rule.suggestion,
                    line=body.count("\n", 0, match.start()) + 1,
                    column=match.start() - line_start + 1,
                    excerpt=body[line_start:line_end].strip(),
                )
            )
    return sorted(findings, key=lambda item: (item.line, item.column, item.rule_id))


def lint_parsed_product_copy(
    parsed: ParsedMarkdown,
    *,
    source_text: str = "",
    source_path: str | Path = "",
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    search_cursor = 0
    for product in parsed.products:
        for script in product.scripts:
            body_offset = _find_body_offset(source_text, script.body, search_cursor)
            if body_offset >= 0:
                search_cursor = body_offset + len(script.body)
            for finding in lint_product_copy(script.body):
                payload = finding.to_dict()
                payload.update(
                    {
                        "uid": product.uid,
                        "title": product.title,
                        "block_label": script.label or "正文",
                        "path": str(source_path) if source_path else "",
                        "line": _absolute_line(source_text, body_offset, finding.line),
                        "body_line": finding.line,
                    }
                )
                findings.append(payload)
    return findings


def diagnose_product_copy_lint(db: Database, *, project_id: int) -> dict[str, Any]:
    project = Repository(db).project(project_id)
    if not project:
        raise ValueError(f"project does not exist: {project_id}")

    markdown_path, path_issue_code = project_asset_markdown_path(project)
    if not markdown_path or not markdown_path.is_file():
        return {
            "ok": False,
            "project_id": int(project_id),
            "path": str(markdown_path) if markdown_path else "",
            "path_issue_code": path_issue_code or "",
            "summary": {"products_scanned": 0, "variants_scanned": 0, "findings": 0},
            "findings": [],
            "error": "current project has no readable product-copy Markdown",
        }

    source_text = markdown_path.read_text(encoding="utf-8-sig")
    parsed = parse_markdown_text(source_text)
    findings = lint_parsed_product_copy(parsed, source_text=source_text, source_path=markdown_path)
    return {
        "ok": not findings,
        "project_id": int(project_id),
        "path": str(markdown_path),
        "path_issue_code": path_issue_code or "",
        "summary": {
            "products_scanned": len(parsed.products),
            "variants_scanned": sum(len(product.scripts) for product in parsed.products),
            "failed_products": len({safe_text(item.get("uid")) for item in findings}),
            "findings": len(findings),
        },
        "findings": findings,
    }


def group_findings_by_uid(findings: Iterable[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for finding in findings:
        grouped.setdefault(safe_text(finding.get("uid")), []).append(finding)
    return grouped


def _find_body_offset(source_text: str, body: str, cursor: int) -> int:
    if not source_text or not body:
        return -1
    offset = source_text.find(body, max(0, cursor))
    if offset >= 0:
        return offset
    return source_text.find(body)


def _absolute_line(source_text: str, body_offset: int, body_line: int) -> int:
    if not source_text or body_offset < 0:
        return body_line
    return source_text.count("\n", 0, body_offset) + body_line
