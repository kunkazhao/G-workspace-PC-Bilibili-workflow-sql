from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .db import Database
from .markdown_paths import project_asset_markdown_path
from .md_parser import ParsedMarkdown, parse_markdown_text
from .repositories import Repository
from .utils import safe_text


PROFILE_ROOT = Path(__file__).resolve().parents[1] / "config" / "product-copy-voice-profiles"
SENTENCE_RE = re.compile(r"[^。！？!?]+[。！？!?]?")
TAIL_SPLIT_RE = re.compile(r"[，,；;：:]\s*")
CONDITION_RE = re.compile(r"如果|只要|想要|想买|预算|平时|经常|主要|在意|看重|优先")
PRODUCT_SUBJECT_RE = re.compile(r"(?:这|该)?(?:一)?(?:件|款)(?:商品|衣服)?|选它")
VERDICT_SHELL_RE = re.compile(r"属于|就是|就选|值得|推荐|更|很|好选|没毛病|可以考虑")
CONCRETE_BOUNDARY_RE = re.compile(
    r"不必|不用|不需要|少花|多花|加预算|选小一码|按.+选|"
    r"能|不会|减少|更快|更少|覆盖|散热|排出|挡住|遮住|收纳|收进|不占|卡住|多护住"
)
MECHANICAL_REVERSAL_RE = re.compile(
    r"(?:不是|并非|不在于)[^。！？!?\n]{0,64}(?:而是|而在于)"
)
SYNTHETIC_SUMMARY_RE = re.compile(r"简单说|总结来说|总的来说|综上所述")
UNVERIFIED_EXPERIENCE_RE = re.compile(
    r"我(?:用下来|实测|亲测|买回来|入手后|拿到手|试下来|敲下来)"
)
REPEATED_FEATURE_LEAD_RE = re.compile(r"最核心的(?:点|亮点)|最大的亮点")


@dataclass(frozen=True)
class ProductCopyVoiceProfile:
    profile_id: str
    description: str
    rejected_closing_phrases: tuple[str, ...]
    abstract_evaluation_terms: tuple[str, ...]


@dataclass(frozen=True)
class ClosingSample:
    uid: str
    title: str
    block_label: str
    sentence: str
    tail: str
    body_offset: int
    sentence_offset: int
    line: int
    column: int

    def location(self) -> dict[str, Any]:
        return {
            "uid": self.uid,
            "title": self.title,
            "block_label": self.block_label,
            "line": self.line,
            "column": self.column,
            "excerpt": self.sentence,
        }


@dataclass(frozen=True)
class BodySample:
    uid: str
    title: str
    block_label: str
    body: str
    body_offset: int


def load_voice_profile(profile_id: str = "zhaoer") -> ProductCopyVoiceProfile:
    normalized = safe_text(profile_id) or "zhaoer"
    if not re.fullmatch(r"[A-Za-z0-9_-]+", normalized):
        raise ValueError(f"invalid product-copy voice profile id: {profile_id}")
    path = PROFILE_ROOT / f"{normalized}.json"
    if not path.is_file():
        raise ValueError(f"product-copy voice profile does not exist: {normalized}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if safe_text(payload.get("id")) != normalized:
        raise ValueError(f"product-copy voice profile id mismatch: {path}")
    return ProductCopyVoiceProfile(
        profile_id=normalized,
        description=safe_text(payload.get("description")),
        rejected_closing_phrases=_string_tuple(payload.get("rejected_closing_phrases")),
        abstract_evaluation_terms=_string_tuple(payload.get("abstract_evaluation_terms")),
    )


def audit_parsed_product_copy(
    parsed: ParsedMarkdown,
    *,
    source_text: str = "",
    source_path: str | Path = "",
    voice_profile: str = "zhaoer",
) -> list[dict[str, Any]]:
    profile = load_voice_profile(voice_profile)
    body_samples = _body_samples(parsed, source_text)
    samples = _closing_samples(parsed, source_text)
    findings = _human_writing_findings(body_samples, source_text, source_path)
    abstract_samples: list[ClosingSample] = []

    for sample in samples:
        rejected = next(
            (phrase for phrase in profile.rejected_closing_phrases if phrase and phrase in sample.tail),
            "",
        )
        abstract_term = next(
            (term for term in profile.abstract_evaluation_terms if term and term in sample.tail),
            "",
        )
        abstract = abstract_term or (_structural_abstract_closing(sample) and sample.tail)
        if rejected:
            findings.append(
                _sample_finding(
                    sample,
                    source_path=source_path,
                    rule_id="voice_phrase_rejected",
                    category="voice_preference",
                    match=rejected,
                    message="段尾使用了赵二口吻配置中已明确排除的抽象判断。",
                    suggestion="把选择边界并入前面的条件或取舍；如果信息已经完整，直接停在具体事实或使用结果上。",
                )
            )
        elif abstract:
            findings.append(
                _sample_finding(
                    sample,
                    source_path=source_path,
                    rule_id="abstract_evaluative_closing",
                    category="voice_preference",
                    match=abstract,
                    message="段尾用抽象评价替代了可感知结果或明确选择边界。",
                    suggestion="检查这句能否删除；需要保留判断时，改写为具体条件、代价或使用后果。",
                )
            )
        if rejected or abstract:
            abstract_samples.append(sample)

    if len(abstract_samples) >= 3:
        first = abstract_samples[0]
        findings.append(
            {
                "rule_id": "repeated_abstract_closing_form",
                "category": "document_structure",
                "severity": "warning",
                "match": "",
                "message": "整篇有多个商品段落重复以“商品主体 + 抽象判断”收尾，形成可预测的模板节奏。",
                "suggestion": "逐段做删除测试；只保留真正改变选择的条件或取舍，不要求每段补独立结论句。",
                "uid": first.uid,
                "title": first.title,
                "block_label": first.block_label,
                "path": str(source_path) if source_path else "",
                "line": first.line,
                "column": first.column,
                "excerpt": first.sentence,
                "locations": [sample.location() for sample in abstract_samples],
            }
        )
    return findings


def _human_writing_findings(
    samples: list[BodySample],
    source_text: str,
    source_path: str | Path,
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    rules = (
        (
            "mechanical_reversal",
            MECHANICAL_REVERSAL_RE,
            "正文使用了机械翻案句，容易形成可预测的模型腔。",
            "拆回两句普通事实或判断，保留原来的事实强度，不另造漂亮反转。",
        ),
        (
            "synthetic_summary_marker",
            SYNTHETIC_SUMMARY_RE,
            "正文使用了总结路标，前文已经成立的信息被再次概括。",
            "删除路标并做整句删除测试；需要保留时，落到具体条件或使用后果。",
        ),
        (
            "unverified_first_person_experience",
            UNVERIFIED_EXPERIENCE_RE,
            "正文使用了需要真实亲测证据的第一人称经历表达。",
            "确认确有亲测证据；否则改为“我会选”“我更看重”等作者判断或直接写产品事实。",
        ),
    )
    feature_leads: list[dict[str, Any]] = []

    for sample in samples:
        for rule_id, pattern, message, suggestion in rules:
            match = pattern.search(sample.body)
            if match:
                findings.append(
                    _body_sample_finding(
                        sample,
                        match=match,
                        source_text=source_text,
                        source_path=source_path,
                        rule_id=rule_id,
                        category="human_writing",
                        message=message,
                        suggestion=suggestion,
                    )
                )
        feature_match = REPEATED_FEATURE_LEAD_RE.search(sample.body)
        if feature_match:
            feature_leads.append(
                _body_location(sample, feature_match, source_text=source_text)
            )

    if len(feature_leads) >= 3:
        first = feature_leads[0]
        findings.append(
            {
                "rule_id": "repeated_feature_lead_form",
                "category": "document_structure",
                "severity": "warning",
                "match": first["match"],
                "message": "整篇有多个商品重复使用“最核心的点/最大的亮点”起手，形成统一模板。",
                "suggestion": "让每款从自己的场景、动作或差异进入，不要求所有商品先声明核心卖点。",
                "uid": first["uid"],
                "title": first["title"],
                "block_label": first["block_label"],
                "path": str(source_path) if source_path else "",
                "line": first["line"],
                "column": first["column"],
                "excerpt": first["excerpt"],
                "locations": feature_leads,
            }
        )
    return findings


def diagnose_product_copy_audit(
    db: Database,
    *,
    project_id: int,
    voice_profile: str = "zhaoer",
) -> dict[str, Any]:
    project = Repository(db).project(project_id)
    if not project:
        raise ValueError(f"project does not exist: {project_id}")

    markdown_path, path_issue_code = project_asset_markdown_path(project)
    if not markdown_path or not markdown_path.is_file():
        return {
            "ok": False,
            "clean": False,
            "project_id": int(project_id),
            "path": str(markdown_path) if markdown_path else "",
            "path_issue_code": path_issue_code or "",
            "voice_profile": voice_profile,
            "summary": {"products_scanned": 0, "variants_scanned": 0, "findings": 0},
            "findings": [],
            "error": "current project has no readable product-copy Markdown",
        }

    source_text = markdown_path.read_text(encoding="utf-8-sig")
    parsed = parse_markdown_text(source_text)
    findings = audit_parsed_product_copy(
        parsed,
        source_text=source_text,
        source_path=markdown_path,
        voice_profile=voice_profile,
    )
    return {
        "ok": True,
        "clean": not findings,
        "project_id": int(project_id),
        "path": str(markdown_path),
        "path_issue_code": path_issue_code or "",
        "voice_profile": voice_profile,
        "summary": {
            "products_scanned": len(parsed.products),
            "variants_scanned": sum(len(product.scripts) for product in parsed.products),
            "flagged_variants": len(
                {
                    (safe_text(item.get("uid")), safe_text(item.get("block_label")))
                    for item in findings
                    if safe_text(item.get("uid"))
                    and item.get("rule_id")
                    not in {"repeated_abstract_closing_form", "repeated_feature_lead_form"}
                }
            ),
            "findings": len(findings),
        },
        "findings": findings,
    }


def _closing_samples(parsed: ParsedMarkdown, source_text: str) -> list[ClosingSample]:
    samples: list[ClosingSample] = []
    search_cursor = 0
    for product in parsed.products:
        for script in product.scripts:
            body_offset = _find_body_offset(source_text, script.body, search_cursor)
            if body_offset >= 0:
                search_cursor = body_offset + len(script.body)
            sentence, sentence_offset = _last_sentence(script.body)
            if not sentence:
                continue
            absolute_offset = body_offset + sentence_offset if body_offset >= 0 else sentence_offset
            line_start = source_text.rfind("\n", 0, absolute_offset) + 1 if source_text else 0
            samples.append(
                ClosingSample(
                    uid=product.uid,
                    title=product.title,
                    block_label=script.label or "正文",
                    sentence=sentence,
                    tail=TAIL_SPLIT_RE.split(sentence.rstrip("。！？!?"))[-1].strip(),
                    body_offset=body_offset,
                    sentence_offset=sentence_offset,
                    line=source_text.count("\n", 0, absolute_offset) + 1 if source_text else 1,
                    column=absolute_offset - line_start + 1 if source_text else sentence_offset + 1,
                )
            )
    return samples


def _body_samples(parsed: ParsedMarkdown, source_text: str) -> list[BodySample]:
    samples: list[BodySample] = []
    search_cursor = 0
    for product in parsed.products:
        for script in product.scripts:
            body_offset = _find_body_offset(source_text, script.body, search_cursor)
            if body_offset >= 0:
                search_cursor = body_offset + len(script.body)
            samples.append(
                BodySample(
                    uid=product.uid,
                    title=product.title,
                    block_label=script.label or "正文",
                    body=script.body,
                    body_offset=body_offset,
                )
            )
    return samples


def _last_sentence(body: str) -> tuple[str, int]:
    matches = [match for match in SENTENCE_RE.finditer(safe_text(body)) if match.group(0).strip()]
    if not matches:
        return "", -1
    match = matches[-1]
    raw = match.group(0)
    leading = len(raw) - len(raw.lstrip())
    return raw.strip(), match.start() + leading


def _structural_abstract_closing(sample: ClosingSample) -> bool:
    if len(sample.tail) > 36 or not CONDITION_RE.search(sample.sentence):
        return False
    subject_removed = sample.tail
    for value in (sample.title, sample.uid):
        if value:
            subject_removed = subject_removed.replace(value, "")
    has_product_subject = subject_removed != sample.tail or bool(PRODUCT_SUBJECT_RE.search(subject_removed))
    if not has_product_subject:
        return False
    predicate = PRODUCT_SUBJECT_RE.sub("", subject_removed).strip()
    if not predicate or CONCRETE_BOUNDARY_RE.search(predicate):
        return False
    return bool(VERDICT_SHELL_RE.search(predicate) or len(predicate.rstrip("。！？!?")) <= 8)


def _sample_finding(
    sample: ClosingSample,
    *,
    source_path: str | Path,
    rule_id: str,
    category: str,
    match: str,
    message: str,
    suggestion: str,
) -> dict[str, Any]:
    return {
        "rule_id": rule_id,
        "category": category,
        "severity": "warning",
        "match": match,
        "message": message,
        "suggestion": suggestion,
        "uid": sample.uid,
        "title": sample.title,
        "block_label": sample.block_label,
        "path": str(source_path) if source_path else "",
        "line": sample.line,
        "column": sample.column,
        "excerpt": sample.sentence,
    }


def _body_sample_finding(
    sample: BodySample,
    *,
    match: re.Match[str],
    source_text: str,
    source_path: str | Path,
    rule_id: str,
    category: str,
    message: str,
    suggestion: str,
) -> dict[str, Any]:
    location = _body_location(sample, match, source_text=source_text)
    return {
        "rule_id": rule_id,
        "category": category,
        "severity": "warning",
        "match": match.group(0),
        "message": message,
        "suggestion": suggestion,
        "uid": sample.uid,
        "title": sample.title,
        "block_label": sample.block_label,
        "path": str(source_path) if source_path else "",
        "line": location["line"],
        "column": location["column"],
        "excerpt": location["excerpt"],
    }


def _body_location(
    sample: BodySample,
    match: re.Match[str],
    *,
    source_text: str,
) -> dict[str, Any]:
    excerpt, excerpt_offset = _sentence_containing(sample.body, match.start())
    relative_offset = excerpt_offset if excerpt_offset >= 0 else match.start()
    absolute_offset = sample.body_offset + relative_offset if sample.body_offset >= 0 else relative_offset
    line_start = source_text.rfind("\n", 0, absolute_offset) + 1 if source_text else 0
    return {
        "uid": sample.uid,
        "title": sample.title,
        "block_label": sample.block_label,
        "line": source_text.count("\n", 0, absolute_offset) + 1 if source_text else 1,
        "column": absolute_offset - line_start + 1 if source_text else relative_offset + 1,
        "excerpt": excerpt or match.group(0),
        "match": match.group(0),
    }


def _sentence_containing(body: str, offset: int) -> tuple[str, int]:
    for match in SENTENCE_RE.finditer(safe_text(body)):
        if match.start() <= offset < match.end():
            raw = match.group(0)
            leading = len(raw) - len(raw.lstrip())
            return raw.strip(), match.start() + leading
    return "", -1


def _find_body_offset(source_text: str, body: str, cursor: int) -> int:
    if not source_text or not body:
        return -1
    offset = source_text.find(body, max(0, cursor))
    if offset >= 0:
        return offset
    return source_text.find(body)


def _string_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in (safe_text(raw) for raw in value) if item)
