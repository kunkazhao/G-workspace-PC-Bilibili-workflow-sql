from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path


FFMPEG = Path(r"G:\workspace\tools-quzimu\node_modules\ffmpeg-static\ffmpeg.exe")
VIDEOS = [
    Path(r"G:\2026项目-b站\素材-剪辑\roll-b素材\数码-耳夹蓝牙耳机\89元-LY040-音贝奇ClipLite.mov"),
    Path(r"G:\2026项目-b站\素材-剪辑\roll-b素材\数码-耳夹蓝牙耳机\199元-LY006-水落雨 音乐胶囊.mov"),
    Path(r"G:\2026项目-b站\素材-剪辑\roll-b素材\数码-耳夹蓝牙耳机\139元-EJLY061-音贝奇ClipAir.mov"),
]


def sample_raw(path: Path, crop: str, pix_fmt: str, filters_prefix: str = "") -> list[int]:
    with tempfile.NamedTemporaryFile(suffix=".raw", delete=False) as handle:
        raw_path = Path(handle.name)
    try:
        subprocess.run(
            [
                str(FFMPEG),
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(path),
                "-vf",
                f"{filters_prefix}{crop}",
                "-frames:v",
                "1",
                "-f",
                "rawvideo",
                "-pix_fmt",
                pix_fmt,
                str(raw_path),
            ],
            check=True,
        )
        return list(raw_path.read_bytes())
    finally:
        raw_path.unlink(missing_ok=True)


def main() -> None:
    rows = []
    for video in VIDEOS:
        item = {
            "path": str(video),
            "exists": video.exists(),
        }
        if video.exists():
            item["top_left_alpha_2x2"] = sample_raw(video, "crop=2:2:0:0", "gray", "alphaextract,")
            item["inside_alpha_2x2"] = sample_raw(video, "crop=2:2:80:80", "gray", "alphaextract,")
            item["top_left_rgba_1x1"] = sample_raw(video, "crop=1:1:0:0", "rgba")
            item["inside_rgba_1x1"] = sample_raw(video, "crop=1:1:80:80", "rgba")
        rows.append(item)
    print(json.dumps(rows, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
