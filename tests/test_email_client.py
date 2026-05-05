from __future__ import annotations

import unittest

from jessa_app.services.email_client import classify, match_job


class EmailClassifierTests(unittest.TestCase):
    def test_bulk_newsletters_do_not_get_actionable_labels(self) -> None:
        label, confidence, _ = classify(
            "Copy Fail Roots Linux, DPRK Web3 Job Attacks",
            "Today's security newsletter includes a coding challenge and next steps for readers.",
            "TLDR InfoSec <dan@tldrnewsletter.com>",
        )

        self.assertEqual(label, "unclassified")
        self.assertLess(confidence, 0.5)

    def test_non_job_rejection_language_is_not_a_rejection(self) -> None:
        label, confidence, _ = classify(
            "Alert from Zander ID Theft Solutions",
            "Unfortunately, we could not verify this alert and will not be proceeding.",
            '"Zander ID Theft Solutions" <noreply@Email.ZanderIDT.com>',
        )

        self.assertEqual(label, "unclassified")
        self.assertLess(confidence, 0.5)

    def test_real_application_confirmation_still_classifies(self) -> None:
        label, confidence, _ = classify(
            "Thank you for applying at IndustrialEnet",
            "We received your application for the Systems Engineer position.",
            "IndustrialEnet <notifications@app.bamboohr.com>",
        )

        self.assertEqual(label, "application_confirmation")
        self.assertGreaterEqual(confidence, 0.78)

    def test_ziprecruiter_job_alert_is_not_recruiter_outreach(self) -> None:
        label, confidence, _ = classify(
            "$156K/yr Principal Enterprise Applications Engineer job in Portland, OR",
            "These new jobs may be a match for your profile.",
            "ZipRecruiter <alerts@ziprecruiter.com>",
        )

        self.assertEqual(label, "unclassified")
        self.assertLess(confidence, 0.5)

    def test_interview_request_needs_job_context(self) -> None:
        label, confidence, _ = classify(
            "Interview availability for Systems Engineer",
            "We would like to schedule an interview for this role.",
            "Recruiter <recruiter@example.com>",
        )

        self.assertEqual(label, "interview_request")
        self.assertGreaterEqual(confidence, 0.78)


class EmailJobMatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.jobs = [
            {
                "id": 1,
                "company": "Blueprint Technologies",
                "title": "Linux Kernel & Device Driver Engineer",
            },
            {"id": 2, "company": "IndustrialEnet", "title": "Systems Engineer"},
            {"id": 3, "company": "Amazon.com Services LLC", "title": "IT Manager , OTS"},
        ]

    def test_company_evidence_matches_the_right_job(self) -> None:
        match = match_job(
            "Thank you for applying at IndustrialEnet",
            "We received your application for the Systems Engineer position.",
            "IndustrialEnet <notifications@app.bamboohr.com>",
            self.jobs,
        )

        self.assertEqual(match.job_id, 2)
        self.assertGreaterEqual(match.confidence, 0.60)
        self.assertIn("company", match.reason)

    def test_title_only_application_does_not_guess_a_job(self) -> None:
        match = match_job(
            "Indeed Application: Systems Engineer",
            "Your application has been submitted.",
            '"Indeed Apply" <indeedapply@indeed.com>',
            self.jobs,
        )

        self.assertIsNone(match.job_id)
        self.assertEqual(match.confidence, 0.0)

    def test_bulk_retail_email_does_not_match_same_brand_job(self) -> None:
        match = match_job(
            "Don't let anything pull you out of the game",
            "Amazon.com services sale with accessories for IT managers.",
            '"Amazon.com" <store-news@amazon.com>',
            self.jobs,
        )

        self.assertIsNone(match.job_id)
        self.assertEqual(match.confidence, 0.0)


if __name__ == "__main__":
    unittest.main()
