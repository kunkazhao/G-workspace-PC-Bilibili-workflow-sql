from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from bworkflow_sql import final_video_pipeline
from bworkflow_sql.settings import INTERNAL_WORKSPACE_ROOT


pytestmark = pytest.mark.usefixtures("isolated_final_video_workspace")


REAL_PROJECT_ROOT = INTERNAL_WORKSPACE_ROOT.resolve() / "project-23"
REAL_GUARDED_ROOTS = (
    REAL_PROJECT_ROOT / "runs",
    REAL_PROJECT_ROOT / "render" / "final-video-cache",
)


def _tree_snapshot(roots: tuple[Path, ...]) -> dict[str, dict[str, int | str]]:
    snapshot: dict[str, dict[str, int | str]] = {}
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(item for item in root.rglob("*") if item.is_file()):
            stat = path.stat()
            snapshot[str(path.resolve())] = {
                "size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
    return snapshot


def test_fake_final_video_run_does_not_write_real_project_workspace(tmp_path: Path):
    package_path = tmp_path / "render-package.json"
    job_package_path = tmp_path / "job" / "render-package.json"
    output_mp4 = tmp_path / "final.mp4"
    package_path.write_text(
        json.dumps({"schemaVersion": "1.0.0", "segments": []}),
        encoding="utf-8",
    )

    class FakeWorkflow:
        def regenerate_product_card_images(self, *args, **kwargs):
            return {"ok": True, "regenerated": [], "skipped": []}

        def prepare_product_recommendation_output(self, *args, **kwargs):
            return {
                "ok": True,
                "package_path": str(package_path),
                "next": {"target_mp4": str(output_mp4)},
            }

    class FakeCutMeAdapter:
        def render_final(self, package, *, output_path, cache_dir):
            job_package_path.parent.mkdir(parents=True, exist_ok=True)
            job_package_path.write_text(package_path.read_text(encoding="utf-8"), encoding="utf-8")
            output_mp4.write_bytes(b"mp4")
            return {
                "ok": True,
                "artifacts": {
                    "source_package_path": str(package_path),
                    "job_package_path": str(job_package_path),
                    "output_path": str(output_mp4),
                },
                "cache": None,
                "timings": {"total_ms": 1},
            }

    before = _tree_snapshot(REAL_GUARDED_ROOTS)

    final_video_pipeline.run_final_video_pipeline(
        FakeWorkflow(),
        project_id=23,
        account_label="test-account",
        product_image_mode="missing",
        package_output_path=package_path,
        output_path=output_mp4,
        acceptance_mode="none",
        cutme_root=tmp_path,
        cutme_adapter=FakeCutMeAdapter(),
        probe_video=lambda path: {"duration": 1.0, "video": "h264", "audio": "aac"},
    )

    after = _tree_snapshot(REAL_GUARDED_ROOTS)
    assert after == before
