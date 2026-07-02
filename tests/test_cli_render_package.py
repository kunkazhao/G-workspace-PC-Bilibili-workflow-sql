from __future__ import annotations

import json
from argparse import Namespace

from bworkflow_sql import cli


def test_render_package_parser_registers_command():
    args = cli.build_parser().parse_args(
        [
            "render-package",
            "3",
            "--account",
            "xiaobo",
            "--output-mode",
            "final_mp4",
            "--product-media-mode",
            "cover_only",
            "--stale-product-image-policy",
            "reuse",
            "--mode",
            "top",
            "--top-uids",
            "P003,P001",
            "--output",
            "out.json",
        ]
    )

    assert args.command == "render-package"
    assert args.project_id == 3
    assert args.account == "xiaobo"
    assert args.output_mode == "final_mp4"
    assert args.product_media_mode == "cover_only"
    assert args.stale_product_image_policy == "reuse"
    assert args.mode == "top"
    assert args.top_uids == "P003,P001"
    assert args.output == "out.json"


def test_product_images_parser_registers_command():
    args = cli.build_parser().parse_args(
        [
            "product-images",
            "3",
            "--account",
            "xiaobo",
            "--mode",
            "missing",
            "--product-uid",
            "P001",
        ]
    )

    assert args.command == "product-images"
    assert args.project_id == 3
    assert args.account == "xiaobo"
    assert args.mode == "missing"
    assert args.product_uid == "P001"


def test_template_calibrate_parser_registers_command():
    args = cli.build_parser().parse_args(
        [
            "template-calibrate",
            "3",
            "--account",
            "小燃",
            "--product-uid",
            "P001",
            "--draft-name",
            "校准-P001",
            "--draft-root",
            "drafts",
            "--product-media-mode",
            "video_preferred",
        ]
    )

    assert args.command == "template-calibrate"
    assert args.project_id == 3
    assert args.account == "小燃"
    assert args.product_uid == "P001"
    assert args.draft_name == "校准-P001"
    assert args.draft_root == "drafts"
    assert args.product_media_mode == "video_preferred"


def test_render_final_video_parser_registers_command():
    args = cli.build_parser().parse_args(
        [
            "render-final-video",
            "3",
            "--account",
            "小燃",
            "--product-media-mode",
            "cover_only",
            "--product-image-mode",
            "missing",
            "--stale-product-image-policy",
            "reuse",
            "--mode",
            "top",
            "--top-uids",
            "P003,P001",
            "--package-output",
            "package.json",
            "--output",
            "out.mp4",
        ]
    )

    assert args.command == "render-final-video"
    assert args.project_id == 3
    assert args.account == "小燃"
    assert args.product_media_mode == "cover_only"
    assert args.product_image_mode == "missing"
    assert args.stale_product_image_policy == "reuse"
    assert args.mode == "top"
    assert args.top_uids == "P003,P001"
    assert args.package_output == "package.json"
    assert args.output == "out.mp4"


def test_cmd_product_images_writes_regeneration_json(capsys, monkeypatch):
    calls: list[dict[str, object]] = []

    class FakeWorkflow:
        def regenerate_product_card_images(self, project_id, *, account_label, mode, product_uid):
            calls.append(
                {
                    "project_id": project_id,
                    "account_label": account_label,
                    "mode": mode,
                    "product_uid": product_uid,
                }
            )
            return {
                "ok": True,
                "project_id": project_id,
                "account": account_label,
                "mode": mode,
                "regenerated": [{"uid": "P001"}],
                "skipped": [],
            }

    monkeypatch.setattr(cli, "_init", lambda: ("db", None, None, FakeWorkflow()))

    cli.cmd_product_images(Namespace(project_id=3, account="xiaobo", mode="stale", product_uid="P001"))

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["regenerated"] == [{"uid": "P001"}]
    assert calls == [
        {
            "project_id": 3,
            "account_label": "xiaobo",
            "mode": "stale",
            "product_uid": "P001",
        }
    ]


def test_cmd_template_calibrate_writes_probe_json(capsys, monkeypatch):
    calls: list[dict[str, object]] = []

    class FakeWorkflow:
        def template_calibration_probe(
            self,
            project_id,
            *,
            account_label,
            product_uid,
            draft_name,
            draft_root,
            product_media_mode,
        ):
            calls.append(
                {
                    "project_id": project_id,
                    "account_label": account_label,
                    "product_uid": product_uid,
                    "draft_name": draft_name,
                    "draft_root": draft_root,
                    "product_media_mode": product_media_mode,
                }
            )
            return {
                "ok": True,
                "probe_manifest_path": "probe.json",
                "draft": {"returncode": 0},
            }

    monkeypatch.setattr(cli, "_init", lambda: ("db", None, None, FakeWorkflow()))

    cli.cmd_template_calibrate(
        Namespace(
            project_id=3,
            account="小燃",
            product_uid="P001",
            draft_name="校准-P001",
            draft_root="drafts",
            product_media_mode="video_preferred",
        )
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["probe_manifest_path"] == "probe.json"
    assert calls == [
        {
            "project_id": 3,
            "account_label": "小燃",
            "product_uid": "P001",
            "draft_name": "校准-P001",
            "draft_root": "drafts",
            "product_media_mode": "video_preferred",
        }
    ]


def test_cmd_render_package_writes_success_json_and_package(
    tmp_path,
    capsys,
    monkeypatch,
):
    calls: list[dict[str, object]] = []

    class FakeWorkflow:
        def prepare_product_recommendation_output(
            self,
            project_id,
            *,
            account_label,
            output_mode,
            product_media_mode,
            stale_product_image_policy,
            mode,
            top_uids,
            package_output_path,
        ):
            calls.append(
                {
                    "project_id": project_id,
                    "account_label": account_label,
                    "output_mode": output_mode,
                    "product_media_mode": product_media_mode,
                    "stale_product_image_policy": stale_product_image_policy,
                    "mode": mode,
                    "top_uids": top_uids,
                    "package_output_path": package_output_path,
                }
            )
            return {
                "ok": True,
                "project_id": project_id,
                "account": account_label,
                "output_mode": output_mode,
                "package_path": str(package_output_path),
                "missing": [],
                "segment_counts": {
                    "price_transition": 1,
                    "product_recommendation": 2,
                },
                "next": {"mode": output_mode},
            }

    output = tmp_path / "render-package.json"
    monkeypatch.setattr(cli, "_init", lambda: ("db", None, None, FakeWorkflow()))

    cli.cmd_render_package(
        Namespace(
            project_id=3,
            account="xiaobo",
            output_mode="jianying_draft",
            product_media_mode="video_preferred",
            stale_product_image_policy="block",
            mode="standard",
            top_uids="",
            output=str(output),
        )
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["project_id"] == 3
    assert payload["account"] == "xiaobo"
    assert payload["output_mode"] == "jianying_draft"
    assert payload["package_path"] == str(output)
    assert payload["missing"] == []
    assert payload["segment_counts"] == {
        "price_transition": 1,
        "product_recommendation": 2,
    }
    assert payload["next"] == {"mode": "jianying_draft"}
    assert calls == [
        {
            "project_id": 3,
            "account_label": "xiaobo",
            "output_mode": "jianying_draft",
            "product_media_mode": "video_preferred",
            "stale_product_image_policy": "block",
            "mode": "standard",
            "top_uids": "",
            "package_output_path": str(output),
        }
    ]


def test_cmd_render_package_reports_missing_without_writing_package(
    tmp_path,
    capsys,
    monkeypatch,
):
    missing = [{"kind": "product_voice", "uid": "P001"}]

    class FakeWorkflow:
        def prepare_product_recommendation_output(
            self,
            project_id,
            *,
            account_label,
            output_mode,
            product_media_mode,
            stale_product_image_policy,
            mode,
            top_uids,
            package_output_path,
        ):
            return {
                "ok": False,
                "project_id": project_id,
                "account": account_label,
                "output_mode": output_mode,
                "package_path": str(package_output_path),
                "missing": missing,
                "next": None,
            }

    output = tmp_path / "render-package.json"
    monkeypatch.setattr(cli, "_init", lambda: ("db", None, None, FakeWorkflow()))

    cli.cmd_render_package(
        Namespace(
            project_id=3,
            account="xiaobo",
            output_mode="jianying_draft",
            product_media_mode="video_preferred",
            mode="standard",
            top_uids="",
            output=str(output),
        )
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["missing"] == missing
    assert not output.exists()
