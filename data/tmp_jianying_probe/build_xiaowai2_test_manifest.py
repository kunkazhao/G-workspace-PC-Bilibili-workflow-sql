from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE_MANIFEST = ROOT / "tmp_jianying_probe" / "xiaoran1-three-products.manifest.json"
OUTPUT_MANIFEST = Path(__file__).resolve().parent / "xiaowai2-position-test.manifest.json"
TEST_VIDEO = Path(__file__).resolve().parent / "xiaowai2-slot-test.mp4"
TEST_AUDIO = Path(r"G:\2026项目-b站\素材-配音\公共-结尾\小歪\结尾-小歪.wav")
XIAOWAI2_SLOT = {
    "x": -843,
    "y": -34,
    "width": 1037,
    "height": 528,
    "coordinate_mode": "clip_transform_pixels",
}
XIAOWAI2_IMAGES = [
    Path(__file__).resolve().parent / "xiaowai2-html-render" / "xiaowai2--default.png",
    Path(__file__).resolve().parent / "xiaowai2-html-render" / "xiaowai2--long-title.png",
    Path(__file__).resolve().parent / "xiaowai2-html-render" / "xiaowai2--long-spec.png",
]


def main() -> None:
    if not TEST_VIDEO.exists():
        raise SystemExit(f"Test video missing: {TEST_VIDEO}")
    if not TEST_AUDIO.exists():
        raise SystemExit(f"Test audio missing: {TEST_AUDIO}")

    payload = json.loads(SOURCE_MANIFEST.read_text(encoding="utf-8-sig"))
    entries = payload.get("entries") or []
    product_entries = [entry for entry in entries if entry.get("type") == "product"][: len(XIAOWAI2_IMAGES)]
    if len(product_entries) < len(XIAOWAI2_IMAGES):
        raise SystemExit(f"Need {len(XIAOWAI2_IMAGES)} product entries, found {len(product_entries)}")

    for index, (entry, image_path) in enumerate(zip(product_entries, XIAOWAI2_IMAGES), start=1):
        if not image_path.exists():
            raise SystemExit(f"Image missing: {image_path}")
        entry["order_index"] = index
        entry["section_order"] = index
        entry["account_label"] = "小歪"
        entry["account_id"] = "小歪"
        entry["audio_path"] = str(TEST_AUDIO)
        entry["image_path"] = str(image_path)
        entry["video_path"] = ""
        entry["display_video_path"] = str(TEST_VIDEO)
        entry["display_video_slot"] = dict(XIAOWAI2_SLOT)

    test_payload = dict(payload)
    test_payload["entries"] = product_entries
    test_payload["project_name"] = "小歪-模板2位置测试"
    test_payload["category"] = "有线耳机"
    test_payload["account_label"] = "小歪"
    test_payload["account_id"] = "小歪"
    test_payload["mode"] = "xiaowai2_position_probe"
    test_payload["probe_note"] = "Xiaowai template 2 position probe with red-frame display video."
    test_payload["display_video_slot"] = dict(XIAOWAI2_SLOT)

    OUTPUT_MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_MANIFEST.write_text(
        json.dumps(test_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(OUTPUT_MANIFEST)


if __name__ == "__main__":
    main()
