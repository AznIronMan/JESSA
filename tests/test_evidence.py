import unittest

from jessa_app.evidence import (
    build_evidence_context,
    ranked_evidence,
    visible_evidence,
)


def item(
    external_id,
    *,
    scope="global",
    job_id=None,
    title="",
    content="",
    tags=None,
    claim_status="verified",
    category="career",
    confidentiality="reusable",
):
    return {
        "external_id": external_id,
        "scope": scope,
        "job_id": job_id,
        "title": title,
        "content": content,
        "tags": tags or [],
        "claim_status": claim_status,
        "category": category,
        "confidentiality": confidentiality,
        "source_heading": title,
        "employer": "",
    }


class EvidenceTests(unittest.TestCase):
    def test_visible_evidence_never_crosses_job_boundaries(self):
        values = [
            item("global"),
            item("job-655", scope="job", job_id=655),
            item("job-900", scope="job", job_id=900),
        ]
        self.assertEqual(
            [value["external_id"] for value in visible_evidence(values, None)],
            ["global"],
        )
        self.assertEqual(
            [value["external_id"] for value in visible_evidence(values, 655)],
            ["global", "job-655"],
        )

    def test_ranked_evidence_prefers_title_tags_and_employer_signals(self):
        values = [
            item(
                "salesforce",
                title="Salesforce claim boundary",
                tags=["salesforce"],
                content="Limited CRM access.",
            ),
            item("generic", title="General workflow", content="General modernization background."),
        ]
        ranked = ranked_evidence(values, "Salesforce CRM governance")
        self.assertEqual(ranked[0]["external_id"], "salesforce")
        self.assertEqual(ranked_evidence(values, "unrelated-secret-name"), [])

    def test_do_not_claim_is_present_as_a_control_not_positive_evidence(self):
        values = [
            item(
                "control",
                title="Salesforce",
                content="Do not claim Salesforce administrator experience.",
                claim_status="do_not_claim",
                category="claim-controls",
            ),
            item(
                "parallel",
                title="Workflow governance",
                content="Use platform governance and structured workflow evidence.",
            ),
        ]
        context = build_evidence_context(values, "Salesforce platform governance")
        self.assertIn("DO_NOT_CLAIM", context)
        self.assertIn("Do not claim Salesforce administrator experience.", context)
        self.assertIn("Workflow governance", context)

    def test_context_labels_current_job_evidence_as_non_reusable(self):
        values = [
            item(
                "job",
                scope="job",
                job_id=655,
                title="Internal referral context",
                content="Current-job-only detail.",
                claim_status="context_only",
            )
        ]
        context = build_evidence_context(values, "internal referral")
        self.assertIn("Current-Job Evidence", context)
        self.assertIn("Never reuse it for another employer.", context)

    def test_current_job_confidential_context_has_a_reserved_section(self):
        values = [
            item(
                "confidential",
                scope="job",
                job_id=655,
                title="CEO preference",
                content="Internal current-job guidance.",
                claim_status="context_only",
                confidentiality="job_confidential",
            )
        ]
        context = build_evidence_context(values, "unrelated public requirements")
        self.assertIn("Current-Job Confidential Context", context)
        self.assertIn("Internal current-job guidance.", context)


if __name__ == "__main__":
    unittest.main()
