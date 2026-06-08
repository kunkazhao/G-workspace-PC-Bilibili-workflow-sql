from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont


ROOT = Path(__file__).resolve().parents[1]
ASSET_DIR = ROOT / "packaging" / "assets"
PNG_PATH = ASSET_DIR / "bworkflow_icon.png"
ICO_PATH = ASSET_DIR / "bworkflow_icon.ico"


def rounded_rect_mask(size: int, radius: int) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=255)
    return mask


def vertical_gradient(size: int, top: tuple[int, int, int], bottom: tuple[int, int, int]) -> Image.Image:
    image = Image.new("RGB", (size, size), top)
    pixels = image.load()
    for y in range(size):
        t = y / max(1, size - 1)
        color = tuple(round(top[i] * (1 - t) + bottom[i] * t) for i in range(3))
        for x in range(size):
            pixels[x, y] = color
    return image


def draw_icon(size: int = 1024) -> Image.Image:
    scale = size / 1024
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))

    shadow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow)
    pad = round(88 * scale)
    shadow_draw.rounded_rectangle(
        (pad, pad + round(28 * scale), size - pad, size - pad + round(28 * scale)),
        radius=round(210 * scale),
        fill=(0, 0, 0, 95),
    )
    image.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(round(30 * scale))))

    mask = rounded_rect_mask(size - pad * 2, round(190 * scale))
    card = vertical_gradient(size - pad * 2, (255, 94, 121), (94, 72, 255)).convert("RGBA")
    sheen = Image.new("RGBA", card.size, (0, 0, 0, 0))
    sheen_draw = ImageDraw.Draw(sheen)
    sheen_draw.ellipse(
        (-round(120 * scale), -round(280 * scale), round(840 * scale), round(520 * scale)),
        fill=(255, 255, 255, 58),
    )
    card.alpha_composite(sheen)
    card.putalpha(mask)
    image.alpha_composite(card, (pad, pad))

    rim = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    rim_draw = ImageDraw.Draw(rim)
    rim_draw.rounded_rectangle(
        (pad + round(10 * scale), pad + round(10 * scale), size - pad - round(10 * scale), size - pad - round(10 * scale)),
        radius=round(180 * scale),
        outline=(255, 255, 255, 82),
        width=round(10 * scale),
    )
    image.alpha_composite(rim)

    font_path = Path(r"C:\Windows\Fonts\arialbd.ttf")
    font = ImageFont.truetype(str(font_path), round(575 * scale))
    text = "B"
    text_layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    text_draw = ImageDraw.Draw(text_layer)
    box = text_draw.textbbox((0, 0), text, font=font)
    tw, th = box[2] - box[0], box[3] - box[1]
    x = round((size - tw) / 2 - box[0] - 4 * scale)
    y = round((size - th) / 2 - box[1] - 22 * scale)

    text_shadow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(text_shadow)
    shadow_draw.text((x + round(12 * scale), y + round(20 * scale)), text, font=font, fill=(21, 13, 70, 115))
    image.alpha_composite(text_shadow.filter(ImageFilter.GaussianBlur(round(10 * scale))))

    text_draw.text((x, y), text, font=font, fill=(255, 255, 255, 255))
    highlight = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    highlight_draw = ImageDraw.Draw(highlight)
    highlight_draw.text((x - round(7 * scale), y - round(9 * scale)), text, font=font, fill=(255, 255, 255, 62))
    image.alpha_composite(highlight.filter(ImageFilter.GaussianBlur(round(2 * scale))))
    image.alpha_composite(text_layer)

    sparkle = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    sparkle_draw = ImageDraw.Draw(sparkle)
    cx, cy = round(764 * scale), round(244 * scale)
    r = round(30 * scale)
    points = []
    for i in range(8):
        angle = -math.pi / 2 + i * math.pi / 4
        radius = r if i % 2 == 0 else r * 0.36
        points.append((cx + math.cos(angle) * radius, cy + math.sin(angle) * radius))
    sparkle_draw.polygon(points, fill=(255, 255, 255, 182))
    image.alpha_composite(sparkle)
    return image


def main() -> None:
    ASSET_DIR.mkdir(parents=True, exist_ok=True)
    icon = draw_icon()
    icon.save(PNG_PATH)
    sizes = [16, 24, 32, 48, 64, 128, 256]
    icon.save(ICO_PATH, sizes=[(size, size) for size in sizes])
    print(PNG_PATH)
    print(ICO_PATH)


if __name__ == "__main__":
    main()
