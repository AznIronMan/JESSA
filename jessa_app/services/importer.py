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


@dataclass
class LinkedInProfileSnapshot:
    url: str = ""
    title: str = ""
    content: str = ""
    extraction_note: str = ""


def source_from_url(url: str) -> str:
    host = urlparse(url).netloc.lower()
    for marker in ("indeed", "dice", "ziprecruiter", "linkedin", "greenhouse", "lever", "ashby", "workday"):
        if marker in host:
            return marker
    return host.replace("www.", "") or "url"


def is_linkedin_url(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return host == "linkedin.com" or host.endswith(".linkedin.com")


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


def _first_text(soup: BeautifulSoup, selectors: tuple[str, ...], limit: int | None = None) -> str:
    for selector in selectors:
        node = soup.select_one(selector)
        if node:
            text = clean_text(node.get_text(" "), limit=limit)
            if text:
                return text
    return ""


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
            return await page.content()
        finally:
            await context.close()


async def _click_profile_expanders(page: Any) -> None:
    await page.evaluate(
        """
        () => {
          const patterns = [/^see more$/i, /^show more$/i, /more about/i];
          for (const node of document.querySelectorAll('button, a')) {
            const text = (node.innerText || node.textContent || '').replace(/\\s+/g, ' ').trim();
            if (!text || text.length > 80) continue;
            if (patterns.some((pattern) => pattern.test(text))) {
              try { node.click(); } catch {}
            }
          }
        }
        """
    )


async def _scroll_linkedin_profile(page: Any) -> None:
    for _ in range(config.LINKEDIN_PROFILE_SCROLL_PASSES):
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
        message = "Sign in if needed, scroll/expand the profile until it looks complete, then capture."
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


async def _visible_body_text(page: Any) -> str:
    text = await page.evaluate(
        """
        () => {
          document.getElementById('jessa-linkedin-overlay')?.remove();
          return document.body ? document.body.innerText : '';
        }
        """
    )
    return clean_text(text, limit=60000)


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


def parse_linkedin_html(url: str, html: str) -> ImportedJob:
    if _linkedin_login_required(html):
        raise RuntimeError(
            "LinkedIn login is required. Sign in using the browser window JESSA opened, then import again."
        )
    soup = BeautifulSoup(html, "html.parser")
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
    imported.location = _first_text(
        soup,
        (
            ".job-details-jobs-unified-top-card__primary-description-container",
            ".jobs-unified-top-card__bullet",
            ".topcard__flavor--bullet",
        ),
        limit=160,
    )
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
    imported.description = description or _main_text(soup)
    imported.extraction_note = "Imported from LinkedIn using the local persistent browser profile."
    return imported


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
    if is_linkedin_url(url) and method in {"http", "playwright", "linkedin", "auto"}:
        html = await fetch_url_with_persistent_browser(url)
        return parse_linkedin_html(url, html)
    html = await fetch_url_with_playwright(url) if method == "playwright" else await fetch_url(url)
    return parse_html(url, html)


async def fetch_linkedin_profile(url: str) -> LinkedInProfileSnapshot:
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
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(2500)
            html = await page.content()
            if _linkedin_login_required(html):
                await _wait_for_linkedin_login_continue(page)
                await page.goto(url, wait_until="domcontentloaded", timeout=60000)
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
            content = await _visible_body_text(page)
            title = clean_text(await page.title(), limit=200)
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
            "LinkedIn profile capture did not collect enough text. Sign in, open the full profile, "
            "expand visible sections, then click the JESSA capture button."
        )
    return LinkedInProfileSnapshot(
        url=url,
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
