from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from .. import config


USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 JESSA/1.0"
)


@dataclass
class ImportedJob:
    source: str = "manual"
    url: str = ""
    apply_url: str = ""
    title: str = ""
    company: str = ""
    location: str = ""
    salary: str = ""
    posted_date: str = ""
    description: str = ""
    extraction_note: str = ""


def source_from_url(url: str) -> str:
    host = urlparse(url).netloc.lower()
    for marker in ("indeed", "dice", "ziprecruiter", "linkedin", "greenhouse", "lever", "ashby", "workday"):
        if marker in host:
            return marker
    return host.replace("www.", "") or "url"


def clean_text(value: str | None, limit: int | None = None) -> str:
    if not value:
        return ""
    text = re.sub(r"\s+", " ", value).strip()
    if limit and len(text) > limit:
        return text[:limit].rsplit(" ", 1)[0] + "..."
    return text


def strip_html(value: str | None) -> str:
    if not value:
        return ""
    return clean_text(BeautifulSoup(value, "html.parser").get_text(" "))


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _find_jobposting(payload: Any) -> dict[str, Any] | None:
    if isinstance(payload, list):
        for item in payload:
            found = _find_jobposting(item)
            if found:
                return found
    if not isinstance(payload, dict):
        return None
    graph = payload.get("@graph")
    if graph:
        found = _find_jobposting(graph)
        if found:
            return found
    type_value = payload.get("@type")
    types = type_value if isinstance(type_value, list) else [type_value]
    if any(str(item).lower() == "jobposting" for item in types):
        return payload
    return None


def _salary_from_json_ld(job: dict[str, Any]) -> str:
    base = job.get("baseSalary")
    if not isinstance(base, dict):
        return ""
    currency = base.get("currency", "")
    value = base.get("value")
    if isinstance(value, dict):
        minimum = value.get("minValue")
        maximum = value.get("maxValue")
        unit = value.get("unitText", "")
        if minimum and maximum:
            return clean_text(f"{currency} {minimum} - {maximum} {unit}")
        if value.get("value"):
            return clean_text(f"{currency} {value.get('value')} {unit}")
    if value:
        return clean_text(f"{currency} {value}")
    return ""


def _location_from_json_ld(job: dict[str, Any]) -> str:
    if job.get("jobLocationType") == "TELECOMMUTE":
        return "Remote"
    locations: list[str] = []
    for item in _as_list(job.get("jobLocation")):
        if not isinstance(item, dict):
            continue
        address = item.get("address", {})
        if isinstance(address, dict):
            parts = [
                address.get("addressLocality", ""),
                address.get("addressRegion", ""),
                address.get("addressCountry", ""),
            ]
            text = clean_text(", ".join(part for part in parts if part))
            if text:
                locations.append(text)
        elif isinstance(address, str):
            locations.append(clean_text(address))
    return "; ".join(dict.fromkeys(locations))


def _company_from_json_ld(job: dict[str, Any]) -> str:
    org = job.get("hiringOrganization")
    if isinstance(org, dict):
        return clean_text(org.get("name", ""))
    if isinstance(org, str):
        return clean_text(org)
    return ""


def _extract_json_ld(soup: BeautifulSoup) -> dict[str, Any] | None:
    for script in soup.find_all("script", attrs={"type": re.compile("ld\\+json", re.I)}):
        raw = script.string or script.get_text()
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        found = _find_jobposting(payload)
        if found:
            return found
    return None


def _main_text(soup: BeautifulSoup) -> str:
    for tag in soup(["script", "style", "noscript", "svg", "header", "footer", "nav", "aside"]):
        tag.decompose()
    candidate = (
        soup.find(attrs={"data-testid": re.compile("job|description", re.I)})
        or soup.find(id=re.compile("job|description|posting", re.I))
        or soup.find(class_=re.compile("job|description|posting|content", re.I))
        or soup.find("main")
        or soup.body
        or soup
    )
    return clean_text(candidate.get_text(" "), limit=20000)


async def fetch_url(url: str) -> str:
    async with httpx.AsyncClient(timeout=20, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.text


async def fetch_url_with_playwright(url: str) -> str:
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        launch_args: dict[str, Any] = {"headless": False}
        if config.PLAYWRIGHT_BROWSER_PATH:
            launch_args["executable_path"] = config.PLAYWRIGHT_BROWSER_PATH
        browser = await p.chromium.launch(**launch_args)
        page = await browser.new_page(user_agent=USER_AGENT)
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_timeout(2500)
        html = await page.content()
        await browser.close()
        return html


def parse_html(url: str, html: str) -> ImportedJob:
    soup = BeautifulSoup(html, "html.parser")
    job = _extract_json_ld(soup)
    imported = ImportedJob(source=source_from_url(url), url=url, apply_url=url)
    if job:
        imported.title = clean_text(job.get("title", ""))
        imported.company = _company_from_json_ld(job)
        imported.location = _location_from_json_ld(job)
        imported.salary = _salary_from_json_ld(job)
        imported.posted_date = clean_text(job.get("datePosted", ""))
        imported.description = strip_html(job.get("description", ""))
        imported.apply_url = clean_text(job.get("url") or url)
        imported.extraction_note = "Extracted from JobPosting JSON-LD."
        return imported

    title = soup.find("h1")
    meta_description = soup.find("meta", attrs={"name": "description"})
    imported.title = clean_text(title.get_text(" ") if title else (soup.title.string if soup.title else ""))
    imported.description = _main_text(soup)
    if meta_description and not imported.description:
        imported.description = clean_text(meta_description.get("content", ""))
    imported.extraction_note = "Extracted from rendered page text; review fields before applying."
    return imported


async def import_from_url(url: str, method: str = "http") -> ImportedJob:
    method = (method or "http").lower()
    html = await fetch_url_with_playwright(url) if method == "playwright" else await fetch_url(url)
    return parse_html(url, html)


def import_from_text(text: str) -> ImportedJob:
    lines = [clean_text(line) for line in text.splitlines() if clean_text(line)]
    title = lines[0] if lines else "Manual job import"
    company = ""
    location = ""
    if len(lines) > 1 and len(lines[1]) < 120:
        company = lines[1]
    if len(lines) > 2 and len(lines[2]) < 120:
        location = lines[2]
    return ImportedJob(
        source="manual",
        title=title,
        company=company,
        location=location,
        description=clean_text(text, limit=20000),
        extraction_note="Imported from pasted text.",
    )
