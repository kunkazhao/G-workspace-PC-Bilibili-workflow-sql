from __future__ import annotations

import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
CUTME_ROOT = Path(r"G:\workspace\赵二-工具-CutMe")

CASES = [
    {
        "name": "earbuds",
        "project_id": 12,
        "package": ROOT / "data/workspace/project-12/render/probe-xiaobo-template1-earbuds-final.json",
        "manifest": ROOT / "data/workspace/project-12/render/probe-xiaobo-template1-earbuds-jianying.jianying.manifest.json",
        "job_package": CUTME_ROOT / "render_jobs/job_probe_xiaobo_template1_earbuds/render-package.json",
        "mp4": CUTME_ROOT / "render_jobs/job_probe_xiaobo_template1_earbuds/probe-xiaobo-template1-earbuds.mp4",
        "expected_video_segments": 1,
        "expected_cover_segments": 1,
    },
    {
        "name": "quickdry",
        "project_id": 22,
        "package": ROOT / "data/workspace/project-22/render/probe-xiaobo-template1-quickdry-final.json",
        "manifest": ROOT / "data/workspace/project-22/render/probe-xiaobo-template1-quickdry-jianying.jianying.manifest.json",
        "job_package": CUTME_ROOT / "render_jobs/job_probe_xiaobo_template1_quickdry/render-package.json",
        "mp4": CUTME_ROOT / "render_jobs/job_probe_xiaobo_template1_quickdry/probe-xiaobo-template1-quickdry.mp4",
        "expected_video_segments": 0,
        "expected_cover_segments": 2,
    },
]


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def assert_template(package: dict[str, Any]) -> None:
    output = package["output"]
    template = output["productCardTemplate"]
    assert output["productCardTemplateId"] == "muban-xiaobo-1"
    assert output["productCardTemplateVersion"] == "1.0.2"
    assert template == {
        "id": "muban-xiaobo-1",
        "displayName": "小博模板1",
        "version": "1.0.2",
        "confirmed": True,
        "selectionSource": "explicit",
    }


def assert_product_card(segment: dict[str, Any]) -> None:
    card = segment["productCard"]
    assert card["templateId"] == "muban-xiaobo-1"
    assert card["templateVersion"] == "1.0.2"
    assert card["outputCanvas"] == {"width": 1920, "height": 1080}
    assert card["cardPlacement"] == {
        "x": 0,
        "y": 0,
        "width": 1920,
        "height": 960,
        "anchor": "top",
        "bottomReserve": 120,
    }
    assert card["coverMediaSlot"] == {
        "x": 442,
        "y": 69,
        "width": 496,
        "height": 279,
        "sourceWidth": 970,
        "sourceHeight": 480,
        "fitMode": "contain",
        "anchor": "center",
    }


def assert_no_absolute_drive_paths(path: Path) -> None:
    text = path.read_text(encoding="utf-8-sig")
    assert "G:\\" not in text
    assert "C:\\" not in text


def main() -> None:
    summary: list[dict[str, Any]] = []
    for case in CASES:
        package = read_json(case["package"])
        manifest = read_json(case["manifest"])
        job_package = read_json(case["job_package"])

        assert_template(package)
        assert_template(job_package)
        assert_no_absolute_drive_paths(case["job_package"])

        products = [s for s in package["segments"] if s.get("type") == "product_recommendation"]
        assert len(products) == 2
        for segment in products:
            assert_product_card(segment)
            assert segment.get("imageCardAsset")
            assert segment.get("voiceAsset")

        video_segments = [s for s in products if s.get("videoAsset")]
        cover_segments = [s for s in products if not s.get("videoAsset")]
        assert len(video_segments) == case["expected_video_segments"]
        assert len(cover_segments) == case["expected_cover_segments"]

        entries = [e for e in manifest["entries"] if e.get("type") == "product"]
        assert len(entries) == 2
        manifest_video_entries = [e for e in entries if e.get("display_video_path")]
        assert len(manifest_video_entries) == case["expected_video_segments"]
        for entry in manifest_video_entries:
            assert entry.get("display_video_slot")

        assert case["mp4"].is_file()
        summary.append(
            {
                "name": case["name"],
                "segments": len(package["segments"]),
                "products": len(products),
                "video_segments": len(video_segments),
                "cover_fallback_segments": len(cover_segments),
                "manifest_video_entries": len(manifest_video_entries),
                "mp4": str(case["mp4"]),
                "mp4_bytes": case["mp4"].stat().st_size,
            }
        )

    print(json.dumps({"ok": True, "cases": summary}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
