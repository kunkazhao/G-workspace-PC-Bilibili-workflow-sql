from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bworkflow_sql.db import Database
from bworkflow_sql.render_package_builder import build_product_recommendation_package
from bworkflow_sql.workflow_service import (
    render_package_to_jianying_manifest,
    render_segment_counts,
)


def split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a short RenderPackage probe for selected product UIDs.")
    parser.add_argument("project_id", type=int)
    parser.add_argument("--account", required=True)
    parser.add_argument("--uids", required=True, help="Comma-separated product UIDs to include.")
    parser.add_argument("--output-mode", choices=["jianying_draft", "final_mp4"], required=True)
    parser.add_argument("--product-media-mode", choices=["cover_only", "video_preferred"], default="video_preferred")
    parser.add_argument("--product-card-template-id", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--allow-stale", action="store_true")
    args = parser.parse_args()

    db = Database()
    uids = split_csv(args.uids)
    result = build_product_recommendation_package(
        db,
        project_id=args.project_id,
        account_label=args.account,
        output_mode=args.output_mode,
        product_media_mode=args.product_media_mode,
        product_card_template_id=args.product_card_template_id,
        mode="standard",
        product_uids=uids,
    )
    output = Path(args.output)
    payload = {
        "ok": not result.missing and (args.allow_stale or not result.stale_product_images),
        "project_id": args.project_id,
        "account": args.account,
        "uids": uids,
        "output_mode": args.output_mode,
        "product_media_mode": args.product_media_mode,
        "product_card_template_id": args.product_card_template_id,
        "package_path": str(output),
        "missing": result.missing,
        "stale_product_images": result.stale_product_images,
        "segment_counts": render_segment_counts(result.package.get("segments", [])),
    }
    if not payload["ok"]:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        sys.exit(1)

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result.package, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    if args.output_mode == "jianying_draft":
        manifest = output.with_suffix(".jianying.manifest.json")
        render_package_to_jianying_manifest(
            result.package,
            manifest,
            project_id=args.project_id,
            account_label=args.account,
        )
        payload["jianying_manifest_path"] = str(manifest)
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
