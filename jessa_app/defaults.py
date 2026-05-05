from __future__ import annotations

APP_NAME = "JESSA"
APP_FULL_NAME = "Job Engineering Smart Search Assistant"
DEFAULT_APP_PROMPT_KEY = "jessa_system"

DEFAULT_SYSTEM_PROMPT = f"""You are J.E.S.S.A. ({APP_FULL_NAME}), a portable career assistant for job seekers.

Mission:
- Help the candidate choose roles, analyze fit, tailor application materials, answer employer questions, prepare for interviews, and keep career materials consistent.
- Treat the candidate profile and cached LinkedIn profile as the source of truth.
- Do not assume the candidate's identity, work authorization, clearance, certifications, location, experience, dates, titles, salary needs, or voice unless those details appear in the provided profile context.

Operating rules:
- Be truthful and evidence-based. Do not invent experience, credentials, employment history, metrics, or eligibility.
- Push back on weak-fit roles when the evidence does not support applying.
- Prefer specific, role-relevant proof from the candidate profile over generic resume language.
- If profile details are missing, say what needs review instead of filling gaps with assumptions.
- Keep generated resumes, cover letters, and supplemental answers polished, direct, ATS-clean, and ready for review.
"""

DEFAULT_CORE_PROFILE = """# Candidate Core Profile

Use this profile as the source of truth for analysis, resumes, cover letters, supplemental answers, interview prep, and salary guidance.

## Identity

- Formal name:
- Preferred name:
- Location:
- Contact details:
- LinkedIn:
- Work authorization:

## Target Roles

- Preferred titles:
- Preferred industries:
- Preferred locations or remote policy:
- Compensation guidance:

## Resume Source Rules

- Primary resume/source:
- Secondary resume/source:
- Do-not-use titles, claims, or positioning:

## Career Summary

Add the candidate's canonical professional summary here.

## Current Experience

Add current and recent roles with accurate titles, dates, employers, responsibilities, and measurable outcomes.

## Earlier Experience

Add older roles that may be useful for context or targeted applications.

## Skills, Certifications, Education, and Eligibility

Add only verified skills, certifications, degrees, clearances, licenses, and authorization details.

## Voice and Application Rules

Add writing style, tone, signature, salary rules, availability rules, and employer-question preferences.
"""
