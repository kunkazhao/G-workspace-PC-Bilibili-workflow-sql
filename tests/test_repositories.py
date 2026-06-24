from pathlib import Path

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
