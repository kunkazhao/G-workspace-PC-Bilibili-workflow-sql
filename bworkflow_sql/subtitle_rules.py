from __future__ import annotations

import re


SUBTITLE_BREAK_RE = re.compile(r"[，,。!！?？；;：:]|……|…")
SUBTITLE_DROP_PUNCT_RE = re.compile(r"[，,。!！?？；;：:]|……|…")
SUBTITLE_ALIGN_DROP_RE = re.compile(r'[\s，,。.!！?？；;：:、/\\\-—_~·`"“”\'‘’（）()【】\[\]{}《》<>]+|……|…')

_SUBTITLE_BREAK_CONJUNCTIONS = (
    "但是", "不过", "所以", "因为", "因此", "而且", "并且", "于是",
    "然后", "其实", "另外", "如果", "虽然", "同时", "比如", "除了",
    "不仅", "只是", "就是", "这样", "那么",
)
_SUBTITLE_CJK_DIGITS = set("〇零一二两三四五六七八九十百千万亿点")
_SUBTITLE_NO_BREAK_CHARS = set("的地得")


def _safe_text(value: object) -> str:
    return str(value or "").strip()


def _subtitle_is_digit(ch: str) -> bool:
    return ch.isdigit() or ch in _SUBTITLE_CJK_DIGITS


def _subtitle_is_alpha(ch: str) -> bool:
    return ch.isascii() and ch.isalpha()


def _subtitle_can_break(left: str, right: str) -> bool:
    if not left or not right:
        return True
    if left in _SUBTITLE_NO_BREAK_CHARS or right in _SUBTITLE_NO_BREAK_CHARS:
        return False
    if (left == "." and _subtitle_is_digit(right)) or (_subtitle_is_digit(left) and right == "."):
        return False
    left_num, right_num = _subtitle_is_digit(left), _subtitle_is_digit(right)
    left_alpha, right_alpha = _subtitle_is_alpha(left), _subtitle_is_alpha(right)
    if left_num and (right_num or right_alpha or not right.isascii()):
        return False
    if left_alpha and (right_alpha or right_num):
        return False
    return True


def _subtitle_choose_cut(text: str, lo: int, hi: int, prefer: int) -> int | None:
    candidates = [cut for cut in range(max(lo, 1), min(hi, len(text) - 1) + 1)]
    if not candidates:
        return None
    conj_cuts = [
        cut
        for cut in candidates
        if _subtitle_can_break(text[cut - 1], text[cut])
        and any(text.startswith(word, cut) for word in _SUBTITLE_BREAK_CONJUNCTIONS)
    ]
    pool = conj_cuts or [cut for cut in candidates if _subtitle_can_break(text[cut - 1], text[cut])]
    if not pool:
        return None
    return min(pool, key=lambda cut: (abs(cut - prefer), -cut))


def _break_long_clause(clause: str, max_chars: int) -> list[str]:
    min_head = min(4, max_chars)
    lines: list[str] = []
    rest = clause
    while len(rest) > max_chars:
        if len(rest) <= max_chars * 2:
            lo = max(min_head, len(rest) - max_chars)
            hi = min(max_chars, len(rest) - min_head)
            cut = _subtitle_choose_cut(rest, lo, hi, (len(rest) + 1) // 2)
        else:
            cut = _subtitle_choose_cut(rest, min_head, max_chars, max_chars)
        if not cut:
            cut = max_chars
        lines.append(rest[:cut])
        rest = rest[cut:]
    if rest:
        lines.append(rest)
    return lines


def split_subtitle_text(text: object, *, max_chars: int = 24) -> list[str]:
    body = re.sub(r"\s+", "", _safe_text(text))
    if not body:
        return []
    clauses = [SUBTITLE_DROP_PUNCT_RE.sub("", item) for item in SUBTITLE_BREAK_RE.split(body)]
    clauses = [item for item in clauses if item]
    chunks: list[str] = []
    for clause in clauses or [body]:
        if len(clause) > max_chars:
            chunks.extend(_break_long_clause(clause, max_chars))
            continue
        chunks.append(clause)
    return [chunk for chunk in chunks if chunk]


def normalize_subtitle_alignment_text(text: object) -> str:
    return SUBTITLE_ALIGN_DROP_RE.sub("", _safe_text(text)).casefold()
