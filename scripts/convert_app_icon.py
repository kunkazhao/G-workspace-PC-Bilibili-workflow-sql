from __future__ import annotations

import os
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ENV = "BWORKFLOW_ICON_SOURCE"
SOURCE_PATH = Path(os.environ[SOURCE_ENV])
ASSET_DIR = ROOT / "packaging" / "assets"
PNG_PATH = ASSET_DIR / "bworkflow_icon.png"
ICO_PATH = ASSET_DIR / "bworkflow_icon.ico"
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)


def square_canvas(image: Image.Image) -> Image.Image:
    rgba = image.convert("RGBA")
    side = max(rgba.size)
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    offset = ((side - rgba.width) // 2, (side - rgba.height) // 2)
    canvas.alpha_composite(rgba, offset)
    return canvas


def main() -> None:
    ASSET_DIR.mkdir(parents=True, exist_ok=True)
    with Image.open(SOURCE_PATH) as image:
        icon = square_canvas(image)
        icon.save(PNG_PATH)
        icon.save(ICO_PATH, sizes=[(size, size) for size in ICO_SIZES])

    print(f"source={SOURCE_PATH}")
    print(f"png={PNG_PATH}")
    print(f"ico={ICO_PATH}")


if __name__ == "__main__":
    main()
