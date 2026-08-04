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


def _live_source() -> dict:
    return {
        "status": "ready",
        "master_snapshot_id": "sha256:" + "a" * 64,
        "generated_at_utc": "2026-07-29T00:00:00Z",
        "workspace_id": "workspace-1",
        "scheme_id": "scheme-1",
        "product_uids": ["FSY032", "FSY033"],
        "featured_products": [{"uid": "FSY032", "title": "Featured item"}],
    }


def test_confirm_phase7_selection_writes_explicit_hash_bound_state(tmp_path: Path) -> None:
    pipeline = _pipeline(tmp_path)

    result = confirm_phase7_selection(
        pipeline,
        output_branch="final_mp4",
        account="荣荣",
        product_card_template_id="muban-rongrong-1",
        product_media_mode="video_preferred",
        product_order_strategy="price_segment_shuffle",
        mode="standard",
        confirmed_at="2026-07-19T12:00:00+08:00",
    )

    payload = json.loads(pipeline.read_text(encoding="utf-8"))
    confirmation = payload["phases"]["assembly"]["selection_confirmation"]
    assert result["selection_hash"].startswith("sha256:")
    assert confirmation["source"] == "explicit_user_confirmation"
    assert confirmation["selection_hash"] == result["selection_hash"]


def test_schema_v3_phase7_confirmation_binds_current_master_source(tmp_path: Path) -> None:
    pipeline = _pipeline(tmp_path)
    payload = json.loads(pipeline.read_text(encoding="utf-8"))
    payload["schema_version"] = 3
    pipeline.write_text(json.dumps(payload), encoding="utf-8")

    confirmed = confirm_phase7_selection(
        pipeline,
        output_branch="final_mp4",
        account="xiaobo",
        product_card_template_id="muban-xiaobo-1",
        product_media_mode="video_preferred",
        product_order_strategy="price_segment_shuffle",
        mode="top",
        top_uids="FSY032",
        source_snapshot=_live_source(),
    )
    validated = validated_phase7_selection(
        pipeline,
        required_output="final_mp4",
        account="xiaobo",
        product_card_template_id="muban-xiaobo-1",
        product_media_mode="video_preferred",
        product_order_strategy="price_segment_shuffle",
        mode="top",
        top_uids="FSY032",
    )

    assert confirmed["confirmation"]["source_snapshot"]["master_snapshot_id"] == "sha256:" + "a" * 64
    assert validated["source_snapshot"]["featured_products"] == [{"uid": "FSY032", "title": "Featured item"}]


def test_reconfirming_changed_product_set_clears_existing_order_lock(tmp_path: Path) -> None:
    pipeline = _pipeline(tmp_path)
    payload = json.loads(pipeline.read_text(encoding="utf-8"))
    payload["schema_version"] = 3
    payload["episode_id"] = "episode:locked-order"
    payload["phases"]["assembly"]["product_order_lock"] = {
        "version": 1,
        "status": "locked",
        "episode_id": "episode:locked-order",
        "product_uids": ["FSY032", "FSY033"],
        "product_uids_hash": "sha256:placeholder",
    }
    pipeline.write_text(json.dumps(payload), encoding="utf-8")

    same_source = _live_source()
    same_source["product_uids"] = ["FSY033", "FSY032"]
    confirm_phase7_selection(
        pipeline,
        output_branch="final_mp4",
        account="xiaobo",
        product_card_template_id="muban-xiaobo-1",
        product_media_mode="video_preferred",
        product_order_strategy="price_segment_shuffle",
        mode="top",
        top_uids="FSY032",
        source_snapshot=same_source,
    )
    same_saved = json.loads(pipeline.read_text(encoding="utf-8"))
    assert same_saved["phases"]["assembly"]["product_order_lock"]["product_uids"] == [
        "FSY032",
        "FSY033",
    ]

    changed_source = _live_source()
    changed_source["product_uids"] = ["FSY032", "FSY034"]

    confirm_phase7_selection(
        pipeline,
        output_branch="final_mp4",
        account="xiaobo",
        product_card_template_id="muban-xiaobo-1",
        product_media_mode="video_preferred",
        product_order_strategy="price_segment_shuffle",
        mode="top",
        top_uids="FSY032",
        source_snapshot=changed_source,
    )

    saved = json.loads(pipeline.read_text(encoding="utf-8"))
    assert "product_order_lock" not in saved["phases"]["assembly"]


def test_schema_v3_phase7_confirmation_without_live_source_cannot_render(tmp_path: Path) -> None:
    pipeline = _pipeline(tmp_path)
    payload = json.loads(pipeline.read_text(encoding="utf-8"))
    payload["schema_version"] = 3
    pipeline.write_text(json.dumps(payload), encoding="utf-8")
    confirm_phase7_selection(
        pipeline,
        output_branch="final_mp4",
        account="xiaobo",
        product_card_template_id="muban-xiaobo-1",
        product_media_mode="video_preferred",
        product_order_strategy="price_segment_shuffle",
        mode="standard",
    )

    with pytest.raises(Phase7SelectionError, match="Master live-source"):
        validated_phase7_selection(
            pipeline,
            required_output="final_mp4",
            account="xiaobo",
            product_card_template_id="muban-xiaobo-1",
            product_media_mode="video_preferred",
            product_order_strategy="price_segment_shuffle",
            mode="standard",
        )
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
        output_branch="final_mp4",
        account="荣荣",
        product_card_template_id="muban-rongrong-1",
        product_media_mode="video_preferred",
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
            product_order_strategy="stable",
            mode="standard",
        )

    assert caught.value.code == "phase7_selection_mismatch"


def test_formal_render_accepts_confirmed_final_mp4_output(tmp_path: Path) -> None:
    pipeline = _pipeline(tmp_path)
    confirmed = confirm_phase7_selection(
        pipeline,
        output_branch="final_mp4",
        account="荣荣",
        product_card_template_id="muban-rongrong-1",
        product_media_mode="video_preferred",
        product_order_strategy="stable",
        mode="top",
        top_uids="FSY032,FSY033",
    )

    validated = validated_phase7_selection(
        pipeline,
        required_output="final_mp4",
        account="荣荣",
        product_card_template_id="muban-rongrong-1",
        product_media_mode="video_preferred",
        product_order_strategy="stable",
        mode="top",
        top_uids="FSY032,FSY033",
    )

    assert validated["selection_hash"] == confirmed["selection_hash"]
