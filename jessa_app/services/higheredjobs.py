"""Bounded public HigherEdJobs discovery and posting extraction.

Saved public HTML is an explicit alternative when the website requires a human
security check. No browser-session access, accounts, click-through requests, or applications.
"""
from __future__ import annotations

import asyncio
import hashlib
import re
from datetime import date, datetime, timedelta
from html import unescape
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser
from xml.etree import ElementTree
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

import httpx
from bs4 import BeautifulSoup

DEFAULT_SEARCH_URL = "https://www.higheredjobs.com/admin/search.cfm?JobCat=163&SortBy=1"
MAX_BYTES = 2_000_000
SEARCH_PATHS = {"/admin/search.cfm", "/executive/search.cfm", "/search/advance_action.cfm", "/search/remote.cfm", "/search/rss.cfm"}
POSTING_PATHS = {"/admin/details.cfm", "/faculty/details.cfm", "/executive/details.cfm", "/details.cfm"}
USER_AGENT = "JESSA-HigherEdJobs/1.0"


class HigherEdJobsError(ValueError):
    status_code = 422


class HigherEdJobsBlocked(HigherEdJobsError):
    status_code = 409


def is_higheredjobs_url(url: str) -> bool:
    return (urlsplit(url).hostname or "").lower() in {"higheredjobs.com", "www.higheredjobs.com"}


def validate_url(url: str, *, search: bool = False) -> str:
    parsed = urlsplit(url.strip())
    try:
        port = parsed.port
    except ValueError as exc:
        raise HigherEdJobsError("Invalid HigherEdJobs URL.") from exc
    paths = SEARCH_PATHS if search else POSTING_PATHS
    if (not is_higheredjobs_url(url) or parsed.scheme != "https" or port not in (None, 443)
            or parsed.username or parsed.password or parsed.path.lower() not in paths):
        raise HigherEdJobsError("Use a public https://www.higheredjobs.com search or posting URL.")
    return urlunsplit(("https", "www.higheredjobs.com", parsed.path.lower(), parsed.query, ""))


def job_code(url: str) -> str:
    values = [v for k, v in parse_qsl(urlsplit(url).query) if k.lower() == "jobcode"]
    if len(values) != 1 or not re.fullmatch(r"\d{1,12}", values[0]):
        raise HigherEdJobsError("A HigherEdJobs posting URL must contain one numeric JobCode.")
    return values[0]


def canonical_job_url(url: str) -> str:
    validate_url(url)
    return f"https://www.higheredjobs.com/admin/details.cfm?JobCode={job_code(url)}"


def text(node) -> str:
    if node is None:
        return ""
    raw = node.get_text(" ", strip=True) if hasattr(node, "get_text") else str(node)
    return re.sub(r"\s+", " ", unescape(raw)).strip()


def multiline(node) -> str:
    return "\n".join(text(line) for line in node.get_text("\n").splitlines() if text(line)) if node else ""


def normalized_date(value: str, observed_on: date | None = None) -> str:
    raw = text(value).removeprefix("Posted ").strip()
    observed_on = observed_on or datetime.now(ZoneInfo("America/Los_Angeles")).date()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:T.*)?", raw):
        try:
            return date.fromisoformat(raw[:10]).isoformat()
        except ValueError:
            return ""
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            pass
    if raw.lower() in {"today", "yesterday"}:
        return (observed_on - timedelta(days=int(raw.lower() == "yesterday"))).isoformat()
    match = re.fullmatch(r"(\d+) days? ago", raw, re.I)
    return (observed_on - timedelta(days=int(match[1]))).isoformat() if match else ""


def checked_soup(html: str) -> BeautifulSoup:
    if len(html.encode("utf-8")) > MAX_BYTES:
        raise HigherEdJobsError("Saved page exceeds the 2 MB capture limit.")
    soup = BeautifulSoup(html, "html.parser")
    visible = text(soup).lower()
    # Check the document itself, not a normal page's third-party ad scripts.
    if ("additional security check is required" in visible or "verify you are human" in visible
            or "access denied" in visible or soup.select_one('iframe[src*="_Incapsula_Resource"]')
            or (not soup.select_one("#jobDesc, .row.record, item") and (
                "_incapsula_resource" in html.lower() or "captcha" in visible or len(visible) < 20))):
        raise HigherEdJobsBlocked("HigherEdJobs requires a human security check. Open the URL in Chrome, complete the check, save Webpage, HTML Only, and use the saved HTML option. No job was imported.")
    return soup


def parse_posting(url: str, html: str, *, observed_on: date | None = None) -> dict:
    canonical = canonical_job_url(url)
    code = job_code(canonical)
    soup = checked_soup(html)
    if re.search(r"(?:this job|this position|job posting) (?:has been closed|is no longer available|has expired)", text(soup), re.I):
        raise HigherEdJobsError("The HigherEdJobs posting is closed or expired.")
    header, description = soup.select_one("#job-header"), soup.select_one("#jobDesc")
    if not header or not description:
        raise HigherEdJobsError("HigherEdJobs posting fields were not found. Save the job detail page, not a search, login, or security-check page.")
    codes = {str(n.get("data-jobcode")) for n in header.select("[data-jobcode]")}
    apply_node = header.select_one("#js-applyurl") or header.select_one('a[href*="clickthru"]')
    if apply_node and "jobcode=" in str(apply_node.get("href", "")).lower():
        codes.add(job_code(urljoin(canonical, str(apply_node["href"]))))
    if codes and codes != {code}:
        raise HigherEdJobsError("Saved posting JobCode does not match the supplied URL.")
    attributes = {}
    for label in soup.select("#jobAttrib strong"):
        parts = []
        for sibling in label.next_siblings:
            if getattr(sibling, "name", None) in {"br", "strong"}:
                break
            parts.append(text(sibling))
        attributes[text(label).rstrip(":").lower()] = text(" ".join(parts))
    location = text(header.select_one(".job-loc"))
    location = re.sub(r"^in\s+", "", location)
    category = "; ".join(dict.fromkeys(text(a) for a in soup.select("#jobAttrib a.job-cat") if not text(a).startswith("+")))
    apply_url = canonical
    if apply_node:
        # Public data-orig-href reveals the employer link without a disallowed
        # /clickthru request. Preserve a redirect only as a browser handoff.
        candidate = urljoin(canonical, str(apply_node.get("data-orig-href") or apply_node.get("href") or ""))
        parsed = urlsplit(candidate)
        if parsed.scheme in {"http", "https"} and not parsed.username and not parsed.password:
            apply_url = candidate
    posted_raw = attributes.get("posted", "")
    deadline_raw = attributes.get("application due", "")
    details = {
        "job_code": code, "employment_type": attributes.get("type", ""), "category": category,
        "deadline": normalized_date(deadline_raw, observed_on), "deadline_raw": deadline_raw,
        "posted_date_raw": posted_raw, "observed_on": (observed_on or datetime.now(ZoneInfo("America/Los_Angeles")).date()).isoformat(),
        "work_mode": "remote" if re.search(r"\b(remote|online)\b", location, re.I) else "",
        "apply_url_kind": "higheredjobs_redirect" if is_higheredjobs_url(apply_url) else "employer",
    }
    title, company = text(header.select_one("#jobtitle-header, h1")), text(header.select_one(".job-inst"))
    body = multiline(description)
    statement = multiline(soup.select_one("#jobStatement"))
    if not title or not company or len(body) < 40:
        raise HigherEdJobsError("HigherEdJobs posting is incomplete; title, institution, and description are required.")
    extra = [f"JobCode: {code}", f"Type: {details['employment_type']}", f"Category: {category}"]
    if deadline_raw:
        extra.append(f"Application Due: {details['deadline'] or deadline_raw}")
    if posted_raw:
        extra.append(f"Posted: {posted_raw} (observed {details['observed_on']})")
    return {
        "source": "higheredjobs", "url": canonical, "apply_url": apply_url,
        "title": title, "company": company, "location": location, "salary": attributes.get("salary", ""),
        "posted_date": normalized_date(posted_raw, observed_on),
        "description": "\n\n".join(part for part in (body, statement, "HigherEdJobs Details\n" + "\n".join(extra)) if part),
        "extraction_note": "Extracted from HigherEdJobs job-header, jobAttrib, jobDesc, and public employer apply link. Review eligibility before applying.",
        "details": details,
    }


def parse_search(url: str, html: str, *, observed_on: date | None = None) -> dict:
    url = validate_url(url, search=True)
    soup = checked_soup(html)
    jobs = {}
    for card in soup.select(".row.record"):
        link = card.select_one('a[href*="JobCode="], a[href*="jobcode="]')
        if not link:
            continue
        try:
            posting_url = canonical_job_url(urljoin(url, str(link.get("href"))))
        except HigherEdJobsError:
            continue
        left = card.select_one(".col-sm-7")
        lines = [text(line) for line in left.get_text("\n").splitlines() if text(line)] if left else []
        heading = text(link)
        # A wrapped title remains one anchor; remove its lines before employer/location.
        title_lines = [text(line) for line in link.get_text("\n").splitlines() if text(line)]
        for line in title_lines:
            if lines and lines[0] == line:
                lines.pop(0)
        right = multiline(card.select_one(".col-sm-5"))
        posted_match = re.search(r"Posted\s+([^\n]+)", right, re.I)
        raw = posted_match[1] if posted_match else ""
        jobs[posting_url] = {
            "url": posting_url, "job_code": job_code(posting_url), "title": heading,
            "company": lines[0] if lines else "", "location": lines[1] if len(lines) > 1 else "",
            "posted_date": normalized_date(raw, observed_on), "posted_date_raw": raw,
            "summary": multiline(card), "source": "higheredjobs",
        }
    if not jobs and soup.find("item"):
        try:
            feed = ElementTree.fromstring(html)
        except ElementTree.ParseError as exc:
            raise HigherEdJobsError("Invalid HigherEdJobs RSS feed.") from exc
        for item in feed.findall("./channel/item"):
            try:
                posting_url = canonical_job_url(item.findtext("link", ""))
            except HigherEdJobsError:
                continue
            raw_date = item.findtext("pubDate", "")
            try:
                posted_date = parsedate_to_datetime(raw_date).date().isoformat()
            except (TypeError, ValueError):
                posted_date = ""
            jobs.setdefault(posting_url, {"url": posting_url, "job_code": job_code(posting_url), "title": text(item.findtext("title", "")),
                                 "company": "", "location": "", "posted_date": posted_date, "posted_date_raw": raw_date,
                                 "summary": text(BeautifulSoup(item.findtext("description", ""), "html.parser")), "source": "higheredjobs"})
    if not jobs and not re.search(r"no (?:jobs|results|positions)|0 (?:jobs|results)|results\s+0", text(soup), re.I):
        raise HigherEdJobsError("No recognizable HigherEdJobs search results. Save a results page or RSS feed, not the search form.")
    page = next((int(v) for k, v in parse_qsl(urlsplit(url).query) if k.lower() == "page" and v.isdigit()), 1)
    next_url = ""
    for link in soup.select('a[href]'):
        candidate = urljoin(url, str(link.get("href")))
        try:
            candidate = validate_url(candidate, search=True)
        except HigherEdJobsError:
            continue
        current = dict((k.lower(), v) for k, v in parse_qsl(urlsplit(candidate).query))
        if current.get("page") == str(page + 1) and urlsplit(candidate).path == urlsplit(url).path:
            next_url = candidate
            break
    return {"jobs": list(jobs.values()), "next_url": next_url}


async def fetch_public(url: str, *, search: bool = False) -> str:
    url = validate_url(url, search=search)
    async with httpx.AsyncClient(timeout=20, follow_redirects=False, headers={"User-Agent": USER_AGENT}) as client:
        robots_response = await client.get("https://www.higheredjobs.com/robots.txt")
        robots_response.raise_for_status()
        checked_soup(robots_response.text) if "<html" in robots_response.text.lower() else None
        robots = RobotFileParser()
        robots.parse(robots_response.text.splitlines())
        for _ in range(4):
            if not robots.can_fetch(USER_AGENT, url):
                raise HigherEdJobsBlocked("HigherEdJobs robots.txt disallows this URL; use a public browser capture instead.")
            async with client.stream("GET", url) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    url = validate_url(urljoin(url, response.headers.get("location", "")), search=search)
                    continue
                if response.status_code in {403, 429}:
                    raise HigherEdJobsBlocked(f"HigherEdJobs returned HTTP {response.status_code}; use Chrome and saved public HTML. No automatic retries.")
                response.raise_for_status()
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > MAX_BYTES:
                        raise HigherEdJobsError("HigherEdJobs response exceeds the 2 MB limit.")
                html = data.decode(response.encoding or "utf-8", errors="replace")
                checked_soup(html)
                return html
        raise HigherEdJobsError("Too many HigherEdJobs redirects.")


async def discover(url: str = DEFAULT_SEARCH_URL, *, html: str = "", limit: int = 20, max_pages: int = 1,
                   observed_on: date | None = None) -> dict:
    url = validate_url(url, search=True)
    if not 1 <= limit <= 50 or not 1 <= max_pages <= 3:
        raise HigherEdJobsError("Use 1-50 results and 1-3 pages.")
    jobs, pages, warnings = {}, [], []
    current = url
    for index in range(1 if html else max_pages):
        try:
            capture = html if html else await fetch_public(current, search=True)
            parsed = parse_search(current, capture, observed_on=observed_on)
        except (HigherEdJobsError, httpx.HTTPError) as exc:
            if not pages:
                raise
            warnings.append(f"Stopped after {len(pages)} page(s): {exc}")
            break
        pages.append({"url": current, "sha256": hashlib.sha256(capture.encode()).hexdigest(), "count": len(parsed["jobs"])})
        for job in parsed["jobs"]:
            jobs.setdefault(job["url"], job)
        if len(jobs) >= limit or not parsed["next_url"]:
            break
        current = parsed["next_url"]
        await asyncio.sleep(1)
    if html and max_pages > 1:
        warnings.append("Saved HTML parses only the supplied page; no additional pages were fetched.")
    return {"source": "higheredjobs", "search_url": url, "capture_mode": "saved_html" if html else "public_http",
            "jobs": list(jobs.values())[:limit], "pages": pages, "warnings": warnings}
