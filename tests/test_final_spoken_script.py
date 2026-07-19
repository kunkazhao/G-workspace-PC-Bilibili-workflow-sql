import json
from pathlib import Path

from bworkflow_sql.db import Database
from bworkflow_sql.final_spoken_script import backfill_final_spoken_script, validate_spoken_script_evidence
from bworkflow_sql.repositories import Repository


def test_backfill_uses_exact_render_package_segments_and_binds_evidence(tmp_path: Path, monkeypatch) -> None:
    import bworkflow_sql.final_spoken_script as module

    spoken_root = tmp_path / "spoken"
    monkeypatch.setattr(module, "DEFAULT_SPOKEN_MD_ROOT", spoken_root)
    db = Database(tmp_path / "test.db")
    project_id = db.upsert_project({"name": "家居-防晒衣", "scheme_id": "s1", "scheme_name": "主方案"})
    repo = Repository(db)
    package_path = tmp_path / "artifacts" / "process" / "render-package.json"
    package_path.parent.mkdir(parents=True)
    package_path.write_text(
        json.dumps(
            {
                "segments": [
                    {"type": "intro", "spokenText": "这是引言"},
                    {
                        "type": "product_recommendation",
                        "productUid": "FSY019",
                        "productTitle": "蕉下男款",
                        "sourceScriptBlockId": 11184,
                        "spokenText": "必须保留正文二的实际版本。",
                    },
                    {"type": "price_transition", "priceRangeLabel": "200以上", "transitionText": "接着看高预算。"},
                    {"type": "outro", "spokenText": "这是结尾"},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    manifest_path = tmp_path / "final-video-20260719_014332.run-manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schemaVersion": "1.0.0",
                "kind": "bworkflow.final_video_run",
                "createdAt": "2026-07-19T01:49:20",
                "project": {"id": project_id, "account": "荣荣"},
                "inputs": {"render_package_path": str(package_path)},
                "file_fingerprints": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    pipeline = tmp_path / ".pipeline.json"
    pipeline.write_text(
        json.dumps({"phases": {"assembly": {"run_manifest_path": str(manifest_path)}}}),
        encoding="utf-8",
    )

    result = backfill_final_spoken_script(repo, run_manifest_path=manifest_path, pipeline_path=pipeline)

    current = Path(result["spoken_script"]["current_path"])
    snapshot = Path(result["spoken_script"]["snapshot_path"])
    text = current.read_text(encoding="utf-8")
    assert "必须保留正文二的实际版本。" in text
    assert '"source_script_block_id": 11184' in text
    assert current.name == "7月-荣荣.md"
    assert snapshot.name == "完整口播稿.md"
    assert snapshot.read_bytes() == current.read_bytes()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    validate_spoken_script_evidence(manifest)
    assert manifest["schemaVersion"] == "1.1.0"
    assert repo.project(project_id)["spoken_md_path"] == str(current.resolve())
    db.close()
