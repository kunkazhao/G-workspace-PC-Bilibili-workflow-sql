from pathlib import Path
import sqlite3

from bworkflow_sql.db import CURRENT_SCHEMA_VERSION, Database
from bworkflow_sql.repositories import Repository


def test_projects_are_sorted_by_name(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    repo = Repository(db)

    db.upsert_project({"name": "数码-充电宝"})
    db.upsert_project({"name": "A-键盘"})
    db.upsert_project({"name": "数码-耳机"})

    assert [project["name"] for project in repo.projects()] == [
        "A-键盘",
        "数码-充电宝",
        "数码-耳机",
    ]


def test_database_migrates_known_minimax_voice_aliases(tmp_path: Path):
    db_path = tmp_path / "legacy.db"
    db = Database(db_path)
    with db.connect() as conn:
        conn.execute("UPDATE accounts SET label='荣荣', voice_id='荣荣' WHERE 1=0")
        conn.execute(
            """
            INSERT INTO accounts (label, account_id, voice_id, minimax_voice_id, voice_name, created_at, updated_at)
            VALUES ('占位', 'placeholder', 'placeholder', '', '占位', 'now', 'now')
            """
        )
    with db.connect() as conn:
        conn.execute("ALTER TABLE accounts RENAME TO accounts_old")
        conn.execute(
            """
            CREATE TABLE accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                label TEXT NOT NULL UNIQUE,
                account_id TEXT NOT NULL DEFAULT '',
                voice_id TEXT NOT NULL DEFAULT '',
                voice_name TEXT NOT NULL DEFAULT '',
                media_identity TEXT NOT NULL DEFAULT '',
                closing_audio_path TEXT NOT NULL DEFAULT '',
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO accounts (label, account_id, voice_id, voice_name, media_identity, closing_audio_path, enabled, created_at, updated_at)
            VALUES ('荣荣', 'rongrong', '荣荣', '荣荣音色', '', '', 1, 'now', 'now')
            """
        )
        conn.execute("DROP TABLE accounts_old")
        conn.execute("DELETE FROM schema_version")
    db.close()

    migrated = Database(db_path)
    row = migrated.fetchone("SELECT label, voice_id, minimax_voice_id FROM accounts WHERE label='荣荣'")

    assert row["voice_id"] == "荣荣"
    assert row["minimax_voice_id"] == "rongrong-v2"
    migrated.close()


def test_fresh_db_gets_current_schema_version(tmp_path: Path):
    db = Database(tmp_path / "fresh.db")
    row = db.fetchone("SELECT MAX(version) AS v FROM schema_version")
    assert row["v"] == CURRENT_SCHEMA_VERSION
    db.close()


def test_migrations_are_idempotent(tmp_path: Path):
    db_path = tmp_path / "idem.db"
    db = Database(db_path)
    db.close()
    db2 = Database(db_path)
    rows = db2.fetchall("SELECT version FROM schema_version ORDER BY version")
    assert len(rows) == CURRENT_SCHEMA_VERSION
    assert rows[-1]["version"] == CURRENT_SCHEMA_VERSION
    db2.close()


def test_schema_version_table_exists(tmp_path: Path):
    db = Database(tmp_path / "ver.db")
    tables = {
        row[0]
        for row in db.fetchall("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert "schema_version" in tables
    db.close()


def test_database_binds_confirmed_master_and_bilibili_account_ids(tmp_path: Path):
    db = Database(tmp_path / "account-bindings.db")
    repo = Repository(db)

    for label in ("小燃", "小博", "小歪", "荣荣", "知了"):
        repo.upsert_account({"label": label, "account_id": label})

    # Re-run the versioned migration against rows created after initial schema setup.
    with db.connect() as conn:
        db._migrate_v9(conn)

    accounts = {row["label"]: row for row in repo.accounts()}
    assert accounts["小燃"]["master_account_id"] == "c025960c-5560-4630-8344-509a5c6d92a5"
    assert accounts["小燃"]["bilibili_mid"] == "3546911325817533"
    assert accounts["小博"]["master_account_id"] == "5fe6305b-c1ca-4ee4-bfd7-9407bd4e5302"
    assert accounts["小博"]["bilibili_mid"] == "673644511"
    assert accounts["小歪"]["master_account_id"] == "db915307-c99d-49e8-9a82-3e28df2f68c1"
    assert accounts["小歪"]["bilibili_mid"] == "1602507900"
    assert accounts["荣荣"]["master_account_id"] == "91c09fcc-b2b8-49c6-abb4-4a512f486837"
    assert accounts["荣荣"]["bilibili_mid"] == "439372"
    assert accounts["知了"]["master_account_id"] is None
    assert accounts["知了"]["bilibili_mid"] is None
    db.close()


def test_fresh_db_has_nullable_master_snapshot_provenance(tmp_path: Path):
    db = Database(tmp_path / "fresh-v4.db")
    project_id = db.upsert_project({"name": "keyboard"})
    columns = {
        row["name"]: row
        for row in db.fetchall("PRAGMA table_info(projects)")
    }
    project = db.fetchone(
        "SELECT master_snapshot_id, master_snapshot_applied_at FROM projects WHERE id=?",
        (project_id,),
    )

    assert CURRENT_SCHEMA_VERSION == 15
    assert "master_snapshot_id" in columns
    assert "master_snapshot_applied_at" in columns
    assert project["master_snapshot_id"] is None
    assert project["master_snapshot_applied_at"] is None
    db.close()


def test_v3_database_upgrades_to_current_without_backfilling_existing_project(tmp_path: Path):
    db_path = tmp_path / "legacy-v3.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            spoken_md_path TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE schema_version (version INTEGER NOT NULL, applied_at TEXT NOT NULL);
        INSERT INTO schema_version (version, applied_at) VALUES
            (1, 'v1'), (2, 'v2'), (3, 'v3');
        INSERT INTO projects (name, spoken_md_path, created_at, updated_at)
        VALUES ('legacy', '', 'before', 'before');
        """
    )
    conn.commit()
    conn.close()

    db = Database(db_path)
    project = db.fetchone(
        "SELECT master_snapshot_id, master_snapshot_applied_at FROM projects WHERE name='legacy'"
    )
    versions = [row["version"] for row in db.fetchall(
        "SELECT version FROM schema_version ORDER BY version"
    )]

    assert versions == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15]
    assert project["master_snapshot_id"] is None
    assert project["master_snapshot_applied_at"] is None
    db.close()


def test_resource_cleanup_candidate_schema_is_created(tmp_path: Path):
    db = Database(tmp_path / "resource-lifecycle.db")

    columns = {
        row["name"] for row in db.fetchall("PRAGMA table_info(resource_cleanup_candidates)")
    }
    indexes = {
        row["name"]
        for row in db.fetchall("PRAGMA index_list(resource_cleanup_candidates)")
    }

    assert {
        "project_id",
        "resource_kind",
        "path",
        "status",
        "eligible_at",
        "details_json",
    } <= columns
    assert "idx_resource_cleanup_candidates_project_status" in indexes
    db.close()


def test_resource_lifecycle_event_and_delete_batch_schema_is_created(tmp_path: Path):
    db = Database(tmp_path / "resource-delete-contract.db")

    tables = {
        row["name"]
        for row in db.fetchall("SELECT name FROM sqlite_master WHERE type='table'")
    }
    event_columns = {
        row["name"] for row in db.fetchall("PRAGMA table_info(resource_state_events)")
    }
    batch_columns = {
        row["name"] for row in db.fetchall("PRAGMA table_info(resource_cleanup_batches)")
    }
    item_columns = {
        row["name"]
        for row in db.fetchall("PRAGMA table_info(resource_cleanup_batch_items)")
    }

    assert {
        "resource_state_events",
        "resource_cleanup_batches",
        "resource_cleanup_batch_items",
    } <= tables
    assert {
        "project_id",
        "resource_kind",
        "resource_key",
        "previous_state",
        "new_state",
        "reason",
        "source",
        "created_at",
    } <= event_columns
    assert {
        "id",
        "project_id",
        "status",
        "snapshot_hash",
        "confirmation_token_hash",
        "confirmed_by",
        "result_json",
    } <= batch_columns
    assert {
        "batch_id",
        "candidate_id",
        "path",
        "expected_entry_kind",
        "expected_size_bytes",
        "expected_mtime_ns",
        "expected_fingerprint",
        "status",
        "result_message",
        "deleted_at",
    } <= item_columns
    db.close()


def test_resource_lifecycle_event_and_delete_batch_contract_round_trip(tmp_path: Path):
    db = Database(tmp_path / "resource-delete-round-trip.db")
    project_id = db.upsert_project({"name": "home-oven"})
    with db.connect() as conn:
        candidate = conn.execute(
            """
            INSERT INTO resource_cleanup_candidates
                (project_id, resource_kind, path, reason, status, eligible_at,
                 details_json, first_seen_at, last_seen_at)
            VALUES (?, 'asset_voice', 'G:/voice/old.mp3', 'script_changed',
                    'pending', '2026-07-20T00:00:00Z', '{}', 'now', 'now')
            """,
            (project_id,),
        )
        conn.execute(
            """
            INSERT INTO resource_state_events
                (project_id, resource_kind, resource_key, path, previous_state,
                 new_state, reason, source, account_label, details_json, created_at)
            VALUES (?, 'voice', 'asset_binding:7', 'G:/voice/old.mp3', 'ready',
                    'superseded', 'script_changed', 'markdown_sync', 'rongrong',
                    '{"script_id":"intro-1"}', 'now')
            """,
            (project_id,),
        )
        conn.execute(
            """
            INSERT INTO resource_cleanup_batches
                (id, project_id, status, filters_json, snapshot_hash,
                 confirmation_token_hash, candidate_count, total_size_bytes,
                 created_at)
            VALUES ('batch-1', ?, 'prepared', '{"kind":"voice"}',
                    'snapshot-hash', 'token-hash', 1, 128, 'now')
            """,
            (project_id,),
        )
        conn.execute(
            """
            INSERT INTO resource_cleanup_batch_items
                (batch_id, candidate_id, resource_kind, path, reason,
                 expected_entry_kind, expected_size_bytes, expected_mtime_ns,
                 expected_fingerprint)
            VALUES ('batch-1', ?, 'asset_voice', 'G:/voice/old.mp3',
                    'script_changed', 'file', 128, 123456, 'fingerprint')
            """,
            (candidate.lastrowid,),
        )

    event = db.fetchone(
        "SELECT new_state, reason, account_label FROM resource_state_events"
    )
    batch_item = db.fetchone(
        """
        SELECT b.status AS batch_status, i.status AS item_status, i.path
        FROM resource_cleanup_batches b
        JOIN resource_cleanup_batch_items i ON i.batch_id=b.id
        WHERE b.id='batch-1'
        """
    )

    assert dict(event) == {
        "new_state": "superseded",
        "reason": "script_changed",
        "account_label": "rongrong",
    }
    assert dict(batch_item) == {
        "batch_status": "prepared",
        "item_status": "prepared",
        "path": "G:/voice/old.mp3",
    }
    db.close()


def test_v15_backfills_existing_cleanup_candidate_without_duplicate_event(tmp_path: Path):
    db_path = tmp_path / "resource-event-backfill.db"
    db = Database(db_path)
    project_id = db.upsert_project({"name": "oven"})
    with db.connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO resource_cleanup_candidates
                (project_id, resource_kind, path, reason, status, eligible_at,
                 details_json, first_seen_at, last_seen_at)
            VALUES (?, 'asset_voice', 'G:/voice/legacy.mp3', 'legacy_candidate',
                    'pending', '2026-07-01T00:00:00Z', '{}', 'first', 'last')
            """,
            (project_id,),
        )
        candidate_id = int(cursor.lastrowid)
        conn.execute("DELETE FROM resource_state_events")
        conn.execute("DELETE FROM schema_version WHERE version=15")
    db.close()

    migrated = Database(db_path)
    events = migrated.fetchall(
        """
        SELECT resource_key, new_state, reason, source, created_at
        FROM resource_state_events
        WHERE project_id=?
        """,
        (project_id,),
    )

    assert [dict(row) for row in events] == [
        {
            "resource_key": f"cleanup_candidate:{candidate_id}",
            "new_state": "pending",
            "reason": "legacy_candidate",
            "source": "resource_lifecycle_v15_backfill",
            "created_at": "first",
        }
    ]
    with migrated.connect() as conn:
        migrated._migrate_v15(conn)
    assert migrated.fetchone(
        "SELECT COUNT(*) AS count FROM resource_state_events WHERE project_id=?",
        (project_id,),
    )["count"] == 1
    migrated.close()


def test_voice_provenance_schema_and_account_profiles_are_created(tmp_path: Path):
    db = Database(tmp_path / "voice-provenance.db")
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO accounts
                (label, account_id, voice_id, minimax_voice_id, voice_name, created_at, updated_at)
            VALUES ('小燃', 'xiaoran', 'local-xiaoran', 'cloud-xiaoran', '小燃', 'now', 'now')
            """
        )
        conn.execute("DELETE FROM account_voice_profiles")
        conn.execute("DELETE FROM schema_version WHERE version>=8")
    db.close()

    migrated = Database(tmp_path / "voice-provenance.db")
    asset_columns = {row["name"] for row in migrated.fetchall("PRAGMA table_info(asset_bindings)")}
    profiles = migrated.fetchall(
        "SELECT provider, voice_id FROM account_voice_profiles ORDER BY provider"
    )

    assert {
        "voice_provider",
        "voice_model",
        "voice_id",
        "synthesis_settings_hash",
        "generation_fingerprint",
    }.issubset(asset_columns)
    assert [(row["provider"], row["voice_id"]) for row in profiles] == [
        ("indextts", "local-xiaoran"),
        ("minimax", "cloud-xiaoran"),
    ]
    migrated.close()


def test_upsert_account_keeps_provider_profiles_in_sync(tmp_path: Path):
    db = Database(tmp_path / "account-profiles.db")
    repo = Repository(db)

    account_id = repo.upsert_account(
        {
            "label": "小燃",
            "account_id": "xiaoran",
            "voice_id": "local-voice",
            "minimax_voice_id": "cloud-voice",
            "voice_name": "小燃",
        }
    )

    assert repo.account_voice_profile(account_id, "indextts")["voice_id"] == "local-voice"
    assert repo.account_voice_profile(account_id, "minimax")["voice_id"] == "cloud-voice"

    repo.upsert_account({"label": "小燃", "voice_id": "local-voice-from-legacy"})
    assert repo.account_voice_profile(account_id, "minimax")["voice_id"] == "cloud-voice"

    repo.upsert_account(
        {
            "label": "小燃",
            "account_id": "xiaoran",
            "voice_id": "local-voice-2",
            "minimax_voice_id": "",
            "voice_name": "小燃",
        }
    )

    assert repo.account_voice_profile(account_id, "indextts")["voice_id"] == "local-voice-2"
    assert repo.account_voice_profile(account_id, "minimax") is None
    db.close()


def test_products_store_master_product_card_json(tmp_path: Path):
    db = Database(tmp_path / "product-card.db")
    repo = Repository(db)
    project_id = db.upsert_project({"name": "keyboard"})

    repo.upsert_products_from_master(
        project_id,
        [
            {
                "uid": "P001",
                "title": "Alpha Keyboard",
                "price_label": "199元",
                "cover": r"G:\covers\P001.png",
                "remark": "Good for long typing sessions.",
                "spec": {"switch": "silver", "_meta": "hidden"},
            }
        ],
    )

    product = repo.products(project_id)[0]

    assert '"cover":"G:\\\\covers\\\\P001.png"' in product["product_card_json"]
    assert '"remark":"Good for long typing sessions."' in product["product_card_json"]
    assert '"label":"switch"' in product["product_card_json"]
    assert "_meta" not in product["product_card_json"]
    db.close()
