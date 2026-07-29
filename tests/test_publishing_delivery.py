from __future__ import annotations

import pytest

from bworkflow_sql.publishing_delivery import (
    PublishingDeliveryError,
    _contract_hash,
    _delivery_contract,
    upload_approved_publishing_assets,
)


def test_delivery_contract_requires_publish_copy_and_complete_blue_links() -> None:
    with pytest.raises(PublishingDeliveryError, match="标题.*简介.*商品链接.*补充蓝链"):
        _delivery_contract(
            {"id": "task-1", "blue_link_status": "partial"},
            video_sha256="a" * 64,
            video_size=1,
            cover_sha256="b" * 64,
            cover_size=2,
        )


def test_delivery_contract_hash_is_stable_for_the_same_authorized_facts() -> None:
    contract = _delivery_contract(
        {
            "id": "task-1",
            "title": "标题",
            "description": "简介",
            "product_links": "https://example.test/product",
            "blue_link_status": "complete",
        },
        video_sha256="a" * 64,
        video_size=1,
        cover_sha256="b" * 64,
        cover_size=2,
    )

    assert _contract_hash(contract) == _contract_hash(dict(contract))
    assert contract["artifacts"]["full_mp4"]["size_bytes"] == 1


def test_upload_requires_explicit_archive_confirmation(tmp_path) -> None:
    pipeline = tmp_path / ".pipeline.json"
    pipeline.write_text("{}", encoding="utf-8")

    with pytest.raises(PublishingDeliveryError, match="confirm-upload-archive"):
        upload_approved_publishing_assets(pipeline)
