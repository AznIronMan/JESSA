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
COMPANY_TOKEN_STOP_WORDS = {
    "co",
    "com",
    "corp",
    "corporation",
    "company",
    "inc",
    "llc",
    "ltd",
    "limited",
    "plc",
    "services",
}
GENERIC_COMPANY_TOKENS = {
    "center",
    "centers",
    "data",
    "group",
    "mission",
    "solutions",
    "systems",
    "tech",
    "technology",
    "technologies",
}
TITLE_TOKEN_STOP_WORDS = {
    "iii",
    "ii",
    "level",
    "principal",
    "staff",
}
BULK_MESSAGE_TERMS = {
    "job alerts",
    "jobalerts",
    "match.indeed.com",
    "newsletter",
    "newsletters-",
    "store-news",
    "tldr",
    "alerts@ziprecruiter.com",
}
BULK_SUBJECT_TERMS = {
    " and more new jobs",
    "digest",
    "job alert",
    "jobs you may be interested in",
    "might be right for you",
    "new jobs",
    "positions available",
    "pre-approved",
}
JOB_CONTEXT_TERMS = {
    "application",
    "applied",
    "candidate",
    "employment",
    "hiring",
    "interview",
    "job",
    "position",
    "recruiter",
    "recruiting",
    "role",
    "talent",
}
JOB_MATCH_MIN_CONFIDENCE = 0.60


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


def _has_any(text: str, needles: Iterable[str]) -> bool:
    return any(needle in text for needle in needles)


def classify(subject: str, body: str, sender: str = "") -> tuple[str, float, str]:
    text = f"{subject}\n{body}".lower()
    if _looks_like_bulk_message(subject, sender):
        return "unclassified", 0.10, _clean(body, 240)

    has_job_context = _has_any(text, JOB_CONTEXT_TERMS)
    if has_job_context and _has_any(
        text,
        [
            "not selected",
            "not be moving forward",
            "pursue other candidates",
            "decided to move forward with other",
            "will not be proceeding",
            "unable to offer",
            "we regret to inform",
        ],
    ):
        return "rejection", 0.88, _clean(body, 240)

    if has_job_context and _has_any(
        text,
        ["assessment", "coding challenge", "technical test", "take-home", "skills test", "hackerrank", "codility"],
    ):
        return "assessment_request", 0.84, _clean(body, 240)

    if has_job_context and _has_any(
        text,
        ["interview", "schedule a call", "phone screen", "meet with", "schedule time", "availability for"],
    ):
        return "interview_request", 0.88, _clean(body, 240)

    if _has_any(
        text,
        [
            "application received",
            "received your application",
            "thank you for applying",
            "application has been submitted",
            "we have your application",
            "thanks for applying",
            "your application for",
            "indeed application:",
        ],
    ):
        return "application_confirmation", 0.84, _clean(body, 240)

    if has_job_context and _has_any(
        text,
        ["contract role", "w2", "c2c", "recruiter", "staffing", "resume", "sourcing"],
    ):
        return "recruiter_outreach", 0.58, _clean(body, 240)
    return "unclassified", 0.25, _clean(body, 240)


def _useful_token(token: str) -> bool:
    return len(token) > 2 or (len(token) >= 2 and any(char.isdigit() for char in token))


def _tokens(value: str, extra_stop_words: set[str] | None = None) -> list[str]:
    stop_words = JOB_MATCH_STOP_WORDS | (extra_stop_words or set())
    return [
        token
        for token in re.findall(r"[a-z0-9]+", value.lower())
        if _useful_token(token) and token not in stop_words
    ]


def _sender_domain(sender: str) -> str:
    match = re.search(r"@([A-Za-z0-9.-]+)", sender)
    return match.group(1).lower() if match else ""


def _looks_like_bulk_message(subject: str, sender: str) -> bool:
    combined = f"{sender} {subject}".lower()
    subject_lower = subject.lower()
    domain = _sender_domain(sender)
    if _has_any(combined, BULK_MESSAGE_TERMS) or _has_any(subject_lower, BULK_SUBJECT_TERMS):
        return True
    if domain.startswith("news.") or ".news." in domain:
        return True
    return False


def _contains_sequence(haystack: list[str], needle: list[str]) -> bool:
    if not needle or len(needle) > len(haystack):
        return False
    length = len(needle)
    return any(haystack[index : index + length] == needle for index in range(len(haystack) - length + 1))


def match_job(subject: str, body: str, sender: str, jobs: Iterable[dict]) -> EmailJobMatch:
    if _looks_like_bulk_message(subject, sender):
        return EmailJobMatch()
    text = f"{subject} {body}"
    text_tokens = _tokens(text)
    text_token_set = set(text_tokens)
    sender_domain = _sender_domain(sender)
    sender_tokens = set(_tokens(sender, COMPANY_TOKEN_STOP_WORDS))
    best = EmailJobMatch()
    for job in jobs:
        score = 0.0
        reasons: list[str] = []
        company_evidence = False
        sender_evidence = False
        company = _clean(str(job.get("company") or ""), 120).lower()
        title = _clean(str(job.get("title") or ""), 180).lower()
        company_tokens = _tokens(company, COMPANY_TOKEN_STOP_WORDS)
        distinctive_company_tokens = [token for token in company_tokens if token not in GENERIC_COMPANY_TOKENS]
        company_hits = [token for token in company_tokens if token in text_token_set]
        distinctive_hits = [token for token in company_hits if token in distinctive_company_tokens]
        if company_tokens and _contains_sequence(text_tokens, company_tokens):
            score += 0.68
            company_evidence = True
            reasons.append("company name")
        elif len(company_tokens) == 1 and company_hits:
            score += 0.62
            company_evidence = True
            reasons.append("company token")
        elif company_tokens and distinctive_hits and len(company_hits) >= min(2, len(company_tokens)):
            score += min(0.52, 0.18 * len(company_hits))
            company_evidence = True
            reasons.append(f"company tokens {len(company_hits)}/{len(company_tokens)}")

        title_tokens = _tokens(title, TITLE_TOKEN_STOP_WORDS)
        title_hits = [token for token in title_tokens if token in text_token_set]
        if title_tokens and _contains_sequence(text_tokens, title_tokens):
            score += 0.28
            reasons.append("title")
        elif title_tokens and len(title_hits) >= min(2, len(title_tokens)):
            score += min(0.26, 0.09 * len(title_hits))
            reasons.append(f"title tokens {len(title_hits)}/{len(title_tokens)}")

        domain_tokens = [token for token in distinctive_company_tokens if len(token) > 3]
        if sender_domain and sender_tokens and any(token in sender_tokens for token in domain_tokens):
            score += 0.24
            sender_evidence = True
            reasons.append("sender domain")
        if not (company_evidence or sender_evidence):
            continue
        if score > best.confidence:
            best = EmailJobMatch(
                job_id=int(job["id"]),
                confidence=min(score, 0.98),
                reason=", ".join(reasons),
            )
    return best if best.confidence >= JOB_MATCH_MIN_CONFIDENCE else EmailJobMatch()


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
            classification, confidence, summary = classify(subject, body, sender)
            match = match_job(subject, body, sender, jobs)
            if _looks_like_bulk_message(subject, sender) and classification == "unclassified" and not match.job_id:
                continue
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
