from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import re
from typing import Any, Iterable

from .master_contracts import MasterPriceRange, MasterSnapshotProduct


_DECIMAL_PATTERN = re.compile(r"^[0-9]+(?:\.[0-9]+)?$")


@dataclass(frozen=True, slots=True)
class DynamicPreflightIssue:
    code: str
    product_uid: str
    field: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "product_uid": self.product_uid,
            "field": self.field,
            "message": self.message,
        }


@dataclass(frozen=True, slots=True)
class DynamicProductContext:
    product_uid: str
    title: str
    display_price: str
    specs: tuple[tuple[str, str], ...]
    review: str
    price_band_label: str
    category_label: str
    media_kind: str
    media_asset: str
    voice_asset: str
    spoken_text: str
    source_script_block_id: int

    @property
    def data_map(self) -> dict[str, str]:
        return {
            "title": self.title,
            "displayPrice": self.display_price,
            "review": self.review,
            "priceBandLabel": self.price_band_label,
            "categoryLabel": self.category_label,
            "productMedia": self.media_asset,
        }

    def template_validation_card(self) -> dict[str, Any]:
        return {
            "coverAsset": self.media_asset,
            "dataMap": {
                "title": self.title,
                "price": self.display_price,
                "remark": self.review,
                "priceBandLabel": self.price_band_label,
                "categoryLabel": self.category_label,
            },
            "slots": [
                {"label": label, "value": value}
                for label, value in self.specs
            ],
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "product_uid": self.product_uid,
            "data_map": self.data_map,
            "specs": [
                {"label": label, "value": value}
                for label, value in self.specs
            ],
            "media_kind": self.media_kind,
            "media_asset": self.media_asset,
            "voice_asset": self.voice_asset,
            "spoken_text": self.spoken_text,
            "source_script_block_id": self.source_script_block_id,
        }


@dataclass(frozen=True, slots=True)
class ParsedPriceRange:
    minimum: Decimal
    maximum: Decimal
    label: str


def parse_non_negative_decimal(value: Any) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise ValueError("price must be a finite non-negative decimal")
    text = format(value, "f") if isinstance(value, Decimal) else str(value).strip()
    if not _DECIMAL_PATTERN.fullmatch(text):
        raise ValueError("price must be a finite non-negative decimal")
    try:
        amount = Decimal(text)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("price must be a finite non-negative decimal") from exc
    if not amount.is_finite() or amount < 0:
        raise ValueError("price must be a finite non-negative decimal")
    return amount


def format_display_price(value: Any) -> str:
    amount = parse_non_negative_decimal(value)
    rounded = amount.to_integral_value(rounding=ROUND_HALF_UP)
    return f"{rounded:.0f}元"


def category_leaf_name(value: Any) -> str:
    parts = [part.strip() for part in str(value or "").split("-") if part.strip()]
    return parts[-1] if parts else ""


def validate_price_ranges(
    ranges: Iterable[MasterPriceRange],
) -> tuple[tuple[ParsedPriceRange, ...], tuple[DynamicPreflightIssue, ...]]:
    parsed: list[ParsedPriceRange] = []
    issues: list[DynamicPreflightIssue] = []
    for index, item in enumerate(ranges):
        label = str(item.label or "").strip()
        try:
            minimum = parse_non_negative_decimal(item.min_amount)
            maximum = parse_non_negative_decimal(item.max_amount)
            if minimum >= maximum:
                raise ValueError("minimum must be lower than maximum")
            if not label:
                raise ValueError("label is required")
        except ValueError as exc:
            issues.append(
                DynamicPreflightIssue(
                    code="invalid_price_range",
                    product_uid="",
                    field=f"price_ranges[{index}]",
                    message=f"Invalid Master price range at index {index}: {exc}",
                )
            )
            continue
        parsed.append(ParsedPriceRange(minimum=minimum, maximum=maximum, label=label))
    if not parsed and not issues:
        issues.append(
            DynamicPreflightIssue(
                code="invalid_price_range",
                product_uid="",
                field="price_ranges",
                message="Master scheme has no configured price ranges.",
            )
        )
    return tuple(parsed), tuple(issues)


def match_price_band(value: Any, ranges: Iterable[MasterPriceRange]) -> str:
    parsed, issues = validate_price_ranges(ranges)
    if issues:
        raise ValueError(issues[0].message)
    amount = parse_non_negative_decimal(value)
    for item in parsed:
        if item.minimum <= amount <= item.maximum:
            return item.label
    raise ValueError("price does not match a configured range")


def build_dynamic_product_context(
    product: MasterSnapshotProduct,
    *,
    parsed_price_ranges: tuple[ParsedPriceRange, ...],
    category_label: str,
    media_kind: str,
    media_asset: str,
    voice_asset: str,
    spoken_text: str,
    source_script_block_id: int,
) -> tuple[DynamicProductContext | None, tuple[DynamicPreflightIssue, ...]]:
    uid = str(product.uid or "").strip()
    issues: list[DynamicPreflightIssue] = []
    title = str(product.title or "").strip()
    if not title:
        issues.append(
            DynamicPreflightIssue(
                code="missing_product_title",
                product_uid=uid,
                field="title",
                message="Current Master snapshot product title is empty.",
            )
        )

    amount: Decimal | None = None
    display_price = ""
    try:
        amount = parse_non_negative_decimal(product.price.amount)
        display_price = format_display_price(amount)
    except ValueError as exc:
        issues.append(
            DynamicPreflightIssue(
                code="invalid_product_price",
                product_uid=uid,
                field="price.amount",
                message=str(exc),
            )
        )

    price_band_label = ""
    if amount is not None and parsed_price_ranges:
        # Master range order is authoritative. Inclusive overlapping boundaries
        # intentionally resolve to the first configured range.
        price_band_label = next(
            (
                item.label
                for item in parsed_price_ranges
                if item.minimum <= amount <= item.maximum
            ),
            "",
        )
        if not price_band_label:
            issues.append(
                DynamicPreflightIssue(
                    code="price_band_not_matched",
                    product_uid=uid,
                    field="price.amount",
                    message="Product price does not match any configured Master price range.",
                )
            )

    if not voice_asset:
        issues.append(
            DynamicPreflightIssue(
                code="missing_product_voice",
                product_uid=uid,
                field="voice",
                message="Current product script has no account-matched ready local voice file.",
            )
        )
    if not media_asset:
        issues.append(
            DynamicPreflightIssue(
                code="missing_product_media",
                product_uid=uid,
                field="productMedia",
                message="Product has neither a ready local video nor a valid current cover.",
            )
        )
    if issues:
        return None, tuple(issues)

    context = DynamicProductContext(
        product_uid=uid,
        title=title,
        display_price=display_price,
        specs=tuple(
            (str(slot.label).strip(), str(slot.value).strip())
            for slot in product.card.spec_slots
            if str(slot.label).strip() and str(slot.value).strip()
        ),
        review=str(product.card.remark or "").strip(),
        price_band_label=price_band_label,
        category_label=category_leaf_name(category_label),
        media_kind=media_kind,
        media_asset=media_asset,
        voice_asset=voice_asset,
        spoken_text=spoken_text,
        source_script_block_id=source_script_block_id,
    )
    return context, ()
