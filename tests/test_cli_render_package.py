from __future__ import annotations

import json
from argparse import Namespace
from types import SimpleNamespace

from bworkflow_sql import cli


def test_render_package_parser_registers_command():
    args = cli.build_parser().parse_args(
        [
            "render-package",
            "3",
            "--account",
            "小博",
            "--output-mode",
            "final_mp4",
            "--output",
            "out.json",
        ]
    )

    assert args.command == "render-package"
    assert args.project_id == 3
    assert args.account == "小博"
    assert args.output_mode == "final_mp4"
    assert args.output == "out.json"


def test_cmd_render_package_writes_success_json_and_package(
    tmp_path,
    capsys,
    monkeypatch,
):
    package = {
        "schemaVersion": "1.0.0",
        "packageType": "bilibili_video",
        "segments": [
            {"type": "price_transition"},
            {"type": "product_recommendation"},
            {"type": "product_recommendation"},
        ],
    }
    calls: list[dict[str, object]] = []

    def fake_build(db, *, project_id, account_label, output_mode):
        calls.append(
            {
                "db": db,
                "project_id": project_id,
                "account_label": account_label,
                "output_mode": output_mode,
            }
        )
        return SimpleNamespace(package=package, missing=[])

    output = tmp_path / "render-package.json"
    monkeypatch.setattr(cli, "_init", lambda: ("db", None, None, None))
    monkeypatch.setattr(cli, "build_product_recommendation_package", fake_build, raising=False)

    cli.cmd_render_package(
        Namespace(
            project_id=3,
            account="小博",
            output_mode="jianying_draft",
            output=str(output),
        )
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["project_id"] == 3
    assert payload["account"] == "小博"
    assert payload["output_mode"] == "jianying_draft"
    assert payload["package_path"] == str(output)
    assert payload["missing"] == []
    assert payload["segment_counts"] == {
        "price_transition": 1,
        "product_recommendation": 2,
    }
    assert json.loads(output.read_text(encoding="utf-8")) == package
    assert calls == [
        {
            "db": "db",
            "project_id": 3,
            "account_label": "小博",
            "output_mode": "jianying_draft",
        }
    ]


def test_cmd_render_package_reports_missing_without_writing_package(
    tmp_path,
    capsys,
    monkeypatch,
):
    missing = [{"kind": "product_voice", "uid": "P001"}]

    def fake_build(db, *, project_id, account_label, output_mode):
        return SimpleNamespace(package={"segments": []}, missing=missing)

    output = tmp_path / "render-package.json"
    monkeypatch.setattr(cli, "_init", lambda: ("db", None, None, None))
    monkeypatch.setattr(cli, "build_product_recommendation_package", fake_build, raising=False)

    cli.cmd_render_package(
        Namespace(
            project_id=3,
            account="小博",
            output_mode="jianying_draft",
            output=str(output),
        )
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["missing"] == missing
    assert not output.exists()
