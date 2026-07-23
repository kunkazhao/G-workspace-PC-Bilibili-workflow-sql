from __future__ import annotations

import pytest

from bworkflow_sql import cli
from bworkflow_sql.pages import PAGE_MAP
from bworkflow_sql.render_package_builder import SUPPORTED_OUTPUT_MODES
from bworkflow_sql.workflow_service import WorkflowService


def test_public_cli_keeps_final_video_and_hides_retired_routes(capsys) -> None:
    parser = cli.build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["--help"])

    help_text = capsys.readouterr().out
    assert "render-final-video" in help_text
    assert "product-images" not in help_text
    assert "jianying" not in help_text.lower()
    assert "template-calibrate" not in help_text


def test_page_registry_hides_jianying_generation() -> None:
    assert all("剪映" not in page_name for page_name in PAGE_MAP.keys())


def test_render_package_builder_only_accepts_formal_mp4() -> None:
    assert SUPPORTED_OUTPUT_MODES == {"final_mp4"}


def test_final_video_parser_has_no_static_image_controls() -> None:
    parser = cli.build_parser()
    command_parser = parser._subparsers._group_actions[0].choices["render-final-video"]
    option_names = {option for action in command_parser._actions for option in action.option_strings}

    assert "--product-image-mode" not in option_names
    assert "--stale-product-image-policy" not in option_names


def test_workflow_service_has_no_jianying_command_builder() -> None:
    assert not hasattr(WorkflowService, "build_jianying_command")


def test_internal_jianying_dispatch_is_rejected(monkeypatch) -> None:
    service = object.__new__(WorkflowService)
    monkeypatch.setattr(
        WorkflowService,
        "generate_jianying_draft",
        lambda *args, **kwargs: object(),
    )

    with pytest.raises(ValueError):
        service.run_command(["internal:jianying", "--project-id", "1"])
