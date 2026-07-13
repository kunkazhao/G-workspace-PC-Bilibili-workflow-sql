from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from bworkflow_sql.production_delivery import (
    record_pipeline_video_path,
    resolve_project_delivery_dir,
)


def test_resolve_project_delivery_dir_creates_and_reuses_pipeline_output_dir(tmp_path: Path):
    pipeline_path = tmp_path / ".pipeline.json"
    pipeline_path.write_text("{}", encoding="utf-8")

    output_dir = resolve_project_delivery_dir(
        project={"name": "家居-冲牙器"},
        account_label="荣荣",
        pipeline_path=pipeline_path,
        delivery_root=tmp_path / "deliveries",
        now=datetime(2026, 7, 13),
    )

    assert output_dir == (tmp_path / "deliveries" / "0713-冲牙器-荣荣").resolve()
    assert output_dir.is_dir()
    payload = json.loads(pipeline_path.read_text(encoding="utf-8"))
    assert payload["output_dir"] == str(output_dir)

    reused = resolve_project_delivery_dir(
        project={"name": "不会覆盖-旧目录"},
        account_label="其他账号",
        pipeline_path=pipeline_path,
        delivery_root=tmp_path / "other-root",
        now=datetime(2026, 8, 1),
    )
    assert reused == output_dir


def test_record_pipeline_video_path_keeps_absolute_and_relative_paths(tmp_path: Path):
    output_dir = (tmp_path / "0713-冲牙器-荣荣").resolve()
    output_dir.mkdir()
    pipeline_path = tmp_path / ".pipeline.json"
    pipeline_path.write_text(
        json.dumps({"output_dir": str(output_dir)}, ensure_ascii=False),
        encoding="utf-8",
    )
    intro = output_dir / "引言视频.mp4"
    intro.write_bytes(b"mp4")

    record_pipeline_video_path(pipeline_path, key="intro_video", video_path=intro)

    payload = json.loads(pipeline_path.read_text(encoding="utf-8"))
    assert payload["paths"]["intro_video"] == str(intro)
    assert payload["paths"]["intro_video_relative"] == "引言视频.mp4"
