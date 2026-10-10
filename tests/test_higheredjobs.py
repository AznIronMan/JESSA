from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException

from jessa_app.services import higheredjobs as h
from jessa_app.services.importer import parse_html, source_from_url
from jessa_app import main

FIXTURES = Path(__file__).parent / "fixtures"
POSTING_URL = "https://www.higheredjobs.com/admin/details.cfm?JobCode=179580105&Title=Director&utm_source=mail"
CAPTURE_DATE = date(2026, 10, 9)


class HigherEdJobsParserTests(unittest.TestCase):
    def test_observed_dom_fields_and_employer_link_without_redirect(self):
        job = h.parse_posting(POSTING_URL, (FIXTURES / "higheredjobs_posting.html").read_text(), observed_on=CAPTURE_DATE)
        self.assertEqual(job["title"], "Director of Information Technology")
        self.assertEqual(job["company"], "Example University")
        self.assertEqual(job["location"], "Portland, OR")
        self.assertEqual(job["salary"], "$205,363 - $236,168 per year")
        self.assertEqual(job["posted_date"], "2026-10-06")
        self.assertEqual(job["details"]["deadline"], "2026-10-25")
        self.assertIn("IT Manager/Director", job["details"]["category"])
        self.assertEqual(job["apply_url"], "https://careers.example.edu/jobs/123")
        self.assertNotIn("Save this Job", job["description"])
        self.assertIn("Manage IT staff", job["description"])
        self.assertEqual(job["url"], "https://www.higheredjobs.com/admin/details.cfm?JobCode=179580105")

    def test_search_extracts_card_employer_location_dates_and_pagination(self):
        result = h.parse_search(h.DEFAULT_SEARCH_URL, (FIXTURES / "higheredjobs_search.html").read_text(), observed_on=CAPTURE_DATE)
        self.assertEqual(len(result["jobs"]), 2)
        self.assertEqual(result["jobs"][0]["company"], "ASU Mid-South")
        self.assertEqual(result["jobs"][0]["location"], "West Memphis, AR")
        self.assertEqual(result["jobs"][1]["posted_date"], "2026-10-09")
        self.assertIn("Page=2", result["next_url"])

    def test_rss_parsing_deduplicates_jobcode_aliases(self):
        feed = '<rss><channel><item><title>IT Director</title><link>https://www.higheredjobs.com/admin/details.cfm?JobCode=123&amp;Title=A</link><pubDate>Fri, 09 Oct 2026 10:00:00 GMT</pubDate></item><item><title>IT Director</title><link>https://www.higheredjobs.com/admin/details.cfm?JobCode=123&amp;Title=B</link></item></channel></rss>'
        result = h.parse_search("https://www.higheredjobs.com/search/rss.cfm?JobCat=163", feed)
        self.assertEqual(len(result["jobs"]), 1)
        self.assertEqual(result["jobs"][0]["job_code"], "123")
        self.assertEqual(result["jobs"][0]["posted_date"], "2026-10-09")

    def test_security_pages_and_wrong_capture_are_not_jobs(self):
        for html in ('<html><script src="/_Incapsula_Resource"></script><body></body></html>',
                     '<html><body>Additional security check is required</body></html>'):
            with self.assertRaises(h.HigherEdJobsBlocked):
                h.parse_posting(POSTING_URL, html)
        with self.assertRaises(h.HigherEdJobsError):
            h.parse_posting(POSTING_URL, (FIXTURES / "higheredjobs_search.html").read_text())
        with self.assertRaises(h.HigherEdJobsError):
            h.parse_posting(POSTING_URL.replace("179580105", "123"), (FIXTURES / "higheredjobs_posting.html").read_text())

    def test_network_boundary_and_no_clickthru_fetch(self):
        for url in ("https://evil.example/admin/search.cfm", "http://www.higheredjobs.com/admin/search.cfm",
                    "https://www.higheredjobs.com:8080/admin/search.cfm", "https://user:pass@www.higheredjobs.com/admin/search.cfm",
                    "https://www.higheredjobs.com/clickthru/redirect.cfm?JobCode=123"):
            with self.assertRaises(h.HigherEdJobsError):
                h.validate_url(url, search=True)
        with self.assertRaises(h.HigherEdJobsError):
            h.canonical_job_url("https://www.higheredjobs.com/admin/details.cfm?JobCode=1&jobcode=2")

    def test_importer_routes_higheredjobs_and_normalizes_source(self):
        self.assertEqual(source_from_url(POSTING_URL), "higheredjobs")
        imported = parse_html(POSTING_URL, (FIXTURES / "higheredjobs_posting.html").read_text())
        self.assertEqual(imported.company, "Example University")
        self.assertIn("Application Due: 2026-10-25", imported.description)

    def test_unknown_dates_are_not_invented(self):
        self.assertEqual(h.normalized_date("Open until filled", CAPTURE_DATE), "")
        self.assertEqual(h.normalized_date("02/30/2026", CAPTURE_DATE), "")

    def test_legacy_url_identity_and_conflicts(self):
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = [
            {"id": 10, "url": POSTING_URL, "status": "interview", "title": "Director"},
            {"id": 11, "url": POSTING_URL.replace("Title=Director", "Title=Different"), "status": "new", "title": "Director"},
        ]
        by_url = main._higheredjobs_existing(conn)
        self.assertEqual(len(by_url), 1)
        self.assertEqual(next(iter(by_url.values()))["duplicate_ids"], [10, 11])


class HigherEdJobsDiscoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_bounds_and_stop_at_limit_without_more_requests(self):
        html = (FIXTURES / "higheredjobs_search.html").read_text()
        with patch.object(h, "fetch_public", new=AsyncMock(return_value=html)) as fetch:
            result = await h.discover(limit=1, max_pages=3, observed_on=CAPTURE_DATE)
            self.assertEqual(len(result["jobs"]), 1)
            fetch.assert_awaited_once()
        with self.assertRaises(h.HigherEdJobsError):
            await h.discover(limit=51)

    async def test_saved_capture_never_fetches_network(self):
        with patch.object(h, "fetch_public", new=AsyncMock()) as fetch:
            result = await h.discover(html=(FIXTURES / "higheredjobs_search.html").read_text(), max_pages=3)
            self.assertEqual(len(result["pages"]), 1)
            self.assertTrue(result["warnings"])
            fetch.assert_not_called()

    async def test_partial_crawl_reports_blocked_page(self):
        html = (FIXTURES / "higheredjobs_search.html").read_text()
        with patch.object(h, "fetch_public", new=AsyncMock(side_effect=[html, h.HigherEdJobsBlocked("security check")])), patch.object(h.asyncio, "sleep", new=AsyncMock()):
            result = await h.discover(limit=20, max_pages=2)
        self.assertEqual(len(result["jobs"]), 2)
        self.assertIn("security check", result["warnings"][0])

    async def test_api_challenge_response_does_not_touch_database(self):
        request = main.ImportRequest(url=POSTING_URL, html='<html>Additional security check is required</html>')
        with patch.object(main, "get_db") as db:
            with self.assertRaises(HTTPException) as caught:
                await main.import_job(request)
            self.assertEqual(caught.exception.status_code, 409)
            db.assert_not_called()

    async def test_preview_keeps_capture_date_and_does_not_import(self):
        request = main.HigherEdJobsPostingRequest(url=POSTING_URL, html=(FIXTURES / "higheredjobs_posting.html").read_text(), observed_on="2026-10-09")
        with patch.object(main, "get_db"), patch.object(main, "_higheredjobs_existing", return_value={}), patch.object(main, "_insert_or_update_job") as insert:
            result = await main.preview_higheredjobs(request)
        self.assertEqual(result["posted_date"], "2026-10-06")
        self.assertEqual(len(result["capture_sha256"]), 64)
        insert.assert_not_called()


if __name__ == "__main__":
    unittest.main()
