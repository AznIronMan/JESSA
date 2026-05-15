from __future__ import annotations

import json
import unittest

from jessa_app.services.importer import parse_html, source_from_url


PARTNERS_URL = (
    "https://jobs.partnersindiversity.org/job/s2z9pv/"
    "z-os-systems-administrator-(information-systems-specialist-8)/salem/or"
)
HEYHEALTHTECH_URL = "https://jobs.heyhealthtech.com/jobs/implementation-manager-enterprise-scribe-ccfb206f"


def partners_job_html() -> str:
    payload = {
        "@context": "http://schema.org/",
        "@type": "JobPosting",
        "baseSalary": {
            "@type": "MonetaryAmount",
            "currency": "USD",
            "value": {
                "@type": "QuantitativeValue",
                "minValue": "7807.00",
                "maxValue": "11823.00",
                "unitText": "MONTH",
            },
        },
        "datePosted": "2026-04-28T13:54:55.8470000",
        "description": "<p>z/OS Systems Administrator details.</p><ul><li>Support IBM mainframe environments.</li></ul>",
        "hiringOrganization": {
            "@type": "Organization",
            "name": "Oregon Department of Administrative Services",
        },
        "jobLocation": {
            "@type": "Place",
            "address": {
                "@type": "PostalAddress",
                "addressLocality": "Salem",
                "addressRegion": "OR",
                "addressCountry": "US",
            },
        },
        "title": "z/OS Systems Administrator (Information Systems Specialist 8)",
    }
    return f"""
    <html>
      <body>
        <h1>Fallback title</h1>
        <span id="lblOutEmployer">Oregon Department of Administrative Services</span>
        <input id="btnApply" onclick="window.open('/applyredirect/s2z9pv', 'Employer_Window'); return(false);" />
        <span id="lblOutPostedDate">4/28/2026</span>
        <span id="lblOutSalary">7,807.00 - 11,823.00 Month</span>
        <div class="customFields">
          <div class="formItemContainer">
            <span class="formLabel">Location</span>
            <span class="formDataLabel">Hybrid</span>
          </div>
          <div class="formItemContainer">
            <span class="formLabel">Position Type</span>
            <span class="formDataLabel">Full Time</span>
          </div>
          <div class="formItemContainer">
            <span class="formLabel">Experience</span>
            <span class="formDataLabel">2-5 years | 5-10 years</span>
          </div>
        </div>
        <span id="lblOutAddress">550 Airport RD SE<br />Salem, OR 97301</span>
        <script type="application/ld+json">{json.dumps(payload)}</script>
      </body>
    </html>
    """


def heyhealthtech_job_html() -> str:
    payload = {
        "@context": "https://schema.org/",
        "@type": "JobPosting",
        "title": "Implementation Manager, Enterprise Scribe",
        "description": (
            "<p>At Commure, our mission is to simplify healthcare.</p>"
            "<p><strong>About the Role</strong></p>"
            "<p>We are seeking an experienced Implementation Manager.</p>"
            "<ul><li>Drive end-to-end activation projects.</li></ul>"
        ),
        "url": HEYHEALTHTECH_URL,
        "identifier": {"@type": "PropertyValue", "name": "Commure", "value": 9985989},
        "datePosted": "2026-04-26",
        "hiringOrganization": {
            "@type": "Organization",
            "name": "Commure",
            "sameAs": "https://commure.com",
        },
        "directApply": True,
        "validThrough": "2026-05-26T16:51:58-05:00",
        "employmentType": "FULL_TIME",
        "baseSalary": {
            "@type": "MonetaryAmount",
            "currency": "USD",
            "value": {
                "@type": "QuantitativeValue",
                "unitText": "YEAR",
                "minValue": 100000.0,
                "maxValue": 140000.0,
            },
        },
        "jobLocationType": "TELECOMMUTE",
        "applicantLocationRequirements": {"@type": "Country", "name": "United States"},
    }
    return f"""
    <html>
      <head>
        <meta property="og:title" content="Implementation Manager, Enterprise Scribe" />
      </head>
      <body>
        <div data-controller="job">
          <div>
            <h1>Implementation Manager, Enterprise Scribe</h1>
            <a target="_blank" rel="noopener noreferrer" href="https://commure.com">Commure</a>
            <div id="job-posted-at">18 days ago</div>
            <div>Full-time</div>
            <div>Remote</div>
            <div>United States</div>
            <div>$100,000 - $140,000 USD yearly</div>
            <div>Non-clinical</div>
          </div>
          <div class="rich-text">
            <p>At Commure, our mission is to simplify healthcare.</p>
            <p><strong>About the Role</strong></p>
            <p>We are seeking an experienced Implementation Manager.</p>
          </div>
          <a id="apply-btn" target="_blank" rel="noopener noreferrer"
             href="https://www.commure.com/careers?ashby_jid=fa8384c7-d12c-42f9-95a1-892f2e89bd25&amp;utm_id=9985989&amp;utm_source=Hey+Health+Tech+Job+Board#career-content">Apply now</a>
        </div>
        <script type="application/ld+json">{json.dumps(payload)}</script>
      </body>
    </html>
    """


class ImporterTests(unittest.TestCase):
    def test_partnersindiversity_source_from_url(self) -> None:
        self.assertEqual(source_from_url(PARTNERS_URL), "partnersindiversity")

    def test_partnersindiversity_jobboardhq_fields(self) -> None:
        imported = parse_html(PARTNERS_URL, partners_job_html())

        self.assertEqual(imported.source, "partnersindiversity")
        self.assertEqual(imported.title, "z/OS Systems Administrator (Information Systems Specialist 8)")
        self.assertEqual(imported.company, "Oregon Department of Administrative Services")
        self.assertEqual(imported.location, "Salem, OR, US - Hybrid")
        self.assertEqual(imported.salary, "USD 7,807.00 - 11,823.00 Month")
        self.assertEqual(imported.posted_date, "2026-04-28T13:54:55.8470000")
        self.assertEqual(imported.apply_url, "https://jobs.partnersindiversity.org/applyredirect/s2z9pv")
        self.assertIn("Support IBM mainframe environments.", imported.description)
        self.assertIn("Location mode: Hybrid", imported.description)
        self.assertIn("Position Type: Full Time", imported.description)
        self.assertIn("Address: 550 Airport RD SE Salem, OR 97301", imported.description)

    def test_heyhealthtech_source_from_url(self) -> None:
        self.assertEqual(source_from_url(HEYHEALTHTECH_URL), "heyhealthtech")

    def test_heyhealthtech_jobboardly_fields(self) -> None:
        imported = parse_html(HEYHEALTHTECH_URL, heyhealthtech_job_html())

        self.assertEqual(imported.source, "heyhealthtech")
        self.assertEqual(imported.title, "Implementation Manager, Enterprise Scribe")
        self.assertEqual(imported.company, "Commure")
        self.assertEqual(imported.location, "Remote - United States")
        self.assertEqual(imported.salary, "$100,000 - $140,000 USD yearly")
        self.assertEqual(imported.posted_date, "2026-04-26")
        self.assertEqual(
            imported.apply_url,
            "https://www.commure.com/careers?ashby_jid=fa8384c7-d12c-42f9-95a1-892f2e89bd25"
            "&utm_id=9985989&utm_source=Hey+Health+Tech+Job+Board#career-content",
        )
        self.assertIn("Drive end-to-end activation projects.", imported.description)
        self.assertIn("Employment Type: Full-time", imported.description)
        self.assertIn("Applicant location: United States", imported.description)
        self.assertIn("Category: Non-clinical", imported.description)


if __name__ == "__main__":
    unittest.main()
