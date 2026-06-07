from __future__ import annotations

import json
import re
from io import BytesIO
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from starlette.requests import Request

from . import __version__, config, db as db_module
from .db import (
    AUTO_ARCHIVE_STATUSES,
    DatabaseConfigError,
    JOB_LIFECYCLE_ACTIVE,
    JOB_LIFECYCLE_ARCHIVED,
    JOB_LIFECYCLE_TRASH,
    TRASH_RETENTION_HOURS,
    get_db,
    get_app_prompt,
    init_db,
    log_event,
    purge_expired_trashed_jobs,
    row_to_dict,
    rows_to_dicts,
    utc_now,
)
from .defaults import DEFAULT_CORE_PROFILE
from .env_settings import settings_payload, update_env_values
from .services.email_client import classify, match_job, sync_inbox, test_smtp
from .services.importer import ImportedJob, fetch_linkedin_profile, import_from_text, import_from_url
from .services.llm import analyze_job, answer_supplemental_questions, generate_application_package
from .services.pdf import markdown_to_pdf


APP_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))

app = FastAPI(title="JESSA", version=__version__)
app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")

STARTUP_STATUS: dict[str, Any] = {
    "database_ready": False,
    "database_error": "Startup has not completed.",
    "llm_ready": False,
    "active_llm_provider": None,
    "setup_required": True,
    "onboarding_required": False,
    "issues": ["Startup has not completed."],
}


@app.middleware("http")
async def restrict_clients(request: Request, call_next):
    client_host = request.client.host if request.client else None
    if not config.client_host_allowed(client_host):
        return JSONResponse(
            status_code=403,
            content={
                "detail": "Client address is not allowed.",
                "allowed_networks": config.allowed_client_networks(),
            },
        )
    return await call_next(request)


def _database_setup_message() -> str:
    if not config.postgres_configured():
        return (
            "PostgreSQL is not configured. Edit .env and set POSTGRES_HOST, POSTGRES_PORT, "
            "POSTGRES_USER, POSTGRES_PASS or POSTGRES_PASSWORD, and POSTGRES_DB_NAME, then restart JESSA."
        )
    return (
        "PostgreSQL settings are present but the connection failed. Check the server address, port, "
        "database name, user, password, SSL mode, and pg_hba.conf access for this app host, then restart JESSA."
    )


def _llm_setup_message() -> str:
    return (
        "No LLM API key is configured. Edit .env and add at least one of OPENAI_API_KEY, "
        "CLAUDE_API_KEY or ANTHROPIC_API_KEY, GEMINI_API_KEY or GOOGLE_API_KEY, "
        "or GROK_API_KEY or XAI_API_KEY. JESSA tries providers in JESSA_LLM_PROVIDER_PRIORITY order."
    )


async def _database_exception_handler(_: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={
            "detail": _database_setup_message(),
            "error": str(exc),
            "startup": STARTUP_STATUS,
        },
    )


app.add_exception_handler(DatabaseConfigError, _database_exception_handler)
if db_module.psycopg is not None:
    app.add_exception_handler(db_module.psycopg.OperationalError, _database_exception_handler)


class ImportRequest(BaseModel):
    url: str = ""
    text: str = ""
    method: str = "http"


class ProfileUpdate(BaseModel):
    content: str


class SettingsUpdate(BaseModel):
    values: dict[str, str]


class LinkedInProfileUpdate(BaseModel):
    url: str = ""
    title: str = ""
    content: str = ""


class LinkedInProfileFetch(BaseModel):
    url: str = ""


class JobUpdate(BaseModel):
    title: str | None = None
    company: str | None = None
    location: str | None = None
    salary: str | None = None
    posted_date: str | None = None
    description: str | None = None
    status: str | None = None
    apply_url: str | None = None


class SupplementalRequest(BaseModel):
    questions_text: str


class ArtifactUpdate(BaseModel):
    title: str | None = None
    content: str | None = None
    is_submitted: bool | None = None


class BulkJobRequest(BaseModel):
    job_ids: list[int]


class BulkStatusUpdate(BulkJobRequest):
    status: str


JOB_STATUS_VALUES = {
    "new",
    "not_applied",
    "tailor",
    "ready",
    "applied",
    "follow-up",
    "interview",
    "on_hold",
    "job_expired",
    "not_for_me",
    "rejected",
}

EMAIL_STATUS_CLASSIFICATIONS = {
    "application_confirmation": "applied",
    "assessment_request": "follow-up",
    "interview_request": "interview",
    "rejection": "rejected",
}

EMAIL_STATUS_RANK = {
    "new": 0,
    "not_applied": 0,
    "tailor": 1,
    "ready": 2,
    "on_hold": 2,
    "applied": 3,
    "follow-up": 4,
    "interview": 5,
    "not_for_me": 99,
    "job_expired": 99,
    "rejected": 99,
}

EMAIL_STATUS_MIN_CLASSIFICATION_CONFIDENCE = 0.78
EMAIL_STATUS_MIN_MATCH_CONFIDENCE = 0.70
EMAIL_DISPLAY_MIN_MATCH_CONFIDENCE = 0.55
EMAIL_METADATA_REFRESH_LIMIT = 500

JOB_LIST_VIEWS = {
    JOB_LIFECYCLE_ACTIVE,
    JOB_LIFECYCLE_ARCHIVED,
    JOB_LIFECYCLE_TRASH,
    "all",
}


def _utc_datetime() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _utc_stamp(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def _trash_window() -> tuple[str, str]:
    now = _utc_datetime()
    return _utc_stamp(now), _utc_stamp(now + timedelta(hours=TRASH_RETENTION_HOURS))


def _parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _auto_archive_status(status: str | None) -> bool:
    return str(status or "").strip() in AUTO_ARCHIVE_STATUSES


def _email_suggested_status(message: Any) -> str:
    if float(getattr(message, "confidence", 0.0) or 0.0) < EMAIL_STATUS_MIN_CLASSIFICATION_CONFIDENCE:
        return ""
    if float(getattr(message, "match_confidence", 0.0) or 0.0) < EMAIL_STATUS_MIN_MATCH_CONFIDENCE:
        return ""
    return EMAIL_STATUS_CLASSIFICATIONS.get(str(getattr(message, "classification", "") or ""), "")


def _email_has_display_match(message: dict[str, Any]) -> bool:
    return (
        bool(message.get("job_id"))
        and float(message.get("match_confidence") or 0.0) >= EMAIL_DISPLAY_MIN_MATCH_CONFIDENCE
        and bool(str(message.get("match_reason") or "").strip())
    )


def _email_rows_to_dicts(rows: Any) -> list[dict[str, Any]]:
    messages = rows_to_dicts(rows)
    for message in messages:
        if _email_has_display_match(message):
            continue
        message["job_id"] = None
        message["job_title"] = ""
        message["job_company"] = ""
        message["match_confidence"] = 0.0
        message["match_reason"] = ""
    return messages


def _refresh_email_review_metadata(conn) -> int:
    jobs = rows_to_dicts(
        conn.execute(
            "SELECT id, title, company FROM jobs WHERE lifecycle_state <> ?",
            (JOB_LIFECYCLE_TRASH,),
        ).fetchall()
    )
    rows = rows_to_dicts(
        conn.execute(
            """
            SELECT id, subject, sender, raw_excerpt
            FROM emails
            ORDER BY id DESC
            LIMIT ?
            """,
            (EMAIL_METADATA_REFRESH_LIMIT,),
        ).fetchall()
    )
    for row in rows:
        body = str(row.get("raw_excerpt") or "")
        classification, confidence, summary = classify(
            str(row.get("subject") or ""),
            body,
            str(row.get("sender") or ""),
        )
        matched = match_job(
            str(row.get("subject") or ""),
            body,
            str(row.get("sender") or ""),
            jobs,
        )
        conn.execute(
            """
            UPDATE emails
            SET job_id = ?, classification = ?, confidence = ?,
                match_confidence = ?, match_reason = ?, summary = ?
            WHERE id = ?
            """,
            (
                matched.job_id,
                classification,
                confidence,
                matched.confidence,
                matched.reason,
                summary,
                int(row["id"]),
            ),
        )
    return len(rows)


def _should_apply_email_status(current_status: str, suggested_status: str) -> bool:
    if not suggested_status:
        return False
    if suggested_status == "rejected":
        return current_status not in {"rejected", "not_for_me", "job_expired"}
    if current_status in AUTO_ARCHIVE_STATUSES:
        return False
    return EMAIL_STATUS_RANK.get(suggested_status, 0) > EMAIL_STATUS_RANK.get(current_status or "new", 0)


def _apply_email_status(conn, message: Any) -> str:
    job_id = getattr(message, "job_id", None)
    if not job_id:
        return ""
    suggested_status = _email_suggested_status(message)
    if not suggested_status:
        return ""
    job = conn.execute(
        "SELECT id, status, lifecycle_state FROM jobs WHERE id = ?",
        (int(job_id),),
    ).fetchone()
    if not job or job.get("lifecycle_state") == JOB_LIFECYCLE_TRASH:
        return ""
    current_status = str(job.get("status") or "new")
    if not _should_apply_email_status(current_status, suggested_status):
        return ""
    now = utc_now()
    if _auto_archive_status(suggested_status):
        conn.execute(
            """
            UPDATE jobs
            SET status = ?, status_updated_at = ?, updated_at = ?,
                lifecycle_state = ?, archived_at = ?, trashed_at = NULL,
                purge_after = NULL, previous_lifecycle_state = NULL
            WHERE id = ?
            """,
            (suggested_status, now, now, JOB_LIFECYCLE_ARCHIVED, now, int(job_id)),
        )
        log_event(conn, int(job_id), "archived", "Auto archived after email terminal status.")
    else:
        conn.execute(
            "UPDATE jobs SET status = ?, status_updated_at = ?, updated_at = ? WHERE id = ?",
            (suggested_status, now, now, int(job_id)),
        )
    note = f"{current_status} -> {suggested_status}"
    log_event(
        conn,
        int(job_id),
        "status",
        f"{note} from email:{getattr(message, 'classification', '')}",
    )
    return note


def _normalize_job_ids(job_ids: list[int]) -> list[int]:
    normalized = sorted({int(job_id) for job_id in job_ids if int(job_id) > 0})
    if not normalized:
        raise HTTPException(status_code=400, detail="Select at least one job.")
    if len(normalized) > 250:
        raise HTTPException(status_code=400, detail="Bulk actions are limited to 250 jobs at a time.")
    return normalized


def _where_ids(ids: list[int]) -> tuple[str, list[int]]:
    return ", ".join("?" for _ in ids), ids


def _profile(conn) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM core_profile WHERE id = 1").fetchone()
    item = row_to_dict(row)
    if not item:
        raise HTTPException(status_code=500, detail="Core profile is missing.")
    return item


def _is_default_profile(content: str) -> bool:
    return content.strip() == DEFAULT_CORE_PROFILE.strip()


def _runtime_counts(conn) -> dict[str, int]:
    tables = ("jobs", "application_artifacts", "emails")
    counts: dict[str, int] = {}
    for table in tables:
        row = conn.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()
        counts[table] = int(row["count"] or 0)
    return counts


def _linkedin_profile(conn) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM linkedin_profile_cache WHERE id = 1").fetchone()
    item = row_to_dict(row)
    if item:
        return item
    return {
        "id": 1,
        "url": config.LINKEDIN_PROFILE_URL,
        "title": "",
        "content": "",
        "fetched_at": None,
        "updated_at": None,
    }


def _upsert_linkedin_profile(conn, url: str, title: str, content: str, fetched_at: str | None = None) -> dict[str, Any]:
    now = utc_now()
    conn.execute(
        """
        INSERT INTO linkedin_profile_cache (id, url, title, content, fetched_at, updated_at)
        VALUES (1, ?, ?, ?, ?, ?)
        ON CONFLICT (id) DO UPDATE SET
            url = EXCLUDED.url,
            title = EXCLUDED.title,
            content = EXCLUDED.content,
            fetched_at = EXCLUDED.fetched_at,
            updated_at = EXCLUDED.updated_at
        """,
        (url, title, content, fetched_at, now),
    )
    return _linkedin_profile(conn)


def _profile_context(conn) -> tuple[dict[str, Any], str]:
    profile = _profile(conn)
    linkedin = _linkedin_profile(conn)
    profile_text = str(profile["content"])
    linkedin_content = str(linkedin.get("content") or "").strip()
    if linkedin_content:
        profile_text = (
            f"{profile_text}\n\n"
            "# Cached LinkedIn Profile Context\n\n"
            f"Source URL: {linkedin.get('url') or 'Not recorded'}\n"
            f"Cached At: {linkedin.get('fetched_at') or linkedin.get('updated_at') or 'Not recorded'}\n\n"
            f"{linkedin_content}"
        )
    return profile, profile_text


def _llm_context(conn) -> tuple[dict[str, Any], str, str]:
    profile, profile_text = _profile_context(conn)
    return profile, profile_text, get_app_prompt(conn)


def _build_startup_status(initialize: bool = False) -> dict[str, Any]:
    issues: list[str] = []
    active_provider = config.active_llm_provider()
    provider_status = config.llm_provider_status()
    llm_ready = bool(active_provider)
    if not llm_ready:
        issues.append(_llm_setup_message())

    database_ready = False
    database_error = ""
    onboarding_required = False
    profile_is_default = False
    counts: dict[str, int] = {}

    if not config.postgres_configured():
        database_error = _database_setup_message()
        issues.insert(0, database_error)
    else:
        try:
            if initialize:
                init_db()
            database_ready = True
            with get_db() as conn:
                if initialize:
                    _refresh_email_review_metadata(conn)
                profile = _profile(conn)
                profile_is_default = _is_default_profile(str(profile.get("content") or ""))
                counts = _runtime_counts(conn)
                onboarding_required = profile_is_default and counts.get("jobs", 0) == 0
        except Exception as exc:
            database_error = f"{_database_setup_message()} Last error: {exc}"
            issues.insert(0, database_error)

    if onboarding_required:
        issues.append("First run detected. Add the candidate profile before importing jobs.")

    return {
        "version": __version__,
        "database_ready": database_ready,
        "database_error": database_error,
        "postgres_configured": config.postgres_configured(),
        "postgres_host": config.POSTGRES_HOST,
        "postgres_port": config.POSTGRES_PORT,
        "postgres_database": config.POSTGRES_DB_NAME,
        "llm_ready": llm_ready,
        "active_llm_provider": active_provider["name"] if active_provider else None,
        "active_llm_model": active_provider["model"] if active_provider else None,
        "llm_provider_priority": list(config.LLM_PROVIDER_PRIORITY),
        "llm_providers": provider_status,
        "setup_required": (not database_ready) or (not llm_ready) or onboarding_required,
        "onboarding_required": onboarding_required,
        "profile_is_default": profile_is_default,
        "counts": counts,
        "issues": issues,
    }


def _get_job(conn, job_id: int) -> dict[str, Any]:
    purge_expired_trashed_jobs(conn)
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    item = row_to_dict(row)
    if not item:
        raise HTTPException(status_code=404, detail="Job not found.")
    return item


def _job_details(conn, job_id: int) -> dict[str, Any]:
    job = _get_job(conn, job_id)
    events = conn.execute(
        "SELECT * FROM job_events WHERE job_id = ? ORDER BY created_at DESC, id DESC",
        (job_id,),
    ).fetchall()
    emails = conn.execute(
        """
        SELECT *
        FROM emails
        WHERE job_id = ?
          AND match_confidence >= ?
          AND COALESCE(match_reason, '') <> ''
        ORDER BY received_at DESC, id DESC
        """,
        (job_id, EMAIL_DISPLAY_MIN_MATCH_CONFIDENCE),
    ).fetchall()
    artifacts = conn.execute(
        "SELECT * FROM application_artifacts WHERE job_id = ? ORDER BY created_at DESC, id DESC",
        (job_id,),
    ).fetchall()
    job["events"] = rows_to_dicts(events)
    job["emails"] = _email_rows_to_dicts(emails)
    job["artifacts"] = rows_to_dicts(artifacts)
    return job


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.lower()).strip("-")
    return slug or "jessa-document"


def _artifact(conn, artifact_id: int) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM application_artifacts WHERE id = ?", (artifact_id,)).fetchone()
    item = row_to_dict(row)
    if not item:
        raise HTTPException(status_code=404, detail="Artifact not found.")
    return item


def _insert_artifact(
    conn,
    job_id: int,
    artifact_type: str,
    title: str,
    content: str,
    profile_version: int | None,
) -> int:
    row = conn.execute(
        "SELECT COALESCE(MAX(version), 0) AS version FROM application_artifacts WHERE job_id = ? AND artifact_type = ?",
        (job_id, artifact_type),
    ).fetchone()
    version = int(row["version"] or 0) + 1
    now = utc_now()
    cur = conn.execute(
        """
        INSERT INTO application_artifacts (
            job_id, artifact_type, title, content, format, version,
            source_profile_version, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, 'markdown', ?, ?, ?, ?)
        RETURNING id
        """,
        (job_id, artifact_type, title, content, version, profile_version, now, now),
    )
    log_event(conn, job_id, f"artifact:{artifact_type}", f"Created {title} v{version}")
    row = cur.fetchone()
    return int(row["id"])


def _insert_or_update_job(conn, imported: ImportedJob) -> int:
    now = utc_now()
    existing = None
    if imported.url:
        existing = conn.execute("SELECT id FROM jobs WHERE url = ?", (imported.url,)).fetchone()
    values = (
        imported.source,
        imported.url or None,
        imported.apply_url,
        imported.title,
        imported.company,
        imported.location,
        imported.salary,
        imported.posted_date,
        imported.description,
        now,
        now,
        now,
    )
    if existing:
        job_id = int(existing["id"])
        conn.execute(
            """
            UPDATE jobs
            SET source = ?, url = ?, apply_url = ?, title = ?, company = ?, location = ?,
                salary = ?, posted_date = ?, description = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                imported.source,
                imported.url or None,
                imported.apply_url,
                imported.title,
                imported.company,
                imported.location,
                imported.salary,
                imported.posted_date,
                imported.description,
                now,
                job_id,
            ),
        )
        log_event(conn, job_id, "updated", imported.extraction_note)
        return job_id
    cur = conn.execute(
        """
        INSERT INTO jobs (
            source, url, apply_url, title, company, location, salary, posted_date,
            description, status_updated_at, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        RETURNING id
        """,
        values,
    )
    row = cur.fetchone()
    job_id = int(row["id"])
    log_event(conn, job_id, "imported", imported.extraction_note)
    return job_id


@app.on_event("startup")
def startup() -> None:
    global STARTUP_STATUS
    STARTUP_STATUS = _build_startup_status(initialize=True)


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "version": __version__,
            "model": STARTUP_STATUS.get("active_llm_model") or config.OPENAI_MODEL,
            "email_user": config.EMAIL_USER,
        },
    )


@app.get("/api/health")
def health() -> dict[str, Any]:
    linkedin_cached = False
    linkedin_url = config.LINKEDIN_PROFILE_URL
    if STARTUP_STATUS.get("database_ready"):
        with get_db() as conn:
            linkedin = _linkedin_profile(conn)
            linkedin_cached = bool(str(linkedin.get("content") or "").strip())
            linkedin_url = str(linkedin.get("url") or linkedin_url)
    return {
        "ok": bool(STARTUP_STATUS.get("database_ready") and STARTUP_STATUS.get("llm_ready")),
        "version": __version__,
        "db_backend": config.DB_BACKEND,
        "postgres_configured": config.postgres_configured(),
        "postgres_host": config.POSTGRES_HOST,
        "postgres_port": config.POSTGRES_PORT,
        "postgres_database": config.POSTGRES_DB_NAME,
        "llm_configured": bool(STARTUP_STATUS.get("llm_ready")),
        "llm_active_provider": STARTUP_STATUS.get("active_llm_provider"),
        "llm_active_model": STARTUP_STATUS.get("active_llm_model"),
        "llm_provider_priority": STARTUP_STATUS.get("llm_provider_priority"),
        "llm_providers": STARTUP_STATUS.get("llm_providers"),
        "openai_configured": bool(config.OPENAI_API_KEY),
        "email_configured": bool(config.EMAIL_USER and config.EMAIL_PASSWORD),
        "email_imap_host": config.EMAIL_IMAP_HOST,
        "email_smtp_host": config.EMAIL_SMTP_HOST,
        "allowed_client_networks": config.allowed_client_networks(),
        "playwright_browser_path": config.PLAYWRIGHT_BROWSER_PATH,
        "playwright_rendered_headless": config.PLAYWRIGHT_RENDERED_HEADLESS,
        "linkedin_browser_headless": config.LINKEDIN_BROWSER_HEADLESS,
        "trash_retention_hours": TRASH_RETENTION_HOURS,
        "linkedin_profile_cached": linkedin_cached,
        "linkedin_profile_url": linkedin_url,
        "linkedin_browser_profile_dir": str(config.LINKEDIN_BROWSER_PROFILE_DIR),
        "setup_required": STARTUP_STATUS.get("setup_required"),
        "onboarding_required": STARTUP_STATUS.get("onboarding_required"),
        "startup_issues": STARTUP_STATUS.get("issues", []),
    }


@app.get("/api/startup")
def startup_status() -> dict[str, Any]:
    global STARTUP_STATUS
    STARTUP_STATUS = _build_startup_status(initialize=False)
    return STARTUP_STATUS


@app.get("/api/settings")
def get_settings() -> dict[str, Any]:
    return settings_payload()


@app.put("/api/settings")
def update_settings(update: SettingsUpdate) -> dict[str, Any]:
    return update_env_values(update.values)


@app.get("/api/profile")
def get_profile() -> dict[str, Any]:
    with get_db() as conn:
        return _profile(conn)


@app.put("/api/profile")
def update_profile(update: ProfileUpdate) -> dict[str, Any]:
    with get_db() as conn:
        current = _profile(conn)
        version = int(current["version"]) + 1
        conn.execute(
            "UPDATE core_profile SET content = ?, version = ?, updated_at = ? WHERE id = 1",
            (update.content, version, utc_now()),
        )
        return _profile(conn)


def _decode_profile_upload(upload: UploadFile, data: bytes) -> str:
    name = (upload.filename or "resume").lower()
    if name.endswith(".pdf"):
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise HTTPException(
                status_code=422,
                detail="PDF resume import requires pypdf. Run python -m pip install -r requirements.txt.",
            ) from exc
        reader = PdfReader(BytesIO(data))
        pages = [page.extract_text() or "" for page in reader.pages]
        return "\n\n".join(page.strip() for page in pages if page.strip())
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("latin-1", errors="replace")


@app.post("/api/profile/import")
async def import_profile(
    resume_text: str = Form(""),
    mode: str = Form("append"),
    resume_file: UploadFile | None = File(None),
) -> dict[str, Any]:
    parts: list[str] = []
    if resume_text.strip():
        parts.append(resume_text.strip())
    if resume_file and resume_file.filename:
        data = await resume_file.read()
        if data:
            decoded = _decode_profile_upload(resume_file, data).strip()
            if decoded:
                parts.append(f"## Uploaded Resume: {resume_file.filename}\n\n{decoded}")
    imported = "\n\n".join(parts).strip()
    if not imported:
        raise HTTPException(status_code=400, detail="Paste profile text or choose a resume file first.")
    with get_db() as conn:
        current = _profile(conn)
        current_content = str(current.get("content") or "")
        replace = mode.strip().lower() == "replace" or _is_default_profile(current_content)
        if replace:
            content = (
                "# Candidate Core Profile\n\n"
                "Review and edit this imported profile before generating application materials.\n\n"
                "## Imported Resume/Profile\n\n"
                f"{imported}\n"
            )
        else:
            content = (
                f"{current_content.rstrip()}\n\n"
                "# Imported Resume/Profile\n\n"
                "Review and merge this imported material into the canonical sections above.\n\n"
                f"{imported}\n"
            )
        version = int(current["version"]) + 1
        conn.execute(
            "UPDATE core_profile SET content = ?, version = ?, updated_at = ? WHERE id = 1",
            (content, version, utc_now()),
        )
        return _profile(conn)


@app.get("/api/linkedin-profile")
def get_linkedin_profile() -> dict[str, Any]:
    with get_db() as conn:
        return _linkedin_profile(conn)


@app.put("/api/linkedin-profile")
def update_linkedin_profile(update: LinkedInProfileUpdate) -> dict[str, Any]:
    with get_db() as conn:
        return _upsert_linkedin_profile(
            conn,
            update.url.strip(),
            update.title.strip(),
            update.content.strip(),
            utc_now(),
        )


@app.post("/api/linkedin-profile/fetch")
async def fetch_cached_linkedin_profile(request: LinkedInProfileFetch) -> dict[str, Any]:
    url = request.url.strip() or config.LINKEDIN_PROFILE_URL
    if not url:
        with get_db() as conn:
            url = str(_linkedin_profile(conn).get("url") or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="Provide the candidate's LinkedIn profile URL first.")
    try:
        snapshot = await fetch_linkedin_profile(url)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if len(snapshot.content.strip()) < config.LINKEDIN_MIN_PROFILE_CONTENT_CHARS:
        raise HTTPException(status_code=422, detail="LinkedIn profile capture did not collect enough text.")
    with get_db() as conn:
        return _upsert_linkedin_profile(
            conn,
            snapshot.url,
            snapshot.title,
            snapshot.content,
            utc_now(),
        )


@app.get("/api/jobs")
def list_jobs(view: str = JOB_LIFECYCLE_ACTIVE, status: str = "") -> list[dict[str, Any]]:
    view = view.strip().lower()
    if view not in JOB_LIST_VIEWS:
        raise HTTPException(status_code=400, detail="Unknown jobs view.")
    status = status.strip()
    conditions = []
    params: list[Any] = []
    order_clause = "updated_at DESC, id DESC"
    if view in {JOB_LIFECYCLE_ACTIVE, JOB_LIFECYCLE_ARCHIVED, JOB_LIFECYCLE_TRASH}:
        conditions.append("lifecycle_state = ?")
        params.append(view)
    if status and status != "all":
        if status not in JOB_STATUS_VALUES:
            raise HTTPException(status_code=400, detail="Unknown job status.")
        conditions.append("status = ?")
        params.append(status)
    if view == JOB_LIFECYCLE_ARCHIVED:
        order_clause = "archived_at DESC NULLS LAST, updated_at DESC, id DESC"
    elif view == JOB_LIFECYCLE_TRASH:
        order_clause = "trashed_at DESC NULLS LAST, updated_at DESC, id DESC"
    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    with get_db() as conn:
        purge_expired_trashed_jobs(conn)
        rows = conn.execute(
            f"""
            SELECT id, source, title, company, location, salary, status, match_score,
                   qualification_band, interview_odds, salary_ask_range, resume_base,
                   recommendation, lifecycle_state, archived_at, trashed_at, purge_after,
                   previous_lifecycle_state, created_at, updated_at
            FROM jobs
            {where_clause}
            ORDER BY {order_clause}
            """,
            params,
        ).fetchall()
        return rows_to_dicts(rows)


@app.post("/api/jobs/import")
async def import_job(request: ImportRequest) -> dict[str, Any]:
    if request.url.strip():
        try:
            imported = await import_from_url(request.url.strip(), request.method)
        except Exception as exc:
            if not request.text.strip():
                raise HTTPException(status_code=422, detail=f"URL import failed: {exc}") from exc
            imported = import_from_text(request.text)
            imported.url = request.url.strip()
            imported.apply_url = request.url.strip()
            imported.extraction_note = f"URL import failed; stored pasted text instead. Error: {exc}"
    elif request.text.strip():
        imported = import_from_text(request.text)
    else:
        raise HTTPException(status_code=400, detail="Provide a URL or pasted job text.")

    with get_db() as conn:
        job_id = _insert_or_update_job(conn, imported)
        return _get_job(conn, job_id)


@app.post("/api/jobs/bulk/trash")
def bulk_trash_jobs(request: BulkJobRequest) -> dict[str, Any]:
    ids = _normalize_job_ids(request.job_ids)
    placeholders, values = _where_ids(ids)
    with get_db() as conn:
        rows = conn.execute(
            f"SELECT id, lifecycle_state FROM jobs WHERE id IN ({placeholders}) ORDER BY id",
            values,
        ).fetchall()
        now, purge_after = _trash_window()
        updated = 0
        skipped = 0
        for row in rows:
            job_id = int(row["id"])
            lifecycle_state = row.get("lifecycle_state") or JOB_LIFECYCLE_ACTIVE
            if lifecycle_state == JOB_LIFECYCLE_TRASH:
                skipped += 1
                continue
            conn.execute(
                """
                UPDATE jobs
                SET lifecycle_state = ?, previous_lifecycle_state = ?, trashed_at = ?,
                    purge_after = ?, updated_at = ?
                WHERE id = ?
                """,
                (JOB_LIFECYCLE_TRASH, lifecycle_state, now, purge_after, now, job_id),
            )
            log_event(conn, job_id, "trashed", f"Bulk trash; recoverable until {purge_after}.")
            updated += 1
        return {
            "requested": len(ids),
            "matched": len(rows),
            "updated": updated,
            "skipped": skipped + (len(ids) - len(rows)),
        }


@app.put("/api/jobs/bulk/status")
def bulk_update_job_status(request: BulkStatusUpdate) -> dict[str, Any]:
    ids = _normalize_job_ids(request.job_ids)
    status = request.status.strip()
    if status not in JOB_STATUS_VALUES:
        raise HTTPException(status_code=400, detail="Unknown job status.")
    placeholders, values = _where_ids(ids)
    with get_db() as conn:
        rows = conn.execute(
            f"SELECT id, status, lifecycle_state FROM jobs WHERE id IN ({placeholders}) ORDER BY id",
            values,
        ).fetchall()
        now = utc_now()
        auto_archive = _auto_archive_status(status)
        updated = 0
        archived = 0
        skipped = len(ids) - len(rows)
        for row in rows:
            job_id = int(row["id"])
            if row.get("lifecycle_state") == JOB_LIFECYCLE_TRASH:
                skipped += 1
                continue
            if auto_archive:
                conn.execute(
                    """
                    UPDATE jobs
                    SET status = ?, status_updated_at = ?, updated_at = ?,
                        lifecycle_state = ?, archived_at = ?,
                        trashed_at = NULL, purge_after = NULL, previous_lifecycle_state = NULL
                    WHERE id = ?
                    """,
                    (status, now, now, JOB_LIFECYCLE_ARCHIVED, now, job_id),
                )
                archived += 1
            else:
                conn.execute(
                    "UPDATE jobs SET status = ?, status_updated_at = ?, updated_at = ? WHERE id = ?",
                    (status, now, now, job_id),
                )
            log_event(conn, job_id, "status", f"{row.get('status', '')} -> {status}")
            if auto_archive:
                log_event(conn, job_id, "archived", "Auto archived after bulk terminal status.")
            updated += 1
        return {
            "requested": len(ids),
            "matched": len(rows),
            "updated": updated,
            "archived": archived,
            "skipped": skipped,
        }


@app.get("/api/jobs/{job_id}")
def get_job(job_id: int) -> dict[str, Any]:
    with get_db() as conn:
        return _job_details(conn, job_id)


@app.put("/api/jobs/{job_id}")
def update_job(job_id: int, update: JobUpdate) -> dict[str, Any]:
    allowed = update.model_dump(exclude_unset=True)
    if not allowed:
        with get_db() as conn:
            return _get_job(conn, job_id)
    with get_db() as conn:
        before = _get_job(conn, job_id)
        if before.get("lifecycle_state") == JOB_LIFECYCLE_TRASH:
            raise HTTPException(status_code=409, detail="Recover this job before editing it.")
        now = utc_now()
        fields = []
        values: list[Any] = []
        for key, value in allowed.items():
            fields.append(f"{key} = ?")
            values.append(value or "")
        fields.append("updated_at = ?")
        values.append(now)
        auto_archive = False
        if "status" in allowed:
            status_value = allowed["status"] or ""
            auto_archive = _auto_archive_status(status_value)
            fields.append("status_updated_at = ?")
            values.append(now)
            if auto_archive:
                fields.extend(
                    [
                        "lifecycle_state = ?",
                        "archived_at = ?",
                        "trashed_at = NULL",
                        "purge_after = NULL",
                        "previous_lifecycle_state = NULL",
                    ]
                )
                values.extend([JOB_LIFECYCLE_ARCHIVED, now])
        values.append(job_id)
        conn.execute(f"UPDATE jobs SET {', '.join(fields)} WHERE id = ?", values)
        if "status" in allowed:
            log_event(conn, job_id, "status", f"{before.get('status', '')} -> {allowed['status'] or ''}")
            if auto_archive:
                log_event(conn, job_id, "archived", "Auto archived after terminal status.")
        else:
            log_event(conn, job_id, "edited", "Job fields updated.")
        return _get_job(conn, job_id)


@app.post("/api/jobs/{job_id}/archive")
def archive_job(job_id: int) -> dict[str, Any]:
    with get_db() as conn:
        job = _get_job(conn, job_id)
        if job.get("lifecycle_state") == JOB_LIFECYCLE_TRASH:
            raise HTTPException(status_code=409, detail="Recover this job before archiving it.")
        now = utc_now()
        conn.execute(
            """
            UPDATE jobs
            SET lifecycle_state = ?, archived_at = ?, updated_at = ?,
                trashed_at = NULL, purge_after = NULL, previous_lifecycle_state = NULL
            WHERE id = ?
            """,
            (JOB_LIFECYCLE_ARCHIVED, now, now, job_id),
        )
        log_event(conn, job_id, "archived", "Manually archived.")
        return _job_details(conn, job_id)


@app.post("/api/jobs/{job_id}/restore")
def restore_job(job_id: int) -> dict[str, Any]:
    with get_db() as conn:
        job = _get_job(conn, job_id)
        if job.get("lifecycle_state") == JOB_LIFECYCLE_TRASH:
            raise HTTPException(status_code=409, detail="Use recover for trashed jobs.")
        now = utc_now()
        conn.execute(
            """
            UPDATE jobs
            SET lifecycle_state = ?, archived_at = NULL, updated_at = ?
            WHERE id = ?
            """,
            (JOB_LIFECYCLE_ACTIVE, now, job_id),
        )
        log_event(conn, job_id, "restored", "Restored to active jobs.")
        return _job_details(conn, job_id)


@app.delete("/api/jobs/{job_id}")
def trash_job(job_id: int) -> dict[str, Any]:
    with get_db() as conn:
        job = _get_job(conn, job_id)
        if job.get("lifecycle_state") == JOB_LIFECYCLE_TRASH:
            return _job_details(conn, job_id)
        now, purge_after = _trash_window()
        previous_state = job.get("lifecycle_state") or JOB_LIFECYCLE_ACTIVE
        if previous_state == JOB_LIFECYCLE_TRASH:
            previous_state = JOB_LIFECYCLE_ACTIVE
        conn.execute(
            """
            UPDATE jobs
            SET lifecycle_state = ?, previous_lifecycle_state = ?, trashed_at = ?,
                purge_after = ?, updated_at = ?
            WHERE id = ?
            """,
            (JOB_LIFECYCLE_TRASH, previous_state, now, purge_after, now, job_id),
        )
        log_event(conn, job_id, "trashed", f"Recoverable until {purge_after}.")
        return _job_details(conn, job_id)


@app.post("/api/jobs/{job_id}/recover")
def recover_job(job_id: int) -> dict[str, Any]:
    with get_db() as conn:
        job = _get_job(conn, job_id)
        if job.get("lifecycle_state") != JOB_LIFECYCLE_TRASH:
            return _job_details(conn, job_id)
        purge_after = _parse_utc(job.get("purge_after"))
        if purge_after and purge_after <= _utc_datetime():
            conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
            raise HTTPException(status_code=410, detail="Recovery window expired; job was purged.")
        restore_state = job.get("previous_lifecycle_state") or JOB_LIFECYCLE_ACTIVE
        if restore_state not in {JOB_LIFECYCLE_ACTIVE, JOB_LIFECYCLE_ARCHIVED}:
            restore_state = JOB_LIFECYCLE_ACTIVE
        now = utc_now()
        archived_at_update = "archived_at = archived_at"
        values: list[Any] = [restore_state, now]
        if restore_state == JOB_LIFECYCLE_ACTIVE:
            archived_at_update = "archived_at = NULL"
        elif not job.get("archived_at"):
            archived_at_update = "archived_at = ?"
            values.append(now)
        values.append(job_id)
        conn.execute(
            f"""
            UPDATE jobs
            SET lifecycle_state = ?, trashed_at = NULL, purge_after = NULL,
                previous_lifecycle_state = NULL, updated_at = ?, {archived_at_update}
            WHERE id = ?
            """,
            values,
        )
        log_event(conn, job_id, "recovered", f"Recovered to {restore_state} jobs.")
        return _job_details(conn, job_id)


@app.post("/api/jobs/{job_id}/analyze")
def analyze(job_id: int) -> dict[str, Any]:
    with get_db() as conn:
        job = _get_job(conn, job_id)
        profile, profile_text, app_prompt = _llm_context(conn)
        result = analyze_job(job, profile_text, app_prompt)
        payload = result.model_dump()
        conn.execute(
            """
            UPDATE jobs
            SET match_score = ?, qualification_band = ?, interview_odds = ?,
                interview_confidence = ?, salary_ask_range = ?, salary_floor = ?,
                resume_base = ?, recommendation = ?, analysis_summary = ?,
                analysis_json = ?, cover_letter = ?, resume_notes = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                payload["match_score"],
                payload["qualification_band"],
                payload["interview_odds"],
                payload["interview_confidence"],
                payload["salary_target"]["ask_range"],
                payload["salary_target"]["floor"],
                payload["resume_base"],
                payload["recommendation"],
                payload["analysis_summary"],
                json.dumps(payload),
                payload["cover_letter"],
                payload["tailored_resume_notes"],
                utc_now(),
                job_id,
            ),
        )
        log_event(conn, job_id, "analyzed", f"{payload['match_score']}% {payload['recommendation']}")
        updated_job = _get_job(conn, job_id)
        package = generate_application_package(updated_job, profile_text, payload, app_prompt)
        resume_id = _insert_artifact(
            conn,
            job_id,
            "resume",
            package.resume_title,
            package.resume_markdown,
            int(profile["version"]),
        )
        cover_id = _insert_artifact(
            conn,
            job_id,
            "cover_letter",
            package.cover_letter_title,
            package.cover_letter_markdown,
            int(profile["version"]),
        )
        conn.execute(
            "UPDATE jobs SET cover_letter = ?, resume_notes = ?, updated_at = ? WHERE id = ?",
            (
                package.cover_letter_markdown,
                package.notes or "Full tailored resume generated in Artifacts.",
                utc_now(),
                job_id,
            ),
        )
        log_event(conn, job_id, "package_generated", f"Auto-generated resume artifact #{resume_id}; cover letter artifact #{cover_id}")
        return _job_details(conn, job_id)


@app.post("/api/jobs/{job_id}/generate-package")
def generate_package(job_id: int) -> dict[str, Any]:
    with get_db() as conn:
        job = _get_job(conn, job_id)
        profile, profile_text, app_prompt = _llm_context(conn)
        package = generate_application_package(
            job,
            profile_text,
            job.get("analysis_json") if isinstance(job.get("analysis_json"), dict) else None,
            app_prompt,
        )
        resume_id = _insert_artifact(
            conn,
            job_id,
            "resume",
            package.resume_title,
            package.resume_markdown,
            int(profile["version"]),
        )
        cover_id = _insert_artifact(
            conn,
            job_id,
            "cover_letter",
            package.cover_letter_title,
            package.cover_letter_markdown,
            int(profile["version"]),
        )
        conn.execute(
            "UPDATE jobs SET cover_letter = ?, resume_notes = ?, updated_at = ? WHERE id = ?",
            (package.cover_letter_markdown, package.notes or "Full tailored resume generated in Artifacts.", utc_now(), job_id),
        )
        log_event(conn, job_id, "package_generated", f"Resume artifact #{resume_id}; cover letter artifact #{cover_id}")
        return _job_details(conn, job_id)


@app.post("/api/jobs/{job_id}/supplemental")
def generate_supplemental(job_id: int, request: SupplementalRequest) -> dict[str, Any]:
    if not request.questions_text.strip():
        raise HTTPException(status_code=400, detail="Paste at least one supplemental question.")
    with get_db() as conn:
        job = _get_job(conn, job_id)
        profile, profile_text, app_prompt = _llm_context(conn)
        artifacts = rows_to_dicts(
            conn.execute(
                """
                SELECT artifact_type, title, content, version, is_submitted
                FROM application_artifacts
                WHERE job_id = ?
                ORDER BY created_at DESC
                LIMIT 6
                """,
                (job_id,),
            ).fetchall()
        )
        answers = answer_supplemental_questions(job, profile_text, request.questions_text, artifacts, app_prompt)
        artifact_id = _insert_artifact(
            conn,
            job_id,
            "supplemental_answers",
            answers.title,
            answers.markdown,
            int(profile["version"]),
        )
        conn.execute(
            "UPDATE application_artifacts SET content = ? WHERE id = ?",
            (answers.markdown, artifact_id),
        )
        log_event(conn, job_id, "supplemental_generated", f"Supplemental answer artifact #{artifact_id}")
        return _job_details(conn, job_id)


@app.put("/api/artifacts/{artifact_id}")
def update_artifact(artifact_id: int, update: ArtifactUpdate) -> dict[str, Any]:
    allowed = update.model_dump(exclude_unset=True)
    if not allowed:
        with get_db() as conn:
            return _artifact(conn, artifact_id)
    with get_db() as conn:
        artifact = _artifact(conn, artifact_id)
        fields = []
        values: list[Any] = []
        for key, value in allowed.items():
            if key == "is_submitted":
                fields.append("is_submitted = ?")
                values.append(1 if value else 0)
                fields.append("submitted_at = ?")
                values.append(utc_now() if value else None)
            else:
                fields.append(f"{key} = ?")
                values.append(value or "")
        fields.append("updated_at = ?")
        values.append(utc_now())
        values.append(artifact_id)
        conn.execute(f"UPDATE application_artifacts SET {', '.join(fields)} WHERE id = ?", values)
        if "is_submitted" in allowed and allowed["is_submitted"]:
            log_event(conn, int(artifact["job_id"]), "artifact_submitted", f"{artifact['title']} marked submitted")
        else:
            log_event(conn, int(artifact["job_id"]), "artifact_edited", artifact["title"])
        return _artifact(conn, artifact_id)


@app.get("/api/artifacts/{artifact_id}/download.pdf")
def download_artifact_pdf(artifact_id: int) -> Response:
    with get_db() as conn:
        artifact = _artifact(conn, artifact_id)
    pdf = markdown_to_pdf(artifact["title"], artifact["content"])
    filename = f"{_slug(artifact['title'])}-v{artifact['version']}.pdf"
    return Response(
        pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/api/email/test")
def email_test() -> dict[str, Any]:
    try:
        test_smtp()
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"ok": True, "smtp_host": config.EMAIL_SMTP_HOST, "smtp_port": config.EMAIL_SMTP_PORT}


@app.post("/api/email/sync")
def email_sync() -> dict[str, Any]:
    with get_db() as conn:
        purge_expired_trashed_jobs(conn)
        jobs = rows_to_dicts(
            conn.execute(
                "SELECT id, title, company FROM jobs WHERE lifecycle_state <> ?",
                (JOB_LIFECYCLE_TRASH,),
            ).fetchall()
        )
        try:
            messages = sync_inbox(jobs)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        inserted = 0
        status_updates = 0
        for message in messages:
            cur = conn.execute(
                """
                INSERT INTO emails (
                    message_id, job_id, subject, sender, received_at, classification,
                    confidence, match_confidence, match_reason, status_action,
                    summary, raw_excerpt, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (message_id) DO NOTHING
                """,
                (
                    message.message_id,
                    message.job_id,
                    message.subject,
                    message.sender,
                    message.received_at,
                    message.classification,
                    message.confidence,
                    message.match_confidence,
                    message.match_reason,
                    "",
                    message.summary,
                    message.raw_excerpt,
                    utc_now(),
                ),
            )
            if cur.rowcount:
                inserted += 1
                if message.job_id:
                    log_event(
                        conn,
                        message.job_id,
                        f"email:{message.classification}",
                        f"{message.subject} ({message.match_confidence:.2f}: {message.match_reason or 'matched'})",
                    )
                    status_action = _apply_email_status(conn, message)
                    if status_action:
                        status_updates += 1
                        conn.execute(
                            "UPDATE emails SET status_action = ? WHERE message_id = ?",
                            (status_action, message.message_id),
                        )
            else:
                conn.execute(
                    """
                    UPDATE emails
                    SET job_id = ?, classification = ?, confidence = ?,
                        match_confidence = ?, match_reason = ?,
                        summary = ?, raw_excerpt = ?
                    WHERE message_id = ?
                    """,
                    (
                        message.job_id,
                        message.classification,
                        message.confidence,
                        message.match_confidence,
                        message.match_reason,
                        message.summary,
                        message.raw_excerpt,
                        message.message_id,
                    ),
                )
        recent = _email_rows_to_dicts(
            conn.execute(
                """
                SELECT e.*, j.title AS job_title, j.company AS job_company
                FROM emails e
                LEFT JOIN jobs j ON j.id = e.job_id
                ORDER BY e.received_at DESC, e.id DESC
                LIMIT 50
                """
            ).fetchall()
        )
        return {"fetched": len(messages), "inserted": inserted, "status_updates": status_updates, "emails": recent}


@app.get("/api/emails")
def list_emails() -> list[dict[str, Any]]:
    with get_db() as conn:
        return _email_rows_to_dicts(
            conn.execute(
                """
                SELECT e.*, j.title AS job_title, j.company AS job_company
                FROM emails e
                LEFT JOIN jobs j ON j.id = e.job_id
                ORDER BY e.received_at DESC, e.id DESC
                LIMIT 100
                """
            ).fetchall()
        )
