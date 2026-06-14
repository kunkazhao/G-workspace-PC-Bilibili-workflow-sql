from __future__ import annotations

import sqlite3
import re
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable

from .settings import DB_PATH, ensure_data_dir
from .utils import now_iso, safe_text, text_hash


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    workspace_id TEXT DEFAULT '',
    workspace_name TEXT DEFAULT '',
    category_parent_id TEXT DEFAULT '',
    category_parent_name TEXT DEFAULT '',
    category_id TEXT DEFAULT '',
    category_name TEXT DEFAULT '',
    scheme_id TEXT DEFAULT '',
    scheme_name TEXT DEFAULT '',
    md_path TEXT DEFAULT '',
    spoken_md_path TEXT DEFAULT '',
    image_root TEXT DEFAULT '',
    video_root TEXT DEFAULT '',
    voice_root TEXT DEFAULT '',
    output_root TEXT DEFAULT '',
    status TEXT DEFAULT 'active',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    uid TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    price_label TEXT NOT NULL DEFAULT '',
    sort_order INTEGER NOT NULL DEFAULT 0,
    master_item_id TEXT NOT NULL DEFAULT '',
    active INTEGER NOT NULL DEFAULT 1,
    removed_from_master INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(project_id, uid)
);

CREATE TABLE IF NOT EXISTS script_blocks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    script_type TEXT NOT NULL,
    owner_uid TEXT NOT NULL DEFAULT '',
    price_range_label TEXT NOT NULL DEFAULT '',
    block_label TEXT NOT NULL DEFAULT '正文',
    script_id TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL DEFAULT '',
    text_hash TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'markdown',
    source_anchor TEXT NOT NULL DEFAULT '',
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(project_id, script_type, owner_uid, price_range_label, block_label)
);

CREATE TABLE IF NOT EXISTS asset_bindings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    uid TEXT NOT NULL DEFAULT '',
    script_block_id INTEGER REFERENCES script_blocks(id) ON DELETE SET NULL,
    asset_type TEXT NOT NULL,
    account_label TEXT NOT NULL DEFAULT '',
    account_id TEXT NOT NULL DEFAULT '',
    media_identity TEXT NOT NULL DEFAULT '',
    image_set TEXT NOT NULL DEFAULT '',
    block_label TEXT NOT NULL DEFAULT '',
    script_id TEXT NOT NULL DEFAULT '',
    text_hash TEXT NOT NULL DEFAULT '',
    path TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'missing',
    source_kind TEXT NOT NULL DEFAULT 'scan',
    source_path TEXT NOT NULL DEFAULT '',
    file_size INTEGER,
    file_mtime TEXT NOT NULL DEFAULT '',
    confirmed INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(project_id, uid, script_block_id, asset_type, account_label, block_label, path)
);

CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    label TEXT NOT NULL UNIQUE,
    account_id TEXT NOT NULL DEFAULT '',
    voice_id TEXT NOT NULL DEFAULT '',
    minimax_voice_id TEXT NOT NULL DEFAULT '',
    voice_name TEXT NOT NULL DEFAULT '',
    media_identity TEXT NOT NULL DEFAULT '',
    closing_audio_path TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS voice_profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    voice_id TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL DEFAULT '',
    speaker_audio_path TEXT NOT NULL DEFAULT '',
    emotion_audio_path TEXT NOT NULL DEFAULT '',
    source_audio_path TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sync_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER REFERENCES projects(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'success',
    message TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sync_event_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sync_event_id INTEGER NOT NULL REFERENCES sync_events(id) ON DELETE CASCADE,
    item_kind TEXT NOT NULL DEFAULT '',
    uid TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT '',
    message TEXT NOT NULL DEFAULT '',
    path TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
);
"""


def _script_id_slug(value: Any) -> str:
    text = safe_text(value).casefold()
    text = text.replace("元以下", "-under").replace("以下", "-under")
    text = text.replace("元以上", "-over").replace("以上", "-over")
    text = text.replace("元", "")
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text


class Database:
    def __init__(self, path: Path = DB_PATH):
        ensure_data_dir()
        self.path = path
        self.init()

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def init(self) -> None:
        with sqlite3.connect(self.path) as conn:
            conn.executescript(SCHEMA)
            self._migrate(conn)

    def _migrate(self, conn: sqlite3.Connection) -> None:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(projects)").fetchall()}
        if "spoken_md_path" not in columns:
            conn.execute("ALTER TABLE projects ADD COLUMN spoken_md_path TEXT DEFAULT ''")
        account_columns = {row[1] for row in conn.execute("PRAGMA table_info(accounts)").fetchall()}
        if "minimax_voice_id" not in account_columns:
            conn.execute("ALTER TABLE accounts ADD COLUMN minimax_voice_id TEXT NOT NULL DEFAULT ''")
            self._migrate_minimax_voice_ids(conn)
        asset_columns = {row[1] for row in conn.execute("PRAGMA table_info(asset_bindings)").fetchall()}
        for column, ddl in {
            "media_identity": "TEXT NOT NULL DEFAULT ''",
            "image_set": "TEXT NOT NULL DEFAULT ''",
            "script_id": "TEXT NOT NULL DEFAULT ''",
            "text_hash": "TEXT NOT NULL DEFAULT ''",
            "source_path": "TEXT NOT NULL DEFAULT ''",
        }.items():
            if column not in asset_columns:
                conn.execute(f"ALTER TABLE asset_bindings ADD COLUMN {column} {ddl}")
        script_columns = {row[1] for row in conn.execute("PRAGMA table_info(script_blocks)").fetchall()}
        if "script_id" not in script_columns:
            conn.execute("ALTER TABLE script_blocks ADD COLUMN script_id TEXT NOT NULL DEFAULT ''")
        self._migrate_script_hashes(conn)
        self._migrate_script_ids(conn)

    def _migrate_minimax_voice_ids(self, conn: sqlite3.Connection) -> None:
        aliases = {
            "知了": "bilibili-zhiliao",
            "蓉蓉": "rongrong-v2",
            "荣荣": "rongrong-v2",
        }
        rows = conn.execute("SELECT id, label, voice_id FROM accounts").fetchall()
        for row in rows:
            candidates = [safe_text(row[1]), safe_text(row[2])]
            minimax_voice_id = ""
            for candidate in candidates:
                if candidate in aliases:
                    minimax_voice_id = aliases[candidate]
                    break
            if minimax_voice_id:
                conn.execute("UPDATE accounts SET minimax_voice_id=? WHERE id=?", (minimax_voice_id, row[0]))

    def _migrate_script_hashes(self, conn: sqlite3.Connection) -> None:
        rows = conn.execute("SELECT id, body, text_hash FROM script_blocks").fetchall()
        for row in rows:
            current = safe_text(row[2])
            if current and len(current) != 64:
                continue
            conn.execute("UPDATE script_blocks SET text_hash=? WHERE id=?", (text_hash(row[1]), row[0]))

    def _migrate_script_ids(self, conn: sqlite3.Connection) -> None:
        rows = conn.execute(
            """
            SELECT id, project_id, script_type, owner_uid, price_range_label, block_label, script_id
            FROM script_blocks
            ORDER BY project_id,
                     CASE script_type WHEN 'intro' THEN 1 WHEN 'product' THEN 2 ELSE 3 END,
                     owner_uid, price_range_label, block_label, id
            """
        ).fetchall()
        counters: dict[tuple[Any, ...], int] = {}
        for row in rows:
            if safe_text(row[6]):
                continue
            script_type = safe_text(row[2])
            if script_type == "intro":
                key = (row[1], "intro")
                counters[key] = counters.get(key, 0) + 1
                script_id = f"intro:I{counters[key]:03d}"
            elif script_type == "price_transition":
                price_key = _script_id_slug(row[4]) or "price"
                key = (row[1], "price_transition", row[4])
                counters[key] = counters.get(key, 0) + 1
                script_id = f"price:{price_key}:V{counters[key]:03d}"
            else:
                uid = safe_text(row[3]) or "UNKNOWN"
                key = (row[1], "product", uid)
                counters[key] = counters.get(key, 0) + 1
                script_id = f"product:{uid}:V{counters[key]:03d}"
            conn.execute("UPDATE script_blocks SET script_id=? WHERE id=?", (script_id, row[0]))

    def execute(self, sql: str, params: Iterable[Any] = ()) -> None:
        with self.connect() as conn:
            conn.execute(sql, tuple(params))

    def fetchall(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return list(conn.execute(sql, tuple(params)).fetchall())

    def fetchone(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute(sql, tuple(params)).fetchone()

    def upsert_project(self, payload: dict[str, Any]) -> int:
        ts = now_iso()
        project_id = int(payload.get("id") or 0)
        columns = [
            "name",
            "workspace_id",
            "workspace_name",
            "category_parent_id",
            "category_parent_name",
            "category_id",
            "category_name",
            "scheme_id",
            "scheme_name",
            "md_path",
            "spoken_md_path",
            "image_root",
            "video_root",
            "voice_root",
            "output_root",
            "status",
        ]
        values = {column: safe_text(payload.get(column)) for column in columns}
        values["status"] = values["status"] or "active"
        with self.connect() as conn:
            if project_id:
                assignments = ", ".join(f"{column}=?" for column in columns)
                conn.execute(
                    f"UPDATE projects SET {assignments}, updated_at=? WHERE id=?",
                    [values[column] for column in columns] + [ts, project_id],
                )
                return project_id
            cursor = conn.execute(
                f"INSERT INTO projects ({', '.join(columns)}, created_at, updated_at) VALUES ({', '.join('?' for _ in columns)}, ?, ?)",
                [values[column] for column in columns] + [ts, ts],
            )
            return int(cursor.lastrowid)

    def latest_project_id(self) -> int | None:
        row = self.fetchone("SELECT id FROM projects ORDER BY updated_at DESC, id DESC LIMIT 1")
        return int(row["id"]) if row else None

    def log_event(self, project_id: int | None, event_type: str, status: str, message: str, items: list[dict[str, Any]] | None = None) -> int:
        ts = now_iso()
        with self.connect() as conn:
            cursor = conn.execute(
                "INSERT INTO sync_events (project_id, event_type, status, message, created_at) VALUES (?, ?, ?, ?, ?)",
                (project_id, event_type, status, message, ts),
            )
            event_id = int(cursor.lastrowid)
            for item in items or []:
                conn.execute(
                    """
                    INSERT INTO sync_event_items (sync_event_id, item_kind, uid, title, status, message, path)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event_id,
                        safe_text(item.get("item_kind")),
                        safe_text(item.get("uid")),
                        safe_text(item.get("title")),
                        safe_text(item.get("status")),
                        safe_text(item.get("message")),
                        safe_text(item.get("path")),
                    ),
                )
            return event_id
