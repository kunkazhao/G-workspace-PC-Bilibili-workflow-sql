from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .md_parser import H3_RE, H4_RE, SECTION_RE, UID_PATTERN, clean_body, parse_product_heading
from .utils import safe_text


COPY_UID_RE = re.compile(rf"^\s*商品\s*UID\s*[:：]\s*(?P<uid>{UID_PATTERN})\s*$", re.IGNORECASE)
BODY_LABEL_PREFIX = "正文"


@dataclass
class CopyInputBlock:
    uid: str
    body: str


def parse_uid_copy_blocks(text: str) -> list[CopyInputBlock]:
    blocks: list[CopyInputBlock] = []
    current_uid = ""
    current_lines: list[str] = []

    def flush() -> None:
        nonlocal current_uid, current_lines
        body = clean_body(current_lines)
        if current_uid and body:
            blocks.append(CopyInputBlock(uid=current_uid, body=body))
        current_uid = ""
        current_lines = []

    for raw in text.splitlines():
        match = COPY_UID_RE.match(raw)
        if match:
            flush()
            current_uid = safe_text(match.group("uid"))
            current_lines = []
            continue
        if current_uid:
            current_lines.append(raw)
    flush()
    return blocks


def preview_copy_write(markdown_path: str | Path, text: str, products: list[dict[str, Any]]) -> dict[str, Any]:
    path = Path(markdown_path)
    blocks = parse_uid_copy_blocks(text)
    product_uids = {safe_text(item.get("uid")).casefold(): item for item in products if safe_text(item.get("uid"))}
    headings = _product_heading_indexes(path.read_text(encoding="utf-8-sig") if path.exists() else "")
    matched: list[dict[str, Any]] = []
    missing_product: list[str] = []
    missing_heading: list[str] = []
    duplicate_input: list[str] = []
    seen: set[str] = set()

    for block in blocks:
        key = block.uid.casefold()
        if key in seen:
            duplicate_input.append(block.uid)
            continue
        seen.add(key)
        if key not in product_uids:
            missing_product.append(block.uid)
            continue
        if key not in headings:
            missing_heading.append(block.uid)
            continue
        matched.append({"uid": block.uid, "body": block.body, "label": _next_copy_label(headings[key]["lines"])})

    return {
        "path": str(path),
        "blocks": blocks,
        "matched": matched,
        "missing_product": missing_product,
        "missing_heading": missing_heading,
        "duplicate_input": duplicate_input,
    }


def write_copy_blocks_to_markdown(markdown_path: str | Path, text: str, products: list[dict[str, Any]]) -> dict[str, Any]:
    path = Path(markdown_path)
    if not path.exists():
        raise FileNotFoundError(f"MD 文件不存在：{path}")
    preview = preview_copy_write(path, text, products)
    matched = preview["matched"]
    if not matched:
        return {**preview, "written": []}

    original = path.read_text(encoding="utf-8-sig")
    lines = original.splitlines()
    heading_ranges = _product_heading_ranges(lines)
    by_uid = {item["uid"].casefold(): item for item in matched}
    written: list[dict[str, str]] = []

    for uid_key in sorted(by_uid, key=lambda key: heading_ranges[key][0], reverse=True):
        start, end = heading_ranges[uid_key]
        item = by_uid[uid_key]
        replacement, label = _append_body_to_product_lines(lines[start:end], item["body"])
        lines[start:end] = replacement
        written.append({"uid": item["uid"], "label": label})

    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    written.reverse()
    return {**preview, "written": written}


def _product_heading_indexes(text: str) -> dict[str, dict[str, Any]]:
    lines = text.splitlines()
    ranges = _product_heading_ranges(lines)
    return {
        uid: {"start": start, "end": end, "lines": lines[start:end]}
        for uid, (start, end) in ranges.items()
    }


def _product_heading_ranges(lines: list[str]) -> dict[str, tuple[int, int]]:
    ranges: dict[str, tuple[int, int]] = {}
    current_uid = ""
    current_start = -1
    in_product_section = False

    def close(end_index: int) -> None:
        nonlocal current_uid, current_start
        if current_uid and current_start >= 0:
            ranges[current_uid.casefold()] = (current_start, end_index)
        current_uid = ""
        current_start = -1

    for index, raw in enumerate(lines):
        stripped = raw.strip()
        section_match = SECTION_RE.match(stripped)
        if section_match:
            close(index)
            in_product_section = section_match.group(1).strip() == "商品文案"
            continue
        h3 = H3_RE.match(stripped)
        if h3 and in_product_section:
            close(index)
            parsed = parse_product_heading(h3.group(1).strip())
            if parsed:
                current_uid = parsed[0]
                current_start = index
            continue
    close(len(lines))
    return ranges


def _next_copy_label(product_lines: list[str]) -> str:
    variants = _variant_ranges(product_lines)
    non_empty = [label for label, body_lines, _start, _end in variants if clean_body(body_lines)]
    if not non_empty:
        return BODY_LABEL_PREFIX
    return f"{BODY_LABEL_PREFIX}{len(non_empty) + 1}"


def _append_body_to_product_lines(product_lines: list[str], body: str) -> tuple[list[str], str]:
    body_lines = clean_body(body.splitlines()).splitlines()
    variants = _variant_ranges(product_lines)
    for label, variant_body, start, end in variants:
        if label.startswith(BODY_LABEL_PREFIX) and not clean_body(variant_body):
            replacement = product_lines[: start + 1] + [""] + body_lines + product_lines[end:]
            return _normalize_blank_lines(replacement), label

    label = _next_copy_label(product_lines)
    output = list(product_lines)
    while output and not output[-1].strip():
        output.pop()
    output += ["", f"#### {label}", ""] + body_lines
    return _normalize_blank_lines(output), label


def _variant_ranges(product_lines: list[str]) -> list[tuple[str, list[str], int, int]]:
    ranges: list[tuple[str, list[str], int, int]] = []
    starts: list[tuple[str, int]] = []
    for index, raw in enumerate(product_lines):
        match = H4_RE.match(raw.strip())
        if match:
            starts.append((match.group(1).strip(), index))
    for index, (label, start) in enumerate(starts):
        end = starts[index + 1][1] if index + 1 < len(starts) else len(product_lines)
        ranges.append((label, product_lines[start + 1 : end], start, end))
    return ranges


def _normalize_blank_lines(lines: list[str]) -> list[str]:
    output: list[str] = []
    blank = 0
    for raw in lines:
        if raw.strip():
            blank = 0
            output.append(raw.rstrip())
            continue
        blank += 1
        if blank <= 1:
            output.append("")
    return output
