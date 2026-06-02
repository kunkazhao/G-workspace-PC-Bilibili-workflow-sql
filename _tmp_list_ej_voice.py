"""列出有线耳机配音目录下的所有 wav 文件。"""
from __future__ import annotations

from pathlib import Path

VOICE_ROOT = Path(r"G:\2026项目-b站\素材-配音")
CATEGORY = "数码-有线耳机"


def main() -> None:
    category_dir = VOICE_ROOT / CATEGORY
    if not category_dir.exists():
        print(f"目录不存在: {category_dir}")
        return

    print(f"扫描目录: {category_dir}\n")
    for user_dir in sorted(category_dir.iterdir()):
        if not user_dir.is_dir():
            continue
        wavs = sorted(user_dir.glob("*.wav"))
        print(f"【{user_dir.name}】{len(wavs)} 个 wav 文件")
        for w in wavs:
            print(f"  {w.name}")
        print()


if __name__ == "__main__":
    main()
