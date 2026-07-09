from pathlib import Path


SOURCE = Path(
    r"G:\WriteSpace\B站-文案脚本\10_b站文案\发布内容目录\数码-充电宝-按标签特例口播稿.md"
)
OUTPUT = SOURCE.with_name("数码-充电宝-按标签特例口播稿-剪映字幕匹配版.md")


def is_metadata_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    return (
        stripped.startswith("#")
        or stripped.startswith(">")
        or stripped.startswith("<!--")
        or stripped.startswith("- ")
    )


def main() -> None:
    lines = SOURCE.read_text(encoding="utf-8").splitlines()
    output_lines: list[str] = []
    in_body = False
    previous_blank = False

    for line in lines:
        stripped = line.strip()
        if stripped == "## 正文":
            in_body = True
            continue
        if not in_body:
            continue
        if is_metadata_line(line):
            continue
        if not stripped:
            if output_lines and not previous_blank:
                output_lines.append("")
                previous_blank = True
            continue
        output_lines.append(stripped)
        previous_blank = False

    while output_lines and output_lines[-1] == "":
        output_lines.pop()

    OUTPUT.write_text("\n\n".join(part for part in output_lines if part != "") + "\n", encoding="utf-8")
    print(f"source={SOURCE}")
    print(f"output={OUTPUT}")
    print(f"paragraphs={sum(1 for part in output_lines if part)}")


if __name__ == "__main__":
    main()
