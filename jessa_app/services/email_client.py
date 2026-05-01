from __future__ import annotations

import email
import imaplib
import re
import smtplib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.header import decode_header
from email.message import Message
from email.utils import parsedate_to_datetime
from typing import Iterable

from .. import config


@dataclass
class ClassifiedEmail:
    message_id: str
    subject: str
    sender: str
    received_at: str
    classification: str
    confidence: float
    summary: str
    raw_excerpt: str
    job_id: int | None = None


def _decode(value: str | None) -> str:
    if not value:
        return ""
    parts = decode_header(value)
    decoded: list[str] = []
    for part, encoding in parts:
        if isinstance(part, bytes):
            decoded.append(part.decode(encoding or "utf-8", errors="replace"))
        else:
            decoded.append(part)
    return " ".join(decoded).strip()


def _body(message: Message) -> str:
    if message.is_multipart():
        for part in message.walk():
            content_type = part.get_content_type()
            disposition = part.get_content_disposition()
            if content_type == "text/plain" and disposition != "attachment":
                payload = part.get_payload(decode=True)
                if payload:
                    return payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        for part in message.walk():
            if part.get_content_type() == "text/html":
                payload = part.get_payload(decode=True)
                if payload:
                    text = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
                    return re.sub(r"<[^>]+>", " ", text)
        return ""
    payload = message.get_payload(decode=True)
    if not payload:
        return str(message.get_payload())
    return payload.decode(message.get_content_charset() or "utf-8", errors="replace")


def _clean(value: str, limit: int = 1000) -> str:
    text = re.sub(r"\s+", " ", value).strip()
    if len(text) > limit:
        return text[:limit].rsplit(" ", 1)[0] + "..."
    return text


def classify(subject: str, body: str) -> tuple[str, float, str]:
    text = f"{subject}\n{body}".lower()
    checks = [
        ("interview_request", 0.86, ["interview", "schedule a call", "availability", "phone screen", "next steps"]),
        ("assessment_request", 0.82, ["assessment", "coding challenge", "technical test", "take-home", "skills test"]),
        ("rejection", 0.84, ["unfortunately", "not selected", "not be moving forward", "pursue other candidates", "decided to move forward with other"]),
        ("application_confirmation", 0.80, ["application received", "received your application", "thank you for applying", "application has been submitted", "we have your application"]),
        ("recruiter_outreach", 0.62, ["opportunity", "contract role", "w2", "c2c", "recruiter", "staffing", "resume"]),
    ]
    for label, confidence, needles in checks:
        if any(needle in text for needle in needles):
            return label, confidence, _clean(body, 240)
    return "unclassified", 0.25, _clean(body, 240)


def _match_job(subject: str, body: str, jobs: Iterable[dict]) -> int | None:
    text = f"{subject} {body}".lower()
    for job in jobs:
        company = (job.get("company") or "").lower()
        title = (job.get("title") or "").lower()
        if company and len(company) > 2 and company in text:
            return int(job["id"])
        title_words = [word for word in re.split(r"\W+", title) if len(word) > 4]
        if title_words and sum(1 for word in title_words if word in text) >= min(2, len(title_words)):
            return int(job["id"])
    return None


def test_smtp() -> None:
    if not config.EMAIL_USER or not config.EMAIL_PASSWORD:
        raise RuntimeError("EMAIL_USER and EMAIL_APP_PASSWORD/EMAIL_PASSWORD are required.")
    with smtplib.SMTP(config.EMAIL_SMTP_HOST, config.EMAIL_SMTP_PORT, timeout=20) as smtp:
        if config.EMAIL_SMTP_TLS:
            smtp.starttls()
        smtp.login(config.EMAIL_USER, config.EMAIL_PASSWORD)


def sync_inbox(jobs: list[dict]) -> list[ClassifiedEmail]:
    if not config.EMAIL_USER or not config.EMAIL_PASSWORD:
        raise RuntimeError("EMAIL_USER and EMAIL_APP_PASSWORD/EMAIL_PASSWORD are required.")

    since = (datetime.now(timezone.utc) - timedelta(days=config.EMAIL_LOOKBACK_DAYS)).strftime("%d-%b-%Y")
    results: list[ClassifiedEmail] = []
    with imaplib.IMAP4_SSL(config.EMAIL_IMAP_HOST, config.EMAIL_IMAP_PORT) as imap:
        imap.login(config.EMAIL_USER, config.EMAIL_PASSWORD)
        imap.select("INBOX")
        status, data = imap.search(None, f'(SINCE "{since}")')
        if status != "OK" or not data:
            return []
        ids = data[0].split()[-config.EMAIL_MAX_FETCH :]
        for msg_id in reversed(ids):
            status, msg_data = imap.fetch(msg_id, "(RFC822)")
            if status != "OK" or not msg_data:
                continue
            raw = msg_data[0][1]
            message = email.message_from_bytes(raw)
            subject = _decode(message.get("Subject"))
            sender = _decode(message.get("From"))
            message_id = message.get("Message-ID") or f"imap-{msg_id.decode()}"
            body = _body(message)
            classification, confidence, summary = classify(subject, body)
            received = message.get("Date")
            try:
                received_at = parsedate_to_datetime(received).astimezone(timezone.utc).isoformat(timespec="seconds") if received else ""
            except Exception:
                received_at = ""
            results.append(
                ClassifiedEmail(
                    message_id=message_id,
                    subject=subject,
                    sender=sender,
                    received_at=received_at,
                    classification=classification,
                    confidence=confidence,
                    summary=summary,
                    raw_excerpt=_clean(body, 1200),
                    job_id=_match_job(subject, body, jobs),
                )
            )
    return results
