from __future__ import annotations

import pytest

from bworkflow_sql.publishing_delivery import (
    PublishingDeliveryError,
    _contract_hash,
    _delivery_contract,
    upload_approved_publishing_assets,
)


def test_delivery_contract_requires_publish_copy_and_one_link_material_branch() -> None:
    with pytest.raises(PublishingDeliveryError, match="标题.*简介.*链接资料"):
        _delivery_contract(
            {"id": "task-1", "blue_link_status": "partial"},
            video_sha256="a" * 64,
            video_size=1,
            cover_sha256="b" * 64,
            cover_size=2,
        )


def test_delivery_contract_accepts_ordinary_product_links() -> None:
    contract = _delivery_contract(
        {
            "id": "task-1",
            "title": "标题",
            "description": "简介",
            "product_links": "https://example.test/product",
            "blue_link_status": "partial",
        },
        video_sha256="a" * 64,
        video_size=1,
        cover_sha256="b" * 64,
        cover_size=2,
    )

    assert _contract_hash(contract) == _contract_hash(dict(contract))
    assert contract["artifacts"]["full_mp4"]["size_bytes"] == 1
    assert contract["link_material"]["branch"] == "product_links"


def test_delivery_contract_accepts_complete_blue_links_in_pinned_comment() -> None:
    contract = _delivery_contract(
        {
            "id": "task-1",
            "title": "标题",
            "description": "简介",
            "product_links": "",
            "pinned_comment": "完整蓝链文本",
            "blue_link_status": "complete",
        },
        video_sha256="a" * 64,
        video_size=1,
        cover_sha256="b" * 64,
        cover_size=2,
    )

    assert contract["link_material"]["branch"] == "blue_links"
    assert "pinned_comment_sha256" in contract["link_material"]


def test_upload_requires_explicit_archive_confirmation(tmp_path) -> None:
    pipeline = tmp_path / ".pipeline.json"
    pipeline.write_text("{}", encoding="utf-8")

    with pytest.raises(PublishingDeliveryError, match="confirm-upload-archive"):
        upload_approved_publishing_assets(pipeline)
