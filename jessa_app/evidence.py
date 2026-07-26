"""Scoped career-evidence retrieval for JESSA prompts and the evidence UI."""

from __future__ import annotations

import re
from collections import Counter
from typing import Any, Iterable


STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "has",
    "have", "in", "is", "it", "job", "of", "on", "or", "our", "role", "that",
    "the", "their", "this", "to", "we", "will", "with", "you", "your",
}
VALID_SCOPES = {"global", "job"}
VALID_CLAIM_STATUSES = {"verified", "qualified", "do_not_claim", "context_only"}
VALID_CONFIDENTIALITY = {"reusable", "job_context", "job_confidential"}


def tokenize(value: str) -> list[str]:
    return [
        token
        for token in re.findall(r"[a-z0-9][a-z0-9+.#/-]{1,}", value.lower())
        if token not in STOPWORDS
    ]


def query_terms(value: str, limit: int = 80) -> list[str]:
    counts = Counter(tokenize(value))
    return [term for term, _ in counts.most_common(limit)]


def evidence_score(item: dict[str, Any], terms: Iterable[str]) -> float:
    terms = list(terms)
    if not terms:
        return 0.0
    title = str(item.get("title") or "").lower()
    heading = str(item.get("source_heading") or "").lower()
    employer = str(item.get("employer") or "").lower()
    tags = " ".join(item.get("tags") or []).lower()
    content = str(item.get("content") or "").lower()
    score = 0.0
    for term in terms:
        if term in title:
            score += 8.0
        if term in employer:
            score += 7.0
        if term in tags:
            score += 6.0
        if term in heading:
            score += 4.0
        score += min(content.count(term), 5) * 0.8
    if item.get("claim_status") == "verified":
        score += 1.0
    return score


def ranked_evidence(
    items: Iterable[dict[str, Any]],
    query: str,
    *,
    limit: int = 30,
) -> list[dict[str, Any]]:
    terms = query_terms(query)
    scored = []
    for item in items:
        score = evidence_score(item, terms)
        if terms and score <= 0:
            continue
        copy = dict(item)
        copy["relevance_score"] = round(score, 2)
        scored.append(copy)
    return sorted(
        scored,
        key=lambda item: (
            -float(item.get("relevance_score") or 0),
            str(item.get("category") or ""),
            str(item.get("source_heading") or ""),
        ),
    )[:limit]


def visible_evidence(
    items: Iterable[dict[str, Any]],
    job_id: int | None,
) -> list[dict[str, Any]]:
    """Return globals plus only the explicitly selected job's scoped evidence."""
    visible = []
    for item in items:
        if item.get("scope") == "global":
            visible.append(item)
        elif (
            item.get("scope") == "job"
            and job_id is not None
            and int(item.get("job_id") or 0) == int(job_id)
        ):
            visible.append(item)
    return visible


def _append_with_budget(parts: list[str], value: str, used: int, budget: int) -> int:
    if used >= budget or not value.strip():
        return used
    remaining = budget - used
    clipped = value.strip()
    if len(clipped) > remaining:
        clipped = clipped[: max(0, remaining - 28)].rstrip() + "\n[chunk clipped]"
    parts.append(clipped)
    return used + len(clipped)


def build_evidence_context(
    items: list[dict[str, Any]],
    query: str,
    *,
    global_budget: int = 15000,
    job_budget: int = 8000,
    control_budget: int = 6500,
) -> str:
    """Build a bounded prompt section without crossing job evidence boundaries."""
    controls = [
        item
        for item in items
        if item.get("claim_status") in {"qualified", "do_not_claim"}
        or item.get("category") == "claim-controls"
    ]
    positive = [
        item
        for item in items
        if item.get("claim_status") not in {"do_not_claim"}
        and item.get("category") not in {"canonical-profile", "claim-controls"}
    ]
    ranked_controls = ranked_evidence(controls, query, limit=14)
    ranked_global = ranked_evidence(
        [item for item in positive if item.get("scope") == "global"],
        query,
        limit=24,
    )
    ranked_job = ranked_evidence(
        [item for item in positive if item.get("scope") == "job"],
        query,
        limit=20,
    )

    parts = [
        "# Retrieved Career Evidence",
        "",
        "Source precedence: direct confirmed facts and canonical controls override approved artifacts; "
        "approved artifacts override historical resumes or LinkedIn. Qualified evidence must retain its "
        "boundary. Do-not-claim entries are prohibitions.",
    ]
    if ranked_controls:
        parts.extend(["", "## Claim Controls"])
        used = 0
        for item in ranked_controls:
            label = (
                f"### {item.get('claim_status', '').upper()} — "
                f"{item.get('source_heading') or item.get('title')}\n"
                f"{item.get('content') or ''}"
            )
            used = _append_with_budget(parts, label, used, control_budget)
    if ranked_global:
        parts.extend(["", "## Reusable Evidence"])
        used = 0
        for item in ranked_global:
            label = (
                f"### {item.get('source_heading') or item.get('title')}\n"
                f"{item.get('content') or ''}"
            )
            used = _append_with_budget(parts, label, used, global_budget)
    if ranked_job:
        parts.extend(
            [
                "",
                "## Current-Job Evidence",
                "This section belongs only to the current job. Never reuse it for another employer.",
            ]
        )
        used = 0
        for item in ranked_job:
            label = (
                f"### {item.get('source_heading') or item.get('title')}\n"
                f"{item.get('content') or ''}"
            )
            used = _append_with_budget(parts, label, used, job_budget)
    return "\n\n".join(parts)
