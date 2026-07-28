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
    master_snapshot_id TEXT,
    master_snapshot_applied_at TEXT,
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
    product_card_json TEXT NOT NULL DEFAULT '',
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
    voice_provider TEXT NOT NULL DEFAULT '',
    voice_model TEXT NOT NULL DEFAULT '',
    voice_id TEXT NOT NULL DEFAULT '',
    synthesis_settings_hash TEXT NOT NULL DEFAULT '',
    generation_fingerprint TEXT NOT NULL DEFAULT '',
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
    master_account_id TEXT,
    bilibili_mid TEXT,
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

CREATE TABLE IF NOT EXISTS account_voice_profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    voice_id TEXT NOT NULL,
    model TEXT NOT NULL DEFAULT '',
    settings_json TEXT NOT NULL DEFAULT '{}',
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(account_id, provider)
);

CREATE TABLE IF NOT EXISTS production_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    episode_id TEXT NOT NULL DEFAULT '',
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    account_id INTEGER,
    category_name TEXT NOT NULL,
    scheme_id TEXT NOT NULL DEFAULT '',
    scheme_name TEXT NOT NULL DEFAULT '',
    account_label TEXT NOT NULL,
    template_id TEXT NOT NULL,
    template_display_name TEXT NOT NULL DEFAULT '',
    template_dir TEXT NOT NULL DEFAULT '',
    run_manifest_path TEXT NOT NULL UNIQUE,
    original_full_mp4_path TEXT NOT NULL DEFAULT '',
    full_mp4_path TEXT NOT NULL,
    full_mp4_sha256 TEXT NOT NULL DEFAULT '',
    full_mp4_size INTEGER NOT NULL DEFAULT 0,
    acceptance_mode TEXT NOT NULL,
    generated_at TEXT NOT NULL DEFAULT '',
    confirmed_at TEXT NOT NULL,
    publish_status TEXT NOT NULL DEFAULT 'confirmed',
    published_at TEXT,
    archived_at TEXT,
    recipe_path TEXT NOT NULL DEFAULT '',
    recipe_sha256 TEXT NOT NULL DEFAULT '',
    recipe_status TEXT NOT NULL DEFAULT 'legacy_unknown',
    supersedes_production_run_id INTEGER REFERENCES production_runs(id) ON DELETE SET NULL,
    published_video_url TEXT NOT NULL DEFAULT '',
    bvid TEXT NOT NULL DEFAULT '',
    aid TEXT NOT NULL DEFAULT '',
    video_owner_mid TEXT NOT NULL DEFAULT '',
    blue_link_backfill_id TEXT NOT NULL DEFAULT '',
    blue_link_backfill_status TEXT NOT NULL DEFAULT '',
    blue_link_matched_count INTEGER NOT NULL DEFAULT 0,
    blue_link_unresolved_count INTEGER NOT NULL DEFAULT 0,
    blue_link_browser_pending_count INTEGER NOT NULL DEFAULT 0,
    blue_link_browser_deferred_count INTEGER NOT NULL DEFAULT 0,
    blue_link_browser_suspended_count INTEGER NOT NULL DEFAULT 0,
    blue_link_title_candidate_count INTEGER NOT NULL DEFAULT 0,
    blue_link_master_pending_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS episode_source_snapshots (
    episode_id TEXT PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    master_snapshot_id TEXT NOT NULL,
    source_sha256 TEXT NOT NULL,
    source_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_episode_source_snapshots_project
    ON episode_source_snapshots(project_id, created_at);

CREATE TABLE IF NOT EXISTS resource_cleanup_candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER REFERENCES projects(id) ON DELETE CASCADE,
    resource_kind TEXT NOT NULL,
    path TEXT NOT NULL UNIQUE,
    reason TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    eligible_at TEXT NOT NULL,
    quarantine_path TEXT NOT NULL DEFAULT '',
    details_json TEXT NOT NULL DEFAULT '{}',
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    resolved_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_resource_cleanup_candidates_project_status
ON resource_cleanup_candidates(project_id, status, eligible_at);

CREATE TABLE IF NOT EXISTS resource_state_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    resource_kind TEXT NOT NULL,
    resource_key TEXT NOT NULL,
    path TEXT NOT NULL DEFAULT '',
    previous_state TEXT NOT NULL DEFAULT '',
    new_state TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    account_label TEXT NOT NULL DEFAULT '',
    details_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_resource_state_events_project_resource
ON resource_state_events(project_id, resource_kind, resource_key, created_at);

CREATE INDEX IF NOT EXISTS idx_resource_state_events_path
ON resource_state_events(path, created_at);

CREATE TABLE IF NOT EXISTS resource_cleanup_batches (
    id TEXT PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'prepared',
    filters_json TEXT NOT NULL DEFAULT '{}',
    snapshot_hash TEXT NOT NULL,
    confirmation_token_hash TEXT NOT NULL,
    candidate_count INTEGER NOT NULL DEFAULT 0,
    total_size_bytes INTEGER NOT NULL DEFAULT 0,
    confirmed_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    confirmed_at TEXT,
    executed_at TEXT,
    result_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_resource_cleanup_batches_project_status
ON resource_cleanup_batches(project_id, status, created_at);

CREATE TABLE IF NOT EXISTS resource_cleanup_batch_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id TEXT NOT NULL REFERENCES resource_cleanup_batches(id) ON DELETE CASCADE,
    candidate_id INTEGER REFERENCES resource_cleanup_candidates(id) ON DELETE SET NULL,
    resource_kind TEXT NOT NULL,
    path TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    expected_entry_kind TEXT NOT NULL,
    expected_size_bytes INTEGER NOT NULL DEFAULT 0,
    expected_mtime_ns INTEGER NOT NULL DEFAULT 0,
    expected_fingerprint TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'prepared',
    result_message TEXT NOT NULL DEFAULT '',
    deleted_at TEXT,
    UNIQUE(batch_id, candidate_id)
);

CREATE INDEX IF NOT EXISTS idx_resource_cleanup_batch_items_batch_status
ON resource_cleanup_batch_items(batch_id, status, id);

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL,
    applied_at TEXT NOT NULL
);
"""

CURRENT_SCHEMA_VERSION = 17

CONFIRMED_MASTER_ACCOUNT_BINDINGS = {
    "小燃": ("c025960c-5560-4630-8344-509a5c6d92a5", "3546911325817533"),
    "小博": ("5fe6305b-c1ca-4ee4-bfd7-9407bd4e5302", "673644511"),
    "小歪": ("db915307-c99d-49e8-9a82-3e28df2f68c1", "1602507900"),
    "荣荣": ("91c09fcc-b2b8-49c6-abb4-4a512f486837", "439372"),
}


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
        self._conn: sqlite3.Connection | None = None
        self._read_conn: sqlite3.Connection | None = None
        self._lock = __import__("threading").Lock()
        self._read_lock = __import__("threading").Lock()
        self.init()

    def _get_connection(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self.path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA foreign_keys = ON")
            self._conn.execute("PRAGMA journal_mode = WAL")
        return self._conn

    @contextmanager
    def connect(self):
        with self._lock:
            conn = self._get_connection()
            try:
                yield conn
                conn.commit()
            except BaseException:
                conn.rollback()
                raise

    def _get_read_connection(self) -> sqlite3.Connection:
        if self._read_conn is None:
            self._read_conn = sqlite3.connect(self.path, check_same_thread=False)
            self._read_conn.row_factory = sqlite3.Row
            self._read_conn.execute("PRAGMA journal_mode = WAL")
            self._read_conn.execute("PRAGMA query_only = ON")
        return self._read_conn

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None
        with self._read_lock:
            if self._read_conn is not None:
                self._read_conn.close()
                self._read_conn = None

    def init(self) -> None:
        conn = self._get_connection()
        conn.executescript(SCHEMA)
        self._run_migrations(conn)
        conn.commit()

    def _get_schema_version(self, conn: sqlite3.Connection) -> int:
        try:
            row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
            return row[0] if row and row[0] is not None else 0
        except sqlite3.OperationalError:
            return 0

    def _set_schema_version(self, conn: sqlite3.Connection, version: int) -> None:
        conn.execute(
            "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
            (version, now_iso()),
        )

    def _run_migrations(self, conn: sqlite3.Connection) -> None:
        current = self._get_schema_version(conn)
        migrations = [
            (1, self._migrate_v1),
            (2, self._migrate_v2),
            (3, self._migrate_v3),
            (4, self._migrate_v4),
            (5, self._migrate_v5),
            (6, self._migrate_v6),
            (7, self._migrate_v7),
            (8, self._migrate_v8),
            (9, self._migrate_v9),
            (10, self._migrate_v10),
            (11, self._migrate_v11),
            (12, self._migrate_v12),
            (13, self._migrate_v13),
            (14, self._migrate_v14),
            (15, self._migrate_v15),
            (16, self._migrate_v16),
            (17, self._migrate_v17),
        ]
        for version, func in migrations:
            if current < version:
                func(conn)
                self._set_schema_version(conn, version)

    def _migrate_v1(self, conn: sqlite3.Connection) -> None:
        """Consolidate all pre-versioning column additions into v1."""
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

    def _migrate_v9(self, conn: sqlite3.Connection) -> None:
        """Bind local production accounts to stable Master/Bilibili identities."""
        columns = {row[1] for row in conn.execute("PRAGMA table_info(accounts)").fetchall()}
        if "master_account_id" not in columns:
            conn.execute("ALTER TABLE accounts ADD COLUMN master_account_id TEXT")
        if "bilibili_mid" not in columns:
            conn.execute("ALTER TABLE accounts ADD COLUMN bilibili_mid TEXT")

        for label, (master_account_id, bilibili_mid) in CONFIRMED_MASTER_ACCOUNT_BINDINGS.items():
            conn.execute(
                """
                UPDATE accounts
                SET master_account_id=?, bilibili_mid=?
                WHERE label=? AND (master_account_id IS NULL OR master_account_id='')
                """,
                (master_account_id, bilibili_mid, label),
            )

        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_accounts_master_account_id
            ON accounts(master_account_id)
            WHERE master_account_id IS NOT NULL AND master_account_id != ''
            """
        )
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_accounts_bilibili_mid
            ON accounts(bilibili_mid)
            WHERE bilibili_mid IS NOT NULL AND bilibili_mid != ''
            """
        )

    def _migrate_v10(self, conn: sqlite3.Connection) -> None:
        """Persist the published Bilibili identity and blue-link backfill result."""
        columns = {row[1] for row in conn.execute("PRAGMA table_info(production_runs)").fetchall()}
        for column, ddl in {
            "account_id": "INTEGER",
            "published_video_url": "TEXT NOT NULL DEFAULT ''",
            "bvid": "TEXT NOT NULL DEFAULT ''",
            "aid": "TEXT NOT NULL DEFAULT ''",
            "video_owner_mid": "TEXT NOT NULL DEFAULT ''",
            "blue_link_backfill_id": "TEXT NOT NULL DEFAULT ''",
            "blue_link_backfill_status": "TEXT NOT NULL DEFAULT ''",
            "blue_link_matched_count": "INTEGER NOT NULL DEFAULT 0",
            "blue_link_unresolved_count": "INTEGER NOT NULL DEFAULT 0",
        }.items():
            if column not in columns:
                conn.execute(f"ALTER TABLE production_runs ADD COLUMN {column} {ddl}")
        conn.execute(
            """
            UPDATE production_runs
            SET account_id=(SELECT accounts.id FROM accounts WHERE accounts.label=production_runs.account_label)
            WHERE account_id IS NULL
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_production_runs_account_id ON production_runs(account_id)")

    def _migrate_v11(self, conn: sqlite3.Connection) -> None:
        """Persist Master-authoritative unresolved-state breakdown."""
        columns = {row[1] for row in conn.execute("PRAGMA table_info(production_runs)").fetchall()}
        for column in (
            "blue_link_browser_pending_count",
            "blue_link_browser_deferred_count",
            "blue_link_browser_suspended_count",
            "blue_link_master_pending_count",
        ):
            if column not in columns:
                conn.execute(
                    f"ALTER TABLE production_runs ADD COLUMN {column} INTEGER NOT NULL DEFAULT 0"
                )

    def _migrate_v12(self, conn: sqlite3.Connection) -> None:
        """Persist the batch of title candidates awaiting one user confirmation."""
        columns = {row[1] for row in conn.execute("PRAGMA table_info(production_runs)").fetchall()}
        if "blue_link_title_candidate_count" not in columns:
            conn.execute(
                "ALTER TABLE production_runs ADD COLUMN "
                "blue_link_title_candidate_count INTEGER NOT NULL DEFAULT 0"
            )

    def _migrate_v13(self, conn: sqlite3.Connection) -> None:
        """Track delayed, auditable cleanup candidates without deleting files."""
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS resource_cleanup_candidates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER REFERENCES projects(id) ON DELETE CASCADE,
                resource_kind TEXT NOT NULL,
                path TEXT NOT NULL UNIQUE,
                reason TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending',
                eligible_at TEXT NOT NULL,
                quarantine_path TEXT NOT NULL DEFAULT '',
                details_json TEXT NOT NULL DEFAULT '{}',
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                resolved_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_resource_cleanup_candidates_project_status
            ON resource_cleanup_candidates(project_id, status, eligible_at);
            """
        )

    def _migrate_v14(self, conn: sqlite3.Connection) -> None:
        """Record resource state changes and user-confirmed deletion batches."""
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS resource_state_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                resource_kind TEXT NOT NULL,
                resource_key TEXT NOT NULL,
                path TEXT NOT NULL DEFAULT '',
                previous_state TEXT NOT NULL DEFAULT '',
                new_state TEXT NOT NULL,
                reason TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL DEFAULT '',
                account_label TEXT NOT NULL DEFAULT '',
                details_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_resource_state_events_project_resource
            ON resource_state_events(project_id, resource_kind, resource_key, created_at);
            CREATE INDEX IF NOT EXISTS idx_resource_state_events_path
            ON resource_state_events(path, created_at);

            CREATE TABLE IF NOT EXISTS resource_cleanup_batches (
                id TEXT PRIMARY KEY,
                project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                status TEXT NOT NULL DEFAULT 'prepared',
                filters_json TEXT NOT NULL DEFAULT '{}',
                snapshot_hash TEXT NOT NULL,
                confirmation_token_hash TEXT NOT NULL,
                candidate_count INTEGER NOT NULL DEFAULT 0,
                total_size_bytes INTEGER NOT NULL DEFAULT 0,
                confirmed_by TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                confirmed_at TEXT,
                executed_at TEXT,
                result_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE INDEX IF NOT EXISTS idx_resource_cleanup_batches_project_status
            ON resource_cleanup_batches(project_id, status, created_at);

            CREATE TABLE IF NOT EXISTS resource_cleanup_batch_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_id TEXT NOT NULL REFERENCES resource_cleanup_batches(id) ON DELETE CASCADE,
                candidate_id INTEGER REFERENCES resource_cleanup_candidates(id) ON DELETE SET NULL,
                resource_kind TEXT NOT NULL,
                path TEXT NOT NULL,
                reason TEXT NOT NULL DEFAULT '',
                expected_entry_kind TEXT NOT NULL,
                expected_size_bytes INTEGER NOT NULL DEFAULT 0,
                expected_mtime_ns INTEGER NOT NULL DEFAULT 0,
                expected_fingerprint TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'prepared',
                result_message TEXT NOT NULL DEFAULT '',
                deleted_at TEXT,
                UNIQUE(batch_id, candidate_id)
            );
            CREATE INDEX IF NOT EXISTS idx_resource_cleanup_batch_items_batch_status
            ON resource_cleanup_batch_items(batch_id, status, id);
            """
        )

    def _migrate_v15(self, conn: sqlite3.Connection) -> None:
        """Backfill pre-existing cleanup candidates into the append-only event ledger."""
        conn.execute(
            """
            INSERT INTO resource_state_events
                (project_id, resource_kind, resource_key, path, previous_state,
                 new_state, reason, source, account_label, details_json, created_at)
            SELECT c.project_id,
                   c.resource_kind,
                   'cleanup_candidate:' || c.id,
                   c.path,
                   '',
                   c.status,
                   c.reason,
                   'resource_lifecycle_v15_backfill',
                   '',
                   c.details_json,
                   c.first_seen_at
            FROM resource_cleanup_candidates c
            WHERE c.project_id IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1
                  FROM resource_state_events e
                  WHERE e.project_id=c.project_id
                    AND e.resource_key='cleanup_candidate:' || c.id
              )
            """
        )

    def _migrate_v16(self, conn: sqlite3.Connection) -> None:
        """Identify new production records by episode; legacy rows remain ignored."""
        columns = {row[1] for row in conn.execute("PRAGMA table_info(production_runs)").fetchall()}
        if "episode_id" not in columns:
            conn.execute("ALTER TABLE production_runs ADD COLUMN episode_id TEXT NOT NULL DEFAULT ''")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_production_runs_episode_id "
            "ON production_runs(episode_id, confirmed_at)"
        )

    def _migrate_v17(self, conn: sqlite3.Connection) -> None:
        """Persist immutable Master source projections for schema-v3 episodes."""
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS episode_source_snapshots (
                episode_id TEXT PRIMARY KEY,
                project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                master_snapshot_id TEXT NOT NULL,
                source_sha256 TEXT NOT NULL,
                source_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_episode_source_snapshots_project
                ON episode_source_snapshots(project_id, created_at);
            """
        )

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

    def _migrate_v2(self, conn: sqlite3.Connection) -> None:
        """Add performance indexes on foreign-key columns."""
        for ddl in (
            "CREATE INDEX IF NOT EXISTS idx_products_project ON products(project_id)",
            "CREATE INDEX IF NOT EXISTS idx_script_blocks_project ON script_blocks(project_id)",
            "CREATE INDEX IF NOT EXISTS idx_asset_bindings_project ON asset_bindings(project_id)",
            "CREATE INDEX IF NOT EXISTS idx_sync_events_project ON sync_events(project_id)",
            "CREATE INDEX IF NOT EXISTS idx_sync_event_items_event ON sync_event_items(sync_event_id)",
        ):
            conn.execute(ddl)

    def _migrate_v3(self, conn: sqlite3.Connection) -> None:
        """Store optional Master product-card payload for Remotion rendering."""
        product_columns = {row[1] for row in conn.execute("PRAGMA table_info(products)").fetchall()}
        if "product_card_json" not in product_columns:
            conn.execute("ALTER TABLE products ADD COLUMN product_card_json TEXT NOT NULL DEFAULT ''")

    def _migrate_v4(self, conn: sqlite3.Connection) -> None:
        """Store applied Master snapshot identity without copying its payload."""
        project_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(projects)").fetchall()
        }
        if "master_snapshot_id" not in project_columns:
            conn.execute("ALTER TABLE projects ADD COLUMN master_snapshot_id TEXT")
        if "master_snapshot_applied_at" not in project_columns:
            conn.execute("ALTER TABLE projects ADD COLUMN master_snapshot_applied_at TEXT")

    def _migrate_v5(self, conn: sqlite3.Connection) -> None:
        """Add the user-confirmed formal production ledger."""
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_production_runs_project_account ON production_runs(project_id, account_label, confirmed_at)"
        )

    def _migrate_v6(self, conn: sqlite3.Connection) -> None:
        """Extend formal productions with publish/archive lifecycle state."""
        columns = {row[1] for row in conn.execute("PRAGMA table_info(production_runs)").fetchall()}
        for column, ddl in {
            "original_full_mp4_path": "TEXT NOT NULL DEFAULT ''",
            "full_mp4_sha256": "TEXT NOT NULL DEFAULT ''",
            "full_mp4_size": "INTEGER NOT NULL DEFAULT 0",
            "publish_status": "TEXT NOT NULL DEFAULT 'confirmed'",
            "published_at": "TEXT",
            "archived_at": "TEXT",
        }.items():
            if column not in columns:
                conn.execute(f"ALTER TABLE production_runs ADD COLUMN {column} {ddl}")
        conn.execute(
            "UPDATE production_runs SET original_full_mp4_path=full_mp4_path WHERE original_full_mp4_path=''"
        )

    def _migrate_v7(self, conn: sqlite3.Connection) -> None:
        """Add immutable recipe provenance and explicit revision links."""
        columns = {row[1] for row in conn.execute("PRAGMA table_info(production_runs)").fetchall()}
        for column, ddl in {
            "recipe_path": "TEXT NOT NULL DEFAULT ''",
            "recipe_sha256": "TEXT NOT NULL DEFAULT ''",
            "recipe_status": "TEXT NOT NULL DEFAULT 'legacy_unknown'",
            "supersedes_production_run_id": "INTEGER REFERENCES production_runs(id) ON DELETE SET NULL",
        }.items():
            if column not in columns:
                conn.execute(f"ALTER TABLE production_runs ADD COLUMN {column} {ddl}")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_production_runs_supersedes ON production_runs(supersedes_production_run_id)"
        )

    def _migrate_v8(self, conn: sqlite3.Connection) -> None:
        """Record provider-specific voice profiles and generated-audio provenance."""
        asset_columns = {row[1] for row in conn.execute("PRAGMA table_info(asset_bindings)").fetchall()}
        for column, ddl in {
            "voice_provider": "TEXT NOT NULL DEFAULT ''",
            "voice_model": "TEXT NOT NULL DEFAULT ''",
            "voice_id": "TEXT NOT NULL DEFAULT ''",
            "synthesis_settings_hash": "TEXT NOT NULL DEFAULT ''",
            "generation_fingerprint": "TEXT NOT NULL DEFAULT ''",
        }.items():
            if column not in asset_columns:
                conn.execute(f"ALTER TABLE asset_bindings ADD COLUMN {column} {ddl}")

        existing_profile_rows: list[sqlite3.Row] = []
        profile_table_exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='account_voice_profiles'"
        ).fetchone()
        if profile_table_exists:
            foreign_targets = {
                safe_text(row[2]) for row in conn.execute("PRAGMA foreign_key_list(account_voice_profiles)").fetchall()
            }
            if foreign_targets and foreign_targets != {"accounts"}:
                existing_profile_rows = conn.execute("SELECT * FROM account_voice_profiles").fetchall()
                conn.execute("DROP TABLE account_voice_profiles")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS account_voice_profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                provider TEXT NOT NULL,
                voice_id TEXT NOT NULL,
                model TEXT NOT NULL DEFAULT '',
                settings_json TEXT NOT NULL DEFAULT '{}',
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(account_id, provider)
            )
            """
        )
        ts = now_iso()
        valid_account_ids = {
            int(row[0]) for row in conn.execute("SELECT id FROM accounts").fetchall()
        }
        for row in existing_profile_rows:
            if int(row["account_id"]) not in valid_account_ids:
                continue
            conn.execute(
                """
                INSERT INTO account_voice_profiles
                    (account_id, provider, voice_id, model, settings_json, enabled, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(account_id, provider) DO UPDATE SET
                    voice_id=excluded.voice_id,
                    model=excluded.model,
                    settings_json=excluded.settings_json,
                    enabled=excluded.enabled,
                    updated_at=excluded.updated_at
                """,
                (
                    row["account_id"],
                    row["provider"],
                    row["voice_id"],
                    row["model"],
                    row["settings_json"],
                    row["enabled"],
                    row["created_at"],
                    row["updated_at"],
                ),
            )
        accounts = conn.execute("SELECT id, voice_id, minimax_voice_id FROM accounts").fetchall()
        for account in accounts:
            for provider, voice_id in (("indextts", safe_text(account[1])), ("minimax", safe_text(account[2]))):
                if not voice_id:
                    continue
                conn.execute(
                    """
                    INSERT INTO account_voice_profiles
                        (account_id, provider, voice_id, model, settings_json, enabled, created_at, updated_at)
                    VALUES (?, ?, ?, '', '{}', 1, ?, ?)
                    ON CONFLICT(account_id, provider) DO NOTHING
                    """,
                    (account[0], provider, voice_id, ts, ts),
                )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_account_voice_profiles_account ON account_voice_profiles(account_id, provider)"
        )

    def execute(self, sql: str, params: Iterable[Any] = ()) -> None:
        with self.connect() as conn:
            conn.execute(sql, tuple(params))

    def fetchall(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self._read_lock:
            conn = self._get_read_connection()
            return list(conn.execute(sql, tuple(params)).fetchall())

    def fetchone(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
        with self._read_lock:
            conn = self._get_read_connection()
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
