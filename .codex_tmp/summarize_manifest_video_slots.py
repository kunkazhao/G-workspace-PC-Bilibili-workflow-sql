from __future__ import annotations

import json
from pathlib import Path


MANIFEST_DIR = Path("data/workspace/project-13/manifests")
TARGETS = [
    "音贝奇ClipLite",
    "水落雨 音乐胶囊",
    "音贝奇ClipAir",
]


def main() -> None:
    rows: list[dict[str, object]] = []
    for path in sorted(MANIFEST_DIR.glob("*.manifest.json")):
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        for entry in payload.get("entries", []):
            name = str(entry.get("product_name") or "")
            if not any(target in name for target in TARGETS):
                continue
            slot = entry.get("display_video_slot")
            rows.append(
                {
                    "manifest": path.name,
                    "product_name": name,
                    "display_video": Path(str(entry.get("display_video_path") or "")).name,
                    "suffix": Path(str(entry.get("display_video_path") or "")).suffix,
                    "slot": slot,
                    "round_corner": slot.get("round_corner") if isinstance(slot, dict) else None,
                }
            )
    print(json.dumps(rows, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
