#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

from psycopg import sql  # noqa: E402

from jessa_app import config, db  # noqa: E402


TABLE_COLUMNS = {
    "core_profile": ("id", "content", "version", "updated_at"),
    "app_prompts": ("prompt_key", "content", "updated_at"),
    "jobs": (
        "id",
        "source",
        "url",
        "apply_url",
        "title",
        "company",
        "location",
        "salary",
        "posted_date",
        "description",
        "status",
        "match_score",
        "qualification_band",
        "interview_odds",
        "interview_confidence",
        "salary_ask_range",
        "salary_floor",
        "resume_base",
        "recommendation",
        "analysis_summary",
        "analysis_json",
        "cover_letter",
        "resume_notes",
        "status_updated_at",
        "lifecycle_state",
        "archived_at",
        "trashed_at",
        "purge_after",
        "previous_lifecycle_state",
        "created_at",
        "updated_at",
    ),
    "job_events": ("id", "job_id", "event_type", "note", "created_at"),
    "emails": (
        "id",
        "message_id",
        "job_id",
        "subject",
        "sender",
        "received_at",
        "classification",
        "confidence",
        "match_confidence",
        "match_reason",
        "status_action",
        "summary",
        "raw_excerpt",
        "created_at",
    ),
    "application_artifacts": (
        "id",
        "job_id",
        "artifact_type",
        "title",
        "content",
        "format",
        "version",
        "source_profile_version",
        "is_submitted",
        "submitted_at",
        "created_at",
        "updated_at",
    ),
    "linkedin_profile_cache": ("id", "url", "title", "content", "fetched_at", "updated_at"),
}

SEQUENCE_TABLES = ("jobs", "job_events", "emails", "application_artifacts")
APPLICATION_TABLES = ("jobs", "job_events", "emails", "application_artifacts")
CONFLICT_COLUMNS = {
    "app_prompts": ("prompt_key",),
}
TABLE_ORDER_COLUMNS = {
    "app_prompts": "prompt_key",
}
COUNT_CHECK_TABLES = tuple(table for table in TABLE_COLUMNS if table != "app_prompts")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Copy JESSA data from the legacy SQLite file into the configured PostgreSQL database."
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=config.SQLITE_IMPORT_PATH,
        help="SQLite source path. Defaults to JESSA_SQLITE_IMPORT_PATH or legacy JESSA_DB_PATH.",
    )
    parser.add_argument(
        "--merge",
        action="store_true",
        help="Allow importing into a PostgreSQL database that already has application rows.",
    )
    return parser.parse_args()


def ensure_target_database() -> bool:
    return db.ensure_database_exists()


def sqlite_connect(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise SystemExit(f"SQLite source does not exist: {path}")
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def validate_sqlite(conn: sqlite3.Connection) -> None:
    result = conn.execute("PRAGMA integrity_check").fetchone()[0]
    if result != "ok":
        raise SystemExit(f"SQLite integrity check failed: {result}")


def sqlite_table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return bool(row)


def sqlite_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    if not sqlite_table_exists(conn, table):
        return set()
    return {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def backup_sqlite(path: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"{path.name}.pre-postgres-{stamp}"
    shutil.copy2(path, backup_path)
    return backup_path


def sqlite_counts(conn: sqlite3.Connection) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table in TABLE_COLUMNS:
        if sqlite_table_exists(conn, table):
            counts[table] = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        else:
            counts[table] = 0
    return counts


def postgres_counts(conn: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table in TABLE_COLUMNS:
        row = conn.execute(sql.SQL("SELECT COUNT(*) AS count FROM {}").format(sql.Identifier(table))).fetchone()
        counts[table] = int(row["count"])
    return counts


def table_rows(conn: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    source_columns = sqlite_columns(conn, table)
    if not source_columns:
        return []
    columns = TABLE_COLUMNS[table]
    available_columns = [column for column in columns if column in source_columns]
    if not available_columns:
        return []
    column_sql = ", ".join(available_columns)
    order_column = TABLE_ORDER_COLUMNS.get(table, "id")
    order_sql = f" ORDER BY {order_column}" if order_column in source_columns else ""
    rows: list[dict[str, Any]] = []
    for row in conn.execute(f"SELECT {column_sql} FROM {table}{order_sql}").fetchall():
        rows.append(dict(row))
    return rows


def upsert_rows(conn: Any, table: str, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    columns = tuple(column for column in TABLE_COLUMNS[table] if column in rows[0])
    conflict_columns = CONFLICT_COLUMNS.get(table, ("id",))
    if not all(column in columns for column in conflict_columns):
        return
    assignments = [
        sql.SQL("{} = EXCLUDED.{}").format(sql.Identifier(column), sql.Identifier(column))
        for column in columns
        if column not in conflict_columns
    ]
    if not assignments:
        return
    statement = sql.SQL(
        """
        INSERT INTO {} ({})
        VALUES ({})
        ON CONFLICT ({}) DO UPDATE SET {}
        """
    ).format(
        sql.Identifier(table),
        sql.SQL(", ").join(sql.Identifier(column) for column in columns),
        sql.SQL(", ").join(sql.Placeholder() for _ in columns),
        sql.SQL(", ").join(sql.Identifier(column) for column in conflict_columns),
        sql.SQL(", ").join(assignments),
    )
    for row in rows:
        conn.execute(statement, [row[column] for column in columns])


def reset_identity_sequences(conn: Any) -> None:
    for table in SEQUENCE_TABLES:
        conn.execute(
            sql.SQL(
                """
                SELECT setval(
                    pg_get_serial_sequence(%s, 'id'),
                    COALESCE((SELECT MAX(id) FROM {}), 1),
                    (SELECT COUNT(*) > 0 FROM {})
                )
                """
            ).format(sql.Identifier(table), sql.Identifier(table)),
            (table,),
        )


def format_counts(counts: dict[str, int]) -> str:
    return ", ".join(f"{table}={count}" for table, count in counts.items())


def main() -> int:
    args = parse_args()
    source_path = args.source.expanduser().resolve()

    source = sqlite_connect(source_path)
    try:
        validate_sqlite(source)
        backup_path = backup_sqlite(source_path)
        source_counts = sqlite_counts(source)

        created = ensure_target_database()
        db.init_db()

        target = db.connect_raw(config.POSTGRES_DB_NAME)
        try:
            before_counts = postgres_counts(target)
            if not args.merge and any(before_counts[table] for table in APPLICATION_TABLES):
                raise SystemExit(
                    "PostgreSQL target already has application rows. "
                    "Use --merge only if you intentionally want to upsert from SQLite."
                )
            for table in TABLE_COLUMNS:
                upsert_rows(target, table, table_rows(source, table))
            reset_identity_sequences(target)
            target.commit()
            target_counts = postgres_counts(target)
        except Exception:
            target.rollback()
            raise
        finally:
            target.close()
    finally:
        source.close()

    source_check_counts = {table: source_counts[table] for table in COUNT_CHECK_TABLES}
    target_check_counts = {table: target_counts[table] for table in COUNT_CHECK_TABLES}
    if source_check_counts != target_check_counts:
        raise SystemExit(
            "Migration count mismatch. "
            f"SQLite: {format_counts(source_check_counts)}; PostgreSQL: {format_counts(target_check_counts)}"
        )

    print("PostgreSQL database created: " + str(created))
    print("SQLite backup: " + str(backup_path))
    print("Migrated row counts: " + format_counts(target_counts))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
