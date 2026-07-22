from pathlib import Path
import json
import math
import re
import sqlite3
import struct
import wave

import pytest

from bworkflow_sql.db import Database
from bworkflow_sql.md_parser import parse_markdown_text
from bworkflow_sql.repositories import Repository
from bworkflow_sql.settings import INTERNAL_WORKSPACE_ROOT, JIANYING_ENGINE_DIR
from bworkflow_sql.sync_service import SyncService
from bworkflow_sql.utils import now_iso, text_hash
from bworkflow_sql.workflow_errors import AmbiguousProjectReferenceError
import bworkflow_sql.workflow_service as workflow_service_module
import bworkflow_sql.tts_helpers as tts_helpers_module
from bworkflow_sql.tts_helpers import voice_synthesis_identity
from bworkflow_sql.workflow_service import (
    DEFAULT_CLOSING_TEXT,
    VOICE_PROVIDER_MINIMAX,
    VoiceJob,
    WorkflowService,
    compress_internal_silence,
    markdown_file_to_voice_text,
    markdown_to_voice_text,
    normalize_audio_loudness,
    normalize_generated_voice_silence,
    prepend_silence,
    split_subtitle_text,
    unique_path,
)


def seed_project(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    repo = Repository(db)
    project_id = db.upsert_project(
        {
            "name": "数码-有线耳机",
            "category_id": "cat-1",
            "category_name": "有线耳机",
            "scheme_id": "scheme-1",
            "scheme_name": "模板1",
            "image_root": str(tmp_path / "images"),
            "video_root": str(tmp_path / "videos"),
            "voice_root": str(tmp_path / "voice"),
            "spoken_md_path": str(tmp_path / "口播稿.md"),
            "output_root": str(tmp_path / "legacy-out"),
        }
    )
    repo.upsert_products_from_master(project_id, [{"uid": "YXEJ002", "title": "竹林鸟夜莺Z1", "price_label": "59元"}])
    parsed = parse_markdown_text(
        """
## 引言文案

### 引言1
今天聊有线耳机。

## 商品文案

### 竹林鸟夜莺Z1-YXEJ002-59元
#### 正文
这是商品文案。

## 价格过渡文案

### 0-100
这个价格段值得看。
""".strip()
    )
    SyncService(db).sync_markdown_payload(project_id, parsed)
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO accounts (label, account_id, voice_id, minimax_voice_id, voice_name, created_at, updated_at)
            VALUES ('小燃', 'xiaoran', 'voice-1', 'minimax-voice-1', '小燃音色', 'now', 'now')
            """
        )
    return db, project_id


def seed_ready_voice_assets(
    db: Database,
    project_id: int,
    tmp_path: Path,
    *,
    account_label: str = "小燃",
    include_intro: bool = True,
    include_products: bool = True,
    include_price: bool = True,
) -> None:
    repo = Repository(db)
    account = next(account for account in repo.accounts() if account["label"] == account_label)
    project = repo.project(project_id)
    voice_root = Path(project["voice_root"]) / project["name"] / account_label
    voice_root.mkdir(parents=True, exist_ok=True)
    blocks = repo.script_blocks(project_id)
    rows: list[tuple[str, dict[str, object], str]] = []
    for block in blocks:
        script_type = block["script_type"]
        if script_type == "intro" and include_intro:
            rows.append(("INTRO", block, block["block_label"]))
        elif script_type == "product" and include_products:
            rows.append((block["owner_uid"], block, block["block_label"]))
        elif script_type == "price_transition" and include_price:
            rows.append(("PRICE_TRANSITION", block, block["price_range_label"]))
    with db.connect() as conn:
        for uid, block, block_label in rows:
            path = voice_root / f"{uid}-{block['id']}.wav"
            path.write_bytes(b"voice")
            conn.execute(
                """
                INSERT INTO asset_bindings
                    (project_id, uid, script_block_id, asset_type, account_label, account_id, block_label, script_id, text_hash, path, status, source_kind, created_at, updated_at)
                VALUES (?, ?, ?, 'voice', ?, ?, ?, ?, ?, ?, 'ready', 'test', ?, ?)
                """,
                (
                    project_id,
                    uid,
                    block["id"],
                    account_label,
                    account["account_id"],
                    block_label,
                    block["script_id"],
                    block["text_hash"],
                    str(path),
                    now_iso(),
                    now_iso(),
                ),
            )


def test_load_minimax_api_key_prefers_new_skill_env(monkeypatch, tmp_path: Path):
    new_skill_env = tmp_path / "zhaoer-tools-minimax-tts.env"
    legacy_skill_env = tmp_path / "minimax-tts.env"
    cwd_env = tmp_path / ".env"
    new_skill_env.write_text("MINIMAX_API_KEY=new-skill-key\n", encoding="utf-8")
    legacy_skill_env.write_text("MINIMAX_API_KEY=legacy-key\n", encoding="utf-8")
    cwd_env.write_text("MINIMAX_API_KEY=cwd-key\n", encoding="utf-8")

    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(tts_helpers_module, "MINIMAX_ENV_FILE_PATHS", (new_skill_env, legacy_skill_env))

    assert tts_helpers_module.load_minimax_api_key() == "new-skill-key"

    new_skill_env.unlink()
    assert tts_helpers_module.load_minimax_api_key() == "legacy-key"

    legacy_skill_env.unlink()
    assert tts_helpers_module.load_minimax_api_key() == "cwd-key"


def test_load_minimax_api_key_prefers_process_env(monkeypatch, tmp_path: Path):
    new_skill_env = tmp_path / "zhaoer-tools-minimax-tts.env"
    new_skill_env.write_text("MINIMAX_API_KEY=new-skill-key\n", encoding="utf-8")

    monkeypatch.setenv("MINIMAX_API_KEY", "process-key")
    monkeypatch.setattr(tts_helpers_module, "MINIMAX_ENV_FILE_PATHS", (new_skill_env,))

    assert tts_helpers_module.load_minimax_api_key() == "process-key"


def write_test_wav(path: Path, segments: list[tuple[float, float]], *, frame_rate: int = 16000) -> None:
    samples: list[int] = []
    for duration_sec, amplitude in segments:
        frame_count = int(round(duration_sec * frame_rate))
        for index in range(frame_count):
            value = int(amplitude * 32767 * math.sin(2 * math.pi * 440 * (index / frame_rate)))
            samples.append(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(frame_rate)
        writer.writeframes(b"".join(struct.pack("<h", sample) for sample in samples))


def test_expected_voice_output_dir_matches_account_and_category(tmp_path: Path):
    db, project_id = seed_project(tmp_path)
    repo = Repository(db)
    project = repo.project(project_id)
    account = repo.accounts()[0]

    output_dir = WorkflowService(db).expected_voice_output_dir(project_id, account_label=account["label"])

    assert output_dir == Path(project["voice_root"]) / project["name"] / account["label"]


def test_workflow_commands_use_internal_tasks(tmp_path: Path):
    db, project_id = seed_project(tmp_path)
    service = WorkflowService(db)

    voice = service.build_voice_command(project_id, account_label="小燃", uids=["YXEJ002"])
    assert voice[0] == "internal:voice"
    assert "--project-id" in voice
    assert "audio_segment_registry.json" not in " ".join(voice)
    assert "run_peiyindan.py" not in " ".join(voice)
    assert "--uids" in voice
    assert "YXEJ002" in voice
    script_voice = service.build_voice_command(project_id, account_label="Сȼ", script_ids=["product:YXEJ002:V001"])
    assert "--script-ids" in script_voice
    assert "product:YXEJ002:V001" in script_voice
    minimax_voice = service.build_voice_command(project_id, account_label="小燃", voice_provider=VOICE_PROVIDER_MINIMAX)
    assert "--voice-provider" in minimax_voice
    assert VOICE_PROVIDER_MINIMAX in minimax_voice

    assembly = service.build_assembly_command(project_id, mode="top", top_uids=["YXEJ002"], account_label="小燃", intro_index=1)
    assert assembly[0] == "internal:assembly"
    assert "--output-markdown" in assembly
    assert "--mode" in assembly
    assert "top" in assembly
    assert "--account-label" in assembly
    assert "小燃" in assembly
    assert "generate_spoken_script.py" not in " ".join(assembly)
    assert "audio_segment_registry.json" not in " ".join(assembly)
    assert str(tmp_path / "口播稿.md") in assembly
    internal_manifest = INTERNAL_WORKSPACE_ROOT / f"project-{project_id}" / "manifests" / "口播稿.manifest.json"
    assert "--markdown-path" not in assembly
    assert "--out-dir" not in assembly

    intro_video = tmp_path / "intro.mp4"
    intro_video.write_bytes(b"video")
    jianying = service.build_jianying_command(
        project_id,
        draft_name="数码/有线耳机",
        intro_video_path=intro_video,
    )
    assert jianying[0] == "internal:jianying"
    assert "--manifest" in jianying
    assert str(internal_manifest) in jianying
    assert "--intro-video" in jianying
    assert str(intro_video) in jianying
    assert "--draft-name" in jianying
    assert "数码_有线耳机" in jianying
    assert "--draft-root" in jianying
    assert r"E:\剪辑-剪映\草稿\JianyingPro Drafts" in jianying
    assert "--output-dir" not in jianying
    assert "generate_jianying_draft_with_display_videos.py" not in " ".join(jianying)


def test_voice_jobs_treat_mixed_uids_and_script_ids_as_union(tmp_path: Path):
    db, project_id = seed_project(tmp_path)
    repo = Repository(db)
    service = WorkflowService(db)
    blocks = repo.script_blocks(project_id)
    intro_id = next(block["script_id"] for block in blocks if block["script_type"] == "intro")
    price_id = next(block["script_id"] for block in blocks if block["script_type"] == "price_transition")

    jobs = service._voice_jobs(project_id, uids=["YXEJ002"], script_ids=[intro_id, price_id])

    assert {job.kind for job in jobs} == {"product", "intro", "price_transition"}
    assert {job.block["script_id"] for job in jobs} == {"product:YXEJ002:V001", intro_id, price_id}


def test_assemble_plan_previews_sequence_without_writing_spoken_files(tmp_path: Path):
    db, project_id = seed_project(tmp_path)
    repo = Repository(db)
    service = WorkflowService(db)
    seed_ready_voice_assets(db, project_id, tmp_path)
    spoken_path = Path(repo.project(project_id)["spoken_md_path"])
    if spoken_path.exists():
        spoken_path.unlink()

    plan = service.assemble_spoken_script_plan(project_id, account_label="小燃")

    assert plan["ok"] is True
    assert plan["status"] == "ready_to_assemble"
    assert plan["summary"]["intro_blocks"] == 1
    assert plan["summary"]["product_blocks"] == 1
    assert plan["summary"]["price_transition_blocks"] == 1
    assert [entry["section"] for entry in plan["sequence"]] == [
        "intro",
        "price_transition",
        "product",
        "closing",
    ]
    assert plan["next"]["action"] == "assemble"
    assert not spoken_path.exists()


def test_assemble_plan_top_uids_string_is_not_split_into_characters(tmp_path: Path):
    db, project_id = seed_project(tmp_path)
    repo = Repository(db)
    service = WorkflowService(db)
    ts = now_iso()
    repo.upsert_products_from_master(
        project_id,
        [
            {"uid": "YXEJ002", "title": "竹林鸟夜莺E1", "price_label": "59元"},
            {"uid": "YXEJ006", "title": "Top two", "price_label": "199元"},
            {"uid": "YXEJ007", "title": "Top three", "price_label": "399元"},
        ],
    )
    with db.connect() as conn:
        for uid, body in [
            ("YXEJ006", "TOP TWO BODY."),
            ("YXEJ007", "TOP THREE BODY."),
        ]:
            conn.execute(
                """
                INSERT INTO script_blocks
                    (project_id, script_type, owner_uid, price_range_label, block_label, body, text_hash, source, source_anchor, created_at, updated_at)
                VALUES (?, 'product', ?, '', '正文', ?, ?, 'test', '', ?, ?)
                """,
                (project_id, uid, body, text_hash(body), ts, ts),
            )
    seed_ready_voice_assets(db, project_id, tmp_path)

    plan = service.assemble_spoken_script_plan(
        project_id,
        account_label="小燃",
        mode="top",
        top_uids="YXEJ006,YXEJ002",
        product_order_strategy="price_segment_shuffle",
    )

    product_uids = [
        entry["product_uid"]
        for entry in plan["sequence"]
        if entry.get("section") == "product"
    ]
    assert product_uids[:2] == ["YXEJ006", "YXEJ002"]
    assert "--top-uids Y,X,E,J" not in plan["next"]["command"]
    assert "--top-uids YXEJ006,YXEJ002" in plan["next"]["command"]


def test_assemble_plan_blocks_product_voice_when_text_hash_changed(tmp_path: Path):
    db, project_id = seed_project(tmp_path)
    repo = Repository(db)
    service = WorkflowService(db)
    project = repo.project(project_id)
    voice_path = Path(project["voice_root"]) / project["name"] / "灏忕噧" / "old-YXEJ002.wav"
    voice_path.parent.mkdir(parents=True)
    voice_path.write_bytes(b"old voice")
    product_block = next(block for block in repo.script_blocks(project_id) if block["script_type"] == "product")
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO asset_bindings
                (project_id, uid, asset_type, account_label, account_id, block_label, text_hash, path, status, source_kind, created_at, updated_at)
            VALUES (?, 'YXEJ002', 'voice', '灏忕噧', 'xiaoran', ?, 'old-hash', ?, 'ready', 'legacy_import', 'now', 'now')
            """,
            (project_id, product_block["block_label"], str(voice_path)),
        )

    plan = service.assemble_spoken_script_plan(project_id, account_label="灏忕噧")

    assert plan["ok"] is False
    assert plan["status"] == "voice_incomplete"
    missing_voice = [issue for issue in plan["issues"] if issue["code"] == "missing_voice_asset"]
    product_issue = next(issue for issue in missing_voice if issue["uid"] == "YXEJ002")
    assert product_issue["text_hash"] == product_block["text_hash"]
    assert plan["next"]["action"] == "generate_voice"
    assert "voice-counts" in plan["next"]["command"]


def test_workflow_doctor_blocks_on_script_before_voice_or_template(tmp_path: Path, monkeypatch):
    db, project_id = seed_project(tmp_path)
    service = WorkflowService(db)
    calls: list[str] = []

    def fake_script_doctor(project_id_arg: int, *, intro_label: str = "") -> dict[str, object]:
        calls.append(f"script:{intro_label}")
        return {
            "ok": False,
            "status": "needs_sync",
            "issues": [{"code": "markdown_not_synced"}],
            "next": {"action": "sync_markdown", "command": f"python -m bworkflow_sql sync {project_id_arg} --step markdown"},
        }

    def fail_assemble_plan(*_args, **_kwargs):
        raise AssertionError("workflow-doctor should not check assembly before script passes")

    def fail_template_doctor(*_args, **_kwargs):
        raise AssertionError("workflow-doctor should not check template before script passes")

    monkeypatch.setattr(service, "script_doctor", fake_script_doctor)
    monkeypatch.setattr(service, "assemble_spoken_script_plan", fail_assemble_plan)
    monkeypatch.setattr(service, "template_doctor", fail_template_doctor)

    result = service.workflow_doctor(
        project_id,
        account_label="灏忕噧",
        intro_label="寮曡█1",
        product_card_template_id="muban-xiaoran-1",
    )

    assert result["ok"] is False
    assert result["status"] == "blocked"
    assert result["blocked_by"] == "script"
    assert result["checks"]["script"]["status"] == "needs_sync"
    assert result["checks"]["voice_and_assembly"] is None
    assert result["checks"]["template"] is None
    assert result["issues"] == [{"source": "script-doctor", "code": "markdown_not_synced"}]
    assert result["next"]["action"] == "sync_markdown"
    assert calls == ["script:寮曡█1"]


def test_workflow_doctor_exposes_featured_products_for_phase7_confirmation(tmp_path: Path, monkeypatch):
    db, project_id = seed_project(tmp_path)
    service = WorkflowService(db)
    with db.connect() as conn:
        conn.execute(
            "UPDATE products SET product_card_json=? WHERE project_id=? AND uid='YXEJ002'",
            (json.dumps({"featured": True}, ensure_ascii=False), project_id),
        )

    monkeypatch.setattr(
        service,
        "script_doctor",
        lambda project_id_arg, *, intro_label="": {
            "ok": False,
            "status": "needs_sync",
            "issues": [],
            "next": {"action": "sync_markdown"},
        },
    )

    result = service.workflow_doctor(project_id, account_label="灏忕噧")

    assert result["checks"]["phase7_selection"] == {
        "featured_count": 1,
        "featured_products": [{"uid": "YXEJ002", "title": "竹林鸟夜莺Z1"}],
    }


def test_workflow_doctor_blocks_on_missing_voice_after_script_ready(tmp_path: Path, monkeypatch):
    db, project_id = seed_project(tmp_path)
    service = WorkflowService(db)
    monkeypatch.setattr(
        service,
        "script_doctor",
        lambda project_id_arg, *, intro_label="": {
            "ok": True,
            "status": "ready_for_downstream",
            "issues": [],
            "next": {"action": "continue_downstream"},
        },
    )

    result = service.workflow_doctor(
        project_id,
        account_label="灏忕噧",
        intro_label="寮曡█1",
        intro_index=1,
    )

    assert result["ok"] is False
    assert result["status"] == "blocked"
    assert result["blocked_by"] == "voice_and_assembly"
    assert result["checks"]["script"]["status"] == "ready_for_downstream"
    assert result["checks"]["voice_and_assembly"]["status"] == "voice_incomplete"
    assert result["checks"]["template"] is None
    assert any(issue["source"] == "assemble-plan" and issue["code"] == "missing_voice_asset" for issue in result["issues"])
    assert result["next"]["action"] == "generate_voice"
    assert "voice-counts" in result["next"]["command"]
    assert "voice " in result["next"]["follow_up_command"]


def test_workflow_doctor_blocks_on_intro_preflight_before_template(tmp_path: Path, monkeypatch):
    db, project_id = seed_project(tmp_path)
    service = WorkflowService(db)
    seed_ready_voice_assets(db, project_id, tmp_path)
    source_plan = tmp_path / "source-intro-plan-引言1.json"
    source_plan.write_text("{}", encoding="utf-8")
    calls: list[str] = []

    monkeypatch.setattr(
        service,
        "script_doctor",
        lambda project_id_arg, *, intro_label="": {
            "ok": True,
            "status": "ready_for_downstream",
            "issues": [],
            "selected_intro": {"source_intro_plan_path": str(source_plan)},
            "next": {"action": "continue_downstream"},
        },
    )

    def fake_preflight(**kwargs):
        calls.append(str(kwargs["source_plan_path"]))
        return {
            "ok": False,
            "status": "blocked_missing_intro_demo",
            "message": "缺 3 段数码-桌面音响通用产品展示素材",
            "issues": [{"type": "missing_intro_product_demo", "missing": 3}],
            "next": {"action": "add_intro_product_demo_clips", "needed_count": 3},
        }

    def fail_template_doctor(*_args, **_kwargs):
        raise AssertionError("workflow-doctor should not check template before intro preflight passes")

    monkeypatch.setattr("bworkflow_sql.workflow_service.preflight_intro_plan_for_cutme", fake_preflight)
    monkeypatch.setattr(service, "template_doctor", fail_template_doctor)

    result = service.workflow_doctor(
        project_id,
        account_label="灏忕噧",
        intro_label="寮曡█1",
        intro_index=1,
        product_card_template_id="muban-xiaoran-1",
    )

    assert result["ok"] is False
    assert result["status"] == "blocked"
    assert result["blocked_by"] == "intro_preflight"
    assert result["checks"]["intro_preflight"]["status"] == "blocked_missing_intro_demo"
    assert result["checks"]["template"] is None
    assert result["issues"][-1] == {
        "source": "intro-preflight",
        "type": "missing_intro_product_demo",
        "missing": 3,
    }
    assert result["next"]["action"] == "add_intro_product_demo_clips"
    assert calls == [str(source_plan)]


def test_workflow_doctor_includes_template_check_after_assembly_ready(tmp_path: Path, monkeypatch):
    db, project_id = seed_project(tmp_path)
    service = WorkflowService(db)
    seed_ready_voice_assets(db, project_id, tmp_path)
    template_calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        service,
        "script_doctor",
        lambda project_id_arg, *, intro_label="": {
            "ok": True,
            "status": "ready_for_downstream",
            "issues": [],
            "next": {"action": "continue_downstream"},
        },
    )

    def fake_template_doctor(
        project_id_arg: int,
        *,
        account_label: str,
        product_card_template_id: str,
        product_media_mode: str,
    ) -> dict[str, object]:
        template_calls.append(
            {
                "project_id": project_id_arg,
                "account_label": account_label,
                "product_card_template_id": product_card_template_id,
                "product_media_mode": product_media_mode,
            }
        )
        return {
            "ok": False,
            "status": "issues_found",
            "issues": [{"code": "missing_ready_image_binding", "uid": "YXEJ002"}],
            "next": {"action": "regenerate_product_images", "command": "python -m bworkflow_sql product-images 1"},
        }

    monkeypatch.setattr(service, "template_doctor", fake_template_doctor)

    result = service.workflow_doctor(
        project_id,
        account_label="灏忕噧",
        product_card_template_id="muban-xiaoran-1",
        product_media_mode="video_preferred",
    )

    assert result["ok"] is False
    assert result["blocked_by"] == "template"
    assert result["checks"]["voice_and_assembly"]["status"] == "ready_to_assemble"
    assert result["checks"]["template"]["status"] == "issues_found"
    assert result["issues"][-1] == {
        "source": "template-doctor",
        "code": "missing_ready_image_binding",
        "uid": "YXEJ002",
    }
    assert result["next"]["action"] == "regenerate_product_images"
    assert template_calls == [
        {
            "project_id": project_id,
            "account_label": "灏忕噧",
            "product_card_template_id": "muban-xiaoran-1",
            "product_media_mode": "video_preferred",
        }
    ]


def test_workflow_doctor_resolves_unique_project_name(tmp_path: Path, monkeypatch):
    db, project_id = seed_project(tmp_path)
    project_name = Repository(db).project(project_id)["name"]
    service = WorkflowService(db)
    seed_ready_voice_assets(db, project_id, tmp_path)
    monkeypatch.setattr(
        service,
        "script_doctor",
        lambda project_id_arg, *, intro_label="": {
            "ok": True,
            "status": "ready_for_downstream",
            "issues": [],
            "next": {"action": "continue_downstream"},
        },
    )

    result = service.workflow_doctor(project_name, account_label="灏忕噧")

    assert result["project"]["id"] == project_id
    assert result["status"] == "ready"


def test_workflow_doctor_resolves_project_name_with_scheme_filter(tmp_path: Path, monkeypatch):
    db, first_project_id = seed_project(tmp_path)
    project_name = Repository(db).project(first_project_id)["name"]
    second_project_id = db.upsert_project(
        {
            "name": project_name,
            "category_name": Repository(db).project(first_project_id)["category_name"],
            "scheme_id": "scheme-2",
            "scheme_name": "方案B",
            "image_root": str(tmp_path / "images-2"),
            "video_root": str(tmp_path / "videos-2"),
            "voice_root": str(tmp_path / "voice-2"),
            "spoken_md_path": str(tmp_path / "episode-2.md"),
        }
    )
    service = WorkflowService(db)
    seed_ready_voice_assets(db, first_project_id, tmp_path)
    monkeypatch.setattr(
        service,
        "script_doctor",
        lambda project_id_arg, *, intro_label="": {
            "ok": True,
            "status": "ready_for_downstream",
            "issues": [],
            "next": {"action": "continue_downstream"},
        },
    )
    monkeypatch.setattr(
        service,
        "assemble_spoken_script_plan",
        lambda project_id_arg, **_: {
            "ok": True,
            "status": "ready_to_assemble",
            "issues": [],
            "next": {"action": "assemble", "command": f"python -m bworkflow_sql assemble {project_id_arg}"},
        },
    )

    result = service.workflow_doctor(project_name, scheme_name="方案B", account_label="灏忕噧")

    assert result["project"]["id"] == second_project_id
    assert result["project"]["scheme_name"] == "方案B"


def test_workflow_doctor_reports_ambiguous_project_name(tmp_path: Path):
    db, first_project_id = seed_project(tmp_path)
    first_project = Repository(db).project(first_project_id)
    db.upsert_project(
        {
            "name": first_project["name"],
            "category_name": first_project["category_name"],
            "scheme_id": "scheme-2",
            "scheme_name": "方案B",
            "spoken_md_path": str(tmp_path / "episode-2.md"),
        }
    )
    service = WorkflowService(db)

    try:
        service.workflow_doctor(first_project["name"], account_label="灏忕噧")
    except AmbiguousProjectReferenceError as exc:
        message = str(exc)
    else:
        raise AssertionError("ambiguous project name should be rejected")

    assert "ambiguous project reference" in message
    assert "--scheme-name" in message
    assert "方案B" in message


def test_assemble_refuses_product_voice_when_only_old_uid_voice_exists(tmp_path: Path):
    db, project_id = seed_project(tmp_path)
    repo = Repository(db)
    service = WorkflowService(db)
    project = repo.project(project_id)
    voice_path = Path(project["voice_root"]) / project["name"] / "灏忕噧" / "old-YXEJ002.wav"
    voice_path.parent.mkdir(parents=True)
    voice_path.write_bytes(b"old voice")
    product_block = next(block for block in repo.script_blocks(project_id) if block["script_type"] == "product")
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO asset_bindings
                (project_id, uid, asset_type, account_label, account_id, block_label, text_hash, path, status, source_kind, created_at, updated_at)
            VALUES (?, 'YXEJ002', 'voice', '灏忕噧', 'xiaoran', ?, 'old-hash', ?, 'ready', 'legacy_import', 'now', 'now')
            """,
            (project_id, product_block["block_label"], str(voice_path)),
        )

    try:
        service.assemble_spoken_script(project_id, account_label="灏忕噧")
    except ValueError as exc:
        message = str(exc)
    else:
        raise AssertionError("assemble should reject old uid/account voice fallback")

    assert "missing_voice_asset" in message
    assert "voice-counts" in message
    assert "voice" in message


def test_voice_filename_uses_price_uid_title_and_duplicate_suffix(tmp_path: Path):
    db, project_id = seed_project(tmp_path)
    service = WorkflowService(db)
    repo = Repository(db)
    project = repo.project(project_id)
    block = next(block for block in repo.script_blocks(project_id) if block["script_type"] == "product")
    job = VoiceJob(
        block=block,
        uid="JP097",
        product_name="京东京造JZ990Pro",
        price_label="229元",
        index=1,
        kind="product",
    )

    assert service._voice_filename(job) == "229-JP097-京东京造JZ990Pro-正文.wav"
    existing = Path(project["voice_root"]) / project["name"] / "小燃" / service._voice_filename(job)
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"voice")
    assert unique_path(existing).name == "229-JP097-京东京造JZ990Pro-正文-1.wav"


def test_roll_b_rename_preview_uses_price_uid_title_and_duplicate_suffix(tmp_path: Path):
    db, project_id = seed_project(tmp_path)
    repo = Repository(db)
    repo.upsert_products_from_master(
        project_id,
        [
            {"uid": "JP015", "title": "狼途 LT84有线", "price_label": "99.0"},
            {"uid": "JP018", "title": "凌豹/K98", "price_label": "149元"},
        ],
    )
    video_dir = tmp_path / "videos"
    video_dir.mkdir()
    (video_dir / "JP015.mp4").write_bytes(b"video")
    (video_dir / "JP015-2.mov").write_bytes(b"video")
    (video_dir / "JP018.mp4").write_bytes(b"video")
    (video_dir / "unknown.mp4").write_bytes(b"video")

    preview = WorkflowService(db).preview_roll_b_rename(project_id, video_dir)
    targets = {item["source_name"]: item["target_name"] for item in preview["items"]}

    assert preview["counts"]["rename"] == 3
    assert preview["counts"]["skipped"] == 1
    assert targets["JP015.mp4"] == "99元-JP015-狼途 LT84有线-1.mp4"
    assert targets["JP015-2.mov"] == "99元-JP015-狼途 LT84有线-2.mov"
    assert targets["JP018.mp4"] == "149元-JP018-凌豹_K98.mp4"
    assert preview["can_execute"]


def test_roll_b_rename_execute_renames_files_and_preserves_suffix(tmp_path: Path):
    db, project_id = seed_project(tmp_path)
    repo = Repository(db)
    repo.upsert_products_from_master(project_id, [{"uid": "JP015", "title": "狼途 LT84有线", "price_label": "99元"}])
    video_dir = tmp_path / "videos"
    video_dir.mkdir()
    source = video_dir / "JP015.mp4"
    source.write_bytes(b"video")

    result = WorkflowService(db).execute_roll_b_rename(project_id, video_dir)

    target = video_dir / "99元-JP015-狼途 LT84有线.mp4"
    assert result["renamed"] == 1
    assert target.exists()
    assert not source.exists()


def test_roll_b_rename_blocks_external_target_conflict(tmp_path: Path):
    db, project_id = seed_project(tmp_path)
    repo = Repository(db)
    repo.upsert_products_from_master(project_id, [{"uid": "JP015", "title": "狼途 LT84有线", "price_label": "99元"}])
    video_dir = tmp_path / "videos"
    video_dir.mkdir()
    (video_dir / "JP015.mp4").write_bytes(b"video")
    (video_dir / "99元-JP015-狼途 LT84有线.mp4").mkdir()

    preview = WorkflowService(db).preview_roll_b_rename(project_id, video_dir)
    blocked = [item for item in preview["items"] if item["status"] == "blocked"]

    assert preview["counts"]["blocked"] == 1
    assert not preview["can_execute"]
    assert "目标文件已存在" in blocked[0]["message"]


def test_expired_voice_generation_overwrites_original_filename(tmp_path: Path):
    db, project_id = seed_project(tmp_path)
    service = WorkflowService(db)
    repo = Repository(db)
    project = repo.project(project_id)
    account = repo.accounts()[0]
    block = next(block for block in repo.script_blocks(project_id) if block["script_type"] == "product")
    job = VoiceJob(
        block=block,
        uid="YXEJ002",
        product_name="竹林鸟夜莺Z1",
        price_label="59元",
        index=1,
        kind="product",
    )
    output_dir = Path(project["voice_root"]) / project["name"] / account["label"]
    old_path = output_dir / service._voice_filename(job)
    write_test_wav(old_path, [(0.02, 0.0)])
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO asset_bindings
                (project_id, uid, script_block_id, asset_type, account_label, account_id, block_label, script_id, text_hash, path, status, source_kind, file_size, file_mtime, confirmed, created_at, updated_at)
            VALUES (?, ?, ?, 'voice', ?, ?, ?, ?, ?, ?, 'ready', 'generated', ?, ?, 1, ?, ?)
            """,
            (
                project_id,
                job.uid,
                job.block["id"],
                account["label"],
                account["account_id"],
                job.block["block_label"],
                job.block["script_id"],
                "old-hash",
                str(old_path),
                old_path.stat().st_size,
                "old",
                now_iso(),
                now_iso(),
            ),
        )

    class FakeHttp:
        def post(self, _url, *, json_payload):
            generated = tmp_path / "tts" / json_payload["output_name"]
            write_test_wav(generated, [(0.02, 0.2)])
            return {"audio_path": str(generated)}

    identity = voice_synthesis_identity("indextts", account["voice_id"])
    assert service._has_existing_stale_voice_file(
        project_id,
        job=job,
        account=account,
        identity=identity,
    )
    result_path = service._generate_one_voice(
        FakeHttp(),
        job=job,
        account=account,
        voice_id="voice-1",
        output_dir=output_dir,
        overwrite_expired=True,
    )

    assert result_path == old_path
    assert not old_path.with_name(f"{old_path.stem}-1{old_path.suffix}").exists()


def test_upserting_new_voice_registers_stale_file_without_deleting_it(tmp_path: Path):
    db, project_id = seed_project(tmp_path)
    service = WorkflowService(db)
    repo = Repository(db)
    project = repo.project(project_id)
    account = repo.accounts()[0]
    block = next(block for block in repo.script_blocks(project_id) if block["script_type"] == "product")
    job = VoiceJob(
        block=block,
        uid="YXEJ002",
        product_name="Product One",
        price_label="59",
        index=1,
        kind="product",
    )
    output_dir = Path(project["voice_root"]) / project["name"] / account["label"]
    stale_path = output_dir / "old-product-voice.mp3"
    new_path = output_dir / "new-product-voice.mp3"
    stale_path.parent.mkdir(parents=True)
    stale_path.write_bytes(b"stale")
    new_path.write_bytes(b"new")
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO asset_bindings
                (project_id, uid, script_block_id, asset_type, account_label, account_id, block_label, script_id, text_hash, path, status, source_kind, file_size, file_mtime, confirmed, created_at, updated_at)
            VALUES (?, ?, ?, 'voice', ?, ?, ?, ?, ?, ?, 'ready', 'generated', ?, ?, 1, ?, ?)
            """,
            (
                project_id,
                job.uid,
                job.block["id"],
                account["label"],
                account["account_id"],
                job.block["block_label"],
                job.block["script_id"],
                "old-hash",
                str(stale_path),
                stale_path.stat().st_size,
                "old",
                now_iso(),
                now_iso(),
            ),
        )

    identity = voice_synthesis_identity("indextts", account["voice_id"])
    service._upsert_voice_asset(
        project_id,
        job=job,
        account=account,
        path=new_path,
        identity=identity,
    )

    stale_asset = db.fetchone(
        "SELECT status FROM asset_bindings WHERE project_id=? AND path=?",
        (project_id, str(stale_path)),
    )
    candidate = db.fetchone(
        """
        SELECT status, resource_kind, reason
        FROM resource_cleanup_candidates
        WHERE project_id=? AND path=?
        """,
        (project_id, str(stale_path)),
    )
    assert stale_asset["status"] == "expired"
    assert stale_path.exists()
    assert candidate["status"] == "pending"
    assert candidate["resource_kind"] == "asset_voice"
    assert candidate["reason"] == "voice_generation_identity_changed"
    assert new_path.exists()


def test_export_markdown_uses_database_asset_bindings_and_asset_sync_dedupes(tmp_path: Path):
    db, project_id = seed_project(tmp_path)
    project = Repository(db).project(project_id)
    image_root = Path(project["image_root"])
    video_root = Path(project["video_root"])
    image_root.mkdir(parents=True)
    video_root.mkdir(parents=True)
    image_path = image_root / "59-YXEJ002-竹林鸟夜莺Z1.png"
    video_path = video_root / "59-YXEJ002-竹林鸟夜莺Z1.mp4"
    image_path.write_bytes(b"image")
    video_path.write_bytes(b"video")

    sync = SyncService(db)
    first = sync.sync_assets(project_id)
    second = sync.sync_assets(project_id)
    assert first["image"] == 1
    assert second["image"] == 1

    assets = Repository(db).asset_bindings(project_id)
    assert len([asset for asset in assets if asset["asset_type"] == "image"]) == 1
    assert len([asset for asset in assets if asset["asset_type"] == "video"]) == 1

    markdown_path = WorkflowService(db).export_project_markdown(project_id)
    assert "data" in str(markdown_path)
    assert "workspace" in str(markdown_path)
    text = markdown_path.read_text(encoding="utf-8")
    assert f"图片：{image_path}" in text
    assert f"视频：{video_path}" in text


def test_assembly_generates_spoken_markdown_and_internal_manifest_from_database(tmp_path: Path):
    db, project_id = seed_project(tmp_path)
    repo = Repository(db)
    service = WorkflowService(db)
    project = repo.project(project_id)
    image_path = Path(project["image_root"]) / "59-YXEJ002-竹林鸟夜莺Z1.png"
    video_path = Path(project["video_root"]) / "59-YXEJ002-竹林鸟夜莺Z1.mp4"
    voice_path = Path(project["voice_root"]) / project["name"] / "小燃" / "01-YXEJ002-竹林鸟夜莺Z1.wav"
    image_path.parent.mkdir(parents=True)
    video_path.parent.mkdir(parents=True)
    voice_path.parent.mkdir(parents=True)
    image_path.write_bytes(b"image")
    video_path.write_bytes(b"video")
    voice_path.write_bytes(b"voice")
    product_block = next(block for block in repo.script_blocks(project_id) if block["script_type"] == "product")
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO asset_bindings
                (project_id, uid, asset_type, account_label, account_id, path, status, source_kind, created_at, updated_at)
            VALUES (?, 'YXEJ002', 'image', '', '', ?, 'ready', 'test', 'now', 'now')
            """,
            (project_id, str(image_path)),
        )
        conn.execute(
            """
            INSERT INTO asset_bindings
                (project_id, uid, asset_type, account_label, account_id, path, status, source_kind, created_at, updated_at)
            VALUES (?, 'YXEJ002', 'video', '', '', ?, 'ready', 'test', 'now', 'now')
            """,
            (project_id, str(video_path)),
        )
        conn.execute(
            """
            INSERT INTO asset_bindings
                (project_id, uid, script_block_id, asset_type, account_label, account_id, block_label, text_hash, path, status, source_kind, created_at, updated_at)
            VALUES (?, 'YXEJ002', ?, 'voice', '小燃', 'xiaoran', ?, ?, ?, 'ready', 'test', 'now', 'now')
            """,
            (project_id, product_block["id"], product_block["block_label"], product_block["text_hash"], str(voice_path)),
        )
    seed_ready_voice_assets(db, project_id, tmp_path, include_products=False)

    result = service.run_command(
        service.build_assembly_command(project_id, mode="standard", account_label="小燃", intro_index=1)
    )

    assert result.returncode == 0
    spoken_path = Path(project["spoken_md_path"])
    assert spoken_path.exists()
    manifest_path = INTERNAL_WORKSPACE_ROOT / f"project-{project_id}" / "manifests" / f"{spoken_path.stem}.manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    product_entries = [entry for entry in payload["entries"] if entry["type"] == "product"]
    assert product_entries
    assert product_entries[0]["audio_path"] == str(voice_path)
    assert product_entries[0]["image_path"] == str(image_path)
    assert product_entries[0]["video_path"] == str(video_path)
    intro_entry = next(entry for entry in payload["entries"] if entry["section"] == "intro")
    assert intro_entry["image_path"] == ""
    assert intro_entry["video_path"] == ""


def test_assembly_does_not_fallback_to_other_template_images(tmp_path: Path):
    db, project_id = seed_project(tmp_path)
    repo = Repository(db)
    service = WorkflowService(db)
    project = repo.project(project_id)
    template1_image = Path(project["image_root"]) / "有线耳机" / "小燃" / "模板1" / "59-YXEJ002-竹林鸟夜莺Z1.png"
    template1_image.parent.mkdir(parents=True)
    template1_image.write_bytes(b"template1 image")

    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO asset_bindings
                (project_id, uid, asset_type, account_label, account_id, path, status, source_kind, created_at, updated_at)
            VALUES (?, 'YXEJ002', 'image', '小燃', 'xiaoran', ?, 'ready', 'test', 'now', 'now')
            """,
            (project_id, str(template1_image)),
        )
    seed_ready_voice_assets(db, project_id, tmp_path)

    result = service.run_command(
        service.build_assembly_command(
            project_id,
            mode="standard",
            account_label="小燃",
            intro_index=1,
            display_template="小燃-模板2",
        )
    )

    assert result.returncode == 0
    spoken_path = Path(project["spoken_md_path"])
    manifest_path = INTERNAL_WORKSPACE_ROOT / f"project-{project_id}" / "manifests" / f"{spoken_path.stem}.manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    product_entry = next(entry for entry in payload["entries"] if entry["type"] == "product")
    assert payload["display_template"] == "小燃-模板2"
    assert product_entry["image_path"] == ""


def test_assembly_prefers_current_category_voice_for_shared_price_transition(tmp_path: Path):
    db, project_id = seed_project(tmp_path)
    repo = Repository(db)
    service = WorkflowService(db)
    repo.upsert_products_from_master(project_id, [{"uid": "JP071", "title": "狼蛛F87ProV2超神版", "price_label": "359元"}])
    with db.connect() as conn:
        conn.execute("UPDATE projects SET name='数码-键盘', category_name='键盘' WHERE id=?", (project_id,))
        conn.execute(
            """
            INSERT INTO script_blocks
                (project_id, script_type, owner_uid, price_range_label, block_label, script_id, body, text_hash, source, source_anchor, created_at, updated_at)
            VALUES (?, 'price_transition', '', '300-500元', '正文', 'price:300-500:V001', ?, ?, 'test', '', 'now', 'now')
            """,
            (project_id, "300到500元值得重点看。", text_hash("300到500元值得重点看。")),
        )
    price_block = next(
        block
        for block in repo.script_blocks(project_id)
        if block["script_type"] == "price_transition" and block["price_range_label"] == "300-500元"
    )
    wrong_voice = tmp_path / "voice" / "数码-有线耳机" / "小燃" / "0-价格-300-500元.wav"
    right_voice = tmp_path / "voice" / "数码-键盘" / "小燃" / "0-价格-300-500元.wav"
    wrong_voice.parent.mkdir(parents=True)
    right_voice.parent.mkdir(parents=True)
    wrong_voice.write_bytes(b"wrong")
    right_voice.write_bytes(b"right")
    with db.connect() as conn:
        for path in (wrong_voice, right_voice):
            conn.execute(
                """
                INSERT INTO asset_bindings
                    (project_id, uid, script_block_id, asset_type, account_label, account_id, block_label, script_id, text_hash, path, status, source_kind, created_at, updated_at)
                VALUES (?, 'PRICE_TRANSITION', ?, 'voice', '小燃', 'xiaoran', '300-500元', 'price:300-500:V001', ?, ?, 'ready', 'test', 'now', 'now')
            """,
            (project_id, price_block["id"], price_block["text_hash"], str(path)),
        )
    seed_ready_voice_assets(db, project_id, tmp_path, include_price=False)

    result = service.assemble_spoken_script(project_id, account_label="小燃", product_uids=["JP071"])

    assert result.returncode == 0
    spoken_path = Path(repo.project(project_id)["spoken_md_path"])
    manifest_path = INTERNAL_WORKSPACE_ROOT / f"project-{project_id}" / "manifests" / f"{spoken_path.stem}.manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    transition = next(entry for entry in payload["entries"] if entry["price_range_label"] == "300-500元")
    assert transition["audio_path"] == str(right_voice)


def test_assembly_reuses_shared_price_transition_voice_after_script_block_id_changes(tmp_path: Path):
    db, project_id = seed_project(tmp_path)
    repo = Repository(db)
    service = WorkflowService(db)
    repo.upsert_products_from_master(project_id, [{"uid": "JP071", "title": "狼蛛F87ProV2超神版", "price_label": "359元"}])
    with db.connect() as conn:
        conn.execute("UPDATE projects SET name='数码-键盘', category_name='键盘' WHERE id=?", (project_id,))
        old_cursor = conn.execute(
            """
            INSERT INTO script_blocks
                (project_id, script_type, owner_uid, price_range_label, block_label, script_id, body, text_hash, source, source_anchor, active, created_at, updated_at)
            VALUES (?, 'price_transition', '', '300-500元-旧', '正文', 'price:300-500:OLD', ?, ?, 'test', '', 0, 'now', 'now')
            """,
            (project_id, "300到500元值得重点看。", text_hash("300到500元值得重点看。")),
        )
        old_block_id = old_cursor.lastrowid
        conn.execute(
            """
            INSERT INTO script_blocks
                (project_id, script_type, owner_uid, price_range_label, block_label, script_id, body, text_hash, source, source_anchor, created_at, updated_at)
            VALUES (?, 'price_transition', '', '300-500元', '正文', 'price:300-500:V001', ?, ?, 'test', '', 'now', 'now')
            """,
            (project_id, "300到500元值得重点看。", text_hash("300到500元值得重点看。")),
        )
    price_block = next(
        block
        for block in repo.script_blocks(project_id)
        if block["script_type"] == "price_transition" and block["price_range_label"] == "300-500元"
    )
    voice_path = tmp_path / "voice" / "数码-键盘" / "小燃" / "0-价格-300-500元.wav"
    voice_path.parent.mkdir(parents=True)
    voice_path.write_bytes(b"voice")
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO asset_bindings
                (project_id, uid, script_block_id, asset_type, account_label, account_id, block_label, script_id, text_hash, path, status, source_kind, created_at, updated_at)
            VALUES (?, 'PRICE_TRANSITION', ?, 'voice', '小燃', 'xiaoran', '300-500元', 'price:300-500:V001', ?, ?, 'ready', 'test', 'now', 'now')
            """,
            (project_id, old_block_id, price_block["text_hash"], str(voice_path)),
        )
    seed_ready_voice_assets(db, project_id, tmp_path, include_price=False)

    result = service.assemble_spoken_script(project_id, account_label="小燃", product_uids=["JP071"])

    assert result.returncode == 0
    spoken_path = Path(repo.project(project_id)["spoken_md_path"])
    manifest_path = INTERNAL_WORKSPACE_ROOT / f"project-{project_id}" / "manifests" / f"{spoken_path.stem}.manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    transition = next(entry for entry in payload["entries"] if entry["price_range_label"] == "300-500元")
    assert transition["audio_path"] == str(voice_path)


def test_assembly_writes_reader_friendly_spoken_markdown_without_repeated_price_sections(tmp_path: Path):
    db, project_id = seed_project(tmp_path)
    repo = Repository(db)
    service = WorkflowService(db)
    repo.upsert_products_from_master(
        project_id,
        [
            {"uid": "YXEJ002", "title": "Product One", "price_label": "59"},
            {"uid": "YXEJ003", "title": "Product Two", "price_label": "79"},
        ],
    )
    second_body = "SECOND PRODUCT BODY."
    ts = now_iso()
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO script_blocks
                (project_id, script_type, owner_uid, price_range_label, block_label, body, text_hash, source, source_anchor, created_at, updated_at)
            VALUES (?, 'product', 'YXEJ003', '', '正文', ?, ?, 'test', '', ?, ?)
            """,
            (project_id, second_body, text_hash(second_body), ts, ts),
        )
    seed_ready_voice_assets(db, project_id, tmp_path)

    result = service.run_command(service.build_assembly_command(project_id, mode="standard", account_label="小燃"))

    assert result.returncode == 0
    spoken_path = Path(repo.project(project_id)["spoken_md_path"])
    text = spoken_path.read_text(encoding="utf-8")
    price_body = next(block["body"] for block in repo.script_blocks(project_id) if block["script_type"] == "price_transition")
    assert not any(line.startswith("#") for line in text.splitlines())
    assert text.count(price_body) == 1
    assert second_body in text
    assert text.rstrip().endswith(DEFAULT_CLOSING_TEXT)


def test_assembly_rejects_reusable_asset_markdown_as_output(tmp_path: Path):
    db, project_id = seed_project(tmp_path)
    service = WorkflowService(db)
    asset_path = tmp_path / "product-copy-library.md"
    asset_path.write_text("ASSET COPY SENTINEL\n", encoding="utf-8")
    db.execute("UPDATE projects SET md_path=? WHERE id=?", (str(asset_path), project_id))

    with pytest.raises(ValueError, match="reusable asset Markdown"):
        service.assemble_spoken_script(
            project_id,
            account_label="小燃",
            output_markdown_path=asset_path,
        )

    assert asset_path.read_text(encoding="utf-8") == "ASSET COPY SENTINEL\n"


def test_explicit_product_uids_preserve_exact_assembly_order(tmp_path: Path, monkeypatch):
    db, project_id = seed_project(tmp_path)
    repo = Repository(db)
    service = WorkflowService(db)
    repo.upsert_products_from_master(
        project_id,
        [
            {"uid": "YXEJ002", "title": "Product One", "price_label": "59元"},
            {"uid": "YXEJ003", "title": "Product Two", "price_label": "79元"},
        ],
    )
    monkeypatch.setattr(
        service,
        "_products_in_price_segment_shuffle_order",
        lambda products, _price_blocks: list(reversed(products)),
    )

    products = service._ordered_products(
        project_id,
        mode="standard",
        top_uids=[],
        product_uids=["YXEJ002", "YXEJ003"],
        product_order_strategy="price_segment_shuffle",
    )

    assert [product["uid"] for product in products] == ["YXEJ002", "YXEJ003"]


def test_assembly_randomly_selects_one_product_and_price_version(tmp_path: Path, monkeypatch):
    db, project_id = seed_project(tmp_path)
    repo = Repository(db)
    service = WorkflowService(db)
    ts = now_iso()
    product_version = "PRODUCT VERSION TWO."
    price_version = "PRICE VERSION TWO."
    monkeypatch.setattr(workflow_service_module.random, "choice", lambda items: items[-1])
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO script_blocks
                (project_id, script_type, owner_uid, price_range_label, block_label, body, text_hash, source, source_anchor, created_at, updated_at)
            VALUES (?, 'product', 'YXEJ002', '', '版本2', ?, ?, 'test', '', ?, ?)
            """,
            (project_id, product_version, text_hash(product_version), ts, ts),
        )
        conn.execute(
            """
            INSERT INTO script_blocks
                (project_id, script_type, owner_uid, price_range_label, block_label, body, text_hash, source, source_anchor, created_at, updated_at)
            VALUES (?, 'price_transition', '', '0-100', '版本2', ?, ?, 'test', '', ?, ?)
            """,
            (project_id, price_version, text_hash(price_version), ts, ts),
        )
    seed_ready_voice_assets(db, project_id, tmp_path)

    result = service.run_command(service.build_assembly_command(project_id, mode="standard", account_label="小燃"))

    assert result.returncode == 0
    spoken_path = Path(repo.project(project_id)["spoken_md_path"])
    text = spoken_path.read_text(encoding="utf-8")
    assert price_version in text
    assert product_version in text
    assert "这个价格段值得看。" not in text
    assert "这是商品文案。" not in text
    manifest_path = INTERNAL_WORKSPACE_ROOT / f"project-{project_id}" / "manifests" / f"{spoken_path.stem}.manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert [entry["source_label"] for entry in payload["entries"][:3]] == ["引言1", "价格过渡 0-100", "版本2"]


def test_assembly_matches_imported_voice_by_uid_account_and_hash_without_script_block_id(tmp_path: Path):
    db, project_id = seed_project(tmp_path)
    repo = Repository(db)
    service = WorkflowService(db)
    project = repo.project(project_id)
    voice_path = Path(project["voice_root"]) / project["name"] / "小燃" / "59-YXEJ002-竹林鸟夜莺Z1.wav"
    voice_path.parent.mkdir(parents=True)
    voice_path.write_bytes(b"voice")
    product_block = next(block for block in repo.script_blocks(project_id) if block["script_type"] == "product")
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO asset_bindings
                (project_id, uid, asset_type, account_label, account_id, block_label, text_hash, path, status, source_kind, created_at, updated_at)
            VALUES (?, 'YXEJ002', 'voice', '小燃', 'xiaoran', ?, ?, ?, 'ready', 'legacy_import', 'now', 'now')
            """,
            (project_id, product_block["block_label"], product_block["text_hash"], str(voice_path)),
        )
    seed_ready_voice_assets(db, project_id, tmp_path, include_products=False)

    result = service.run_command(service.build_assembly_command(project_id, account_label="小燃"))

    assert result.returncode == 0
    spoken_path = Path(project["spoken_md_path"])
    manifest_path = INTERNAL_WORKSPACE_ROOT / f"project-{project_id}" / "manifests" / f"{spoken_path.stem}.manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    product_entry = next(entry for entry in payload["entries"] if entry["type"] == "product")
    assert product_entry["audio_path"] == str(voice_path)


def test_jianying_intro_video_filters_intro_manifest_entries(tmp_path: Path):
    db, project_id = seed_project(tmp_path)
    repo = Repository(db)
    service = WorkflowService(db)
    project = repo.project(project_id)
    seed_ready_voice_assets(db, project_id, tmp_path)

    result = service.run_command(
        service.build_assembly_command(project_id, mode="standard", account_label="小燃", intro_index=1)
    )
    assert result.returncode == 0

    spoken_path = Path(project["spoken_md_path"])
    manifest_path = INTERNAL_WORKSPACE_ROOT / f"project-{project_id}" / "manifests" / f"{spoken_path.stem}.manifest.json"
    intro_video = tmp_path / "intro.mp4"
    intro_video.write_bytes(b"video")

    filtered = service._jianying_manifest_for_intro_video(project_id, manifest_path, intro_video=intro_video)
    payload = json.loads(filtered.read_text(encoding="utf-8"))

    assert payload["intro_video_path"] == str(intro_video)
    assert all(entry.get("section") != "intro" for entry in payload["entries"])
    assert any(entry.get("type") == "product" for entry in payload["entries"])


def test_jianying_manifest_refreshes_display_slots_before_generation(tmp_path: Path):
    db, project_id = seed_project(tmp_path)
    service = WorkflowService(db)
    manifest_path = tmp_path / "stale.manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "version": 2,
                "display_template": "小燃-模板2",
                "entries": [
                    {
                        "type": "product",
                        "section": "product",
                        "display_video_path": str(tmp_path / "rollb.mov"),
                        "display_video_slot": {
                            "x": 50,
                            "y": 322,
                            "width": 1004,
                            "height": 588,
                        },
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    refreshed = service._jianying_manifest_for_intro_video(project_id, manifest_path, intro_video=None)
    payload = json.loads(refreshed.read_text(encoding="utf-8"))

    assert refreshed != manifest_path
    assert payload["entries"][0]["display_video_slot"] == {
        "x": 47,
        "y": 317,
        "width": 1003,
        "height": 588,
        "display_scale": 0.55,
    }


def test_jianying_generation_skips_subtitles_by_default(tmp_path: Path, monkeypatch):
    db, project_id = seed_project(tmp_path)
    service = WorkflowService(db)
    project = Repository(db).project(project_id)
    seed_ready_voice_assets(db, project_id, tmp_path)
    result = service.run_command(service.build_assembly_command(project_id, account_label="小燃"))
    assert result.returncode == 0
    spoken_path = Path(project["spoken_md_path"])
    manifest_path = INTERNAL_WORKSPACE_ROOT / f"project-{project_id}" / "manifests" / f"{spoken_path.stem}.manifest.json"
    captured: dict[str, list[str]] = {}

    def fake_run(cmd: list[str]):
        captured["cmd"] = cmd
        return workflow_service_module.WorkflowRunResult(cmd, returncode=0, stdout="ok\n")

    monkeypatch.setattr(workflow_service_module, "run_subprocess_text", fake_run)

    draft = service.generate_jianying_draft(
        project_id,
        manifest_path=manifest_path,
        draft_name="test-draft",
        draft_root=tmp_path / "drafts",
    )

    assert draft.returncode == 0
    assert "--skip-subtitles" in captured["cmd"]
    assert captured["cmd"][1] == str(JIANYING_ENGINE_DIR / "generate_jianying_draft.py")


def test_jianying_generation_can_enable_subtitles_with_no_vad(tmp_path: Path, monkeypatch):
    db, project_id = seed_project(tmp_path)
    service = WorkflowService(db)
    project = Repository(db).project(project_id)
    seed_ready_voice_assets(db, project_id, tmp_path)
    result = service.run_command(service.build_assembly_command(project_id, account_label="小燃"))
    assert result.returncode == 0
    spoken_path = Path(project["spoken_md_path"])
    manifest_path = INTERNAL_WORKSPACE_ROOT / f"project-{project_id}" / "manifests" / f"{spoken_path.stem}.manifest.json"
    captured: dict[str, list[str]] = {}

    def fake_run(cmd: list[str]):
        captured["cmd"] = cmd
        return workflow_service_module.WorkflowRunResult(cmd, returncode=0, stdout="ok\n")

    monkeypatch.setattr(workflow_service_module, "run_subprocess_text", fake_run)

    draft = service.generate_jianying_draft(
        project_id,
        manifest_path=manifest_path,
        draft_name="test-draft",
        draft_root=tmp_path / "drafts",
        include_subtitles=True,
        subtitle_no_vad=True,
    )

    assert draft.returncode == 0
    assert "--skip-subtitles" not in captured["cmd"]
    assert "--subtitle-no-vad" in captured["cmd"]


def test_jianying_generation_summarizes_json_stdout_for_users(tmp_path: Path, monkeypatch):
    db, project_id = seed_project(tmp_path)
    service = WorkflowService(db)
    project = Repository(db).project(project_id)
    seed_ready_voice_assets(db, project_id, tmp_path)
    result = service.run_command(service.build_assembly_command(project_id, account_label="小燃"))
    assert result.returncode == 0
    spoken_path = Path(project["spoken_md_path"])
    manifest_path = INTERNAL_WORKSPACE_ROOT / f"project-{project_id}" / "manifests" / f"{spoken_path.stem}.manifest.json"

    def fake_run(cmd: list[str]):
        return workflow_service_module.WorkflowRunResult(
            cmd,
            returncode=0,
            stdout=json.dumps(
                {
                    "status": "success",
                    "draft_name": "完整-耳机-小燃",
                    "draft_dir": str(tmp_path / "drafts" / "完整-耳机-小燃"),
                    "total_items": 4,
                    "product_items": 3,
                    "total_duration_sec": 125.2,
                    "total_voice_gap_sec": 0.0,
                    "background_image": str(tmp_path / "bg.png"),
                    "has_intro_video": True,
                    "intro_duration_sec": 47.68,
                    "display_video_segments": 2,
                    "price_transition_title_segments": 2,
                    "image_fallback": {"resolved_count": 1, "missing_uids": ["A001"]},
                    "missing_subtitle_texts": [],
                },
                ensure_ascii=False,
                indent=2,
            ),
        )

    monkeypatch.setattr(workflow_service_module, "run_subprocess_text", fake_run)

    draft = service.generate_jianying_draft(
        project_id,
        manifest_path=manifest_path,
        draft_name="完整-耳机-小燃",
        draft_root=tmp_path / "drafts",
    )

    assert draft.returncode == 0
    assert "total_voice_gap_sec" not in draft.stdout
    assert "background_image" not in draft.stdout
    assert "本次共拼接 4 段素材，其中商品推荐 3 段。" in draft.stdout
    assert "草稿总时长约 2 分 5 秒。" in draft.stdout
    assert "已使用引言成片视频，时长约 48 秒。" in draft.stdout
    assert "已插入 2 段商品展示视频。" in draft.stdout
    assert "已插入 2 段价格过渡标题。" in draft.stdout
    assert "仍有 1 个商品没有找到可用图片：A001" in draft.stdout


def test_jianying_generation_summarizes_failed_json_stdout(tmp_path: Path, monkeypatch):
    db, project_id = seed_project(tmp_path)
    service = WorkflowService(db)
    project = Repository(db).project(project_id)
    seed_ready_voice_assets(db, project_id, tmp_path)
    result = service.run_command(service.build_assembly_command(project_id, account_label="小燃"))
    assert result.returncode == 0
    spoken_path = Path(project["spoken_md_path"])
    manifest_path = INTERNAL_WORKSPACE_ROOT / f"project-{project_id}" / "manifests" / f"{spoken_path.stem}.manifest.json"

    def fake_run(cmd: list[str]):
        return workflow_service_module.WorkflowRunResult(
            cmd,
            returncode=1,
            stdout=json.dumps({"status": "failed", "error": "背景图不存在"}, ensure_ascii=False, indent=2),
        )

    monkeypatch.setattr(workflow_service_module, "run_subprocess_text", fake_run)

    draft = service.generate_jianying_draft(
        project_id,
        manifest_path=manifest_path,
        draft_name="bad-draft",
        draft_root=tmp_path / "drafts",
    )

    assert draft.returncode == 1
    assert draft.stdout == "生成失败：背景图不存在\n"


def test_split_subtitle_text_drops_sentence_punctuation_and_keeps_dunhao():
    chunks = split_subtitle_text("人声也不容易被糊住。颜值简约高级，可以连接App联动。降噪、音质、LDAC高清编码，")

    assert chunks == [
        "人声也不容易被糊住",
        "颜值简约高级",
        "可以连接App联动",
        "降噪、音质、LDAC高清编码",
    ]
    assert all(not re.search(r"[，,。!！?？；;：:]", chunk) for chunk in chunks)


def test_split_subtitle_text_keeps_decimal_dot():
    chunks = split_subtitle_text("蓝牙6.0也非常好用。降噪也稳。")

    assert chunks == ["蓝牙6.0也非常好用", "降噪也稳"]


def test_split_subtitle_text_semantic_keeps_units_and_de():
    text = "这款咖啡机用的是20bar的萃取压力还有93度的恒温控制每天早上都能稳定出一杯好咖啡"
    chunks = split_subtitle_text(text)

    assert all(len(chunk) <= 24 for chunk in chunks)
    assert "".join(chunks) == text  # 不改字、不删字、不加字
    assert any("20bar" in chunk for chunk in chunks)  # 英文型号不拆
    assert any("93度" in chunk for chunk in chunks)  # 数字+单位不拆
    assert all(not chunk.startswith(("的", "地", "得")) for chunk in chunks)  # 的字不落行首


def test_split_subtitle_text_semantic_breaks_before_conjunction():
    chunks = split_subtitle_text("这台机器加热速度非常快而且操作也很简单适合家里每个人用")

    assert chunks == ["这台机器加热速度非常快", "而且操作也很简单适合家里每个人用"]


def test_export_subtitle_srt_from_manifest_text_and_audio(tmp_path: Path):
    db, project_id = seed_project(tmp_path)
    service = WorkflowService(db)
    intro_audio = tmp_path / "intro.wav"
    product_audio = tmp_path / "product.wav"
    write_test_wav(intro_audio, [(1.0, 0.5)])
    write_test_wav(product_audio, [(2.0, 0.5)])
    manifest = tmp_path / "口播稿.manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "type": "transition",
                        "order_index": 1,
                        "section": "intro",
                        "product_uid": "INTRO",
                        "product_name": "引言",
                        "text": "今天聊有线耳机。",
                        "audio_path": str(intro_audio),
                    },
                    {
                        "type": "product",
                        "order_index": 2,
                        "section": "product",
                        "product_uid": "YXEJ002",
                        "product_name": "竹林鸟夜莺Z1",
                        "text": "这是第一句。这里是第二句。",
                        "audio_path": str(product_audio),
                    },
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    output = tmp_path / "out.srt"

    result = service.export_subtitle_srt(project_id, manifest_path=manifest, output_path=output)

    assert result.returncode == 0
    text = output.read_text(encoding="utf-8-sig")
    assert "00:00:00,000 -->" in text
    assert "今天聊有线耳机\n" in text
    assert "这是第一句\n" in text
    assert "这里是第二句\n" in text
    assert "今天聊有线耳机。" not in text
    assert "这是第一句。这里是第二句。" not in text
    assert "00:00:03,000" in text


def test_export_subtitle_srt_offsets_when_intro_video_is_selected(tmp_path: Path):
    db, project_id = seed_project(tmp_path)
    service = WorkflowService(db)
    intro_audio = tmp_path / "intro.wav"
    product_audio = tmp_path / "product.wav"
    intro_video = tmp_path / "intro-video.wav"
    write_test_wav(intro_audio, [(1.0, 0.5)])
    write_test_wav(product_audio, [(2.0, 0.5)])
    write_test_wav(intro_video, [(1.5, 0.5)])
    manifest = tmp_path / "口播稿.manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "type": "transition",
                        "order_index": 1,
                        "section": "intro",
                        "product_uid": "INTRO",
                        "product_name": "引言",
                        "text": "这段引言来自 manifest。",
                        "audio_path": str(intro_audio),
                    },
                    {
                        "type": "product",
                        "order_index": 2,
                        "section": "product",
                        "product_uid": "YXEJ002",
                        "product_name": "竹林鸟夜莺Z1",
                        "text": "商品字幕从引言视频后开始。",
                        "audio_path": str(product_audio),
                    },
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    output = tmp_path / "out.srt"

    result = service.export_subtitle_srt(
        project_id,
        manifest_path=manifest,
        output_path=output,
        intro_video_path=intro_video,
    )

    assert result.returncode == 0
    text = output.read_text(encoding="utf-8-sig")
    assert "这段引言来自 manifest。" not in text
    assert "00:00:01,500 -->" in text
    assert "商品字幕从引言视频后开始" in text


def test_export_subtitle_srt_includes_intro_video_text_when_provided(tmp_path: Path):
    db, project_id = seed_project(tmp_path)
    service = WorkflowService(db)
    intro_audio = tmp_path / "intro.wav"
    product_audio = tmp_path / "product.wav"
    intro_video = tmp_path / "intro-video.wav"
    write_test_wav(intro_audio, [(1.0, 0.5)])
    write_test_wav(product_audio, [(2.0, 0.5)])
    write_test_wav(intro_video, [(1.5, 0.5)])
    manifest = tmp_path / "口播稿.manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "type": "transition",
                        "order_index": 1,
                        "section": "intro",
                        "product_uid": "INTRO",
                        "product_name": "引言",
                        "text": "这段引言来自 manifest。",
                        "audio_path": str(intro_audio),
                    },
                    {
                        "type": "product",
                        "order_index": 2,
                        "section": "product",
                        "product_uid": "YXEJ002",
                        "product_name": "竹林鸟夜莺Z1",
                        "text": "商品字幕从引言视频后开始。",
                        "audio_path": str(product_audio),
                    },
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    output = tmp_path / "out.srt"

    result = service.export_subtitle_srt(
        project_id,
        manifest_path=manifest,
        output_path=output,
        intro_video_path=intro_video,
        intro_video_text="这是片头文案。欢迎回来。",
    )

    assert result.returncode == 0
    text = output.read_text(encoding="utf-8-sig")
    assert "这段引言来自 manifest。" not in text
    assert "00:00:00,000 -->" in text
    assert "这是片头文案\n" in text
    assert "欢迎回来\n" in text
    assert "00:00:01,500 -->" in text
    assert "商品字幕从引言视频后开始" in text


def test_export_subtitle_srt_uses_asr_alignment_when_enabled(tmp_path: Path, monkeypatch):
    db, project_id = seed_project(tmp_path)
    service = WorkflowService(db)
    product_audio = tmp_path / "product.wav"
    write_test_wav(product_audio, [(2.0, 0.5)])
    manifest = tmp_path / "口播稿.manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "type": "product",
                        "order_index": 1,
                        "section": "product",
                        "product_uid": "YXEJ002",
                        "product_name": "竹林鸟夜莺Z1",
                        "text": "第一句。第二句。",
                        "audio_path": str(product_audio),
                    },
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    output = tmp_path / "out.srt"

    def fake_align_jobs(jobs, *, model_name, language, beam_size, workers, provider_name=None):
        assert len(jobs) == 1
        assert Path(jobs[0]["audio_path"]) == product_audio
        assert jobs[0]["text"] == "第一句。第二句。"
        assert jobs[0]["offset_sec"] == 0.0
        assert model_name == workflow_service_module.DEFAULT_SUBTITLE_ASR_MODEL
        assert language == workflow_service_module.DEFAULT_SUBTITLE_ASR_LANGUAGE
        assert beam_size == workflow_service_module.DEFAULT_SUBTITLE_ASR_BEAM_SIZE
        assert workers == workflow_service_module.DEFAULT_SUBTITLE_ASR_WORKERS
        assert provider_name == "fake-provider"
        return [(0.2, 0.9, "第一句"), (0.9, 1.8, "第二句")]

    monkeypatch.setattr(workflow_service_module, "align_subtitle_jobs_with_asr", fake_align_jobs)

    result = service.export_subtitle_srt(
        project_id,
        manifest_path=manifest,
        output_path=output,
        align_with_asr=True,
        subtitle_asr_provider="fake-provider",
    )

    assert result.returncode == 0
    assert "精确原文强制对齐" in result.stdout
    text = output.read_text(encoding="utf-8-sig")
    assert "00:00:00,200 --> 00:00:00,900" in text
    assert "00:00:00,900 --> 00:00:01,800" in text
    assert "第一句\n" in text
    assert "第二句\n" in text


def test_default_subtitle_srt_path_prefixes_spoken_md_name(tmp_path: Path):
    db, project_id = seed_project(tmp_path)
    service = WorkflowService(db)

    spoken_output = service.default_subtitle_srt_path(project_id, tmp_path / "5月-小燃.md")
    manifest_output = service.default_subtitle_srt_path(project_id, tmp_path / "5月-小燃.manifest.json")

    assert spoken_output.name == "字幕-5月-小燃.srt"
    assert manifest_output.name == "字幕-5月-小燃.srt"


def test_top_mode_writes_top_products_before_price_transitions_and_adds_closing(tmp_path: Path):
    db, project_id = seed_project(tmp_path)
    repo = Repository(db)
    service = WorkflowService(db)
    closing_audio = tmp_path / "closing.wav"
    closing_audio.write_bytes(b"closing")
    repo.upsert_products_from_master(
        project_id,
        [
            {"uid": "YXEJ002", "title": "Top One", "price_label": "59"},
            {"uid": "YXEJ006", "title": "Top Two", "price_label": "199"},
            {"uid": "YXEJ007", "title": "Top Three", "price_label": "299"},
            {"uid": "YXEJ008", "title": "Normal Cheap", "price_label": "69"},
            {"uid": "YXEJ009", "title": "Normal Mid", "price_label": "199"},
        ],
    )
    ts = now_iso()
    rows = [
        ("YXEJ006", "TOP TWO BODY."),
        ("YXEJ007", "TOP THREE BODY."),
        ("YXEJ008", "NORMAL CHEAP BODY."),
        ("YXEJ009", "NORMAL MID BODY."),
    ]
    with db.connect() as conn:
        conn.execute("UPDATE accounts SET closing_audio_path=? WHERE label='小燃'", (str(closing_audio),))
        conn.execute("UPDATE script_blocks SET body='TOP ONE BODY.', text_hash=? WHERE project_id=? AND owner_uid='YXEJ002'", (text_hash("TOP ONE BODY."), project_id))
        for uid, body in rows:
            conn.execute(
                """
                INSERT INTO script_blocks
                    (project_id, script_type, owner_uid, price_range_label, block_label, body, text_hash, source, source_anchor, created_at, updated_at)
                VALUES (?, 'product', ?, '', '正文', ?, ?, 'test', '', ?, ?)
                """,
                (project_id, uid, body, text_hash(body), ts, ts),
            )
        conn.execute(
            """
            INSERT INTO script_blocks
                (project_id, script_type, owner_uid, price_range_label, block_label, body, text_hash, source, source_anchor, created_at, updated_at)
            VALUES (?, 'price_transition', '', '100-300', '正文', '100-300 TRANSITION.', ?, 'test', '', ?, ?)
            """,
            (project_id, text_hash("100-300 TRANSITION."), ts, ts),
        )
    seed_ready_voice_assets(db, project_id, tmp_path)

    result = service.run_command(
        service.build_assembly_command(
            project_id,
            mode="top",
            top_uids=["YXEJ002", "YXEJ006", "YXEJ007"],
            account_label="小燃",
        )
    )

    assert result.returncode == 0
    project = repo.project(project_id)
    spoken_path = Path(project["spoken_md_path"])
    paragraphs = [paragraph.strip() for paragraph in spoken_path.read_text(encoding="utf-8").split("\n\n") if paragraph.strip()]
    assert paragraphs == [
        "今天聊有线耳机。",
        "TOP ONE BODY.",
        "TOP TWO BODY.",
        "TOP THREE BODY.",
        "这个价格段值得看。",
        "NORMAL CHEAP BODY.",
        "100-300 TRANSITION.",
        "NORMAL MID BODY.",
        DEFAULT_CLOSING_TEXT,
    ]

    manifest_path = INTERNAL_WORKSPACE_ROOT / f"project-{project_id}" / "manifests" / f"{spoken_path.stem}.manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    sections = [entry["section"] for entry in payload["entries"]]
    assert sections == ["intro", "top", "top", "top", "price_transition", "product", "price_transition", "product", "closing"]
    closing_entry = payload["entries"][-1]
    assert closing_entry["type"] == "closing"
    assert closing_entry["text"] == DEFAULT_CLOSING_TEXT
    assert closing_entry["audio_path"] == str(closing_audio)


def test_compress_internal_silence_shortens_only_internal_long_pauses(tmp_path: Path):
    audio_path = tmp_path / "silence.wav"
    write_test_wav(
        audio_path,
        [
            (0.2, 0.6),
            (0.5, 0.0),
            (0.2, 0.6),
        ],
    )

    result = compress_internal_silence(audio_path)

    assert result["enabled"] is True
    assert result["changed"] is True
    assert result["compressed_count"] == 1
    assert result["original_ms"] == 900
    assert result["fixed_ms"] == 620
    assert result["removed_ms"] == 280


def test_compress_internal_silence_keeps_pauses_up_to_300ms(tmp_path: Path):
    audio_path = tmp_path / "natural-pause.wav"
    write_test_wav(
        audio_path,
        [
            (0.2, 0.6),
            (0.3, 0.0),
            (0.2, 0.6),
        ],
        frame_rate=10000,
    )

    result = compress_internal_silence(audio_path)

    assert result["changed"] is False
    assert result["compressed_count"] == 0
    with wave.open(str(audio_path), "rb") as reader:
        assert reader.getnframes() == 7000


def test_compress_internal_silence_trims_very_long_internal_pauses_to_350ms(tmp_path: Path):
    audio_path = tmp_path / "very-long-silence.wav"
    write_test_wav(
        audio_path,
        [
            (0.2, 0.6),
            (1.0, 0.0),
            (0.2, 0.6),
        ],
        frame_rate=10000,
    )

    result = compress_internal_silence(audio_path)

    assert result["changed"] is True
    assert result["compressed_count"] == 1
    with wave.open(str(audio_path), "rb") as reader:
        assert reader.getnframes() == 7500


def test_normalize_generated_voice_silence_applies_coarse_generation_filter(tmp_path: Path):
    audio_path = tmp_path / "generated.wav"
    write_test_wav(
        audio_path,
        [
            (0.5, 0.0),
            (0.2, 0.6),
            (0.5, 0.0),
            (0.2, 0.6),
            (1.0, 0.0),
            (0.2, 0.6),
            (0.8, 0.0),
        ],
        frame_rate=10000,
    )

    result = normalize_generated_voice_silence(audio_path)

    assert result["changed"] is True
    assert result["changed_count"] == 4
    assert [change["type"] for change in result["changes"]] == ["leading", "internal", "internal_long", "trailing"]
    assert result["fixed_ms"] == 1490


def test_normalize_audio_loudness_uses_two_pass_loudnorm(tmp_path: Path, monkeypatch):
    audio_path = tmp_path / "voice.mp3"
    audio_path.write_bytes(b"source audio")
    commands: list[list[str]] = []

    class FakeCompleted:
        def __init__(self, stderr: str = "") -> None:
            self.returncode = 0
            self.stderr = stderr

    def fake_run(command: list[str], **_: object) -> FakeCompleted:
        commands.append(command)
        if "-f" in command and "null" in command:
            return FakeCompleted(
                json.dumps(
                    {
                        "input_i": "-21.10",
                        "input_tp": "-4.20",
                        "input_lra": "6.10",
                        "input_thresh": "-31.20",
                        "target_offset": "0.40",
                    }
                )
            )
        Path(command[-1]).write_bytes(b"normalized audio")
        return FakeCompleted()

    monkeypatch.setattr(tts_helpers_module.subprocess, "run", fake_run)

    result = normalize_audio_loudness(audio_path)

    assert result["changed"] is True
    assert result["two_pass"] is True
    assert result["target_i_lufs"] == -11.0
    assert audio_path.read_bytes() == b"normalized audio"
    assert len(commands) == 2
    assert any("loudnorm=I=-11.0:TP=-1.0:LRA=11.0" in item for item in commands[0])
    assert any("measured_I=-21.10" in item for item in commands[1])


def test_prepend_silence_adds_100ms_to_wav_start(tmp_path: Path):
    audio_path = tmp_path / "voice.wav"
    write_test_wav(audio_path, [(0.2, 0.6)], frame_rate=1000)

    result = prepend_silence(audio_path)

    assert result["changed"] is True
    assert result["silence_ms"] == 100
    with wave.open(str(audio_path), "rb") as reader:
        assert reader.getnframes() == 300
        first_frames = reader.readframes(100)
        next_frames = reader.readframes(2)
    assert first_frames == b"\x00" * 100 * 2
    assert next_frames != b"\x00" * 2 * 2


def test_generate_one_voice_runs_new_project_audio_postprocess(tmp_path: Path, monkeypatch):
    db, project_id = seed_project(tmp_path)
    service = WorkflowService(db)
    repo = Repository(db)
    block = next(block for block in repo.script_blocks(project_id) if block["script_type"] == "product")
    job = VoiceJob(
        block=block,
        uid="YXEJ002",
        product_name="竹林鸟夜莺Z1",
        price_label="59元",
        kind="product",
    )

    generated_path = tmp_path / "generated.wav"
    write_test_wav(generated_path, [(0.2, 0.6), (0.5, 0.0), (0.2, 0.6)])

    class FakeHttp:
        def post(self, url: str, json_payload: dict[str, object]) -> dict[str, str]:
            return {"audio_path": str(generated_path)}

    called: dict[str, Path] = {}

    def fake_normalize(path: Path, **_: object) -> dict[str, object]:
        called["path"] = path
        return {"enabled": True, "changed": True}

    monkeypatch.setattr(workflow_service_module, "normalize_generated_voice_silence", fake_normalize)
    monkeypatch.setattr(workflow_service_module, "normalize_audio_loudness", lambda _path: {"enabled": True, "changed": True})

    output_path = service._generate_one_voice(
        FakeHttp(),
        job=job,
        account={"label": "小燃"},
        voice_id="voice-1",
        output_dir=tmp_path / "voice-out",
    )

    assert output_path.exists()
    assert called["path"] == output_path
    with wave.open(str(output_path), "rb") as reader:
        assert round(reader.getnframes() * 1000 / reader.getframerate()) == 900


def test_generate_voice_with_minimax_writes_mp3_and_binds_asset(tmp_path: Path, monkeypatch):
    db, project_id = seed_project(tmp_path)
    service = WorkflowService(db)
    called = {"prepare": False}

    def fake_prepare(self, voice_id: str, **kwargs):
        called["prepare"] = True
        assert voice_id == "minimax-voice-1"
        return "minimax-voice-1"

    def fake_synthesize(self, text: str, *, voice_id: str, final_path: Path, **kwargs):
        assert voice_id == "minimax-voice-1"
        assert text.strip()
        final_path.parent.mkdir(parents=True, exist_ok=True)
        final_path.write_bytes(b"mp3")
        return final_path

    monkeypatch.setattr(WorkflowService, "_prepare_minimax_voice", fake_prepare)
    monkeypatch.setattr(WorkflowService, "_synthesize_minimax_to_path", fake_synthesize)
    monkeypatch.setattr(WorkflowService, "_ensure_tts_api_ready", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("IndexTTS should not start")))

    result = service.generate_voice(
        project_id,
        account_label="小燃",
        voice_provider=VOICE_PROVIDER_MINIMAX,
        output_dir=tmp_path / "voice-out",
    )

    assert result.returncode == 0
    assert called["prepare"]
    output_files = list((tmp_path / "voice-out").glob("*.mp3"))
    assert output_files
    asset = db.fetchone("SELECT * FROM asset_bindings WHERE project_id=? AND asset_type='voice'", (project_id,))
    assert asset is not None
    assert Path(asset["path"]).suffix == ".mp3"


def test_generate_voice_cancel_stops_after_current_job(tmp_path: Path, monkeypatch):
    import threading

    db, project_id = seed_project(tmp_path)
    service = WorkflowService(db)
    generated_count = 0

    def fake_prepare(self, voice_id: str, **kwargs):
        return "minimax-voice-1"

    def fake_synthesize(self, text: str, *, voice_id: str, final_path: Path, **kwargs):
        nonlocal generated_count
        final_path.parent.mkdir(parents=True, exist_ok=True)
        final_path.write_bytes(b"mp3")
        generated_count += 1
        return final_path

    monkeypatch.setattr(WorkflowService, "_prepare_minimax_voice", fake_prepare)
    monkeypatch.setattr(WorkflowService, "_synthesize_minimax_to_path", fake_synthesize)

    cancel = threading.Event()
    cancel.set()

    result = service.generate_voice(
        project_id,
        account_label="小燃",
        voice_provider=VOICE_PROVIDER_MINIMAX,
        output_dir=tmp_path / "voice-out",
        cancel_event=cancel,
    )

    assert generated_count == 0
    assert "取消" in result.stdout


def test_generate_voice_binding_failure_keeps_old_audio_and_removes_new_file(tmp_path: Path, monkeypatch):
    db, project_id = seed_project(tmp_path)
    service = WorkflowService(db)
    repo = Repository(db)
    account = repo.accounts()[0]
    job = service._voice_jobs(project_id)[0]
    output_dir = tmp_path / "voice-out"
    old_path = output_dir / Path(service._voice_filename(job)).with_suffix(".mp3").name
    old_path.parent.mkdir(parents=True)
    old_path.write_bytes(b"old-audio")
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO asset_bindings
                (project_id, uid, script_block_id, asset_type, account_label, account_id,
                 block_label, script_id, text_hash, path, status, source_kind,
                 file_size, file_mtime, confirmed, created_at, updated_at)
            VALUES (?, ?, ?, 'voice', ?, ?, ?, ?, 'old-hash', ?, 'ready', 'generated', ?, 'old', 1, ?, ?)
            """,
            (
                project_id,
                job.uid,
                job.block["id"],
                account["label"],
                account["account_id"],
                job.block["block_label"],
                job.block["script_id"],
                str(old_path),
                old_path.stat().st_size,
                now_iso(),
                now_iso(),
            ),
        )

    monkeypatch.setattr(WorkflowService, "_prepare_minimax_voice", lambda self, voice_id, **kwargs: voice_id)

    def fake_synthesize(self, text: str, *, voice_id: str, final_path: Path, **kwargs):
        assert final_path != old_path
        final_path.write_bytes(b"new-audio")
        return final_path

    monkeypatch.setattr(WorkflowService, "_synthesize_minimax_to_path", fake_synthesize)
    monkeypatch.setattr(
        WorkflowService,
        "_upsert_voice_asset",
        lambda *args, **kwargs: (_ for _ in ()).throw(sqlite3.IntegrityError("db failed")),
    )

    result = service.generate_voice(
        project_id,
        account_label=account["label"],
        voice_provider=VOICE_PROVIDER_MINIMAX,
        output_dir=output_dir,
    )

    assert result.returncode == 1
    assert old_path.read_bytes() == b"old-audio"
    assert list(output_dir.glob("*.mp3")) == [old_path]


def test_markdown_file_to_voice_text_keeps_document_as_single_text(tmp_path: Path):
    md = tmp_path / "稿件.md"
    md.write_text(
        """---
title: 测试
---
<!-- script_id: internal -->
# 标题

第一段内容。

## 小节
- 第二段内容。
""",
        encoding="utf-8",
    )

    text = markdown_file_to_voice_text(md)

    assert text == "标题\n第一段内容。\n小节\n第二段内容。"
    assert "\n\n" not in text


def test_markdown_file_to_voice_text_rejects_non_md(tmp_path: Path):
    path = tmp_path / "稿件.txt"
    path.write_text("文字", encoding="utf-8")

    try:
        markdown_file_to_voice_text(path)
    except ValueError as exc:
        assert "只支持选择 MD 文档" in str(exc)
    else:
        raise AssertionError("non-md file should be rejected")


def test_synthesize_standalone_voice_with_configured_account_does_not_bind_assets(tmp_path: Path, monkeypatch):
    db, _project_id = seed_project(tmp_path)
    service = WorkflowService(db)
    generated_path = tmp_path / "generated.wav"
    write_test_wav(generated_path, [(0.2, 0.6)])
    captured: dict[str, object] = {}

    class FakeHttp:
        def __init__(self, timeout: float = 60.0) -> None:
            self.timeout = timeout

        def post(self, url: str, json_payload: dict[str, object] | None = None) -> dict[str, str]:
            captured["url"] = url
            captured["payload"] = json_payload or {}
            return {"audio_path": str(generated_path)}

    monkeypatch.setattr(workflow_service_module, "JsonHttpClient", FakeHttp)
    monkeypatch.setattr(WorkflowService, "_ensure_tts_api_ready", lambda self, http, **kwargs: None)
    monkeypatch.setattr(WorkflowService, "_ensure_registered_voice", lambda self, http, **kwargs: None)

    result = service.synthesize_standalone_voice(
        "这是一段单独配音。",
        account_label="小燃",
        output_dir=tmp_path / "standalone",
        source_label="粘贴文本",
    )

    assert result.returncode == 0
    assert str(captured["url"]).endswith("/v1/clone/voice")
    assert captured["payload"]["voice_id"] == "voice-1"
    output_files = list((tmp_path / "standalone").glob("*.wav"))
    assert len(output_files) == 1
    assert output_files[0].name.startswith("单独配音-小燃音色-粘贴文本-")
    assert db.fetchone("SELECT COUNT(*) AS c FROM asset_bindings WHERE asset_type='voice'")["c"] == 0


def test_synthesize_standalone_voice_with_minimax_writes_mp3_without_local_service(tmp_path: Path, monkeypatch):
    db, _project_id = seed_project(tmp_path)
    service = WorkflowService(db)
    account = Repository(db).accounts()[0]
    called = {"prepare": False, "synthesize": False}

    def fake_prepare(self, voice_id: str, **kwargs):
        called["prepare"] = True
        assert voice_id == "minimax-voice-1"
        return "minimax-voice-1"

    def fake_synthesize(self, text: str, *, voice_id: str, final_path: Path, **kwargs):
        called["synthesize"] = True
        assert voice_id == "minimax-voice-1"
        assert "standalone minimax text" in text
        assert final_path.suffix == ".mp3"
        final_path.parent.mkdir(parents=True, exist_ok=True)
        final_path.write_bytes(b"mp3")
        return final_path

    monkeypatch.setattr(WorkflowService, "_prepare_minimax_voice", fake_prepare)
    monkeypatch.setattr(WorkflowService, "_synthesize_minimax_to_path", fake_synthesize)
    monkeypatch.setattr(
        WorkflowService,
        "_ensure_tts_api_ready",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("IndexTTS should not start")),
    )

    result = service.synthesize_standalone_voice(
        "standalone minimax text",
        account_label=account["label"],
        voice_provider=VOICE_PROVIDER_MINIMAX,
        output_dir=tmp_path / "standalone",
        source_label="manual",
        start_service_if_needed=True,
    )

    assert result.returncode == 0
    assert result.args == ["internal:standalone-voice", "--voice-provider", VOICE_PROVIDER_MINIMAX]
    assert called == {"prepare": True, "synthesize": True}
    output_files = list((tmp_path / "standalone").glob("*.mp3"))
    assert len(output_files) == 1
    assert output_files[0].read_bytes() == b"mp3"
    assert db.fetchone("SELECT COUNT(*) AS c FROM asset_bindings WHERE asset_type='voice'")["c"] == 0


def test_synthesize_standalone_voice_with_reference_audio_uses_clone_path(tmp_path: Path, monkeypatch):
    db, _project_id = seed_project(tmp_path)
    service = WorkflowService(db)
    reference = tmp_path / "参考.wav"
    generated_path = tmp_path / "generated.wav"
    write_test_wav(reference, [(0.2, 0.4)])
    write_test_wav(generated_path, [(0.2, 0.6)])
    captured: dict[str, object] = {}

    class FakeHttp:
        def __init__(self, timeout: float = 60.0) -> None:
            self.timeout = timeout

        def post(self, url: str, json_payload: dict[str, object] | None = None) -> dict[str, str]:
            captured["url"] = url
            captured["payload"] = json_payload or {}
            return {"audio_path": str(generated_path)}

    monkeypatch.setattr(workflow_service_module, "JsonHttpClient", FakeHttp)
    monkeypatch.setattr(WorkflowService, "_ensure_tts_api_ready", lambda self, http, **kwargs: None)

    result = service.synthesize_standalone_voice(
        markdown_to_voice_text("# 标题\n\n正文"),
        reference_audio_path=reference,
        output_dir=tmp_path / "standalone",
        source_label="稿件",
    )

    assert result.returncode == 0
    assert str(captured["url"]).endswith("/v1/clone")
    assert captured["payload"]["speaker_audio_path"] == str(reference)
    assert len(list((tmp_path / "standalone").glob("*.wav"))) == 1


def test_voice_reuse_requires_same_provider_model_voice_and_settings(tmp_path: Path):
    db, project_id = seed_project(tmp_path)
    service = WorkflowService(db)
    repo = Repository(db)
    account = repo.accounts()[0]
    block = next(block for block in repo.script_blocks(project_id) if block["script_type"] == "product")
    job = VoiceJob(
        block=block,
        uid="YXEJ002",
        product_name="Product One",
        price_label="59",
        index=1,
        kind="product",
    )
    ready_path = tmp_path / "voice.wav"
    ready_path.write_bytes(b"voice")
    original_identity = voice_synthesis_identity("indextts", account["voice_id"])
    service._upsert_voice_asset(
        project_id,
        job=job,
        account=account,
        path=ready_path,
        identity=original_identity,
    )

    existing, pending = service._split_existing_voice_jobs(
        project_id,
        [job],
        account,
        identity=original_identity,
    )
    assert existing == [job]
    assert pending == []

    for changed_identity in (
        voice_synthesis_identity("indextts", "another-local-voice"),
        voice_synthesis_identity("minimax", "another-cloud-voice"),
    ):
        existing, pending = service._split_existing_voice_jobs(
            project_id,
            [job],
            account,
            identity=changed_identity,
        )
        assert existing == []
        assert pending == [job]


def test_account_voice_profile_overrides_legacy_model_and_settings(tmp_path: Path):
    db, _project_id = seed_project(tmp_path)
    service = WorkflowService(db)
    repo = Repository(db)
    account = repo.accounts()[0]
    repo.upsert_account(account)
    with db.connect() as conn:
        conn.execute(
            """
            UPDATE account_voice_profiles
            SET voice_id='profile-voice', model='speech-custom', settings_json='{"speed":0.95}'
            WHERE account_id=? AND provider='minimax'
            """,
            (account["id"],),
        )

    identity, settings = service._voice_configuration_for_account(account, "minimax")

    assert identity.voice_id == "profile-voice"
    assert identity.model == "speech-custom"
    assert settings["speed"] == 0.95
    assert settings["sample_rate"] == 32000
    assert identity.settings_hash == voice_synthesis_identity(
        "minimax",
        "profile-voice",
        model="speech-custom",
        settings=settings,
    ).settings_hash


def test_voice_asset_database_failure_preserves_previous_file_and_binding(tmp_path: Path):
    db, project_id = seed_project(tmp_path)
    service = WorkflowService(db)
    repo = Repository(db)
    account = repo.accounts()[0]
    block = next(block for block in repo.script_blocks(project_id) if block["script_type"] == "product")
    job = VoiceJob(
        block=block,
        uid="YXEJ002",
        product_name="Product One",
        price_label="59",
        index=1,
        kind="product",
    )
    stale_path = tmp_path / "old.wav"
    replacement_path = tmp_path / "new.wav"
    stale_path.write_bytes(b"old")
    replacement_path.write_bytes(b"new")
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO asset_bindings
                (project_id, uid, script_block_id, asset_type, account_label, account_id,
                 block_label, script_id, text_hash, path, status, source_kind,
                 file_size, file_mtime, confirmed, created_at, updated_at)
            VALUES (?, ?, ?, 'voice', ?, ?, ?, ?, 'old-hash', ?, 'ready', 'generated', ?, 'old', 1, ?, ?)
            """,
            (
                project_id,
                job.uid,
                job.block["id"],
                account["label"],
                account["account_id"],
                job.block["block_label"],
                job.block["script_id"],
                str(stale_path),
                stale_path.stat().st_size,
                now_iso(),
                now_iso(),
            ),
        )
        conn.execute(
            """
            CREATE TRIGGER reject_voice_binding_insert
            BEFORE INSERT ON asset_bindings
            BEGIN
                SELECT RAISE(ABORT, 'blocked for rollback test');
            END
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="blocked for rollback test"):
        service._upsert_voice_asset(
            project_id,
            job=job,
            account=account,
            path=replacement_path,
            identity=voice_synthesis_identity("indextts", account["voice_id"]),
        )

    binding = db.fetchone("SELECT status, path FROM asset_bindings WHERE path=?", (str(stale_path),))
    assert binding["status"] == "ready"
    assert binding["path"] == str(stale_path)
    assert stale_path.read_bytes() == b"old"
