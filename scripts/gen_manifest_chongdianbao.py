# -*- coding: utf-8 -*-
"""Generate spoken manifest for 充电宝 project with category-based transitions."""
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bworkflow_sql.db import Database
from bworkflow_sql.repositories import Repository


CATEGORY_ORDER = ["高性价比款", "日常款", "小巧精致款", "磁吸便捷款", "高性能款"]

CATEGORY_PRODUCTS = {
    "高性价比款": ["CDB014", "CDB004", "CDB009"],
    "日常款": ["CDB013", "CDB032", "CDB019", "CDB021", "CDB016", "CDB015", "CDB022", "CDB034"],
    "小巧精致款": ["CDB026", "CDB029", "CDB024", "CDB030", "CDB028", "CDB018", "CDB001"],
    "磁吸便捷款": ["CDB031", "CDB002", "CDB033"],
    "高性能款": ["CDB008", "CDB025", "CDB023", "CDB003", "CDB017"],
}

CATEGORY_TEXTS = {
    "高性价比款": "先看高性价比款。这几款是我从这些充电宝里挑出来，日常使用最推荐的。不是最便宜的，也不是最猛的，而是在价格、容量、快充、便携性之间，找到了很好的平衡点。不管你是学生党还是上班族，不想花太多时间研究对比，直接从这个段位里选一个，基本不会出错，不用反复比来比去。",
    "日常款": "下面看日常款。这个段位的充电宝属于六边形战士，不重不轻，容量够用，快充够快，带着出门没有太大负担。没有特别突出的长板，但也没有明显的短板，适合大部分人的日常实用和短途出行，不知道买什么就先看这个区间。",
    "小巧精致款": "下面看小巧精致款。这个段位的充电宝主打一个轻便随身，容量和性能确实比不过那些大块头，但胜在揣兜里就走，完全不累赘。适合对便携性要求高，出门不想背大包的人，日常应急补个电刚刚好。",
    "磁吸便捷款": "下面是磁吸款，这种最大的好处就是省心，往手机后面一贴就开始充，不用插线不用翻包，苹果用户的福音。但要注意，磁吸充电宝普遍容量偏小、功率不高，而且无线充电过程中发热是正常现象，选购的时候要注意甄别。",
    "高性能款": "下面看高性能款。这个段位的充电宝性能拉满了，100W甚至更高的快充，两万到七万毫安时的超大容量，给笔记本和手机同时供电都没问题。代价就是重量和体积，更适合经常出差带笔记本的商务用户，或者户外露营需要给多设备供电的玩家。",
}

DEFAULT_CLOSING_TEXT = "如果你看完这些还是拿不准该选哪款，或者不知道你的预算最适合哪个，按老规矩在评论区留预算和需求，我看到都会回复。"

TEMPLATE_SLOT = {
    "x": 44,
    "y": 172,
    "width": 851,
    "height": 436,
    "display_scale": 0.42,
}


def text_hash(text: str) -> str:
    return hashlib.sha1(text.strip().encode("utf-8")).hexdigest()


def format_price(value) -> str:
    try:
        n = float(str(value).replace("元", "").replace("¥", "").strip())
        return f"{int(n)}元" if n == int(n) else f"{n}元"
    except (ValueError, TypeError):
        return str(value or "未定价")


def find_audio(directory: Path, pattern_parts: list[str]) -> str:
    """Find audio file matching pattern parts in filename."""
    for f in directory.iterdir():
        if not f.is_file() or f.suffix.lower() not in (".mp3", ".wav"):
            continue
        name = f.stem.lower()
        if all(p.lower() in name for p in pattern_parts):
            return str(f)
    return ""


def main():
    db = Database(str(Path(__file__).resolve().parent.parent / "data" / "bworkflow.db"))
    repo = Repository(db)

    project_id = 14
    account_label = "荣荣"
    voice_dir = Path(r"G:\2026项目-b站\素材-配音\数码-充电宝\荣荣")
    image_dir = Path(r"G:\2026项目-b站\素材-商品ppt图片\数码-充电宝\荣荣\模板2")
    closing_audio = Path(r"G:\2026项目-b站\素材-配音\公共-结尾\荣荣\结尾-荣荣.wav")

    project = repo.project(project_id)
    products = {p["uid"]: p for p in repo.products(project_id, include_removed=False)}
    blocks = repo.script_blocks(project_id)

    block_by_uid = {}
    intro_block = None
    for block in blocks:
        if block["script_type"] == "intro":
            intro_block = block
        elif block["script_type"] == "product":
            uid = block.get("owner_uid", "")
            if uid in products:
                block_by_uid[uid] = block

    entries = []
    order = 0
    section_order = 0

    # 1. Intro
    if intro_block:
        intro_audio = find_audio(voice_dir, ["引言"])
        entries.append({
            "type": "transition",
            "order_index": order,
            "section": "intro",
            "section_order": section_order,
            "product_uid": "INTRO",
            "product_name": "引言",
            "price_label": "",
            "price_range_label": "",
            "source_label": intro_block.get("block_label", "版本一"),
            "text": intro_block["body"],
            "audio_path": intro_audio,
            "image_path": "",
            "video_path": "",
            "display_video_path": "",
            "display_video_slot": TEMPLATE_SLOT,
            "binding_id": "",
            "script_id": intro_block.get("script_id", ""),
            "account_id": account_label,
            "account_label": account_label,
            "text_hash": intro_block.get("text_hash", ""),
        })
        order += 1
        section_order += 1

    # 2. Category transitions + products
    for cat_name in CATEGORY_ORDER:
        cat_uids = CATEGORY_PRODUCTS.get(cat_name, [])
        cat_text = CATEGORY_TEXTS.get(cat_name, "")

        # Category transition entry
        cat_audio = find_audio(voice_dir, ["品类过渡", cat_name])
        entries.append({
            "type": "transition",
            "order_index": order,
            "section": "price_transition",
            "section_order": section_order,
            "product_uid": "CATEGORY_TRANSITION",
            "product_name": cat_name,
            "price_label": "",
            "price_range_label": cat_name,
            "source_label": cat_name,
            "text": cat_text,
            "audio_path": cat_audio,
            "image_path": "",
            "video_path": "",
            "display_video_path": "",
            "display_video_slot": TEMPLATE_SLOT,
            "binding_id": "",
            "script_id": f"category:{cat_name}",
            "account_id": account_label,
            "account_label": account_label,
            "text_hash": text_hash(cat_text),
        })
        order += 1
        section_order += 1

        # Products in this category
        for uid in cat_uids:
            if uid not in products or uid not in block_by_uid:
                print(f"WARNING: {uid} not found, skipping")
                continue
            product = products[uid]
            block = block_by_uid[uid]
            price_str = format_price(product.get("price_label"))
            title = product.get("title", "")

            # Find audio
            product_audio = find_audio(voice_dir, [uid])
            # Find image
            product_image = find_audio(image_dir, [uid])  # reuse find function for images
            if not product_image:
                # Try with .png extension specifically
                for f in image_dir.iterdir():
                    if uid in f.stem and f.suffix.lower() == ".png":
                        product_image = str(f)
                        break

            entries.append({
                "type": "product",
                "order_index": order,
                "section": "product",
                "section_order": section_order,
                "product_uid": uid,
                "product_name": title,
                "price_label": price_str,
                "price_range_label": cat_name,
                "source_label": block.get("block_label", "正文"),
                "text": block["body"],
                "audio_path": product_audio,
                "image_path": product_image,
                "video_path": "",
                "display_video_path": "",
                "display_video_slot": TEMPLATE_SLOT,
                "binding_id": "",
                "script_id": block.get("script_id", ""),
                "account_id": account_label,
                "account_label": account_label,
                "text_hash": block.get("text_hash", ""),
            })
            order += 1
            section_order += 1

    # 3. Closing
    entries.append({
        "type": "closing",
        "order_index": order,
        "section": "closing",
        "section_order": section_order,
        "product_uid": "CLOSING",
        "product_name": "结尾",
        "price_label": "",
        "price_range_label": "",
        "source_label": "固定结尾",
        "text": DEFAULT_CLOSING_TEXT,
        "audio_path": str(closing_audio) if closing_audio.exists() else "",
        "image_path": "",
        "video_path": "",
        "display_video_path": "",
        "display_video_slot": TEMPLATE_SLOT,
        "binding_id": f"closing:{account_label}",
        "script_id": "closing-fixed",
        "account_id": account_label,
        "account_label": account_label,
        "text_hash": "",
    })

    manifest = {
        "version": 2,
        "source": "bworkflow-sql",
        "project_id": project_id,
        "project_name": project.get("name", "数码-充电宝") if project else "数码-充电宝",
        "category": "数码",
        "mode": "standard",
        "account_label": account_label,
        "account_id": account_label,
        "display_template": "荣荣-模板2",
        "spoken_markdown_path": "",
        "closing_text": DEFAULT_CLOSING_TEXT,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "entries": entries,
    }

    # Write manifest
    output_dir = Path(__file__).resolve().parent.parent / "data" / "manifests"
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "数码-充电宝-荣荣-品类过渡.manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Manifest written to: {manifest_path}")

    # Summary
    types = {}
    missing_audio = []
    missing_image = []
    for e in entries:
        types[e["section"]] = types.get(e["section"], 0) + 1
        if not e["audio_path"]:
            missing_audio.append(f"{e['product_uid']} {e['product_name']}")
        if e["section"] == "product" and not e["image_path"]:
            missing_image.append(f"{e['product_uid']} {e['product_name']}")

    print(f"\nEntries: {len(entries)}")
    for section, count in types.items():
        print(f"  {section}: {count}")
    if missing_audio:
        print(f"\nMissing audio ({len(missing_audio)}):")
        for m in missing_audio:
            print(f"  {m}")
    if missing_image:
        print(f"\nMissing image ({len(missing_image)}):")
        for m in missing_image:
            print(f"  {m}")
    if not missing_audio and not missing_image:
        print("\nAll audio and images present!")


if __name__ == "__main__":
    main()
