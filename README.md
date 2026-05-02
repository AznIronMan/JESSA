# JESSA

JESSA is a local job-search workstation for Geoff Clark. It imports job URLs or pasted job text, stores applications in SQLite, scores role fit against the editable core profile, generates application materials with OpenAI, and classifies Google Workspace email updates through IMAP.

Current version: `1.1.2`

## v1.1 Scope

- Local FastAPI web app with static HTML/CSS/JS.
- SQLite persistence in `data/jessa.sqlite3`.
- Editable core profile seeded from `jessa_gpt_instructions.txt`.
- Job import from URL with HTTP + BeautifulSoup extraction.
- Optional rendered-page import through Playwright.
- Manual pasted-text import fallback.
- LLM role analysis:
  - match percentage
  - qualification band
  - interview odds estimate
  - salary ask/floor guidance
  - resume base recommendation
  - apply/maybe/skip recommendation
  - tailored resume notes
  - cover letter draft
- Full application package generation:
  - tailored resume artifact
  - tailored cover-letter artifact
  - editable version history
  - submitted timestamp
  - PDF download
- Supplemental question workflow:
  - paste employer questions
  - generate paste-ready answers
  - store answers as a submitted/draft artifact
- Expanded status tracking:
  - applied
  - not applied
  - not for me
  - on hold
  - job expired
  - rejected/interview/follow-up
  - timestamped event log
- IMAP inbox sync and heuristic classification:
  - application confirmation
  - interview request
  - assessment request
  - rejection
  - recruiter outreach
- SMTP login test.

## Setup

Create and activate the virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Optional Playwright browser install:

```bash
python -m playwright install chromium
```

On Linux, Playwright's bundled Chromium installer may reject the current distro label. The app will automatically use `google-chrome`, `google-chrome-stable`, `chromium`, or `chromium-browser` when present on `PATH`.

On macOS, the app will automatically use Google Chrome when it is installed at:

```text
/Applications/Google Chrome.app/Contents/MacOS/Google Chrome
```

You can override the browser path on either OS:

```bash
PLAYWRIGHT_BROWSER_PATH=/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome
```

Initialize and run:

```bash
source .venv/bin/activate
uvicorn jessa_app.main:app --reload --host 0.0.0.0 --port 8765
```

Or use the project launcher:

```bash
./start_jessa.sh
```

To run on another port:

```bash
JESSA_PORT=8766 ./start_jessa.sh
```

Open:

```text
http://127.0.0.1:8765
http://<this-mac-10.0.x.x-ip>:8765
```

The launcher binds to `0.0.0.0` by default so other `10.0.x.x` devices can reach it. The app still rejects clients outside the configured allowed networks.

## Environment

The app reads `.env` from the project root. Keep `.env` out of git.

Minimum:

```bash
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-5.4-mini

EMAIL_USER=geoff@example.com
EMAIL_APP_PASSWORD=...
```

Google Workspace/Gmail defaults are inferred:

```bash
EMAIL_IMAP_HOST=imap.gmail.com
EMAIL_IMAP_PORT=993
EMAIL_SMTP_HOST=smtp.gmail.com
EMAIL_SMTP_PORT=587
EMAIL_FROM=$EMAIL_USER
```

Optional app path overrides:

```bash
JESSA_DB_PATH=data/jessa.sqlite3
JESSA_PROFILE_SOURCE=jessa_gpt_instructions.txt
JESSA_RESUME_DIR=~/Documents/job_hunting
```

LAN access defaults:

```bash
JESSA_ALLOWED_CLIENT_NETWORKS=127.0.0.0/8,::1/128,10.0.0.0/8
```

Use `./stop_jessa.sh` to stop a running JESSA web app process started from this project.

Google Workspace notes:

- IMAP must be enabled for the user in Google Workspace Admin and in Gmail settings.
- Google’s current Workspace guidance recommends OAuth-capable clients, but app passwords remain the practical path for older/simple IMAP-SMTP clients when allowed by account policy.
- Use the app password as `EMAIL_APP_PASSWORD`, not the normal account password.

## Usage

### Jobs

Paste a job URL and click `Import`. If a page is heavily dynamic, choose `Rendered`; this uses a visible Playwright Chromium session. If URL import fails, paste the job text and use `Import Text`.

Select a job on the left, review/edit fields, then click `Analyze`. JESSA updates the match metrics, salary target, resume base, resume notes, and cover letter.

Click `Generate Docs` to create a tailored resume and cover letter. These are stored in the Application Materials section as versioned artifacts. Edit the text if needed, click `Save`, then use `PDF` to download. After you submit a document to an employer, click `Mark Submitted` so JESSA records what went out and when.

Paste application questions into `Supplemental Questions` and click `Generate Answers`. The generated answers are saved as another artifact, with the same save, PDF, and submitted tracking.

### Core Profile

The core profile is the source of truth for future scoring and resume generation. Fix dates, titles, canonical bullets, and career rules here first. Every save increments the profile version.

Resume-source rule: the Director and DevSecOps resumes are the preferred current version of the career history. The unabridged resume is retained for older detail and context, not as the primary canonical source when there is a conflict.

### Email

`Sync Inbox` checks recent inbox messages, classifies job-search mail, and links messages to jobs by company/title when possible. `Test SMTP` only verifies login; v1.0 does not auto-send email.

## Data Model

SQLite tables:

- `core_profile`
- `jobs`
- `job_events`
- `emails`
- `application_artifacts`

Generated resumes, cover letters, and supplemental answers are stored in `application_artifacts` with version numbers and submitted timestamps. `job_events` records status changes and document lifecycle actions.

## Versioning

This project uses semantic versioning.

- `1.0.x`: bug fixes and small UI/API improvements.
- `1.x.0`: additive features that preserve the local database shape or migrate it safely.
- `2.0.0`: breaking schema or workflow changes.

## Roadmap

- DOCX export.
- ATS-specific assisted form filling.
- OAuth-based Gmail integration.
- Salary benchmark source adapters.
- Feedback loop that compares match scores against actual interview outcomes.

## Changelog

### 1.1.2

- Changed the launcher default bind host to `0.0.0.0` for LAN access.
- Added app-level client network filtering for localhost and `10.0.0.0/8` by default.
- Added `stop_jessa.sh` to find and stop the running JESSA web app process.

### 1.1.1

- Added macOS Chrome auto-detection for rendered job imports after Ubuntu-to-macOS migration.
- Made the launcher port check cross-platform.
- Documented the macOS browser path and local resume directory override.

### 1.1.0

- Added versioned application artifacts.
- Added tailored resume and cover-letter generation.
- Added PDF downloads.
- Added supplemental question answer generation.
- Added submitted timestamp tracking for artifacts.
- Expanded job statuses and status/event log display.

### 1.0.0

- Initial local app with job import, analysis, core profile editing, and email sync.

## Sources

- Google Workspace Gmail IMAP/SMTP settings: https://knowledge.workspace.google.com/admin/sync/set-up-gmail-with-a-third-party-email-client
- OpenAI Structured Outputs and Responses API: https://developers.openai.com/api/docs/guides/structured-outputs
