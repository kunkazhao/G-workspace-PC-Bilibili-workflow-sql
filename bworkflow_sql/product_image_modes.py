from __future__ import annotations

from collections.abc import Iterable


MISSING_IMAGE_ISSUES = {"missing_ready_image_binding"}
EXISTING_IMAGE_ISSUES = {
    "wrong_template_binding",
    "unknown_legacy_image_hash",
    "stale_product_image",
}


def regeneration_mode_for_issue_codes(issue_codes: Iterable[str]) -> str:
    codes = {str(code or "").strip() for code in issue_codes}
    has_missing = bool(codes.intersection(MISSING_IMAGE_ISSUES))
    has_existing_issues = bool(codes.intersection(EXISTING_IMAGE_ISSUES))
    if has_missing and has_existing_issues:
        return "all"
    if has_missing:
        return "missing"
    return "stale"
