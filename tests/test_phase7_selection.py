import json
from pathlib import Path

import pytest

from bworkflow_sql.phase7_selection import (
    Phase7SelectionError,
    confirm_phase7_selection,
    validated_phase7_selection,
)


def _pipeline(tmp_path: Path) -> Path:
    path = tmp_path / ".pipeline.json"
    path.write_text(
        json.dumps({"bworkflow_project_id": 18, "phases": {"assembly": {"status": "pending"}}}),
        encoding="utf-8",
    )
    return path


def test_confirm_phase7_selection_writes_explicit_hash_bound_state(tmp_path: Path) -> None:
    pipeline = _pipeline(tmp_path)

    result = confirm_phase7_selection(
        pipeline,
        output_branch="final_mp4",
        account="荣荣",
        product_card_template_id="muban-rongrong-1",
        product_media_mode="cover_only",
        product_order_strategy="price_segment_shuffle",
        mode="standard",
        confirmed_at="2026-07-19T12:00:00+08:00",
    )

    payload = json.loads(pipeline.read_text(encoding="utf-8"))
    confirmation = payload["phases"]["assembly"]["selection_confirmation"]
    assert result["selection_hash"].startswith("sha256:")
    assert confirmation["source"] == "explicit_user_confirmation"
    assert confirmation["selection_hash"] == result["selection_hash"]
    assert confirmation["selection"]["product_media_mode"] == "cover_only"
    assert payload["phases"]["assembly"]["generate_jianying_draft"] is False


def test_formal_render_rejects_missing_phase7_confirmation(tmp_path: Path) -> None:
    pipeline = _pipeline(tmp_path)

    with pytest.raises(Phase7SelectionError) as caught:
        validated_phase7_selection(
            pipeline,
            required_output="final_mp4",
            account="荣荣",
            product_card_template_id="muban-rongrong-1",
            product_media_mode="video_preferred",
            product_order_strategy="price_segment_shuffle",
            mode="standard",
        )

    assert caught.value.code == "phase7_selection_unconfirmed"


def test_formal_render_rejects_arguments_that_differ_from_user_confirmation(tmp_path: Path) -> None:
    pipeline = _pipeline(tmp_path)
    confirm_phase7_selection(
        pipeline,
        output_branch="both",
        account="荣荣",
        product_card_template_id="muban-rongrong-1",
        product_media_mode="cover_only",
        product_order_strategy="price_segment_shuffle",
        mode="standard",
    )

    with pytest.raises(Phase7SelectionError) as caught:
        validated_phase7_selection(
            pipeline,
            required_output="final_mp4",
            account="荣荣",
            product_card_template_id="muban-rongrong-1",
            product_media_mode="video_preferred",
            product_order_strategy="price_segment_shuffle",
            mode="standard",
        )

    assert caught.value.code == "phase7_selection_mismatch"


def test_formal_render_accepts_both_branch_for_each_confirmed_output(tmp_path: Path) -> None:
    pipeline = _pipeline(tmp_path)
    confirmed = confirm_phase7_selection(
        pipeline,
        output_branch="both",
        account="荣荣",
        product_card_template_id="muban-rongrong-1",
        product_media_mode="video_preferred",
        product_order_strategy="stable",
        mode="top",
        top_uids="FSY032,FSY033",
    )

    validated = validated_phase7_selection(
        pipeline,
        required_output="jianying_draft",
        account="荣荣",
        product_card_template_id="muban-rongrong-1",
        product_media_mode="video_preferred",
        product_order_strategy="stable",
        mode="top",
        top_uids="FSY032,FSY033",
    )

    assert validated["selection_hash"] == confirmed["selection_hash"]
