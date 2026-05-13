from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE_MANIFEST = ROOT / "workspace" / "project-3" / "manifests" / "5月-小燃.manifest.json"
OUTPUT_MANIFEST = Path(__file__).resolve().parent / "xiaoran1-three-products.manifest.json"
XIAORAN1_SLOT = {
    "x": -830,
    "y": -77,
    "width": 970,
    "height": 590,
    "coordinate_mode": "clip_transform_pixels",
}


def main() -> None:
    payload = json.loads(SOURCE_MANIFEST.read_text(encoding="utf-8-sig"))
    entries = payload.get("entries") or []
    product_entries = [entry for entry in entries if entry.get("type") == "product"][:3]
    if len(product_entries) < 3:
        raise SystemExit(f"Need 3 product entries, found {len(product_entries)} in {SOURCE_MANIFEST}")

    for index, entry in enumerate(product_entries, start=1):
        entry["order_index"] = index
        entry["section_order"] = index
        if entry.get("display_video_path"):
            entry["display_video_slot"] = dict(XIAORAN1_SLOT)

    test_payload = dict(payload)
    test_payload["entries"] = product_entries
    test_payload["mode"] = "xiaoran1_three_product_probe"
    test_payload["created_from"] = str(SOURCE_MANIFEST)
    test_payload["probe_note"] = "Three-product Xiaoran template 1 alignment probe."

    OUTPUT_MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_MANIFEST.write_text(
        json.dumps(test_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(OUTPUT_MANIFEST)


if __name__ == "__main__":
    main()
