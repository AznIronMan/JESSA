from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from starlette.requests import Request

from . import __version__, config
from .db import get_db, init_db, log_event, row_to_dict, rows_to_dicts, utc_now
from .services.email_client import sync_inbox, test_smtp
from .services.importer import ImportedJob, import_from_text, import_from_url
from .services.llm import analyze_job, answer_supplemental_questions, generate_application_package
from .services.pdf import markdown_to_pdf


APP_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))

app = FastAPI(title="JESSA", version=__version__)
app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")


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


class ImportRequest(BaseModel):
    url: str = ""
    text: str = ""
    method: str = "http"


class ProfileUpdate(BaseModel):
    content: str


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


def _profile(conn) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM core_profile WHERE id = 1").fetchone()
    item = row_to_dict(row)
    if not item:
        raise HTTPException(status_code=500, detail="Core profile is missing.")
    return item


def _get_job(conn, job_id: int) -> dict[str, Any]:
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
        "SELECT * FROM emails WHERE job_id = ? ORDER BY received_at DESC, id DESC",
        (job_id,),
    ).fetchall()
    artifacts = conn.execute(
        "SELECT * FROM application_artifacts WHERE job_id = ? ORDER BY created_at DESC, id DESC",
        (job_id,),
    ).fetchall()
    job["events"] = rows_to_dicts(events)
    job["emails"] = rows_to_dicts(emails)
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
    init_db()


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "version": __version__,
            "model": config.OPENAI_MODEL,
            "email_user": config.EMAIL_USER,
        },
    )


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "version": __version__,
        "db_backend": config.DB_BACKEND,
        "postgres_configured": config.postgres_configured(),
        "postgres_host": config.POSTGRES_HOST,
        "postgres_port": config.POSTGRES_PORT,
        "postgres_database": config.POSTGRES_DB_NAME,
        "openai_configured": bool(config.OPENAI_API_KEY),
        "email_configured": bool(config.EMAIL_USER and config.EMAIL_PASSWORD),
        "email_imap_host": config.EMAIL_IMAP_HOST,
        "email_smtp_host": config.EMAIL_SMTP_HOST,
        "allowed_client_networks": config.allowed_client_networks(),
        "playwright_browser_path": config.PLAYWRIGHT_BROWSER_PATH,
    }


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


@app.get("/api/jobs")
def list_jobs() -> list[dict[str, Any]]:
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT id, source, title, company, location, salary, status, match_score,
                   qualification_band, interview_odds, salary_ask_range, resume_base,
                   recommendation, created_at, updated_at
            FROM jobs
            ORDER BY updated_at DESC, id DESC
            """
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
    fields = []
    values: list[Any] = []
    for key, value in allowed.items():
        fields.append(f"{key} = ?")
        values.append(value or "")
    fields.append("updated_at = ?")
    values.append(utc_now())
    if "status" in allowed:
        fields.append("status_updated_at = ?")
        values.append(utc_now())
    values.append(job_id)
    with get_db() as conn:
        before = _get_job(conn, job_id)
        conn.execute(f"UPDATE jobs SET {', '.join(fields)} WHERE id = ?", values)
        if "status" in allowed:
            log_event(conn, job_id, "status", f"{before.get('status', '')} -> {allowed['status'] or ''}")
        else:
            log_event(conn, job_id, "edited", "Job fields updated.")
        return _get_job(conn, job_id)


@app.post("/api/jobs/{job_id}/analyze")
def analyze(job_id: int) -> dict[str, Any]:
    with get_db() as conn:
        job = _get_job(conn, job_id)
        profile = _profile(conn)["content"]
        result = analyze_job(job, profile)
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
        return _get_job(conn, job_id)


@app.post("/api/jobs/{job_id}/generate-package")
def generate_package(job_id: int) -> dict[str, Any]:
    with get_db() as conn:
        job = _get_job(conn, job_id)
        profile = _profile(conn)
        package = generate_application_package(job, profile["content"], job.get("analysis_json") if isinstance(job.get("analysis_json"), dict) else None)
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
        profile = _profile(conn)
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
        answers = answer_supplemental_questions(job, profile["content"], request.questions_text, artifacts)
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
        jobs = rows_to_dicts(conn.execute("SELECT id, title, company FROM jobs").fetchall())
        try:
            messages = sync_inbox(jobs)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        inserted = 0
        for message in messages:
            cur = conn.execute(
                """
                INSERT INTO emails (
                    message_id, job_id, subject, sender, received_at, classification,
                    confidence, summary, raw_excerpt, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    message.summary,
                    message.raw_excerpt,
                    utc_now(),
                ),
            )
            if cur.rowcount:
                inserted += 1
                if message.job_id:
                    log_event(conn, message.job_id, f"email:{message.classification}", message.subject)
        recent = rows_to_dicts(
            conn.execute("SELECT * FROM emails ORDER BY received_at DESC, id DESC LIMIT 50").fetchall()
        )
        return {"fetched": len(messages), "inserted": inserted, "emails": recent}


@app.get("/api/emails")
def list_emails() -> list[dict[str, Any]]:
    with get_db() as conn:
        return rows_to_dicts(conn.execute("SELECT * FROM emails ORDER BY received_at DESC, id DESC LIMIT 100").fetchall())
