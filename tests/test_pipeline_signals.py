import os
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from jessa_app import winnability as w
from jessa_app.services.importer import _linkedin_top_card_fields

NOW = datetime(2026, 10, 9, tzinfo=timezone.utc)


class WinnabilityParsingTests(unittest.TestCase):
    def test_posting_age(self):
        self.assertEqual(w.parse_posting_age_days("3 days ago", NOW), 3)
        self.assertEqual(w.parse_posting_age_days("Reposted 2 weeks ago", NOW), 14)
        self.assertEqual(w.parse_posting_age_days("1 month ago", NOW), 30)
        self.assertEqual(w.parse_posting_age_days("5 hours ago", NOW), 0)
        self.assertEqual(w.parse_posting_age_days("2026-10-01", NOW), 8)
        self.assertEqual(w.parse_posting_age_days("30+ days ago", NOW), 30)
        self.assertIsNone(w.parse_posting_age_days("", NOW))
        self.assertIsNone(w.parse_posting_age_days("Full-time", NOW))

    def test_applicant_count(self):
        self.assertEqual(w.parse_applicant_count("Over 100 applicants"), (100, True))
        self.assertEqual(w.parse_applicant_count("57 applicants"), (57, False))
        self.assertEqual(w.parse_applicant_count("100+ applicants"), (100, True))
        self.assertEqual(w.parse_applicant_count("Be an early applicant"), (None, False))

    def test_employer_type(self):
        self.assertEqual(w.employer_type("Jobgether"), "aggregator")
        self.assertEqual(w.employer_type("DataAnnotation"), "crowd_work")
        self.assertEqual(w.employer_type("Robert Half"), "staffing_agency")
        self.assertEqual(w.employer_type("Portland State University"), "higher_ed")
        self.assertEqual(w.employer_type("City of Portland"), "public_sector")
        self.assertEqual(w.employer_type("First Tech Federal Credit Union"), "direct")


class WinnabilityScoreTests(unittest.TestCase):
    def test_fresh_local_direct_scores_high(self):
        job = {"company": "Example Health", "location": "Hillsboro, OR - Hybrid",
               "posted_date": "2 days ago", "description": "Applicants: 18 applicants"}
        with patch.dict(os.environ, {"JESSA_LOCAL_LOCATIONS": "Portland,Hillsboro"}):
            result = w.score_job(job, NOW)
        self.assertEqual(result["band"], "High")
        self.assertGreaterEqual(result["score"], 80)

    def test_stale_crowded_aggregator_scores_low(self):
        job = {"company": "Jobgether", "location": "United States (Remote)", "posted_date": "Reposted 1 month ago",
               "description": "Applicants: Over 100 applicants\nApply method: Easy Apply"}
        result = w.score_job(job, NOW)
        self.assertEqual(result["band"], "Low")
        self.assertEqual(result["score"], 0)
        self.assertEqual(result["employer_type"], "aggregator")

    def test_unknown_signals_are_neutral(self):
        result = w.score_job({"company": "Acme", "location": "Denver, CO"}, NOW)
        self.assertEqual(result["score"], 50)
        self.assertEqual(result["band"], "Medium")

    def test_combine_recommendation(self):
        low = {"score": 20, "band": "Low", "employer_type": "direct", "factors": [{"points": -20, "factor": "stale posting (40d)"}]}
        self.assertEqual(w.combine_recommendation("Apply", low)[0], "Maybe")
        self.assertIn("stale posting", w.combine_recommendation("Apply", low)[1])
        agg = {"score": 55, "band": "Medium", "employer_type": "aggregator", "factors": []}
        self.assertEqual(w.combine_recommendation("Apply", agg)[0], "Maybe")
        high = {"score": 80, "band": "High", "employer_type": "direct", "factors": []}
        self.assertEqual(w.combine_recommendation("Apply", high), ("Apply", ""))
        self.assertEqual(w.combine_recommendation("Skip", low), ("Skip", ""))


class LinkedInTopCardTests(unittest.TestCase):
    def test_applicants_and_easy_apply_captured(self):
        lines = ["Example Co", "Platform Engineer", "Portland, OR · 3 days ago · Over 100 applicants",
                 "Hybrid", "Full-time", "Easy Apply"]
        fields = _linkedin_top_card_fields(lines, "")
        self.assertEqual(fields["posted_date"], "3 days ago")
        self.assertEqual(fields["applicants"], "Over 100 applicants")
        self.assertEqual(fields["apply_method"], "Easy Apply")


class StatusRankTests(unittest.TestCase):
    """The email auto-advance rules must not revert quiet statuses on a late confirmation."""

    @classmethod
    def setUpClass(cls):
        try:
            from jessa_app import main
        except Exception as exc:  # pragma: no cover - import needs app dependencies only
            raise unittest.SkipTest(f"main import unavailable: {exc}")
        cls.main = main

    def test_new_statuses_are_valid(self):
        for status in ("blocked", "ghosted", "no_response"):
            self.assertIn(status, self.main.JOB_STATUS_VALUES)

    def test_late_confirmation_does_not_reopen(self):
        should = self.main._should_apply_email_status
        self.assertFalse(should("no_response", "applied"))
        self.assertFalse(should("ghosted", "applied"))
        self.assertFalse(should("ghosted", "follow-up"))

    def test_real_progress_reopens(self):
        should = self.main._should_apply_email_status
        self.assertTrue(should("no_response", "interview"))
        self.assertTrue(should("no_response", "follow-up"))
        self.assertTrue(should("ghosted", "interview"))
        self.assertTrue(should("no_response", "rejected"))

    def test_quiet_statuses_archive(self):
        self.assertTrue(self.main._auto_archive_status("no_response"))
        self.assertTrue(self.main._auto_archive_status("ghosted"))
        self.assertFalse(self.main._auto_archive_status("blocked"))


if __name__ == "__main__":
    unittest.main()
