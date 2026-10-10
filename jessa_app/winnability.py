"""Deterministic winnability signals: how likely an application is to be seen, not whether the candidate fits.

Fit (match_score) answers "could the candidate do this job". Winnability answers
"is this application likely to reach a human". It uses posting signals only:
posting age, applicant count, repost, one-click apply, employer type, and
local versus remote. It never calls an LLM and never reads candidate text.
Pattern lists are configurable through environment variables so the app stays
portable (see README v3.5.0).
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from typing import Any

DEFAULT_AGGREGATORS = r"jobgether|dataannotation|data annotation|turing|prolific|outlier|mercor|hackajob|jooble|talent\.com|lensa"
DEFAULT_CROWD_WORK = r"dataannotation|data annotation|prolific|outlier|mercor|ai trainer|annotation"
DEFAULT_AGENCIES = (r"robert half|insight global|jobot|teksystems|kforce|randstad|apex systems|motion recruitment|"
                    r"\blhh\b|beacon hill|franklin fitch|crossing hurdles|optomi|harnham|huxley|staffing|recruit|"
                    r"talent group|search partners|consulting group|technology partners")
HIGHER_ED = r"universit|college|school district|community college|polytechnic"
PUBLIC = (r"\bcity of\b|\bcounty\b|\bstate of\b|secretary of state|department of|\bmetro\b|trimet|port of|"
          r"federal (agency|government)|\bgovernment\b|air force|\barmy\b|\bnavy\b")


def _pattern(env_name: str, default: str) -> re.Pattern[str]:
    return re.compile(os.getenv(env_name, "").strip() or default, re.I)


def _local_pattern() -> re.Pattern[str] | None:
    raw = os.getenv("JESSA_LOCAL_LOCATIONS", "").strip()
    if not raw:
        return None
    # Whole place names only, at least three characters, so "WA" or "OR" fragments never match inside words.
    parts = [re.escape(p.strip()) for p in raw.split(",") if len(p.strip()) >= 3]
    return re.compile(r"\b(?:" + "|".join(parts) + r")\b", re.I) if parts else None


def parse_posting_age_days(posted: str, now: datetime | None = None) -> int | None:
    text = (posted or "").strip().lower()
    if not text:
        return None
    now = now or datetime.now(timezone.utc)
    iso = re.search(r"(\d{4}-\d{2}-\d{2})", text)
    if iso:
        try:
            d = datetime.fromisoformat(iso.group(1)).replace(tzinfo=timezone.utc)
            return max(0, (now - d).days)
        except ValueError:
            pass
    if re.search(r"just now|today|moments? ago|\bnow\b", text):
        return 0
    m = re.search(r"(\d+)\+?\s*(minute|hour|day|week|month|year)s?\s+ago", text)
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2)
    return {"minute": 0, "hour": 0, "day": n, "week": n * 7, "month": n * 30, "year": n * 365}[unit]


def parse_applicant_count(text: str) -> tuple[int | None, bool]:
    """Return (count, is_lower_bound). 'Over 100 applicants' -> (100, True)."""
    t = (text or "").lower()
    m = re.search(r"(?:over|more than)\s+(\d[\d,]*)\s+applicants", t)
    if m:
        return int(m.group(1).replace(",", "")), True
    m = re.search(r"(\d[\d,]*)\+\s*applicants", t)
    if m:
        return int(m.group(1).replace(",", "")), True
    m = re.search(r"(\d[\d,]*)\s+applicants", t)
    if m:
        return int(m.group(1).replace(",", "")), False
    return None, False


def employer_type(company: str, source: str = "", description: str = "") -> str:
    company_text = f"{company} {source}"
    if _pattern("JESSA_CROWD_WORK_PATTERNS", DEFAULT_CROWD_WORK).search(company_text):
        return "crowd_work"
    if _pattern("JESSA_AGGREGATOR_PATTERNS", DEFAULT_AGGREGATORS).search(company_text):
        return "aggregator"
    if _pattern("JESSA_AGENCY_PATTERNS", DEFAULT_AGENCIES).search(company):
        return "staffing_agency"
    if re.search(HIGHER_ED, company, re.I) or "higheredjobs" in source.lower():
        return "higher_ed"
    if re.search(PUBLIC, company, re.I) and not re.search(r"credit union|bank", company, re.I):
        return "public_sector"
    return "direct"


def _field(description: str, label: str) -> str:
    m = re.search(rf"(?im)^{label}:\s*(.+)$", description or "")
    return m.group(1).strip() if m else ""


def score_job(job: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    description = str(job.get("description") or "")
    location = str(job.get("location") or "")
    posted = str(job.get("posted_date") or "") or _field(description, "Posted")
    applicants_text = _field(description, "Applicants") or description[:3000]
    company = str(job.get("company") or "")
    source = str(job.get("source") or "")

    score, factors = 50, []

    def add(points: int, label: str) -> None:
        nonlocal score
        score += points
        factors.append({"points": points, "factor": label})

    age = parse_posting_age_days(posted, now)
    if age is None:
        factors.append({"points": 0, "factor": "posting age unknown"})
    elif age <= 3:
        add(15, f"fresh posting ({age}d)")
    elif age <= 7:
        add(10, f"recent posting ({age}d)")
    elif age <= 14:
        add(0, f"posting {age}d old")
    elif age <= 30:
        add(-10, f"aging posting ({age}d)")
    else:
        add(-20, f"stale posting ({age}d)")

    count, lower_bound = parse_applicant_count(applicants_text)
    if count is None:
        factors.append({"points": 0, "factor": "applicant count unknown"})
    elif count >= 100:
        add(-15, f"{'over ' if lower_bound else ''}{count} applicants")
    elif count < 25:
        add(10, f"{count} applicants")
    else:
        add(0, f"{count} applicants")

    if re.search(r"\breposted\b", posted, re.I) or re.search(r"(?im)^posted:.*\breposted\b", description):
        add(-5, "reposted")
    if re.search(r"(?im)^apply method:\s*easy apply", description):
        add(-10, "one-click Easy Apply (high-volume pool)")

    etype = employer_type(company, source, description)
    add({"aggregator": -30, "crowd_work": -30, "staffing_agency": -10, "higher_ed": 10,
         "public_sector": 10, "direct": 0}[etype], f"employer type: {etype}")

    local = _local_pattern()
    loc_text = f"{location} {_field(description, 'Location')}"
    if re.search(r"\bremote\b", loc_text, re.I) and not re.search(r"hybrid", loc_text, re.I):
        add(-5, "remote (national applicant pool)")
    elif local and local.search(loc_text):
        add(15, "local / commutable")

    score = max(0, min(100, score))
    band = "High" if score >= 65 else "Medium" if score >= 40 else "Low"
    return {"score": score, "band": band, "employer_type": etype, "posting_age_days": age,
            "applicant_count": count, "applicant_count_is_lower_bound": lower_bound, "factors": factors}


def combine_recommendation(llm_recommendation: str, winnability: dict[str, Any]) -> tuple[str, str]:
    """Downgrade Apply to Maybe when the application is unlikely to be seen. Returns (recommendation, reason)."""
    if winnability.get("employer_type") in {"aggregator", "crowd_work"} and llm_recommendation == "Apply":
        return "Maybe", f"Posted through a {winnability['employer_type'].replace('_', ' ')}; apply on the employer's own site if identifiable."
    if llm_recommendation == "Apply" and winnability.get("band") == "Low":
        top = ", ".join(f["factor"] for f in winnability.get("factors", []) if f["points"] < 0)[:200]
        return "Maybe", f"Strong fit but low winnability ({winnability['score']}): {top}."
    return llm_recommendation, ""
