import json
from types import SimpleNamespace

from bworkflow_sql import cli


class FakeWorkflowService:
    def __init__(self) -> None:
        self.generated_provider = ""
        self.counted_provider = ""
        self.generated_script_ids = None
        self.counted_script_ids = None

    def generate_voice(self, project_id, **kwargs):
        self.generated_provider = kwargs["voice_provider"]
        self.generated_script_ids = kwargs.get("script_ids")
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    def voice_generation_counts(self, project_id, **kwargs):
        self.counted_provider = kwargs["voice_provider"]
        self.counted_script_ids = kwargs.get("script_ids")
        return 3, 1, 2


def test_voice_cli_defaults_to_current_minimax_behavior(monkeypatch, capsys):
    workflow = FakeWorkflowService()
    monkeypatch.setattr(cli, "_init", lambda: (None, None, None, workflow))
    args = cli.build_parser().parse_args(["voice", "7", "--account", "小燃"])

    cli.cmd_voice(args)

    assert workflow.generated_provider == "minimax"
    assert json.loads(capsys.readouterr().out)["ok"] is True


def test_voice_and_counts_cli_accept_indextts_provider(monkeypatch, capsys):
    workflow = FakeWorkflowService()
    monkeypatch.setattr(cli, "_init", lambda: (None, None, None, workflow))
    parser = cli.build_parser()

    cli.cmd_voice(parser.parse_args(["voice", "7", "--voice-provider", "indextts"]))
    capsys.readouterr()
    cli.cmd_voice_counts(
        parser.parse_args(["voice-counts", "7", "--voice-provider", "indextts"])
    )

    assert workflow.generated_provider == "indextts"
    assert workflow.counted_provider == "indextts"
    assert json.loads(capsys.readouterr().out)["pending"] == 2


def test_voice_and_counts_cli_can_target_one_script_id(monkeypatch, capsys):
    workflow = FakeWorkflowService()
    monkeypatch.setattr(cli, "_init", lambda: (None, None, None, workflow))
    parser = cli.build_parser()

    cli.cmd_voice(parser.parse_args(["voice", "7", "--script-ids", "intro:V003"]))
    capsys.readouterr()
    cli.cmd_voice_counts(
        parser.parse_args(["voice-counts", "7", "--script-ids", "intro:V003"])
    )

    assert workflow.generated_script_ids == ["intro:V003"]
    assert workflow.counted_script_ids == ["intro:V003"]
