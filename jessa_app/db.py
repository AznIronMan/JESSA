from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

try:
    import psycopg
    from psycopg import sql
    from psycopg.rows import dict_row
except ImportError:  # pragma: no cover - exercised only before dependencies are installed.
    psycopg = None
    sql = None
    dict_row = None

from . import config
from .defaults import DEFAULT_APP_PROMPT_KEY, DEFAULT_CORE_PROFILE, DEFAULT_SYSTEM_PROMPT


class DatabaseConfigError(RuntimeError):
    """Raised when PostgreSQL is not configured or the driver is unavailable."""


JOB_LIFECYCLE_ACTIVE = "active"
JOB_LIFECYCLE_ARCHIVED = "archived"
JOB_LIFECYCLE_TRASH = "trash"
VALID_JOB_LIFECYCLE_STATES = {
    JOB_LIFECYCLE_ACTIVE,
    JOB_LIFECYCLE_ARCHIVED,
    JOB_LIFECYCLE_TRASH,
}
AUTO_ARCHIVE_STATUSES = frozenset({"not_for_me", "job_expired", "rejected"})
TRASH_RETENTION_HOURS = 24


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _require_psycopg() -> None:
    if psycopg is None or dict_row is None:
        raise DatabaseConfigError(
            "PostgreSQL support requires psycopg. Run: python -m pip install -r requirements.txt"
        )


def _connection_kwargs(dbname: str | None = None) -> dict[str, Any]:
    missing = [
        name
        for name, value in (
            ("POSTGRES_HOST", config.POSTGRES_HOST),
            ("POSTGRES_USER", config.POSTGRES_USER),
            ("POSTGRES_DB_NAME", config.POSTGRES_DB_NAME),
        )
        if not value
    ]
    if missing:
        raise DatabaseConfigError(
            "PostgreSQL is required. Missing environment setting(s): " + ", ".join(missing)
        )
    return {
        "host": config.POSTGRES_HOST,
        "port": config.POSTGRES_PORT,
        "dbname": dbname or config.POSTGRES_DB_NAME,
        "user": config.POSTGRES_USER,
        "password": config.POSTGRES_PASSWORD,
        "sslmode": config.POSTGRES_SSLMODE,
        "connect_timeout": config.POSTGRES_CONNECT_TIMEOUT,
        "row_factory": dict_row,
    }


def connect_raw(dbname: str | None = None):
    _require_psycopg()
    return psycopg.connect(**_connection_kwargs(dbname))


def ensure_database_exists() -> bool:
    _require_psycopg()
    try:
        conn = connect_raw(config.POSTGRES_DB_NAME)
    except psycopg.errors.InvalidCatalogName:
        if sql is None:
            raise
        maintenance = connect_raw(config.POSTGRES_MAINTENANCE_DB)
        try:
            maintenance.autocommit = True
            row = maintenance.execute(
                "SELECT 1 AS exists FROM pg_database WHERE datname = %s",
                (config.POSTGRES_DB_NAME,),
            ).fetchone()
            if row:
                return False
            maintenance.execute(
                sql.SQL("CREATE DATABASE {}").format(sql.Identifier(config.POSTGRES_DB_NAME))
            )
            return True
        finally:
            maintenance.close()
    else:
        conn.close()
        return False


def _translate_placeholders(query: str) -> str:
    return query.replace("?", "%s")


class Cursor:
    def __init__(self, cursor: Any):
        self._cursor = cursor

    @property
    def rowcount(self) -> int:
        return int(self._cursor.rowcount)

    def fetchone(self) -> Mapping[str, Any] | None:
        return self._cursor.fetchone()

    def fetchall(self) -> list[Mapping[str, Any]]:
        return list(self._cursor.fetchall())


class Connection:
    def __init__(self, conn: Any):
        self._conn = conn

    def execute(self, query: str, params: Sequence[Any] | Mapping[str, Any] | None = None) -> Cursor:
        sql = _translate_placeholders(query)
        if params is None:
            return Cursor(self._conn.execute(sql))
        return Cursor(self._conn.execute(sql, params))

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    def close(self) -> None:
        self._conn.close()


def connect() -> Connection:
    return Connection(connect_raw())


@contextmanager
def get_db() -> Iterator[Connection]:
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def row_to_dict(row: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    item = dict(row)
    for field in ("analysis_json", "generated_json"):
        if item.get(field):
            try:
                item[field] = json.loads(item[field])
            except json.JSONDecodeError:
                pass
    return item


def rows_to_dicts(rows: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [row_to_dict(row) or {} for row in rows]


def _columns(conn: Connection, table: str) -> set[str]:
    rows = conn.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = ?
        """,
        (table,),
    ).fetchall()
    return {str(row["column_name"]) for row in rows}


def _ensure_column(conn: Connection, table: str, name: str, definition: str) -> None:
    if name not in _columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def _read_profile_seed(path: Path | None) -> str:
    if path and path.exists():
        return path.read_text(encoding="utf-8", errors="replace")
    return DEFAULT_CORE_PROFILE


SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS core_profile (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        content TEXT NOT NULL,
        version INTEGER NOT NULL DEFAULT 1,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS app_prompts (
        prompt_key TEXT PRIMARY KEY,
        content TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS jobs (
        id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
        source TEXT NOT NULL DEFAULT 'manual',
        url TEXT UNIQUE,
        apply_url TEXT,
        title TEXT NOT NULL DEFAULT '',
        company TEXT NOT NULL DEFAULT '',
        location TEXT NOT NULL DEFAULT '',
        salary TEXT NOT NULL DEFAULT '',
        posted_date TEXT NOT NULL DEFAULT '',
        description TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'new',
        match_score INTEGER,
        qualification_band TEXT,
        interview_odds TEXT,
        interview_confidence DOUBLE PRECISION,
        salary_ask_range TEXT,
        salary_floor TEXT,
        resume_base TEXT,
        recommendation TEXT,
        analysis_summary TEXT,
        analysis_json TEXT,
        cover_letter TEXT,
        resume_notes TEXT,
        status_updated_at TEXT,
        lifecycle_state TEXT NOT NULL DEFAULT 'active',
        archived_at TEXT,
        trashed_at TEXT,
        purge_after TEXT,
        previous_lifecycle_state TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS job_events (
        id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
        job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
        event_type TEXT NOT NULL,
        note TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS emails (
        id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
        message_id TEXT UNIQUE,
        job_id INTEGER REFERENCES jobs(id) ON DELETE SET NULL,
        subject TEXT NOT NULL DEFAULT '',
        sender TEXT NOT NULL DEFAULT '',
        received_at TEXT NOT NULL DEFAULT '',
        classification TEXT NOT NULL DEFAULT '',
        confidence DOUBLE PRECISION NOT NULL DEFAULT 0,
        match_confidence DOUBLE PRECISION NOT NULL DEFAULT 0,
        match_reason TEXT NOT NULL DEFAULT '',
        status_action TEXT NOT NULL DEFAULT '',
        summary TEXT NOT NULL DEFAULT '',
        raw_excerpt TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS application_artifacts (
        id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
        job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
        artifact_type TEXT NOT NULL,
        title TEXT NOT NULL DEFAULT '',
        content TEXT NOT NULL DEFAULT '',
        format TEXT NOT NULL DEFAULT 'markdown',
        version INTEGER NOT NULL DEFAULT 1,
        source_profile_version INTEGER,
        is_submitted INTEGER NOT NULL DEFAULT 0,
        submitted_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS linkedin_profile_cache (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        url TEXT NOT NULL DEFAULT '',
        title TEXT NOT NULL DEFAULT '',
        content TEXT NOT NULL DEFAULT '',
        fetched_at TEXT,
        updated_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_job_events_job_id ON job_events(job_id)",
    "CREATE INDEX IF NOT EXISTS idx_emails_job_id ON emails(job_id)",
    "CREATE INDEX IF NOT EXISTS idx_application_artifacts_job_id ON application_artifacts(job_id)",
)

JOB_LIFECYCLE_INDEX_STATEMENTS = (
    "CREATE INDEX IF NOT EXISTS idx_jobs_lifecycle_state ON jobs(lifecycle_state)",
    "CREATE INDEX IF NOT EXISTS idx_jobs_purge_after ON jobs(purge_after)",
)


def purge_expired_trashed_jobs(conn: Connection) -> int:
    rows = conn.execute(
        """
        SELECT id
        FROM jobs
        WHERE lifecycle_state = ? AND purge_after IS NOT NULL AND purge_after <= ?
        """,
        (JOB_LIFECYCLE_TRASH, utc_now()),
    ).fetchall()
    for row in rows:
        conn.execute("DELETE FROM jobs WHERE id = ?", (int(row["id"]),))
    return len(rows)


def _archive_existing_terminal_jobs(conn: Connection) -> None:
    conn.execute(
        """
        UPDATE jobs
        SET lifecycle_state = ?,
            archived_at = COALESCE(archived_at, status_updated_at, updated_at, created_at, ?)
        WHERE lifecycle_state = ?
          AND status IN (?, ?, ?)
        """,
        (
            JOB_LIFECYCLE_ARCHIVED,
            utc_now(),
            JOB_LIFECYCLE_ACTIVE,
            *sorted(AUTO_ARCHIVE_STATUSES),
        ),
    )


def get_app_prompt(conn: Connection, prompt_key: str = DEFAULT_APP_PROMPT_KEY) -> str:
    row = conn.execute("SELECT content FROM app_prompts WHERE prompt_key = ?", (prompt_key,)).fetchone()
    if row and str(row.get("content") or "").strip():
        return str(row["content"])
    return DEFAULT_SYSTEM_PROMPT


def set_app_prompt(conn: Connection, content: str, prompt_key: str = DEFAULT_APP_PROMPT_KEY) -> dict[str, Any]:
    now = utc_now()
    conn.execute(
        """
        INSERT INTO app_prompts (prompt_key, content, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT (prompt_key) DO UPDATE SET
            content = EXCLUDED.content,
            updated_at = EXCLUDED.updated_at
        """,
        (prompt_key, content, now),
    )
    row = conn.execute("SELECT * FROM app_prompts WHERE prompt_key = ?", (prompt_key,)).fetchone()
    return dict(row or {})


def _seed_default_prompt(conn: Connection) -> None:
    row = conn.execute("SELECT 1 FROM app_prompts WHERE prompt_key = ?", (DEFAULT_APP_PROMPT_KEY,)).fetchone()
    if not row:
        set_app_prompt(conn, DEFAULT_SYSTEM_PROMPT)


def init_db() -> None:
    ensure_database_exists()
    with get_db() as conn:
        for statement in SCHEMA_STATEMENTS:
            conn.execute(statement)
        _ensure_column(conn, "jobs", "status_updated_at", "TEXT")
        _ensure_column(conn, "jobs", "lifecycle_state", "TEXT NOT NULL DEFAULT 'active'")
        _ensure_column(conn, "jobs", "archived_at", "TEXT")
        _ensure_column(conn, "jobs", "trashed_at", "TEXT")
        _ensure_column(conn, "jobs", "purge_after", "TEXT")
        _ensure_column(conn, "jobs", "previous_lifecycle_state", "TEXT")
        _ensure_column(conn, "emails", "match_confidence", "DOUBLE PRECISION NOT NULL DEFAULT 0")
        _ensure_column(conn, "emails", "match_reason", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "emails", "status_action", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "application_artifacts", "source_profile_version", "INTEGER")
        for statement in JOB_LIFECYCLE_INDEX_STATEMENTS:
            conn.execute(statement)
        _seed_default_prompt(conn)
        _archive_existing_terminal_jobs(conn)
        purge_expired_trashed_jobs(conn)
        exists = conn.execute("SELECT 1 FROM core_profile WHERE id = 1").fetchone()
        if not exists:
            conn.execute(
                "INSERT INTO core_profile (id, content, version, updated_at) VALUES (1, ?, 1, ?)",
                (_read_profile_seed(config.PROFILE_SOURCE), utc_now()),
            )


def log_event(conn: Connection, job_id: int, event_type: str, note: str = "") -> None:
    conn.execute(
        "INSERT INTO job_events (job_id, event_type, note, created_at) VALUES (?, ?, ?, ?)",
        (job_id, event_type, note, utc_now()),
    )
