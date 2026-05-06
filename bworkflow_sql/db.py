from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable

from .settings import DB_PATH, ensure_data_dir
from .utils import now_iso, safe_text


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
    block_label TEXT NOT NULL DEFAULT '',
    path TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'missing',
    source_kind TEXT NOT NULL DEFAULT 'scan',
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
    voice_name TEXT NOT NULL DEFAULT '',
    media_identity TEXT NOT NULL DEFAULT '',
    closing_audio_path TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
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
