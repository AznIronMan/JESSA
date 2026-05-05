from __future__ import annotations

import json
import re
from typing import Literal, TypeVar

import httpx
from openai import OpenAI
from pydantic import BaseModel, Field

from .. import config
from ..defaults import DEFAULT_SYSTEM_PROMPT


class SalaryTarget(BaseModel):
    ask_range: str = ""
    floor: str = ""
    basis: str = ""


class JobAnalysis(BaseModel):
    match_score: int = Field(ge=0, le=100)
    qualification_band: Literal["Underqualified", "Stretch", "Just Right", "Overqualified"]
    interview_odds: Literal["Low", "Medium", "High", "Medium-High", "Medium-Low"]
    interview_confidence: float = Field(ge=0, le=1)
    salary_target: SalaryTarget
    resume_base: str = "Core Profile"
    recommendation: Literal["Apply", "Maybe", "Skip"]
    analysis_summary: str
    top_reasons: list[str]
    risks: list[str]
    keyword_gaps: list[str]
    suggested_angle: str
    tailored_resume_notes: str
    cover_letter: str


class ApplicationPackage(BaseModel):
    resume_title: str
    resume_markdown: str
    cover_letter_title: str
    cover_letter_markdown: str
    notes: str = ""


class SupplementalAnswer(BaseModel):
    question: str
    answer: str
    confidence: float = Field(ge=0, le=1)
    needs_review: bool = False


class SupplementalAnswers(BaseModel):
    title: str
    answers: list[SupplementalAnswer]
    markdown: str


StructuredModel = TypeVar("StructuredModel", bound=BaseModel)


def _client(provider: dict[str, str]) -> OpenAI:
    kwargs = {"api_key": provider["api_key"]}
    if provider.get("base_url"):
        kwargs["base_url"] = provider["base_url"]
    return OpenAI(**kwargs)


def _configured_providers() -> list[dict[str, str]]:
    return config.configured_llm_providers()


def _provider_label(provider: dict[str, str]) -> str:
    return f"{provider['name']}:{provider['model']}"


def _extract_profile_field(profile: str, labels: tuple[str, ...]) -> str:
    label_pattern = "|".join(re.escape(label) for label in labels)
    pattern = re.compile(
        rf"^[ \t]*(?:[-*][ \t]*)?(?:{label_pattern})[ \t]*:[ \t]*(.+?)[ \t]*$",
        re.IGNORECASE | re.MULTILINE,
    )
    match = pattern.search(profile)
    if not match:
        return ""
    value = match.group(1).strip()
    if value.lower() in {"tbd", "todo", "unknown", "not specified", "n/a"}:
        return ""
    return value


def _candidate_context(profile: str) -> dict[str, str]:
    formal_name = _extract_profile_field(profile, ("Formal name", "Formal", "Legal name", "Full name"))
    preferred_name = _extract_profile_field(profile, ("Preferred name", "Preferred", "Informal name", "Informal"))
    signature = _extract_profile_field(profile, ("Signature", "Document signature", "Cover letter signature"))
    work_authorization = _extract_profile_field(profile, ("Work authorization", "Authorization", "Eligibility"))
    location = _extract_profile_field(profile, ("Location", "Base location"))
    display_name = preferred_name or formal_name
    document_name = formal_name or preferred_name or "Candidate"
    return {
        "display_name": display_name or "the candidate",
        "document_name": document_name,
        "signature": signature or document_name,
        "work_authorization": work_authorization,
        "location": location,
    }


def _candidate_possessive(candidate: dict[str, str]) -> str:
    name = candidate.get("display_name") or "the candidate"
    if name == "the candidate":
        return "the candidate's"
    suffix = "'" if name.endswith("s") else "'s"
    return f"{name}{suffix}"


def _task_system_prompt(system_prompt: str, task: str) -> str:
    base = (system_prompt or DEFAULT_SYSTEM_PROMPT).strip()
    return f"{base}\n\nTask: {task.strip()}"


def _json_user_payload(user: dict, model_cls: type[BaseModel]) -> str:
    schema = model_cls.model_json_schema()
    return (
        "Return only valid JSON that conforms to this JSON Schema. "
        "Do not include markdown fences, commentary, or extra keys.\n\n"
        f"JSON Schema:\n{json.dumps(schema, ensure_ascii=False)}\n\n"
        f"Request payload:\n{json.dumps(user, ensure_ascii=False)}"
    )


def _extract_json_object(text: str) -> dict:
    value = text.strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*", "", value, flags=re.IGNORECASE).strip()
        value = re.sub(r"\s*```$", "", value).strip()
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        start = value.find("{")
        end = value.rfind("}")
        if start < 0 or end <= start:
            raise
        parsed = json.loads(value[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("LLM response was not a JSON object.")
    return parsed


def _response_output_text(response: object) -> str:
    text = getattr(response, "output_text", None)
    if text:
        return str(text)
    chunks: list[str] = []
    for output in getattr(response, "output", []) or []:
        for item in getattr(output, "content", []) or []:
            item_text = getattr(item, "text", None)
            if item_text:
                chunks.append(str(item_text))
    return "\n".join(chunks).strip()


def _generate_openai_structured(
    provider: dict[str, str],
    system: str,
    user: dict,
    model_cls: type[StructuredModel],
) -> StructuredModel:
    response = _client(provider).responses.parse(
        model=provider["model"],
        input=[
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
        ],
        text_format=model_cls,
    )
    for output in response.output:
        if output.type != "message":
            continue
        for item in output.content:
            parsed = getattr(item, "parsed", None)
            if parsed:
                return parsed
    raise RuntimeError("OpenAI response did not contain parsed structured output.")


def _generate_xai_structured(
    provider: dict[str, str],
    system: str,
    user: dict,
    model_cls: type[StructuredModel],
) -> StructuredModel:
    response = _client(provider).responses.create(
        model=provider["model"],
        input=[
            {"role": "system", "content": system},
            {"role": "user", "content": _json_user_payload(user, model_cls)},
        ],
    )
    return model_cls.model_validate(_extract_json_object(_response_output_text(response)))


def _generate_gemini_structured(
    provider: dict[str, str],
    system: str,
    user: dict,
    model_cls: type[StructuredModel],
) -> StructuredModel:
    url = f"{provider['base_url']}/models/{provider['model']}:generateContent"
    payload = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": _json_user_payload(user, model_cls)}]}],
        "generationConfig": {
            "response_mime_type": "application/json",
            "temperature": 0.2,
        },
    }
    with httpx.Client(timeout=90) as client:
        response = client.post(url, params={"key": provider["api_key"]}, json=payload)
        response.raise_for_status()
    data = response.json()
    parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
    text = "\n".join(str(part.get("text", "")) for part in parts if part.get("text")).strip()
    if not text:
        raise RuntimeError("Gemini response did not contain text.")
    return model_cls.model_validate(_extract_json_object(text))


def _generate_claude_structured(
    provider: dict[str, str],
    system: str,
    user: dict,
    model_cls: type[StructuredModel],
) -> StructuredModel:
    url = f"{provider['base_url']}/v1/messages"
    payload = {
        "model": provider["model"],
        "max_tokens": 6000,
        "system": system,
        "messages": [{"role": "user", "content": _json_user_payload(user, model_cls)}],
    }
    headers = {
        "x-api-key": provider["api_key"],
        "anthropic-version": config.CLAUDE_VERSION,
        "content-type": "application/json",
    }
    with httpx.Client(timeout=90) as client:
        response = client.post(url, headers=headers, json=payload)
        response.raise_for_status()
    data = response.json()
    text = "\n".join(
        str(item.get("text", ""))
        for item in data.get("content", [])
        if item.get("type") == "text" and item.get("text")
    ).strip()
    if not text:
        raise RuntimeError("Claude response did not contain text.")
    return model_cls.model_validate(_extract_json_object(text))


def _generate_structured(system: str, user: dict, model_cls: type[StructuredModel]) -> StructuredModel:
    providers = _configured_providers()
    if not providers:
        raise RuntimeError("No LLM provider is configured.")
    failures: list[str] = []
    for provider in providers:
        try:
            if provider["name"] == "openai":
                return _generate_openai_structured(provider, system, user, model_cls)
            if provider["name"] == "grok":
                return _generate_xai_structured(provider, system, user, model_cls)
            if provider["name"] == "gemini":
                return _generate_gemini_structured(provider, system, user, model_cls)
            if provider["name"] == "claude":
                return _generate_claude_structured(provider, system, user, model_cls)
            failures.append(f"{provider['name']}: unsupported provider")
        except Exception as exc:
            failures.append(f"{_provider_label(provider)} failed: {exc}")
            continue
    raise RuntimeError("; ".join(failures) or "All configured LLM providers failed.")


def _fallback_score(job: dict, profile: str) -> JobAnalysis:
    text = f"{job.get('title', '')} {job.get('description', '')}".lower()
    profile_text = profile.lower()
    keywords = [
        "devsecops",
        "aws",
        "azure",
        "kubernetes",
        "eks",
        "aks",
        "security",
        "clearance",
        "platform",
        "sre",
        "director",
        "manager",
        "infrastructure",
        "healthcare",
        "federal",
        "dod",
        "ci/cd",
        "terraform",
    ]
    hits = [word for word in keywords if word in text and word in profile_text]
    score = min(92, 35 + len(hits) * 4)
    if any(word in text for word in ("helpdesk", "desktop support", "tier 1", "tier i")):
        score = min(score, 42)
        band = "Overqualified"
        recommendation = "Skip"
    elif score >= 78:
        band = "Just Right"
        recommendation = "Apply"
    elif score >= 60:
        band = "Stretch"
        recommendation = "Maybe"
    else:
        band = "Underqualified"
        recommendation = "Maybe"
    resume_base = "Leadership" if re.search(r"\b(director|manager|head of|lead|principal)\b", text) else "Technical"
    return JobAnalysis(
        match_score=score,
        qualification_band=band,  # type: ignore[arg-type]
        interview_odds="Medium" if score >= 70 else "Low",
        interview_confidence=0.35,
        salary_target=SalaryTarget(
            ask_range=job.get("salary") or "Research before quoting; use market/title/context.",
            floor="Set manually",
            basis="Heuristic fallback; OpenAI analysis was not available.",
        ),
        resume_base=resume_base,
        recommendation=recommendation,  # type: ignore[arg-type]
        analysis_summary="Heuristic analysis only. Configure OpenAI or rerun analysis for full scoring.",
        top_reasons=hits[:5] or ["Job text imported successfully."],
        risks=["LLM analysis unavailable; verify requirements manually."],
        keyword_gaps=[],
        suggested_angle="Lead with the strongest matching proof from the candidate profile.",
        tailored_resume_notes="Run OpenAI analysis to generate tailored resume notes.",
        cover_letter="Run OpenAI analysis to generate a cover letter.",
    )


def analyze_job(job: dict, profile: str, system_prompt: str = DEFAULT_SYSTEM_PROMPT) -> JobAnalysis:
    if not _configured_providers():
        return _fallback_score(job, profile)

    candidate = _candidate_context(profile)
    system = _task_system_prompt(
        system_prompt,
        (
            "Score job fit with a strict, evidence-based rubric. Do not invent experience. "
            "Push back on weak-fit roles. Use the candidate profile as the source of truth. "
            "Return concise, practical output."
        ),
    )
    user = {
        "candidate_context": candidate,
        "candidate_profile": profile[:45000],
        "job": {
            "title": job.get("title", ""),
            "company": job.get("company", ""),
            "location": job.get("location", ""),
            "salary": job.get("salary", ""),
            "description": job.get("description", "")[:30000],
            "source": job.get("source", ""),
            "url": job.get("url", ""),
        },
        "rubric": {
            "hard_requirements": "35%",
            "relevant_experience": "25%",
            "seniority_title_fit": "15%",
            "domain_fit": "10%",
            "location_remote_clearance": "10%",
            "compensation_fit": "5%",
        },
        "instructions": [
            "Honor any resume-source and positioning rules in the candidate profile.",
            "Estimate salary target from posted salary first; otherwise use title, seniority, remote/location, clearance, and contract/full-time hints.",
            "Use Underqualified only when required credentials/domain/hands-on experience are missing.",
            "Use Overqualified for helpdesk, tier 1/2, desktop-only, low-comp, or low-growth roles.",
            f"Generate one cover letter draft in {_candidate_possessive(candidate)} voice, no generic resume padding.",
            "Generate resume tailoring notes, not a full rewritten resume.",
        ],
    }

    try:
        return _generate_structured(system, user, JobAnalysis)
    except Exception as exc:
        fallback = _fallback_score(job, profile)
        fallback.risks.insert(0, f"LLM analysis failed: {exc}")
        return fallback


def _fallback_package(job: dict, profile: str) -> ApplicationPackage:
    title = job.get("title") or "Target Role"
    company = job.get("company") or "Target Company"
    candidate = _candidate_context(profile)
    document_name = candidate["document_name"]
    resume_title = f"{document_name} - {title} Resume"
    cover_title = f"{document_name} - {company} Cover Letter"
    resume = (
        f"# {resume_title}\n\n"
        "## Tailoring Required\n\n"
        "OpenAI package generation was unavailable. Use the Core Profile tab plus the existing "
        "resume/source rules as the source, then tailor the summary and first-page bullets "
        f"toward {title} at {company}.\n\n"
        "## Job Context\n\n"
        f"- Title: {title}\n"
        f"- Company: {company}\n"
        f"- Location: {job.get('location') or 'Not specified'}\n"
    )
    cover = (
        f"# {cover_title}\n\n"
        "Dear Hiring Team,\n\n"
        f"I am applying for the {title} role at {company}. My background includes the experience, "
        "skills, and qualifications outlined in my core profile, and I would tailor the strongest "
        "relevant proof points to your requirements after reviewing the job details.\n\n"
        "I would welcome the opportunity to discuss where my background maps to your current needs.\n\n"
        f"{candidate['signature']}\n"
    )
    return ApplicationPackage(
        resume_title=resume_title,
        resume_markdown=resume,
        cover_letter_title=cover_title,
        cover_letter_markdown=cover,
        notes="Fallback package generated without OpenAI.",
    )


def generate_application_package(
    job: dict,
    profile: str,
    analysis: dict | None = None,
    system_prompt: str = DEFAULT_SYSTEM_PROMPT,
) -> ApplicationPackage:
    if not _configured_providers():
        return _fallback_package(job, profile)

    candidate = _candidate_context(profile)
    system = _task_system_prompt(
        system_prompt,
        (
            "Generate resume and cover letter materials that are truthful, ATS-clean, senior, direct, "
            "and specific to the job. Do not invent experience. Honor any resume-source and positioning "
            "rules in the candidate profile."
        ),
    )
    user = {
        "candidate_context": candidate,
        "candidate_profile": profile[:55000],
        "job": {
            "title": job.get("title", ""),
            "company": job.get("company", ""),
            "location": job.get("location", ""),
            "salary": job.get("salary", ""),
            "description": job.get("description", "")[:30000],
            "source": job.get("source", ""),
        },
        "analysis": analysis or {},
        "instructions": [
            "Return a full tailored resume in clean Markdown.",
            "Keep the candidate's canonical timeline and titles intact.",
            "Resume header must use candidate_context.document_name and only include contact, credential, authorization, clearance, or certification details present in the candidate profile.",
            "Choose the resume positioning that best matches the job and the candidate profile rules.",
            "No generic filler, no invented technologies, no inflated dates or titles.",
            "Return a matching cover letter in clean Markdown.",
            "Keep both documents ready for PDF rendering.",
        ],
    }
    try:
        return _generate_structured(system, user, ApplicationPackage)
    except Exception as exc:
        fallback = _fallback_package(job, profile)
        fallback.notes = f"LLM package generation failed: {exc}"
        return fallback


def _fallback_supplemental(job: dict, questions_text: str) -> SupplementalAnswers:
    questions = [line.strip("-* 0123456789.\t") for line in questions_text.splitlines() if line.strip()]
    if not questions:
        questions = [questions_text.strip() or "Supplemental question"]
    answers = [
        SupplementalAnswer(
            question=question,
            answer="Draft answer unavailable without OpenAI. Answer truthfully using the core profile and job analysis.",
            confidence=0.2,
            needs_review=True,
        )
        for question in questions
    ]
    markdown = "\n\n".join([f"## {item.question}\n\n{item.answer}" for item in answers])
    return SupplementalAnswers(title=f"Supplemental Answers - {job.get('company') or job.get('title') or 'Job'}", answers=answers, markdown=markdown)


def answer_supplemental_questions(
    job: dict,
    profile: str,
    questions_text: str,
    application_artifacts: list[dict] | None = None,
    system_prompt: str = DEFAULT_SYSTEM_PROMPT,
) -> SupplementalAnswers:
    if not _configured_providers():
        return _fallback_supplemental(job, questions_text)

    candidate = _candidate_context(profile)
    system = _task_system_prompt(
        system_prompt,
        (
            "Answer job application supplemental questions for the candidate. Answers must be truthful, "
            "concise, paste-ready, and grounded in the candidate profile and generated application "
            "materials. Say No when the truth is No. Do not invent experience."
        ),
    )
    user = {
        "candidate_context": candidate,
        "candidate_profile": profile[:45000],
        "job": {
            "title": job.get("title", ""),
            "company": job.get("company", ""),
            "location": job.get("location", ""),
            "description": job.get("description", "")[:22000],
        },
        "application_artifacts": application_artifacts or [],
        "questions_text": questions_text,
        "instructions": [
            "Preserve the question text.",
            "Answer in first person when the question is candidate-facing.",
            "For salary, give a direct range and short rationale.",
            "For work authorization, answer only from candidate_context or the candidate profile; mark needs_review=true if it is not documented.",
            "For availability, use immediate availability unless the question requires a date.",
            "Mark needs_review=true if the question asks for information not present in the profile.",
        ],
    }
    try:
        return _generate_structured(system, user, SupplementalAnswers)
    except Exception as exc:
        fallback = _fallback_supplemental(job, questions_text)
        fallback.markdown = f"LLM supplemental answer generation failed: {exc}\n\n{fallback.markdown}"
        return fallback
