from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace

import pytest

import bworkflow_sql.workflow_service as workflow_service
from bworkflow_sql import cli
from bworkflow_sql.workflow_service import WorkflowService
from bworkflow_sql.workflow_errors import (
    AmbiguousProjectReferenceError,
    InvalidWorkflowRequestError,
    ProjectNotFoundError,
)


def test_render_package_parser_registers_command():
    args = cli.build_parser().parse_args(
        [
            "render-package",
            "3",
            "--account",
            "xiaobo",
            "--pipeline",
            ".pipeline.json",
            "--output-mode",
            "final_mp4",
            "--product-media-mode",
            "cover_only",
            "--product-order-strategy",
            "stable",
            "--stale-product-image-policy",
            "reuse",
            "--mode",
            "top",
            "--top-uids",
            "P003,P001",
            "--product-card-template-id",
            "muban-xiaobo-1",
            "--output",
            "out.json",
        ]
    )

    assert args.command == "render-package"
    assert args.project_id == 3
    assert args.account == "xiaobo"
    assert args.output_mode == "final_mp4"
    assert args.product_media_mode == "cover_only"
    assert args.product_order_strategy == "stable"
    assert args.stale_product_image_policy == "reuse"
    assert args.mode == "top"
    assert args.top_uids == "P003,P001"
    assert args.product_card_template_id == "muban-xiaobo-1"
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
            "--product-card-template-id",
            "muban-xiaobo-1",
        ]
    )

    assert args.command == "product-images"
    assert args.project_id == 3
    assert args.account == "xiaobo"
    assert args.mode == "missing"
    assert args.product_uid == "P001"
    assert args.product_card_template_id == "muban-xiaobo-1"


def test_product_card_preflight_parser_registers_command():
    args = cli.build_parser().parse_args(
        [
            "product-card-preflight",
            "3",
            "--account",
            "xiaobo",
            "--product-card-template-id",
            "muban-xiaobo-1",
            "--product-uid",
            "P001",
            "--expect-cover",
            "P001-new.png",
        ]
    )

    assert args.command == "product-card-preflight"
    assert args.project_id == 3
    assert args.account == "xiaobo"
    assert args.product_card_template_id == "muban-xiaobo-1"
    assert args.product_uid == "P001"
    assert args.expect_cover == "P001-new.png"


def test_intro_preflight_parser_registers_command():
    args = cli.build_parser().parse_args(
        [
            "intro-preflight",
            "23",
            "--source-plan",
            "source-intro-plan-引言1.json",
            "--asset-root",
            "assets",
        ]
    )

    assert args.command == "intro-preflight"
    assert args.project_id == 23
    assert args.source_plan == "source-intro-plan-引言1.json"
    assert args.asset_root == "assets"


def test_render_intro_video_parser_registers_command():
    args = cli.build_parser().parse_args(
        [
            "render-intro-video",
            "23",
            "--account",
            "xiaobo",
            "--intro-label",
            "intro-1",
            "--output",
            "intro.mp4",
            "--asset-root",
            "assets",
        ]
    )

    assert args.command == "render-intro-video"
    assert args.project_id == 23
    assert args.account == "xiaobo"
    assert args.intro_label == "intro-1"
    assert args.output == "intro.mp4"
    assert args.asset_root == "assets"


def test_price_transition_plan_parser_defaults_to_no_sync():
    args = cli.build_parser().parse_args(
        [
            "price-transition-plan",
            "23",
            "--plan",
            "price-transition-plan.json",
            "--markdown",
            "copy.md",
        ]
    )

    assert args.command == "price-transition-plan"
    assert args.project_id == 23
    assert args.plan == "price-transition-plan.json"
    assert args.markdown == "copy.md"
    assert args.sync is False


def test_render_intro_video_parser_uses_production_defaults():
    args = cli.build_parser().parse_args(
        [
            "render-intro-video",
            "23",
            "--account",
            "小博",
        ]
    )

    assert args.intro_label == "引言1"
    assert Path(args.asset_root) == Path(r"G:\2026项目-b站\素材-自动剪辑")


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
            "--product-card-template-id",
            "muban-xiaoran-2",
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
    assert args.product_card_template_id == "muban-xiaoran-2"
    assert args.product_media_mode == "video_preferred"


def test_template_doctor_parser_registers_command():
    args = cli.build_parser().parse_args(
        [
            "template-doctor",
            "3",
            "--account",
            "灏忓崥",
            "--product-card-template-id",
            "muban-xiaobo-1",
            "--product-media-mode",
            "video_preferred",
        ]
    )

    assert args.command == "template-doctor"
    assert args.project_id == 3
    assert args.account == "灏忓崥"
    assert args.product_card_template_id == "muban-xiaobo-1"
    assert args.product_media_mode == "video_preferred"


def test_script_doctor_parser_registers_command():
    args = cli.build_parser().parse_args(
        [
            "script-doctor",
            "3",
            "--intro-label",
            "引言1",
        ]
    )

    assert args.command == "script-doctor"
    assert args.project_id == 3
    assert args.intro_label == "引言1"


def test_copy_lint_parser_registers_command():
    args = cli.build_parser().parse_args(["copy-lint", "3"])

    assert args.command == "copy-lint"
    assert args.project_id == 3


def test_copy_audit_parser_registers_voice_profile():
    args = cli.build_parser().parse_args(["copy-audit", "3", "--voice-profile", "zhaoer"])

    assert args.command == "copy-audit"
    assert args.project_id == 3
    assert args.voice_profile == "zhaoer"


def test_materialize_episode_parser_registers_command():
    args = cli.build_parser().parse_args(
        [
            "materialize-episode",
            "3",
            "--library-path",
            "library.md",
        ]
    )

    assert args.command == "materialize-episode"
    assert args.project_id == 3
    assert args.library_path == "library.md"


def test_workflow_doctor_parser_registers_command():
    args = cli.build_parser().parse_args(
        [
            "workflow-doctor",
            "数码-蓝牙音响",
            "--account",
            "xiaobo",
            "--scheme-name",
            "主方案",
            "--intro-label",
            "intro-1",
            "--intro-index",
            "2",
            "--mode",
            "top",
            "--top-uids",
            "P003,P001",
            "--product-order-strategy",
            "stable",
            "--product-card-template-id",
            "muban-xiaobo-2",
            "--product-media-mode",
            "cover_only",
        ]
    )

    assert args.command == "workflow-doctor"
    assert args.project_ref == "数码-蓝牙音响"
    assert args.account == "xiaobo"
    assert args.scheme_name == "主方案"
    assert args.intro_label == "intro-1"
    assert args.intro_index == 2
    assert args.mode == "top"
    assert args.top_uids == "P003,P001"
    assert args.product_order_strategy == "stable"
    assert args.product_card_template_id == "muban-xiaobo-2"
    assert args.product_media_mode == "cover_only"


def test_assemble_plan_parser_registers_command():
    args = cli.build_parser().parse_args(
        [
            "assemble-plan",
            "3",
            "--account",
            "小博",
            "--intro-index",
            "2",
        ]
    )

    assert args.command == "assemble-plan"
    assert args.project_id == 3
    assert args.account == "小博"
    assert args.intro_index == 2


def test_assemble_plan_parser_registers_ordering_options():
    args = cli.build_parser().parse_args(
        [
            "assemble-plan",
            "3",
            "--account",
            "xiaobo",
            "--intro-index",
            "2",
            "--mode",
            "top",
            "--top-uids",
            "P003,P001",
            "--product-uids",
            "P003,P001,P002",
            "--product-order-strategy",
            "stable",
        ]
    )

    assert args.command == "assemble-plan"
    assert args.project_id == 3
    assert args.account == "xiaobo"
    assert args.intro_index == 2
    assert args.mode == "top"
    assert args.top_uids == "P003,P001"
    assert args.product_uids == "P003,P001,P002"
    assert args.product_order_strategy == "stable"


def test_assemble_parser_registers_ordering_options():
    args = cli.build_parser().parse_args(
        [
            "assemble",
            "3",
            "--account",
            "xiaobo",
            "--intro-index",
            "2",
            "--mode",
            "top",
            "--top-uids",
            "P003,P001",
            "--product-uids",
            "P003,P001,P002",
            "--product-order-strategy",
            "stable",
            "--output",
            "spoken.md",
        ]
    )

    assert args.command == "assemble"
    assert args.project_id == 3
    assert args.account == "xiaobo"
    assert args.intro_index == 2
    assert args.mode == "top"
    assert args.top_uids == "P003,P001"
    assert args.product_uids == "P003,P001,P002"
    assert args.product_order_strategy == "stable"
    assert args.output == "spoken.md"


def test_template_calibrate_runner_parser_registers_command():
    args = cli.build_parser().parse_args(
        [
            "template-calibrate-runner",
            "--target",
            "xiaobo-template2",
            "--config",
            "targets.json",
            "--draft-suffix",
            "v3",
            "--dry-run",
            "--include-inactive",
            "--no-regenerate-images",
        ]
    )

    assert args.command == "template-calibrate-runner"
    assert args.target == "xiaobo-template2"
    assert args.config == "targets.json"
    assert args.draft_suffix == "v3"
    assert args.dry_run is True
    assert args.include_inactive is True
    assert args.regenerate_images is False


def test_render_final_video_parser_registers_command():
    args = cli.build_parser().parse_args(
        [
            "render-final-video",
            "3",
            "--account",
            "小燃",
            "--product-media-mode",
            "cover_only",
            "--product-order-strategy",
            "stable",
            "--product-image-mode",
            "missing",
            "--stale-product-image-policy",
            "reuse",
            "--mode",
            "top",
            "--top-uids",
            "P003,P001",
            "--product-card-template-id",
            "muban-xiaobo-1",
            "--package-output",
            "package.json",
            "--output",
            "out.mp4",
            "--intro-video",
            "intro.mp4",
            "--intro-video-text-file",
            "intro.txt",
            "--intro-video-source-plan",
            "source-intro-plan.json",
            "--full-output",
            "full.mp4",
            "--pipeline",
            ".pipeline.json",
            "--subtitle-alignment",
            "asr",
            "--acceptance-mode",
            "quick",
        ]
    )

    assert args.command == "render-final-video"
    assert args.project_id == 3
    assert args.account == "小燃"
    assert args.product_media_mode == "cover_only"
    assert args.product_order_strategy == "stable"
    assert args.product_image_mode == "missing"
    assert args.stale_product_image_policy == "reuse"
    assert args.mode == "top"
    assert args.top_uids == "P003,P001"
    assert args.product_card_template_id == "muban-xiaobo-1"
    assert args.package_output == "package.json"
    assert args.output == "out.mp4"
    assert args.intro_video == "intro.mp4"
    assert args.intro_video_text_file == "intro.txt"
    assert args.intro_video_source_plan == "source-intro-plan.json"
    assert args.full_output == "full.mp4"
    assert args.pipeline == ".pipeline.json"
    assert args.subtitle_alignment == "asr"
    assert args.acceptance_mode == "quick"


def test_render_final_video_parser_accepts_visual_acceptance_mode():
    parser = cli.build_parser()

    args = parser.parse_args(
        [
            "render-final-video",
            "3",
            "--account",
            "小博",
            "--product-media-mode",
            "video_preferred",
            "--product-card-template-id",
            "muban-xiaobo-1",
            "--pipeline",
            ".pipeline.json",
            "--acceptance-mode",
            "visual",
        ]
    )

    assert args.acceptance_mode == "visual"


def test_cmd_product_images_writes_regeneration_json(capsys, monkeypatch):
    calls: list[dict[str, object]] = []

    class FakeWorkflow:
        def regenerate_product_card_images(self, project_id, *, account_label, mode, product_uid, product_card_template_id):
            calls.append(
                {
                    "project_id": project_id,
                    "account_label": account_label,
                    "mode": mode,
                    "product_uid": product_uid,
                    "product_card_template_id": product_card_template_id,
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

    cli.cmd_product_images(
        Namespace(
            project_id=3,
            account="xiaobo",
            mode="stale",
            product_uid="P001",
            product_card_template_id="muban-xiaobo-1",
        )
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["regenerated"] == [{"uid": "P001"}]
    assert calls == [
        {
            "project_id": 3,
            "account_label": "xiaobo",
            "mode": "stale",
            "product_uid": "P001",
            "product_card_template_id": "muban-xiaobo-1",
        }
    ]


def test_product_images_help_requires_explicit_product_card_template() -> None:
    parser = cli.build_parser()
    product_images = parser._subparsers._group_actions[0].choices["product-images"]
    action = next(
        action
        for action in product_images._actions
        if "--product-card-template-id" in action.option_strings
    )

    assert "required for still/product-image generation" in action.help
    assert "defaults to the account template" not in action.help


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
            product_card_template_id,
        ):
            calls.append(
                {
                    "project_id": project_id,
                    "account_label": account_label,
                    "product_uid": product_uid,
                    "draft_name": draft_name,
                    "draft_root": draft_root,
                    "product_media_mode": product_media_mode,
                    "product_card_template_id": product_card_template_id,
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
            product_card_template_id="muban-xiaoran-2",
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
            "product_card_template_id": "muban-xiaoran-2",
        }
    ]


def test_cmd_template_doctor_writes_diagnostic_json(capsys, monkeypatch):
    calls: list[dict[str, object]] = []

    class FakeWorkflow:
        def template_doctor(
            self,
            project_id,
            *,
            account_label,
            product_card_template_id,
            product_media_mode,
        ):
            calls.append(
                {
                    "project_id": project_id,
                    "account_label": account_label,
                    "product_card_template_id": product_card_template_id,
                    "product_media_mode": product_media_mode,
                }
            )
            return {
                "ok": False,
                "status": "issues_found",
                "issues": [{"code": "wrong_template_binding"}],
                "next": {"command": "python -m bworkflow_sql product-images 3"},
            }

    monkeypatch.setattr(cli, "_init", lambda: ("db", None, None, FakeWorkflow()))

    cli.cmd_template_doctor(
        Namespace(
            project_id=3,
            account="灏忓崥",
            product_card_template_id="muban-xiaobo-1",
            product_media_mode="video_preferred",
        )
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["issues"] == [{"code": "wrong_template_binding"}]
    assert calls == [
        {
            "project_id": 3,
            "account_label": "灏忓崥",
            "product_card_template_id": "muban-xiaobo-1",
            "product_media_mode": "video_preferred",
        }
    ]


def test_cmd_script_doctor_writes_diagnostic_json(capsys, monkeypatch):
    calls: list[dict[str, object]] = []

    class FakeWorkflow:
        def script_doctor(self, project_id, *, intro_label):
            calls.append(
                {
                    "project_id": project_id,
                    "intro_label": intro_label,
                }
            )
            return {
                "ok": False,
                "status": "needs_sync",
                "next": {"command": "python -m bworkflow_sql sync 3 --step markdown"},
            }

    monkeypatch.setattr(cli, "_init", lambda: ("db", None, None, FakeWorkflow()))

    cli.cmd_script_doctor(
        Namespace(
            project_id=3,
            intro_label="引言1",
        )
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["status"] == "needs_sync"
    assert calls == [{"project_id": 3, "intro_label": "引言1"}]


def test_cmd_materialize_episode_writes_result_json(capsys, monkeypatch):
    calls: list[dict[str, object]] = []

    class FakeWorkflow:
        def materialize_episode_markdown(self, project_id, *, library_path):
            calls.append(
                {
                    "project_id": project_id,
                    "library_path": library_path,
                }
            )
            return {
                "ok": True,
                "project_id": project_id,
                "materialized": 2,
                "target_path": "episode.md",
            }

    monkeypatch.setattr(cli, "_init", lambda: ("db", None, None, FakeWorkflow()))

    cli.cmd_materialize_episode(
        Namespace(
            project_id=3,
            library_path="library.md",
        )
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["materialized"] == 2
    assert calls == [
        {
            "project_id": 3,
            "library_path": "library.md",
        }
    ]


def _workflow_doctor_args() -> Namespace:
    return Namespace(
        project_ref="数码-蓝牙音响",
        account="xiaobo",
        scheme_name="主方案",
        intro_label="intro-1",
        intro_index=2,
        mode="top",
        top_uids="P003,P001",
        product_order_strategy="stable",
        product_card_template_id="muban-xiaobo-2",
        product_media_mode="cover_only",
    )


def _nested_keys(value) -> set[str]:
    if isinstance(value, dict):
        keys = set(value)
        for item in value.values():
            keys.update(_nested_keys(item))
        return keys
    if isinstance(value, list):
        keys: set[str] = set()
        for item in value:
            keys.update(_nested_keys(item))
        return keys
    return set()


def test_cmd_workflow_doctor_writes_blocked_v1_observation(capsys, monkeypatch):
    calls: list[dict[str, object]] = []

    class FakeWorkflow:
        def workflow_doctor(
            self,
            project_ref,
            *,
            account_label,
            scheme_name,
            intro_label,
            intro_index,
            mode,
            top_uids,
            product_order_strategy,
            product_card_template_id,
            product_media_mode,
        ):
            calls.append(
                {
                    "project_ref": project_ref,
                    "account_label": account_label,
                    "scheme_name": scheme_name,
                    "intro_label": intro_label,
                    "intro_index": intro_index,
                    "mode": mode,
                    "top_uids": top_uids,
                    "product_order_strategy": product_order_strategy,
                    "product_card_template_id": product_card_template_id,
                    "product_media_mode": product_media_mode,
                }
            )
            return {
                "ok": False,
                "status": "blocked",
                "blocked_by": "voice_and_assembly",
                "project": {"id": 23, "name": "数码-蓝牙音响"},
                "account": "xiaobo",
                "checks": {
                    "script": {"status": "ready", "next": {"command": "private"}},
                    "voice_and_assembly": {"status": "voice_incomplete"},
                },
                "issues": [{"source": "assemble-plan", "code": "missing_voice_asset"}],
                "next": {
                    "action": "generate_voice",
                    "task": "补齐当前文案对应的配音",
                    "command": "private",
                },
            }

    monkeypatch.setattr(cli, "_init", lambda: ("db", None, None, FakeWorkflow()))

    cli.cmd_workflow_doctor(_workflow_doctor_args())

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["kind"] == "BWorkflowObservation"
    assert payload["schema_version"] == 1
    assert payload["authoritative"] is False
    assert payload["ok"] is True
    assert payload["status"] == "blocked"
    assert payload["blocked_by"] == ["voice_and_assembly"]
    assert payload["suggestion"] == {
        "action": "generate_voice",
        "task": "补齐当前文案对应的配音",
    }
    assert _nested_keys(payload).isdisjoint({"next", "command", "follow_up_command", "argv", "cwd"})
    assert captured.err == ""
    assert calls == [
        {
            "project_ref": "数码-蓝牙音响",
            "account_label": "xiaobo",
            "scheme_name": "主方案",
            "intro_label": "intro-1",
            "intro_index": 2,
            "mode": "top",
            "top_uids": "P003,P001",
            "product_order_strategy": "stable",
            "product_card_template_id": "muban-xiaobo-2",
            "product_media_mode": "cover_only",
        }
    ]


def test_cmd_workflow_doctor_writes_ready_v1_observation(capsys, monkeypatch):
    class FakeWorkflow:
        def workflow_doctor(self, *_args, **_kwargs):
            return {
                "ok": True,
                "status": "ready",
                "blocked_by": None,
                "project": {"id": 23, "name": "数码-蓝牙音响"},
                "account": "xiaobo",
                "checks": {"script": {"status": "ready_for_downstream"}},
                "issues": [],
                "next": {"action": "assemble"},
            }

    monkeypatch.setattr(cli, "_init", lambda: ("db", None, None, FakeWorkflow()))

    cli.cmd_workflow_doctor(_workflow_doctor_args())

    payload = json.loads(capsys.readouterr().out)
    assert payload["kind"] == "BWorkflowObservation"
    assert payload["ok"] is True
    assert payload["status"] == "ready"
    assert payload["blocked_by"] == []
    assert payload["error"] is None


@pytest.mark.parametrize(
    ("error", "expected_code"),
    [
        (ProjectNotFoundError("private path"), "project_not_found"),
        (AmbiguousProjectReferenceError("private matches"), "ambiguous_project_reference"),
        (InvalidWorkflowRequestError("private detail"), "workflow_doctor_invalid_request"),
        (FileNotFoundError("private asset path"), "workflow_doctor_internal_error"),
        (ValueError("private unexpected value"), "workflow_doctor_internal_error"),
        (RuntimeError("SECRET TRACEBACK"), "workflow_doctor_internal_error"),
    ],
)
def test_cmd_workflow_doctor_writes_safe_failed_v1_and_exits_nonzero(
    capsys,
    monkeypatch,
    error,
    expected_code: str,
):
    class FakeWorkflow:
        def workflow_doctor(self, *_args, **_kwargs):
            raise error

    monkeypatch.setattr(cli, "_init", lambda: ("db", None, None, FakeWorkflow()))

    with pytest.raises(SystemExit) as exit_info:
        cli.cmd_workflow_doctor(_workflow_doctor_args())

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert exit_info.value.code == 1
    assert payload["kind"] == "BWorkflowObservation"
    assert payload["ok"] is False
    assert payload["status"] == "failed"
    assert payload["error"]["code"] == expected_code
    assert payload["subject"] == {"project_id": None, "project_name": None, "account": None}
    assert payload["checks"] == {}
    assert payload["issues"] == []
    assert payload["suggestion"] is None
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "private" not in serialized.lower()
    assert "traceback" not in serialized.lower()
    assert "SECRET" not in serialized
    assert captured.err == ""


def test_cmd_assemble_plan_writes_preview_json(capsys, monkeypatch):
    calls: list[dict[str, object]] = []

    class FakeWorkflow:
        def assemble_spoken_script_plan(
            self,
            project_id,
            *,
            account_label,
            intro_index,
            mode,
            top_uids,
            product_uids,
            product_order_strategy,
        ):
            calls.append(
                {
                    "project_id": project_id,
                    "account_label": account_label,
                    "intro_index": intro_index,
                }
            )
            return {
                "ok": True,
                "status": "ready_to_assemble",
                "next": {"command": "python -m bworkflow_sql assemble 3 --account 小博"},
            }

    monkeypatch.setattr(cli, "_init", lambda: ("db", None, None, FakeWorkflow()))

    cli.cmd_assemble_plan(
        Namespace(
            project_id=3,
            account="小博",
            intro_index=2,
            mode="top",
            top_uids="P003,P001",
            product_uids="P003,P001,P002",
            product_order_strategy="stable",
        )
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["status"] == "ready_to_assemble"
    assert calls == [{"project_id": 3, "account_label": "小博", "intro_index": 2}]


def test_cmd_template_calibrate_runner_writes_report_json(capsys, monkeypatch):
    calls: list[dict[str, object]] = []

    class FakeWorkflow:
        pass

    def fake_load(config_path, *, target_id, include_inactive):
        calls.append(
            {
                "step": "load",
                "config_path": config_path,
                "target_id": target_id,
                "include_inactive": include_inactive,
            }
        )
        return [{"id": "xiaobo-template2"}]

    def fake_run(workflow, *, targets, regenerate_images, dry_run, draft_suffix):
        calls.append(
            {
                "step": "run",
                "workflow": workflow,
                "targets": targets,
                "regenerate_images": regenerate_images,
                "dry_run": dry_run,
                "draft_suffix": draft_suffix,
            }
        )
        return {"ok": True, "summary": {"total": 1, "succeeded": 1, "failed": 0}}

    monkeypatch.setattr(cli, "_init", lambda: ("db", None, None, FakeWorkflow()))
    monkeypatch.setattr(cli, "load_template_calibration_targets", fake_load)
    monkeypatch.setattr(cli, "run_template_calibration_targets", fake_run)

    cli.cmd_template_calibrate_runner(
        Namespace(
            target="xiaobo-template2",
            config="targets.json",
            include_inactive=True,
            regenerate_images=False,
            dry_run=True,
            draft_suffix="v3",
        )
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert calls[0] == {
        "step": "load",
        "config_path": "targets.json",
        "target_id": "xiaobo-template2",
        "include_inactive": True,
    }
    assert calls[1]["targets"] == [{"id": "xiaobo-template2"}]
    assert calls[1]["regenerate_images"] is False
    assert calls[1]["dry_run"] is True
    assert calls[1]["draft_suffix"] == "v3"


def test_cmd_template_calibrate_runner_uses_default_config_when_omitted(capsys, monkeypatch):
    calls: list[dict[str, object]] = []

    class FakeWorkflow:
        pass

    def fake_load(config_path="DEFAULT", *, target_id, include_inactive):
        calls.append(
            {
                "step": "load",
                "config_path": config_path,
                "target_id": target_id,
                "include_inactive": include_inactive,
            }
        )
        return [{"id": "xiaobo-template2"}]

    def fake_run(workflow, *, targets, regenerate_images, dry_run, draft_suffix):
        return {"ok": True, "summary": {"total": 1, "succeeded": 1, "failed": 0}}

    monkeypatch.setattr(cli, "_init", lambda: ("db", None, None, FakeWorkflow()))
    monkeypatch.setattr(cli, "load_template_calibration_targets", fake_load)
    monkeypatch.setattr(cli, "run_template_calibration_targets", fake_run)

    cli.cmd_template_calibrate_runner(
        Namespace(
            target="xiaobo-template2",
            config="",
            include_inactive=False,
            regenerate_images=True,
            dry_run=True,
            draft_suffix="",
        )
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert calls == [
        {
            "step": "load",
            "config_path": "DEFAULT",
            "target_id": "xiaobo-template2",
            "include_inactive": False,
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
            product_order_strategy,
            stale_product_image_policy,
            mode,
            top_uids,
            product_card_template_id,
            package_output_path,
            subtitle_alignment,
        ):
            calls.append(
                {
                    "project_id": project_id,
                    "account_label": account_label,
                    "output_mode": output_mode,
                    "product_media_mode": product_media_mode,
                    "product_order_strategy": product_order_strategy,
                    "stale_product_image_policy": stale_product_image_policy,
                    "mode": mode,
                    "top_uids": top_uids,
                    "product_card_template_id": product_card_template_id,
                    "package_output_path": package_output_path,
                    "subtitle_alignment": subtitle_alignment,
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
    monkeypatch.setattr(cli, "validated_phase7_selection", lambda *_args, **_kwargs: {})

    cli.cmd_render_package(
        Namespace(
            project_id=3,
            account="xiaobo",
            output_mode="jianying_draft",
            product_media_mode="video_preferred",
            product_order_strategy="price_segment_shuffle",
            stale_product_image_policy="block",
            mode="standard",
            top_uids="",
            product_card_template_id="muban-xiaobo-1",
            pipeline=".pipeline.json",
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
            "product_order_strategy": "price_segment_shuffle",
            "stale_product_image_policy": "block",
            "mode": "standard",
            "top_uids": "",
            "product_card_template_id": "muban-xiaobo-1",
            "package_output_path": str(output),
            "subtitle_alignment": "proportional",
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
            product_order_strategy,
            stale_product_image_policy,
            mode,
            top_uids,
            product_card_template_id,
            package_output_path,
            subtitle_alignment,
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
    monkeypatch.setattr(cli, "validated_phase7_selection", lambda *_args, **_kwargs: {})

    cli.cmd_render_package(
        Namespace(
            project_id=3,
            account="xiaobo",
            output_mode="jianying_draft",
            product_media_mode="video_preferred",
            product_order_strategy="price_segment_shuffle",
            stale_product_image_policy="block",
            mode="standard",
            top_uids="",
            product_card_template_id="muban-xiaobo-1",
            pipeline=".pipeline.json",
            output=str(output),
        )
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["missing"] == missing
    assert not output.exists()


def test_cmd_render_package_keeps_final_mp4_next_step_structured(tmp_path, capsys, monkeypatch):
    def fake_build(db, **kwargs):
        return SimpleNamespace(
            package={"schemaVersion": "1.0.0", "segments": []},
            missing=[],
            stale_product_images=[],
        )

    service = WorkflowService.__new__(WorkflowService)
    service.db = "db"
    monkeypatch.setattr(workflow_service, "build_product_recommendation_package", fake_build)
    monkeypatch.setattr(workflow_service, "_product_card_text_capacity_issues", lambda **_kwargs: [])
    monkeypatch.setattr(cli, "_init", lambda: ("db", None, None, service))
    monkeypatch.setattr(cli, "validated_phase7_selection", lambda *_args, **_kwargs: {})
    output = tmp_path / "render-package.json"

    cli.cmd_render_package(
        Namespace(
            project_id=3,
            account="xiaobo",
            output_mode="final_mp4",
            product_media_mode="video_preferred",
            product_order_strategy="price_segment_shuffle",
            stale_product_image_policy="block",
            mode="standard",
            top_uids="",
            product_card_template_id="muban-xiaobo-1",
            pipeline=".pipeline.json",
            output=str(output),
        )
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["next"]["action"] == "render_final_video"
    assert "command" not in payload["next"]
    assert "cutme" not in json.dumps(payload["next"], ensure_ascii=False).lower()


def test_cmd_product_card_preflight_writes_gate_json(capsys, monkeypatch):
    calls: list[dict[str, object]] = []

    class FakeWorkflow:
        def product_card_preflight(
            self,
            project_id,
            *,
            account_label,
            product_card_template_id,
            product_uid,
            expect_cover,
        ):
            calls.append(
                {
                    "project_id": project_id,
                    "account_label": account_label,
                    "product_card_template_id": product_card_template_id,
                    "product_uid": product_uid,
                    "expect_cover": expect_cover,
                }
            )
            return {
                "ok": False,
                "status": "blocked",
                "issues": [{"code": "missing_cover_asset", "uid": "P001"}],
                "next": {"command": "python -m bworkflow_sql sync 3 --step master"},
            }

    monkeypatch.setattr(cli, "_init", lambda: ("db", None, None, FakeWorkflow()))

    cli.cmd_product_card_preflight(
        Namespace(
            project_id=3,
            account="xiaobo",
            product_card_template_id="muban-xiaobo-1",
            product_uid="P001",
            expect_cover="P001-new.png",
        )
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["status"] == "blocked"
    assert payload["next"]["command"] == "python -m bworkflow_sql sync 3 --step master"
    assert calls == [
        {
            "project_id": 3,
            "account_label": "xiaobo",
            "product_card_template_id": "muban-xiaobo-1",
            "product_uid": "P001",
            "expect_cover": "P001-new.png",
        }
    ]


def test_cmd_intro_preflight_writes_gate_json(capsys, monkeypatch):
    class FakeRepo:
        def project(self, project_id: int):
            assert project_id == 23
            return {"id": 23, "name": "数码-桌面音响"}

    def fake_preflight(**kwargs):
        assert kwargs["source_plan_path"] == "source.json"
        assert kwargs["asset_root"] == "assets"
        assert kwargs["project"] == {"id": 23, "name": "数码-桌面音响"}
        return {
            "ok": False,
            "status": "blocked_missing_intro_demo",
            "message": "缺 3 段数码-桌面音响通用产品展示素材",
        }

    monkeypatch.setattr(cli, "_init", lambda: ("db", FakeRepo(), None, None))
    monkeypatch.setattr(cli, "preflight_intro_plan_for_cutme", fake_preflight, raising=False)

    cli.cmd_intro_preflight(
        Namespace(project_id=23, source_plan="source.json", asset_root="assets")
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["status"] == "blocked_missing_intro_demo"
    assert payload["message"] == "缺 3 段数码-桌面音响通用产品展示素材"
def test_cmd_render_intro_video_writes_standard_json(capsys, monkeypatch):
    calls: list[dict[str, object]] = []

    class FakeWorkflow:
        def render_intro_video(
            self,
            project_id,
            *,
                account_label,
                intro_label,
                output_path,
                asset_root,
                pipeline_path,
        ):
            calls.append(
                {
                    "project_id": project_id,
                    "account_label": account_label,
                    "intro_label": intro_label,
                    "output_path": output_path,
                        "asset_root": asset_root,
                        "pipeline_path": pipeline_path,
                }
            )
            return {
                "ok": True,
                "output_path": "G:/workspace/out/intro.mp4",
                "subtitle_count": 3,
            }

    monkeypatch.setattr(cli, "_init", lambda: ("db", None, None, FakeWorkflow()))

    cli.cmd_render_intro_video(
        Namespace(
            project_id=23,
            account="xiaobo",
            intro_label="intro-1",
            output="intro.mp4",
            asset_root="assets",
        )
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["subtitle_count"] == 3
    assert calls == [
        {
            "project_id": 23,
            "account_label": "xiaobo",
            "intro_label": "intro-1",
                "output_path": "intro.mp4",
                "asset_root": "assets",
                "pipeline_path": None,
        }
    ]
