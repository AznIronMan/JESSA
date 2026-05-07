from __future__ import annotations

import json
import re
from dataclasses import dataclass
from html import escape as html_escape
from html import unescape as html_unescape
from typing import Any
from urllib.parse import urljoin, urlparse

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


@dataclass
class LinkedInProfileSnapshot:
    url: str = ""
    title: str = ""
    content: str = ""
    extraction_note: str = ""


def source_from_url(url: str) -> str:
    host = urlparse(url).netloc.lower()
    for marker in (
        "indeed",
        "dice",
        "ziprecruiter",
        "linkedin",
        "greenhouse",
        "lever",
        "ashby",
        "workday",
        "partnersindiversity",
    ):
        if marker in host:
            return marker
    return host.replace("www.", "") or "url"


def is_linkedin_url(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return host == "linkedin.com" or host.endswith(".linkedin.com")


def is_partnersindiversity_url(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return host == "jobs.partnersindiversity.org"


def clean_text(value: str | None, limit: int | None = None) -> str:
    if not value:
        return ""
    text = re.sub(r"\s+", " ", value).strip()
    if limit and len(text) > limit:
        return text[:limit].rsplit(" ", 1)[0] + "..."
    return text


def clean_multiline_text(value: str | None, limit: int | None = None) -> str:
    if not value:
        return ""
    lines: list[str] = []
    previous_blank = False
    for raw_line in value.splitlines():
        line = clean_text(raw_line)
        if line:
            lines.append(line)
            previous_blank = False
        elif lines and not previous_blank:
            lines.append("")
            previous_blank = True
    text = "\n".join(lines).strip()
    if limit and len(text) > limit:
        return text[:limit].rsplit("\n", 1)[0].strip() + "\n..."
    return text


def strip_html(value: str | None) -> str:
    if not value:
        return ""
    return clean_text(BeautifulSoup(value, "html.parser").get_text(" "))


def strip_html_multiline(value: str | None, limit: int | None = None) -> str:
    if not value:
        return ""
    return clean_multiline_text(BeautifulSoup(value, "html.parser").get_text("\n"), limit=limit)


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


def _first_text(soup: BeautifulSoup, selectors: tuple[str, ...], limit: int | None = None) -> str:
    for selector in selectors:
        node = soup.select_one(selector)
        if node:
            text = clean_text(node.get_text(" "), limit=limit)
            if text:
                return text
    return ""


def _node_text(node: Any, limit: int | None = None) -> str:
    if not node:
        return ""
    return clean_text(node.get_text(" "), limit=limit)


def _node_multiline_text(node: Any, limit: int | None = None) -> str:
    if not node:
        return ""
    return clean_multiline_text(node.get_text("\n"), limit=limit)


def _meta_content(soup: BeautifulSoup, *keys: tuple[str, str]) -> str:
    for attr, value in keys:
        node = soup.find("meta", attrs={attr: value})
        if node:
            content = clean_text(node.get("content", ""))
            if content:
                return content
    return ""


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


def _linkedin_login_required(html: str) -> bool:
    lowered = html.lower()
    return (
        "sign in | linkedin" in lowered
        or "login-submit" in lowered
        or "session_key" in lowered
        or "/uas/login" in lowered
    )


async def fetch_url(url: str) -> str:
    async with httpx.AsyncClient(timeout=20, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.text


async def fetch_url_with_persistent_browser(url: str, settle_ms: int | None = None) -> str:
    from playwright.async_api import async_playwright

    settle = settle_ms if settle_ms is not None else config.LINKEDIN_PAGE_SETTLE_MS
    config.LINKEDIN_BROWSER_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        launch_args: dict[str, Any] = {
            "headless": False,
            "user_data_dir": str(config.LINKEDIN_BROWSER_PROFILE_DIR),
        }
        if config.PLAYWRIGHT_BROWSER_PATH:
            launch_args["executable_path"] = config.PLAYWRIGHT_BROWSER_PATH
        context = await p.chromium.launch_persistent_context(**launch_args)
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(2500)
            html = await page.content()
            if _linkedin_login_required(html):
                await _wait_for_linkedin_login_continue(page)
                await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(settle)
            await _expand_linkedin_job_page(page)
            return await _page_content_with_rendered_snapshot(page)
        finally:
            await context.close()


async def _click_profile_expanders(page: Any) -> None:
    await page.evaluate(
        """
        () => {
          const patterns = [
            /^see more$/i,
            /^show more$/i,
            /^\\u2026 more$/i,
            /^\\.\\.\\. more$/i,
            /more about/i,
            /expand .*description/i,
            /expand .*details/i
          ];
          for (const node of document.querySelectorAll('button, [role="button"]')) {
            const parts = [
              node.innerText || '',
              node.textContent || '',
              node.getAttribute('aria-label') || '',
              node.getAttribute('title') || ''
            ];
            const text = parts.join(' ').replace(/\\s+/g, ' ').trim();
            if (!text || text.length > 180) continue;
            if (patterns.some((pattern) => pattern.test(text))) {
              try { node.click(); } catch {}
            }
          }
        }
        """
    )


async def _expand_linkedin_job_page(page: Any) -> None:
    await page.evaluate(
        """
        () => {
          const patterns = [
            /^see more$/i,
            /^show more$/i,
            /^\\u2026 more$/i,
            /^\\.\\.\\. more$/i,
            /show more/i,
            /see more/i,
            /expand .*job/i,
            /expand .*description/i
          ];
          for (const node of document.querySelectorAll('button, [role="button"]')) {
            const parts = [
              node.innerText || '',
              node.textContent || '',
              node.getAttribute('aria-label') || '',
              node.getAttribute('title') || ''
            ];
            const text = parts.join(' ').replace(/\\s+/g, ' ').trim();
            if (!text || text.length > 180) continue;
            if (patterns.some((pattern) => pattern.test(text))) {
              try { node.click(); } catch {}
            }
          }
        }
        """
    )
    await page.wait_for_timeout(1000)


async def _scroll_linkedin_profile(page: Any, passes: int | None = None) -> None:
    scroll_passes = passes if passes is not None else config.LINKEDIN_PROFILE_SCROLL_PASSES
    for _ in range(scroll_passes):
        await page.evaluate("window.scrollBy(0, Math.max(window.innerHeight * 0.85, 600))")
        await page.wait_for_timeout(900)
        await _click_profile_expanders(page)
    await page.evaluate("window.scrollTo(0, 0)")
    await page.wait_for_timeout(500)


async def _install_linkedin_overlay(page: Any, mode: str) -> None:
    if mode == "login":
        title = "JESSA LinkedIn Sign-In"
        message = "Finish signing into LinkedIn in this browser, then continue."
        button = "I'm signed in, continue"
        flag = "__jessaLoginContinueRequested"
    else:
        title = "JESSA LinkedIn Capture"
        message = "Sign in if needed, then capture. JESSA will expand visible text and visit profile detail sections."
        button = "Capture profile now"
        flag = "__jessaCaptureRequested"
    await page.evaluate(
        """
        ({title, message, button, flag}) => {
          window[flag] = false;
          document.getElementById('jessa-linkedin-overlay')?.remove();
          const shell = document.createElement('div');
          shell.id = 'jessa-linkedin-overlay';
          shell.style.position = 'fixed';
          shell.style.zIndex = '2147483647';
          shell.style.right = '18px';
          shell.style.bottom = '18px';
          shell.style.maxWidth = '360px';
          shell.style.padding = '14px';
          shell.style.border = '1px solid #0f766e';
          shell.style.borderRadius = '8px';
          shell.style.background = '#ffffff';
          shell.style.boxShadow = '0 10px 30px rgba(0,0,0,.2)';
          shell.style.font = '14px/1.4 system-ui, -apple-system, Segoe UI, sans-serif';
          shell.style.color = '#17202a';
          shell.innerHTML = `
            <strong style="display:block;margin-bottom:6px;"></strong>
            <div style="margin-bottom:10px;color:#475467;">
            </div>
            <button id="jessa-linkedin-overlay-button" style="height:34px;padding:0 12px;border:1px solid #0f766e;border-radius:6px;background:#0f766e;color:#fff;cursor:pointer;">
            </button>
          `;
          shell.querySelector('strong').textContent = title;
          shell.querySelector('div').textContent = message;
          shell.querySelector('button').textContent = button;
          document.body.appendChild(shell);
          document.getElementById('jessa-linkedin-overlay-button')?.addEventListener('click', () => {
            window[flag] = true;
            shell.remove();
          });
        }
        """,
        {"title": title, "message": message, "button": button, "flag": flag},
    )


async def _wait_for_linkedin_login_continue(page: Any) -> None:
    import time

    await _install_linkedin_overlay(page, "login")
    deadline = time.monotonic() + (config.LINKEDIN_LOGIN_WAIT_MS / 1000)
    while time.monotonic() < deadline:
        try:
            if await page.evaluate("window.__jessaLoginContinueRequested === true"):
                return
            html = await page.content()
            if not _linkedin_login_required(html):
                await page.evaluate("document.getElementById('jessa-linkedin-overlay')?.remove()")
                return
            has_overlay = await page.evaluate("!!document.getElementById('jessa-linkedin-overlay')")
            if not has_overlay:
                await _install_linkedin_overlay(page, "login")
        except Exception:
            pass
        await page.wait_for_timeout(1000)
    await page.evaluate("document.getElementById('jessa-linkedin-overlay')?.remove()")


async def _wait_for_profile_capture_request(page: Any) -> None:
    from playwright.async_api import TimeoutError as PlaywrightTimeoutError

    await _install_linkedin_overlay(page, "capture")
    try:
        await page.wait_for_function(
            "window.__jessaCaptureRequested === true",
            timeout=config.LINKEDIN_PROFILE_CAPTURE_WAIT_MS,
        )
    except PlaywrightTimeoutError:
        await page.evaluate("document.getElementById('jessa-linkedin-overlay')?.remove()")


async def _visible_profile_text(page: Any) -> str:
    text = await page.evaluate(
        """
        () => {
          document.getElementById('jessa-linkedin-overlay')?.remove();
          const root = document.querySelector('main') || document.body;
          return root ? root.innerText : '';
        }
        """
    )
    return clean_multiline_text(text, limit=60000)


async def _visible_body_text(page: Any) -> str:
    return await _visible_profile_text(page)


RENDERED_PAGE_SNAPSHOT_ID = "jessa-rendered-page-snapshot"


async def _page_content_with_rendered_snapshot(page: Any) -> str:
    html = await page.content()
    text = await page.evaluate(
        """
        () => {
          const root = document.querySelector('main') || document.body;
          return root ? root.innerText : '';
        }
        """
    )
    payload = json.dumps(
        {
            "url": page.url,
            "title": await page.title(),
            "text": text,
        }
    )
    return (
        f"{html}\n"
        f'<template id="{RENDERED_PAGE_SNAPSHOT_ID}">{html_escape(payload, quote=False)}</template>'
    )


def _rendered_page_snapshot(soup: BeautifulSoup) -> dict[str, str]:
    node = soup.find(id=RENDERED_PAGE_SNAPSHOT_ID)
    if not node:
        return {}
    try:
        payload = json.loads(html_unescape(node.get_text()))
    except json.JSONDecodeError:
        return {}
    if not isinstance(payload, dict):
        return {}
    return {str(key): str(value or "") for key, value in payload.items()}


LINKEDIN_PROFILE_DETAIL_SLUGS = (
    "experience",
    "education",
    "certifications",
    "skills",
    "projects",
    "volunteering-experiences",
    "recommendations",
    "honors",
)

LINKEDIN_PROFILE_DETAIL_LABELS = {
    "experience": "Experience",
    "education": "Education",
    "certifications": "Licenses and Certifications",
    "skills": "Skills",
    "projects": "Projects",
    "volunteering-experiences": "Volunteering",
    "recommendations": "Recommendations",
    "honors": "Honors and Awards",
}


def _normalize_linkedin_url(value: str) -> str:
    url = value.strip()
    if url and "://" not in url:
        url = f"https://{url}"
    return url


def _linkedin_profile_base_url(url: str) -> str:
    parsed = urlparse(_normalize_linkedin_url(url))
    path = parsed.path or ""
    if "/details/" in path:
        path = path.split("/details/", 1)[0]
    path = path.rstrip("/") or "/"
    scheme = parsed.scheme or "https"
    return f"{scheme}://{parsed.netloc}{path}/"


def _normalize_linkedin_detail_url(url: str) -> str:
    parsed = urlparse(_normalize_linkedin_url(url))
    scheme = parsed.scheme or "https"
    path = parsed.path.rstrip("/") + "/"
    return f"{scheme}://{parsed.netloc}{path}"


def _linkedin_profile_detail_slug(url: str) -> str:
    path = urlparse(url).path
    match = re.search(r"/details/([^/]+)/?", path)
    return match.group(1) if match else ""


def _linkedin_profile_detail_label(url: str) -> str:
    slug = _linkedin_profile_detail_slug(url)
    return LINKEDIN_PROFILE_DETAIL_LABELS.get(slug, slug.replace("-", " ").title() or "Detail")


def _linkedin_profile_detail_urls(profile_url: str, html: str) -> list[str]:
    base_url = _linkedin_profile_base_url(profile_url)
    base_path = urlparse(base_url).path.rstrip("/")
    detail_urls: list[str] = []
    soup = BeautifulSoup(html, "html.parser")
    for node in soup.find_all("a", href=True):
        href = str(node.get("href") or "")
        if "/details/" not in href:
            continue
        candidate = _normalize_linkedin_detail_url(urljoin(base_url, href))
        parsed = urlparse(candidate)
        if not is_linkedin_url(candidate):
            continue
        if not parsed.path.startswith(f"{base_path}/details/"):
            continue
        detail_urls.append(candidate)
    for slug in LINKEDIN_PROFILE_DETAIL_SLUGS:
        detail_urls.append(f"{base_url}details/{slug}/")
    return list(dict.fromkeys(detail_urls))[:8]


def _dedupe_linkedin_lines(text: str, seen: set[str]) -> str:
    lines: list[str] = []
    for line in text.splitlines():
        clean_line = clean_text(line)
        if not clean_line:
            if lines and lines[-1]:
                lines.append("")
            continue
        key = clean_line.lower()
        if key in seen and len(clean_line) > 28:
            continue
        seen.add(key)
        lines.append(clean_line)
    return "\n".join(lines).strip()


def _format_linkedin_profile_sections(sections: list[tuple[str, str, str]]) -> str:
    seen: set[str] = set()
    parts: list[str] = []
    for label, url, text in sections:
        deduped = _dedupe_linkedin_lines(text, seen)
        if len(deduped) < 80:
            continue
        parts.append(f"## {label}\nSource: {url}\n\n{deduped}")
    return "\n\n".join(parts).strip()


async def _capture_linkedin_detail_sections(page: Any, profile_url: str, html: str) -> list[tuple[str, str, str]]:
    sections: list[tuple[str, str, str]] = []
    for detail_url in _linkedin_profile_detail_urls(profile_url, html):
        try:
            await page.goto(detail_url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(1500)
            if _linkedin_login_required(await page.content()):
                continue
            await _scroll_linkedin_profile(page, passes=min(config.LINKEDIN_PROFILE_SCROLL_PASSES, 4))
            text = await _visible_profile_text(page)
        except Exception:
            continue
        if len(text) >= 120:
            sections.append((f"LinkedIn Detail: {_linkedin_profile_detail_label(detail_url)}", page.url, text))
    return sections


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


LINKEDIN_WORK_MODES = {"remote", "hybrid", "on-site", "onsite", "on site"}
LINKEDIN_JOB_TYPES = {
    "full-time",
    "part-time",
    "contract",
    "temporary",
    "internship",
    "volunteer",
}


def _linkedin_job_visible_lines(soup: BeautifulSoup, snapshot: dict[str, str]) -> list[str]:
    text = snapshot.get("text", "")
    if not text:
        root = soup.find("main") or soup.body or soup
        text = root.get_text("\n") if root else ""
    text = clean_multiline_text(text, limit=60000)
    return [clean_text(line) for line in text.splitlines() if clean_text(line)]


def _linkedin_title_parts(title: str) -> list[str]:
    parts = [clean_text(part) for part in title.split(" | ") if clean_text(part)]
    while parts and parts[-1].lower() == "linkedin":
        parts.pop()
    return parts


def _split_linkedin_meta_line(line: str) -> list[str]:
    return [clean_text(part) for part in re.split(r"\s+[\u00b7\u2022]\s+", line) if clean_text(part)]


def _is_work_mode(line: str) -> bool:
    return clean_text(line).lower() in LINKEDIN_WORK_MODES


def _is_job_type(line: str) -> bool:
    return clean_text(line).lower() in LINKEDIN_JOB_TYPES


def _linkedin_top_card_fields(lines: list[str], page_title: str) -> dict[str, str]:
    fields = {"company": "", "title": "", "location": "", "posted_date": "", "job_type": ""}
    if len(lines) >= 2:
        fields["company"] = lines[0]
        fields["title"] = lines[1]
    if len(lines) >= 3:
        meta_parts = _split_linkedin_meta_line(lines[2])
        if meta_parts:
            fields["location"] = meta_parts[0]
        for part in meta_parts[1:]:
            if re.search(r"\b(ago|posted|reposted)\b", part, flags=re.I):
                fields["posted_date"] = part
                break
    for line in lines[3:9]:
        if _is_work_mode(line) and line.lower() not in fields["location"].lower():
            fields["location"] = clean_text(" - ".join(part for part in (fields["location"], line) if part))
        elif _is_job_type(line) and not fields["job_type"]:
            fields["job_type"] = line
    title_parts = _linkedin_title_parts(page_title)
    if title_parts:
        if not fields["title"]:
            fields["title"] = title_parts[0]
            if len(title_parts) > 1 and _is_work_mode(title_parts[1]):
                fields["title"] = f"{fields['title']} | {title_parts[1]}"
        if not fields["company"] and len(title_parts) >= 2:
            fields["company"] = title_parts[-1]
    return fields


def _linkedin_noise_line(line: str) -> bool:
    normalized = clean_text(line).lower()
    return normalized in {
        "... more",
        "\u2026 more",
        "show more",
        "show less",
        "apply",
        "save",
        "message",
    }


def _linkedin_section(
    lines: list[str],
    start_label: str,
    stop_patterns: tuple[str, ...],
    limit: int = 120,
) -> list[str]:
    try:
        start = next(index for index, line in enumerate(lines) if line.lower() == start_label.lower())
    except StopIteration:
        return []
    output: list[str] = []
    for line in lines[start + 1 :]:
        if any(re.search(pattern, line, flags=re.I) for pattern in stop_patterns):
            break
        if _linkedin_noise_line(line):
            continue
        output.append(line)
        if len(output) >= limit:
            break
    return output


def _linkedin_salary_line(lines: list[str]) -> str:
    for line in lines[:120]:
        if re.search(r"(\$\s?\d|USD\s?\d|salary|compensation|/[ ]?(hour|hr|year|yr))", line, flags=re.I):
            return line
    return ""


def _linkedin_job_description(imported: ImportedJob, lines: list[str], fields: dict[str, str]) -> str:
    about_job = _linkedin_section(
        lines,
        "About the job",
        (
            r"^This job alert is on$",
            r"^Job search faster with Premium$",
            r"^About the company$",
            r"^Interested in working with us",
            r"^More jobs$",
        ),
    )
    about_company = _linkedin_section(
        lines,
        "About the company",
        (
            r"^Interested in working with us",
            r"^More jobs$",
            r"^Show more$",
        ),
        limit=80,
    )
    parts: list[str] = []
    header = [
        imported.title,
        f"Company: {imported.company}" if imported.company else "",
        f"Location: {imported.location}" if imported.location else "",
        f"Posted: {imported.posted_date}" if imported.posted_date else "",
        f"Job type: {fields.get('job_type')}" if fields.get("job_type") else "",
    ]
    parts.extend(part for part in header if part)
    if about_job:
        if parts:
            parts.append("")
        parts.append("About the job")
        parts.extend(about_job)
    if about_company:
        if parts:
            parts.append("")
        parts.append("About the company")
        parts.extend(about_company)
    return clean_multiline_text("\n".join(parts), limit=30000)


def parse_linkedin_html(url: str, html: str) -> ImportedJob:
    if _linkedin_login_required(html):
        raise RuntimeError(
            "LinkedIn login is required. Sign in using the browser window JESSA opened, then import again."
        )
    soup = BeautifulSoup(html, "html.parser")
    snapshot = _rendered_page_snapshot(soup)
    page_title = clean_text(snapshot.get("title") or (soup.title.string if soup.title else ""), limit=300)
    visible_lines = _linkedin_job_visible_lines(soup, snapshot)
    fallback_fields = _linkedin_top_card_fields(visible_lines, page_title)
    imported = ImportedJob(source="linkedin", url=url, apply_url=url)
    imported.title = _first_text(
        soup,
        (
            "h1",
            ".jobs-unified-top-card__job-title",
            ".job-details-jobs-unified-top-card__job-title",
            ".top-card-layout__title",
        ),
        limit=200,
    )
    if not imported.title:
        title_meta = _meta_content(soup, ("property", "og:title"), ("name", "title"))
        imported.title = clean_text(title_meta.split("|", 1)[0], limit=200)
    if not imported.title:
        imported.title = clean_text(fallback_fields.get("title"), limit=200)
    imported.company = _first_text(
        soup,
        (
            ".job-details-jobs-unified-top-card__company-name",
            ".jobs-unified-top-card__company-name",
            ".topcard__org-name-link",
            'a[href*="/company/"]',
        ),
        limit=160,
    )
    if not imported.company:
        imported.company = clean_text(fallback_fields.get("company"), limit=160)
    imported.location = _first_text(
        soup,
        (
            ".job-details-jobs-unified-top-card__primary-description-container",
            ".jobs-unified-top-card__bullet",
            ".topcard__flavor--bullet",
        ),
        limit=160,
    )
    if not imported.location:
        imported.location = clean_text(fallback_fields.get("location"), limit=160)
    imported.posted_date = clean_text(fallback_fields.get("posted_date"), limit=80)
    imported.salary = clean_text(_linkedin_salary_line(visible_lines), limit=160)
    description = _first_text(
        soup,
        (
            "#job-details",
            ".jobs-description__content",
            ".jobs-box__html-content",
            ".description__text",
            ".show-more-less-html__markup",
        ),
        limit=20000,
    )
    linkedin_description = _linkedin_job_description(imported, visible_lines, fallback_fields)
    imported.description = linkedin_description or description or _main_text(soup)
    imported.extraction_note = "Imported from LinkedIn rendered page text using the local persistent browser profile."
    return imported


def _partnersindiversity_apply_url(soup: BeautifulSoup, url: str) -> str:
    button = soup.select_one("#btnApply")
    onclick = str(button.get("onclick") or "") if button else ""
    match = re.search(r"window\.open\((['\"])(?P<href>.+?)\1", onclick)
    if match:
        return clean_text(urljoin(url, match.group("href")))
    job = _extract_json_ld(soup)
    if job:
        job_url = clean_text(job.get("url") or "")
        if job_url:
            return job_url
    return url


def _partnersindiversity_custom_fields(soup: BeautifulSoup) -> dict[str, str]:
    fields: dict[str, str] = {}
    for container in soup.select(".customFields .formItemContainer"):
        label = _node_text(container.select_one(".formLabel"), limit=80)
        value = _node_text(container.select_one(".formDataLabel"), limit=200)
        if label and value:
            fields[label] = value
    return fields


def _partnersindiversity_salary(soup: BeautifulSoup, job: dict[str, Any] | None) -> str:
    visible_salary = _node_text(soup.select_one("#lblOutSalary"), limit=160)
    if visible_salary:
        json_salary = _salary_from_json_ld(job or {})
        if json_salary.lower().startswith("usd ") and not re.search(r"\bUSD\b|\$", visible_salary, re.I):
            return f"USD {visible_salary}"
        return visible_salary
    return _salary_from_json_ld(job or {})


def _partnersindiversity_details_text(soup: BeautifulSoup) -> str:
    fields = _partnersindiversity_custom_fields(soup)
    address = _node_text(soup.select_one("#lblOutAddress"), limit=240)
    lines: list[str] = []
    for label, value in fields.items():
        display_label = "Location mode" if label.lower() == "location" else label
        lines.append(f"{display_label}: {value}")
    if address:
        lines.append(f"Address: {address}")
    if not lines:
        return ""
    return "Partners in Diversity Details\n" + "\n".join(lines)


def _partnersindiversity_description(soup: BeautifulSoup, job: dict[str, Any] | None) -> str:
    if job:
        description = strip_html_multiline(job.get("description", ""), limit=28000)
    else:
        description = _node_multiline_text(soup.select_one("#lblOutDescription"), limit=28000)
    details = _partnersindiversity_details_text(soup)
    return clean_multiline_text("\n\n".join(part for part in (description, details) if part), limit=30000)


def parse_partnersindiversity_html(url: str, html: str) -> ImportedJob:
    soup = BeautifulSoup(html, "html.parser")
    job = _extract_json_ld(soup)
    imported = ImportedJob(source="partnersindiversity", url=url, apply_url=_partnersindiversity_apply_url(soup, url))
    imported.title = clean_text((job or {}).get("title", "")) or _first_text(soup, ("h1",), limit=200)
    imported.company = _node_text(soup.select_one("#lblOutEmployer"), limit=160) or _company_from_json_ld(job or {})
    imported.location = _location_from_json_ld(job or {}) or _node_text(soup.select_one("#lblOutAddress"), limit=160)
    custom_fields = _partnersindiversity_custom_fields(soup)
    location_mode = custom_fields.get("Location", "")
    if location_mode and location_mode.lower() not in imported.location.lower():
        imported.location = clean_text(" - ".join(part for part in (imported.location, location_mode) if part))
    imported.salary = _partnersindiversity_salary(soup, job)
    imported.posted_date = clean_text((job or {}).get("datePosted", "")) or _node_text(
        soup.select_one("#lblOutPostedDate"),
        limit=80,
    )
    imported.description = _partnersindiversity_description(soup, job) or _main_text(soup)
    imported.extraction_note = (
        "Extracted from Partners in Diversity JobBoardHQ JSON-LD and page fields."
        if job
        else "Extracted from Partners in Diversity page fields; review fields before applying."
    )
    return imported


def parse_html(url: str, html: str) -> ImportedJob:
    if is_partnersindiversity_url(url):
        return parse_partnersindiversity_html(url, html)

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
    if is_linkedin_url(url) and method in {"http", "playwright", "linkedin", "auto"}:
        html = await fetch_url_with_persistent_browser(url)
        return parse_linkedin_html(url, html)
    html = await fetch_url_with_playwright(url) if method == "playwright" else await fetch_url(url)
    return parse_html(url, html)


async def fetch_linkedin_profile(url: str) -> LinkedInProfileSnapshot:
    url = _normalize_linkedin_url(url)
    if not is_linkedin_url(url):
        raise RuntimeError("Provide a LinkedIn profile URL.")
    from playwright.async_api import async_playwright

    config.LINKEDIN_BROWSER_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        launch_args: dict[str, Any] = {
            "headless": False,
            "user_data_dir": str(config.LINKEDIN_BROWSER_PROFILE_DIR),
        }
        if config.PLAYWRIGHT_BROWSER_PATH:
            launch_args["executable_path"] = config.PLAYWRIGHT_BROWSER_PATH
        context = await p.chromium.launch_persistent_context(**launch_args)
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            profile_url = _linkedin_profile_base_url(url)
            await page.goto(profile_url, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(2500)
            html = await page.content()
            if _linkedin_login_required(html):
                await _wait_for_linkedin_login_continue(page)
                await page.goto(profile_url, wait_until="domcontentloaded", timeout=60000)
                await page.wait_for_timeout(2500)
                html = await page.content()
            if _linkedin_login_required(html):
                raise RuntimeError(
                    "LinkedIn login is still required. Sign in using the browser window, then cache again."
                )
            await _scroll_linkedin_profile(page)
            await _wait_for_profile_capture_request(page)
            await _scroll_linkedin_profile(page)
            html = await page.content()
            main_text = await _visible_profile_text(page)
            title = clean_text(await page.title(), limit=200)
            sections = [("LinkedIn Profile", page.url, main_text)]
            sections.extend(await _capture_linkedin_detail_sections(page, profile_url, html))
            content = _format_linkedin_profile_sections(sections)
        finally:
            await context.close()
    soup = BeautifulSoup(html, "html.parser")
    heading = _first_text(soup, ("h1", ".text-heading-xlarge"), limit=200)
    if heading:
        title = heading
    if not title:
        title = clean_text(_meta_content(soup, ("property", "og:title"), ("name", "title")), limit=200)
    if len(content) < config.LINKEDIN_MIN_PROFILE_CONTENT_CHARS:
        raise RuntimeError(
            "LinkedIn profile capture did not collect enough profile text. Sign in, open the full profile, "
            "wait for it to finish loading, then click the JESSA capture button."
        )
    return LinkedInProfileSnapshot(
        url=profile_url,
        title=title,
        content=content,
        extraction_note="Cached from LinkedIn using the local persistent browser profile.",
    )


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
