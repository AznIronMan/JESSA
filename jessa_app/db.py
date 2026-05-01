from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from . import config


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def get_db() -> Iterator[sqlite3.Connection]:
    conn = connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
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


def rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [row_to_dict(row) or {} for row in rows]


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {row["name"] for row in rows}


def _ensure_column(conn: sqlite3.Connection, table: str, name: str, definition: str) -> None:
    if name not in _columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def _read_profile_seed(path: Path) -> str:
    if path.exists():
        return path.read_text(encoding="utf-8", errors="replace")
    return (
        "# JESSA Core Profile\n\n"
        "Add Geoff's canonical resume/profile data here. Future generated resumes "
        "and job analyses should use this as the source of truth.\n"
    )


def init_db() -> None:
    with get_db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS core_profile (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                content TEXT NOT NULL,
                version INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
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
                interview_confidence REAL,
                salary_ask_range TEXT,
                salary_floor TEXT,
                resume_base TEXT,
                recommendation TEXT,
                analysis_summary TEXT,
                analysis_json TEXT,
                cover_letter TEXT,
                resume_notes TEXT,
                status_updated_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS job_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                note TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS emails (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id TEXT UNIQUE,
                job_id INTEGER,
                subject TEXT NOT NULL DEFAULT '',
                sender TEXT NOT NULL DEFAULT '',
                received_at TEXT NOT NULL DEFAULT '',
                classification TEXT NOT NULL DEFAULT '',
                confidence REAL NOT NULL DEFAULT 0,
                summary TEXT NOT NULL DEFAULT '',
                raw_excerpt TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS application_artifacts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL,
                artifact_type TEXT NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                content TEXT NOT NULL DEFAULT '',
                format TEXT NOT NULL DEFAULT 'markdown',
                version INTEGER NOT NULL DEFAULT 1,
                source_profile_version INTEGER,
                is_submitted INTEGER NOT NULL DEFAULT 0,
                submitted_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE
            );
            """
        )
        _ensure_column(conn, "jobs", "status_updated_at", "TEXT")
        _ensure_column(conn, "application_artifacts", "source_profile_version", "INTEGER")
        exists = conn.execute("SELECT 1 FROM core_profile WHERE id = 1").fetchone()
        if not exists:
            conn.execute(
                "INSERT INTO core_profile (id, content, version, updated_at) VALUES (1, ?, 1, ?)",
                (_read_profile_seed(config.PROFILE_SOURCE), utc_now()),
            )


def log_event(conn: sqlite3.Connection, job_id: int, event_type: str, note: str = "") -> None:
    conn.execute(
        "INSERT INTO job_events (job_id, event_type, note, created_at) VALUES (?, ?, ?, ?)",
        (job_id, event_type, note, utc_now()),
    )
