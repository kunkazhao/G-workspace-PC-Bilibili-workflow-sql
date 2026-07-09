# -*- coding: utf-8 -*-
import json
import socket
from pathlib import Path


WORKSPACE_ID = "de90965d-29e4-4ac3-9730-0ce1fc85b67c"
SCHEME_ID = "901abfd6-8fb7-4110-ab23-3fee96f7bf5a"
MANIFEST = Path("data/manifests/数码-充电宝-荣荣-品类过渡.manifest.json")
OUTPUT = Path(
    r"G:\WriteSpace\B站-文案脚本\10_b站文案\发布内容目录\数码-充电宝-按标签商品链接.md"
)
SIMPLE_OUTPUT = Path(
    r"G:\WriteSpace\B站-文案脚本\10_b站文案\发布内容目录\数码-充电宝-按标签商品链接-精简版.md"
)


def raw_get(path: str, workspace_id: str = WORKSPACE_ID) -> dict:
    sock = socket.create_connection(("127.0.0.1", 8000), timeout=10)
    raw = (
        f"GET {path} HTTP/1.1\r\n"
        "Host: 127.0.0.1:8000\r\n"
        f"X-Workspace-Id: {workspace_id}\r\n"
        "Connection: close\r\n"
        "\r\n"
    )
    sock.sendall(raw.encode("utf-8"))
    resp = b""
    while True:
        chunk = sock.recv(4096)
        if not chunk:
            break
        resp += chunk
    sock.close()

    header_end = resp.find(b"\r\n\r\n")
    header = resp[:header_end].lower()
    body = resp[header_end + 4 :]
    if b"chunked" in header:
        decoded = b""
        idx = 0
        while idx < len(body):
            line_end = body.find(b"\r\n", idx)
            if line_end == -1:
                break
            size = int(body[idx:line_end], 16)
            if size == 0:
                break
            start = line_end + 2
            decoded += body[start : start + size]
            idx = start + size + 2
        body = decoded
    return json.loads(body.decode("utf-8"))


def pick_link(item: dict) -> tuple[str, str]:
    spec = item.get("spec") if isinstance(item.get("spec"), dict) else {}
    candidates = [
        ("京东蓝链", spec.get("_blue_link")),
        ("京东推广链", spec.get("_promo_link")),
        ("淘宝推广链", spec.get("_tb_promo_link")),
        ("原始链接", item.get("link")),
        ("淘宝链接", item.get("taobao_link")),
    ]
    for label, value in candidates:
        text = str(value or "").strip()
        if text:
            return label, text
    return "缺失", ""


def main() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    grouped: dict[str, list[dict]] = {}
    for entry in manifest.get("entries", []):
        if entry.get("type") != "product":
            continue
        group = str(entry.get("price_range_label") or "未分组")
        grouped.setdefault(group, []).append(entry)

    payload = raw_get(f"/api/schemes/{SCHEME_ID}/summary")
    scheme = payload.get("scheme", payload)
    master_items = {str(item.get("uid") or ""): item for item in scheme.get("items", [])}

    lines = [
        "# 数码-充电宝-按标签商品链接",
        "",
        f"- 来源 manifest: `{MANIFEST}`",
        f"- 来源 Master scheme: `{SCHEME_ID}`",
        "- 链接优先级：京东蓝链 > 京东推广链 > 淘宝推广链 > 原始链接 > 淘宝链接",
        "",
    ]
    simple_lines: list[str] = []

    missing: list[str] = []
    for group, entries in grouped.items():
        lines += [f"## {group}", ""]
        simple_lines += [f"## {group}", ""]
        for index, entry in enumerate(entries, start=1):
            uid = str(entry.get("product_uid") or "")
            item = master_items.get(uid, {})
            title = str(item.get("title") or entry.get("product_name") or "").strip()
            price = item.get("display_price") or item.get("price") or entry.get("price_label") or ""
            source_label, link = pick_link(item)
            if not link:
                missing.append(uid)
                link = "【缺失链接】"
            lines.append(f"{index}. {uid}｜{title}｜{price}元")
            lines.append(f"   - {source_label}: {link}")
            simple_lines.append(link)
        lines.append("")
        simple_lines.append("")

    if missing:
        lines += ["## 缺失链接", "", ", ".join(missing), ""]
        simple_lines += ["## 缺失链接", "", *missing, ""]

    OUTPUT.write_text("\n".join(lines), encoding="utf-8")
    SIMPLE_OUTPUT.write_text("\n".join(simple_lines).rstrip() + "\n", encoding="utf-8")
    print(f"output={OUTPUT}")
    print(f"simple_output={SIMPLE_OUTPUT}")
    print(f"groups={len(grouped)}")
    print(f"products={sum(len(v) for v in grouped.values())}")
    print(f"missing_links={len(missing)}")


if __name__ == "__main__":
    main()
