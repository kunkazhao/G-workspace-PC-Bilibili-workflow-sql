from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .utils import normalize_text, safe_text, text_hash


SECTION_RE = re.compile(r"^##\s+(.+?)\s*$")
H3_RE = re.compile(r"^###\s+(.+?)\s*$")
H4_RE = re.compile(r"^####\s+(.+?)\s*$")
UID_PATTERN = r"[A-Za-z]{1,12}\d[A-Za-z0-9_-]*"
UID_RE = re.compile(rf"(?P<uid>{UID_PATTERN})")
PRODUCT_HEADING_RE = re.compile(rf"^(?P<title>.+?)[-/](?P<uid>{UID_PATTERN})[-/](?P<price>.+)$")
PRICE_UID_TITLE_HEADING_RE = re.compile(rf"^(?P<price>.+?)[-/](?P<uid>{UID_PATTERN})[-/](?P<title>.+)$")
SCRIPT_ID_RE = re.compile(r"^<!--\s*script_id:\s*(?P<script_id>[^>]+?)\s*-->$")
VOICE_STATUS_RE = re.compile(r"^<!--\s*voice_status:")
MANUAL_LABEL_RE = re.compile(r"^\*\*(?P<label>.+?)\*\*$")
BLOCK_LABEL_ALIASES = {"正文", "版本1", "版本2", "来源 1", "来源 2", "来源1", "来源2"}


@dataclass
class ScriptVariant:
    label: str
    body: str
    script_id: str = ""

    @property
    def text_hash(self) -> str:
        return text_hash(self.body)


@dataclass
class ProductDoc:
    uid: str
    title: str
    price_label: str = ""
    image_path: str = ""
    video_path: str = ""
    scripts: list[ScriptVariant] = field(default_factory=list)


@dataclass
class PriceTransitionDoc:
    label: str
    scripts: list[ScriptVariant] = field(default_factory=list)


@dataclass
class ParsedMarkdown:
    intro_scripts: list[ScriptVariant]
    products: list[ProductDoc]
    price_transitions: list[PriceTransitionDoc]
    ordered_uids: list[str]
    extra_sections: dict[str, list[str]]


def split_sections(text: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    current = ""
    lines: list[str] = []
    for raw in text.splitlines():
        line = raw.rstrip()
        match = SECTION_RE.match(line)
        if match:
            if current:
                sections[current] = lines
            current = match.group(1).strip()
            lines = []
            continue
        if current:
            lines.append(line)
    if current:
        sections[current] = lines
    return sections


def trim_blank(lines: list[str]) -> list[str]:
    start = 0
    end = len(lines)
    while start < end and not lines[start].strip():
        start += 1
    while end > start and not lines[end - 1].strip():
        end -= 1
    return lines[start:end]


def clean_body(lines: list[str]) -> str:
    return "\n".join(trim_blank(lines)).strip()


def parse_script_variants(lines: list[str], fallback_label: str = "正文") -> list[ScriptVariant]:
    variants: list[ScriptVariant] = []
    current_label = ""
    current_lines: list[str] = []
    loose_lines: list[str] = []
    pending_script_id = ""
    current_script_id = ""
    auto_index = 0

    def next_auto_label() -> str:
        nonlocal auto_index
        auto_index += 1
        return fallback_label if auto_index == 1 else f"{fallback_label}{auto_index}"

    def flush() -> None:
        nonlocal current_label, current_lines, current_script_id
        if not current_label:
            return
        body = clean_body(current_lines)
        if body:
            variants.append(ScriptVariant(label=current_label, body=body, script_id=current_script_id))
        current_label = ""
        current_lines = []
        current_script_id = ""

    def flush_loose() -> None:
        nonlocal loose_lines, pending_script_id
        body = clean_body(loose_lines)
        if body:
            variants.append(ScriptVariant(label=next_auto_label(), body=body, script_id=pending_script_id))
        loose_lines = []
        pending_script_id = ""

    for raw in lines:
        stripped = raw.strip()
        script_match = SCRIPT_ID_RE.match(stripped)
        if script_match:
            if current_label and clean_body(current_lines):
                flush()
            elif loose_lines:
                flush_loose()
            pending_script_id = safe_text(script_match.group("script_id"))
            continue
        if VOICE_STATUS_RE.match(stripped):
            continue
        manual_match = MANUAL_LABEL_RE.match(stripped)
        if manual_match:
            flush()
            current_label = next_auto_label()
            current_lines = []
            current_script_id = pending_script_id
            pending_script_id = ""
            continue
        heading4 = H4_RE.match(stripped)
        heading3 = H3_RE.match(stripped)
        if heading4:
            if loose_lines:
                flush_loose()
            flush()
            current_label = heading4.group(1).strip()
            current_lines = []
            current_script_id = pending_script_id
            pending_script_id = ""
            continue
        if not heading4 and (stripped in BLOCK_LABEL_ALIASES or stripped.startswith("来源 ") or stripped.startswith("版本")):
            if loose_lines:
                flush_loose()
            flush()
            current_label = stripped
            current_lines = []
            current_script_id = pending_script_id
            pending_script_id = ""
            continue
        if current_label:
            current_lines.append(raw)
        elif heading3:
            if loose_lines:
                flush_loose()
            flush()
            current_label = heading3.group(1).strip()
            current_lines = []
            current_script_id = pending_script_id
            pending_script_id = ""
        else:
            loose_lines.append(raw)
    flush()
    flush_loose()
    return variants


def parse_intro(lines: list[str]) -> list[ScriptVariant]:
    # In intro sections, H3 headings are independent intro choices.
    if not any(H3_RE.match(raw.strip()) for raw in lines):
        return parse_script_variants(lines, fallback_label="引言")
    chunks: list[tuple[str, list[str]]] = []
    current_label = ""
    current_lines: list[str] = []
    for raw in lines:
        match = H3_RE.match(raw.strip())
        if match:
            if current_label or current_lines:
                chunks.append((current_label or "正文", current_lines))
            current_label = match.group(1).strip()
            current_lines = []
            continue
        current_lines.append(raw)
    if current_label or current_lines:
        chunks.append((current_label or "正文", current_lines))
    if not chunks:
        return []
    result: list[ScriptVariant] = []
    for index, (label, chunk_lines) in enumerate(chunks, start=1):
        cleaned_lines = [
            raw
            for raw in chunk_lines
            if not SCRIPT_ID_RE.match(raw.strip()) and not VOICE_STATUS_RE.match(raw.strip())
        ]
        body = clean_body(cleaned_lines)
        if body:
            result.append(ScriptVariant(label=label or f"引言{index}", body=body))
    return result or parse_script_variants(lines, fallback_label="正文")


def parse_product_heading(heading: str) -> tuple[str, str, str] | None:
    heading = safe_text(heading)
    price_first_match = PRICE_UID_TITLE_HEADING_RE.match(heading)
    product_match = PRODUCT_HEADING_RE.match(heading)
    if product_match and _looks_like_price(product_match.group("price")) and not _looks_like_price(product_match.group("title")):
        return product_match.group("uid").strip(), product_match.group("title").strip(), product_match.group("price").strip()
    if price_first_match and _looks_like_price(price_first_match.group("price")):
        return price_first_match.group("uid").strip(), price_first_match.group("title").strip(), price_first_match.group("price").strip()
    if product_match:
        return product_match.group("uid").strip(), product_match.group("title").strip(), product_match.group("price").strip()
    uid_match = UID_RE.search(heading)
    if not uid_match:
        return None
    uid = uid_match.group("uid")
    title = heading.replace(uid, "").strip(" -/|")
    return uid, title or uid, ""


def _looks_like_price(value: str) -> bool:
    value = safe_text(value)
    return "元" in value or bool(re.match(r"^\d+(\.\d+)?$", value))


def parse_products(lines: list[str]) -> list[ProductDoc]:
    products: list[ProductDoc] = []
    current_heading = ""
    current_lines: list[str] = []

    def flush() -> None:
        nonlocal current_heading, current_lines
        if not current_heading:
            return
        parsed = parse_product_heading(current_heading)
        if not parsed:
            return
        uid, title, price = parsed
        script_lines: list[str] = []
        image_path = ""
        video_path = ""
        for raw in current_lines:
            stripped = raw.strip()
            if stripped.startswith("图片：") or stripped.startswith("图片:"):
                image_path = stripped[3:].strip()
                continue
            if stripped.startswith("视频：") or stripped.startswith("视频:"):
                video_path = stripped[3:].strip()
                continue
            script_lines.append(raw)
        products.append(
            ProductDoc(
                uid=uid,
                title=title,
                price_label=price,
                image_path=image_path,
                video_path=video_path,
                scripts=parse_script_variants(script_lines, fallback_label="正文"),
            )
        )

    for raw in lines:
        match = H3_RE.match(raw.strip())
        if match:
            flush()
            current_heading = match.group(1).strip()
            current_lines = []
            continue
        current_lines.append(raw)
    flush()
    return products


def parse_price_transitions(lines: list[str]) -> list[PriceTransitionDoc]:
    result: list[PriceTransitionDoc] = []
    current_label = ""
    current_lines: list[str] = []

    def flush() -> None:
        nonlocal current_label, current_lines
        if not current_label:
            return
        scripts = parse_script_variants(current_lines, fallback_label="正文")
        if scripts:
            result.append(PriceTransitionDoc(label=current_label, scripts=scripts))

    for raw in lines:
        match = H3_RE.match(raw.strip())
        if match:
            flush()
            current_label = match.group(1).strip()
            current_lines = []
            continue
        current_lines.append(raw)
    flush()
    return result


def parse_order(lines: list[str]) -> list[str]:
    ordered: list[str] = []
    for raw in lines:
        match = UID_RE.search(raw)
        if match:
            ordered.append(match.group("uid"))
    return ordered


def parse_markdown_text(text: str) -> ParsedMarkdown:
    sections = split_sections(text)
    known = {"引言文案", "商品文案", "价格过渡文案", "商品顺序"}
    return ParsedMarkdown(
        intro_scripts=parse_intro(sections.get("引言文案", [])),
        products=parse_products(sections.get("商品文案", [])),
        price_transitions=parse_price_transitions(sections.get("价格过渡文案", [])),
        ordered_uids=parse_order(sections.get("商品顺序", [])),
        extra_sections={key: value for key, value in sections.items() if key not in known},
    )


def parse_markdown_file(path: str | Path) -> ParsedMarkdown:
    return parse_markdown_text(Path(path).read_text(encoding="utf-8-sig"))
