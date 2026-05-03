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
class EmailJobMatch:
    job_id: int | None = None
    confidence: float = 0.0
    reason: str = ""


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
    match_confidence: float = 0.0
    match_reason: str = ""


JOB_MATCH_STOP_WORDS = {
    "and",
    "for",
    "from",
    "with",
    "the",
    "your",
    "you",
    "job",
    "role",
    "remote",
    "hybrid",
    "onsite",
    "manager",
    "senior",
    "lead",
}


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
        (
            "interview_request",
            0.88,
            ["interview", "schedule a call", "availability", "phone screen", "next steps", "meet with", "calendar"],
        ),
        (
            "assessment_request",
            0.84,
            ["assessment", "coding challenge", "technical test", "take-home", "skills test", "hackerrank", "codility"],
        ),
        (
            "rejection",
            0.86,
            [
                "unfortunately",
                "not selected",
                "not be moving forward",
                "pursue other candidates",
                "decided to move forward with other",
                "will not be proceeding",
            ],
        ),
        (
            "application_confirmation",
            0.82,
            [
                "application received",
                "received your application",
                "thank you for applying",
                "application has been submitted",
                "we have your application",
                "thanks for applying",
            ],
        ),
        (
            "recruiter_outreach",
            0.64,
            ["opportunity", "contract role", "w2", "c2c", "recruiter", "staffing", "resume", "sourcing"],
        ),
    ]
    for label, confidence, needles in checks:
        if any(needle in text for needle in needles):
            return label, confidence, _clean(body, 240)
    return "unclassified", 0.25, _clean(body, 240)


def _tokens(value: str) -> list[str]:
    return [
        token
        for token in re.findall(r"[a-z0-9]+", value.lower())
        if len(token) > 2 and token not in JOB_MATCH_STOP_WORDS
    ]


def _sender_domain(sender: str) -> str:
    match = re.search(r"@([A-Za-z0-9.-]+)", sender)
    return match.group(1).lower() if match else ""


def _match_job(subject: str, body: str, sender: str, jobs: Iterable[dict]) -> EmailJobMatch:
    text = f"{subject} {body}".lower()
    sender_domain = _sender_domain(sender)
    best = EmailJobMatch()
    for job in jobs:
        score = 0.0
        reasons: list[str] = []
        company = _clean(str(job.get("company") or ""), 120).lower()
        title = _clean(str(job.get("title") or ""), 180).lower()
        if company and len(company) > 2 and company in text:
            score += 0.62
            reasons.append("company name")
        else:
            company_tokens = _tokens(company)
            company_hits = [token for token in company_tokens if token in text]
            if company_tokens and len(company_hits) >= min(2, len(company_tokens)):
                score += min(0.42, 0.16 * len(company_hits))
                reasons.append(f"company tokens {len(company_hits)}/{len(company_tokens)}")
        title_tokens = _tokens(title)
        title_hits = [token for token in title_tokens if token in text]
        if title_tokens and len(title_hits) >= min(2, len(title_tokens)):
            score += min(0.38, 0.11 * len(title_hits))
            reasons.append(f"title tokens {len(title_hits)}/{len(title_tokens)}")
        domain_tokens = [token for token in _tokens(company) if len(token) > 3]
        if sender_domain and any(token in sender_domain for token in domain_tokens):
            score += 0.18
            reasons.append("sender domain")
        if score > best.confidence:
            best = EmailJobMatch(
                job_id=int(job["id"]),
                confidence=min(score, 0.98),
                reason=", ".join(reasons),
            )
    return best if best.confidence >= 0.45 else EmailJobMatch()


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
            match = _match_job(subject, body, sender, jobs)
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
                    job_id=match.job_id,
                    match_confidence=match.confidence,
                    match_reason=match.reason,
                )
            )
    return results
