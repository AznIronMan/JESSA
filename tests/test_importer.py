from __future__ import annotations

import json
import unittest

from jessa_app.services.importer import parse_html, source_from_url


PARTNERS_URL = (
    "https://jobs.partnersindiversity.org/job/s2z9pv/"
    "z-os-systems-administrator-(information-systems-specialist-8)/salem/or"
)


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


if __name__ == "__main__":
    unittest.main()
