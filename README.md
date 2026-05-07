# JESSA

JESSA stands for **Job Engineering Smart Search Assistant**. It is a portable job-search workstation that imports job URLs or pasted job text, stores applications in PostgreSQL, scores role fit against an editable candidate core profile, generates application materials with the configured LLM provider, and classifies Google Workspace email updates through IMAP.

Current version: `3.2.3`

## v3.2.3 Scope

- Added explicit Partners in Diversity Career Center URL imports.
- Partners in Diversity imports now use JobBoardHQ JSON-LD plus page fields for apply redirects, work mode, position type, experience, job category, address, and salary display.
- Verified support for Partners in Diversity job URLs containing parenthesized slugs.

## v3.2.2 Scope

- `start_jessa.sh` and `stop_jessa.sh` now read `JESSA_HOST` and `JESSA_PORT` from `.env`.
- The process environment still overrides `.env` for one-off launches.
- The current local listener port is set to `9122`.

## v3.2.1 Scope

- Reduced email-sync false positives from newsletters, job-alert digests, retail notices, and generic alerts.
- Tightened email-to-job matching so title-only overlaps no longer guess a job.
- Raised the match-confidence gate for automatic email-driven status changes.
- Hid legacy zero-confidence email links from job detail and email-list views.
- Re-reviewed stored email metadata at startup with the stricter classifier without applying status changes.

## v3.2 Scope

- Added a gear-button Settings dialog in the top bar.
- Settings can edit supported `.env` values through grouped, user-friendly controls.
- Secret values are write-only in the UI: configured keys are indicated, but current secret values are not returned by the API.
- Saving settings preserves unknown `.env` entries and comments where possible; restart JESSA after saving to apply changed environment values.

## v3.1 Scope

- Startup no longer exits before the web UI when PostgreSQL is missing or invalid.
- `/api/startup` and `/api/health` report setup issues for PostgreSQL and LLM provider configuration.
- LLM provider priority is controlled by `JESSA_LLM_PROVIDER_PRIORITY`, defaulting to `openai,claude,gemini,grok`.
- OpenAI, Claude, Gemini, and Grok API keys are supported. Missing or failing providers are skipped in priority order during generation.
- A first-run setup view directs new users to configure `.env` and fill the Core Profile.
- The Core Profile page can import pasted profile text or uploaded text/PDF resumes without changing existing saved profile data unless the user clicks Import.

## v3.0 Scope

- Portable candidate profile defaults with no hardcoded personal identity.
- DB-backed default JESSA system prompt in `app_prompts`.
- Existing user-specific profile content remains in `core_profile`; fresh databases start with a generic candidate profile template.
- Removed the deprecated `jessa_gpt_instructions.txt` file from runtime seeding.
- Cold-start PostgreSQL initialization can create the configured database when the configured user has permission, then create all runtime tables from scratch.
- Git workflow targets `main` on `origin` at `https://github.com/AznIronMan/JESSA.git`.

## v2.0 Scope

- Local FastAPI web app with static HTML/CSS/JS.
- PostgreSQL persistence for all runtime application data.
- One-time legacy SQLite import from `data/jessa.sqlite3` through `scripts/migrate_sqlite_to_postgres.py`.
- Editable core profile seeded from a generic built-in template or from an optional `JESSA_PROFILE_SOURCE` file.
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

## v2.1 Scope

- Job lifecycle views in the left rail:
  - active
  - archived
  - Trash Bin
- Soft-delete jobs from the left list or detail view.
- Recover trashed jobs for 24 hours.
- Automatically purge Trash Bin jobs on startup or job-list access after the recovery window expires.
- Auto-archive jobs when their status changes to:
  - not for me
  - job expired
  - rejected
- Restore archived jobs back to the active list.

## v2.2 Scope

- Status filtering for the active jobs list.
- Checked-row multi-select in the jobs list.
- Bulk move selected jobs to the Trash Bin.
- Bulk status updates for selected active or archived jobs.
- `Analyze` now also generates a tailored resume and cover-letter artifact.
- Manual application package actions are labeled as regenerate actions because each run creates a new artifact version.

## v2.3 Scope

- LinkedIn job URL imports use a local, visible, persistent browser profile.
- LinkedIn job imports fall back to rendered visible text when LinkedIn's static selectors do not expose the top-card fields.
- LinkedIn URLs automatically switch the import method to `LinkedIn` in the UI.
- The candidate's LinkedIn profile can be cached from a profile URL or saved from pasted profile text.
- LinkedIn profile caching captures the main profile plus supported detail sections such as experience, education, certifications, skills, projects, volunteering, recommendations, and honors.
- Cached LinkedIn profile content is appended to the candidate context for job analysis, application package generation, and supplemental answers.

## v2.4 Scope

- Email sync records job-match confidence and match reasoning for linked messages.
- High-confidence matched application emails can advance job status automatically.
- Rejection emails mark matched jobs as `rejected`, which moves them to Archived through the existing terminal-status lifecycle rule.
- Email views show match reasoning, matched job labels, and any status action taken during sync.

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
uvicorn jessa_app.main:app --reload --host 0.0.0.0 --port 9122
```

Or use the project launcher. It reads `JESSA_HOST` and `JESSA_PORT` from `.env`:

```bash
JESSA_HOST=0.0.0.0
JESSA_PORT=9122
```

```bash
./start_jessa.sh
```

To run on another port for a single launch:

```bash
JESSA_PORT=8766 ./start_jessa.sh
```

Open:

```text
http://127.0.0.1:9122
http://<this-mac-10.0.x.x-ip>:9122
```

The launcher binds to `0.0.0.0` by default so other `10.0.x.x` devices can reach it. The app still rejects clients outside the configured allowed networks.

## Environment

The app reads `.env` from the project root. Keep `.env` out of git.

Use the gear button in the top-right corner to edit supported `.env` settings from the web UI. Secret values are not displayed; leave a secret field blank to keep the existing value, or type a new value to replace it. Restart JESSA after saving settings.

Minimum:

```bash
EMAIL_USER=you@example.com
EMAIL_APP_PASSWORD=...

POSTGRES_HOST=127.0.0.1
POSTGRES_PORT=5432
POSTGRES_USER=jessa
POSTGRES_PASS=...
POSTGRES_DB_NAME=jessa
```

At least one LLM provider key is required for AI analysis and document generation:

```bash
OPENAI_API_KEY=
OPENAI_MODEL=gpt-5.4-mini

CLAUDE_API_KEY=
CLAUDE_MODEL=claude-sonnet-4-5

GEMINI_API_KEY=
GEMINI_MODEL=gemini-2.0-flash

GROK_API_KEY=
GROK_MODEL=grok-4.3

JESSA_LLM_PROVIDER_PRIORITY=openai,claude,gemini,grok
```

JESSA tries providers from left to right and skips providers that are missing a key or fail during a generation request. `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`, and `XAI_API_KEY` are also accepted aliases for Claude, Gemini, and Grok respectively.

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
JESSA_PROFILE_SOURCE=
JESSA_RESUME_DIR=~/Documents/job_hunting
```

`JESSA_PROFILE_SOURCE` is optional and only seeds `core_profile` when the configured PostgreSQL database has no profile row yet. Leave it blank for the portable built-in starter profile.

LinkedIn support:

```bash
JESSA_LINKEDIN_PROFILE_URL=https://www.linkedin.com/in/...
JESSA_LINKEDIN_BROWSER_PROFILE_DIR=data/linkedin-browser
JESSA_LINKEDIN_PAGE_SETTLE_MS=7000
JESSA_LINKEDIN_LOGIN_WAIT_MS=180000
JESSA_LINKEDIN_PROFILE_CAPTURE_WAIT_MS=180000
JESSA_LINKEDIN_PROFILE_SCROLL_PASSES=8
JESSA_LINKEDIN_MIN_PROFILE_CONTENT_CHARS=400
```

Legacy SQLite import path:

```bash
JESSA_SQLITE_IMPORT_PATH=data/jessa.sqlite3
```

LAN access defaults:

```bash
JESSA_ALLOWED_CLIENT_NETWORKS=127.0.0.0/8,::1/128,10.0.0.0/8
```

Use `./stop_jessa.sh` to stop a running JESSA web app process started from this project.

## SQLite to PostgreSQL Migration

JESSA 2.x uses PostgreSQL for runtime storage. The legacy SQLite file is only an import source.

After adding the PostgreSQL settings to `.env`, run:

```bash
source .venv/bin/activate
python scripts/migrate_sqlite_to_postgres.py
```

The PostgreSQL server must allow the configured app host to connect to the configured database as the configured user. On self-managed PostgreSQL, that usually means the database exists, the role exists, and `pg_hba.conf` has a narrow host rule for this app host.

The migration script:

- validates the SQLite source with `PRAGMA integrity_check`;
- creates a timestamped SQLite backup under `data/backups/`;
- creates the configured PostgreSQL database when the configured user has permission;
- creates the PostgreSQL schema;
- copies rows with their original IDs;
- resets PostgreSQL identity sequences;
- verifies table counts before reporting success.

Do not delete `data/jessa.sqlite3` until the migration reports matching counts and `/api/health` shows `db_backend` as `postgresql`.

Google Workspace notes:

- IMAP must be enabled for the user in Google Workspace Admin and in Gmail settings.
- Google’s current Workspace guidance recommends OAuth-capable clients, but app passwords remain the practical path for older/simple IMAP-SMTP clients when allowed by account policy.
- Use the app password as `EMAIL_APP_PASSWORD`, not the normal account password.

## Usage

### Jobs

Paste a job URL and click `Import`. LinkedIn URLs automatically select the `LinkedIn` import mode, which opens a visible browser using the local `data/linkedin-browser` profile so sign-in state can be reused. If a non-LinkedIn page is heavily dynamic, choose `Rendered`; this uses a visible Playwright Chromium session. If URL import fails, paste the job text and use `Import Text`.

Select a job on the left, review/edit fields, then click `Analyze`. JESSA updates the match metrics, salary target, resume base, resume notes, and cover letter, then creates tailored resume and cover-letter artifacts automatically.

Use the left-rail lifecycle buttons to switch between active jobs, archived jobs, and the Trash Bin. In Active, use the status filter above the job list to narrow the title list. Use the row checkboxes to select one or more jobs, then bulk-update their status or move them to the Trash Bin. The detail header still supports moving the open job to Trash. Trashed jobs can be recovered for 24 hours; after that, JESSA purges them automatically the next time the app starts or loads a jobs view. Jobs marked `Not For Me`, `Job Expired`, or `Rejected` are moved to Archived automatically.

Click `Regenerate Docs` to create another tailored resume and cover-letter version. These are stored in the Application Materials section as versioned artifacts. Edit the text if needed, click `Save`, then use `PDF` to download. After you submit a document to an employer, click `Mark Submitted` so JESSA records what went out and when.

Paste application questions into `Supplemental Questions` and click `Generate Answers`. The generated answers are saved as another artifact, with the same save, PDF, and submitted tracking.

### Core Profile

The core profile is the source of truth for future scoring and resume generation. Fix dates, titles, canonical bullets, and career rules here first. Every save increments the profile version.

On a new database, JESSA opens the setup/profile flow before the jobs workflow. Use the Profile Import panel to paste a resume/profile or upload a text/PDF resume, review the imported content in the Core Profile editor, then save any edits before importing jobs. Existing databases are not rewritten automatically.

The LinkedIn Profile cache in this tab stores the candidate's current LinkedIn profile text separately from the canonical core profile. Use `Cache from URL` to open the persistent LinkedIn browser. If LinkedIn asks you to sign in, complete sign-in and click `I'm signed in, continue` in the JESSA overlay. Then click `Capture profile now`. JESSA expands visible profile text, visits supported `/details/...` profile sections, and refuses to save an empty or too-small browser capture. You can also paste profile text and use `Save Cache`. JESSA includes this cached profile as supporting context during job analysis and document generation.

Resume-source rules belong in the Core Profile. Use them to identify the primary resume/source, secondary resume/source, do-not-use titles or claims, and any positioning rules for different role types.

### Email

`Sync Inbox` checks recent inbox messages, classifies job-search mail, and links messages to jobs only when there is concrete company or sender evidence. Obvious newsletters, retail notices, and job-alert digests are ignored during new syncs. Linked messages include match confidence and the reason they matched. Title-only overlaps are left unmatched instead of guessing. When both the email classification and job match are high confidence, JESSA can advance status from application confirmation, assessment, interview, and rejection emails. Rejection emails use the existing terminal-status archive behavior. `Test SMTP` only verifies login; JESSA does not auto-send email.

## Data Model

PostgreSQL tables:

- `core_profile`
- `app_prompts`
- `jobs`
- `job_events`
- `emails`
- `application_artifacts`
- `linkedin_profile_cache`

Generated resumes, cover letters, and supplemental answers are stored in `application_artifacts` with version numbers and submitted timestamps. `job_events` records status changes and document lifecycle actions.

The default JESSA system prompt is stored in `app_prompts` under the `jessa_system` key. Candidate-specific facts, career rules, identity, authorization, credentials, and writing preferences belong in `core_profile`, not in source code.

Email match metadata is stored on `emails` with `match_confidence`, `match_reason`, and `status_action`. These columns are added on startup when needed.

Job lifecycle state is stored on `jobs`. The app adds `lifecycle_state`, `archived_at`, `trashed_at`, `purge_after`, and `previous_lifecycle_state` on startup when needed. Trash Bin rows keep their related events and artifacts until the 24-hour purge deletes the job; `ON DELETE CASCADE` removes dependent job events and application artifacts at purge time.

The LinkedIn profile cache is stored in `linkedin_profile_cache`. The browser sign-in cache is local-only under `data/linkedin-browser` by default and should not be committed.

## Versioning

This project uses semantic versioning.

- `1.0.x`: bug fixes and small UI/API improvements.
- `1.x.0`: additive features that preserve the local database shape or migrate it safely.
- `2.0.0`: breaking schema or workflow changes.
- `2.x.0`: additive features on the PostgreSQL workflow with safe schema migration.
- `3.0.0`: portability release that removes hardcoded personal defaults and starts new databases from generic candidate templates.
- `3.1.0`: setup/onboarding and multi-provider LLM configuration improvements.
- `3.2.0`: settings UI improvements that preserve the existing data model.
- `3.2.1`: email false-positive reduction that preserves the existing data model.
- `3.2.2`: `.env`-controlled app listener host/port for local launcher scripts.

## Roadmap

- DOCX export.
- ATS-specific assisted form filling.
- OAuth-based Gmail integration.
- Salary benchmark source adapters.
- Feedback loop that compares match scores against actual interview outcomes.

## Changelog

### 3.2.3

- Added a Partners in Diversity Career Center importer adapter for `jobs.partnersindiversity.org` URLs.
- Captured JobBoardHQ apply redirects and custom fields alongside structured `JobPosting` JSON-LD.
- Added importer regression tests covering Partners in Diversity URLs with parenthesized slug paths.

### 3.2.2

- Made `start_jessa.sh` read `JESSA_HOST` and `JESSA_PORT` from `.env`, with process environment overrides still supported.
- Made `stop_jessa.sh` use the same `.env` port when looking for the listener.
- Added listener host and port fields to the Settings dialog.
- Updated `.env.example` and local configuration to use port `9122`.

### 3.2.1

- Added bulk/newsletter/job-alert guards to email classification and matching.
- Required company or sender evidence before an email can link to a job.
- Kept title-only application notices unmatched instead of selecting a likely wrong job.
- Raised automatic email status updates to require stronger job-match confidence.
- Hid legacy zero-confidence email links from email and job-detail views.
- Refreshed existing stored email classifications and match metadata at startup without triggering status automation.
- Added focused email classifier and job-match regression tests.

### 3.2.0

- Added a top-right gear button for a Settings dialog.
- Added `/api/settings` endpoints for reading and updating supported `.env` keys.
- Added grouped settings controls for LLM providers, PostgreSQL, email, access, paths, and LinkedIn options.
- Kept secrets masked/write-only in the settings API and UI.
- Documented that a restart is required after settings changes.

### 3.1.0

- Added startup setup status reporting for missing or invalid PostgreSQL configuration.
- Changed `start_jessa.sh` so the web UI can start and show setup guidance even when database settings need correction.
- Added multi-provider LLM selection for OpenAI, Claude, Gemini, and Grok with configurable priority and provider failover.
- Added first-run setup/onboarding UI and Core Profile import for pasted profile text or uploaded text/PDF resumes.
- Updated `.env.example` with provider keys, model settings, and `JESSA_LLM_PROVIDER_PRIORITY=openai,claude,gemini,grok`.

### 3.0.0

- Expanded JESSA as Job Engineering Smart Search Assistant in docs and default prompt text.
- Removed hardcoded candidate identity from LLM prompts, fallback package titles, fallback cover letters, UI placeholder text, and README usage guidance.
- Added `jessa_app/defaults.py` with a portable default system prompt and generic core profile template.
- Added the `app_prompts` table and default `jessa_system` prompt seeding.
- Changed fresh database startup to create the configured PostgreSQL database when permitted and then create all runtime tables from scratch.
- Deprecated and removed `jessa_gpt_instructions.txt`; existing personal profile data should live in `core_profile`.
- Updated legacy SQLite import handling for newer optional runtime tables and columns.
- Added local ignored `AGENTS.md` workflow guidance and pointed Git workflow at the GitHub `main` branch.

### 2.4.0

- Added email job-match confidence and match-reason metadata.
- Added guarded email-driven status updates for confirmations, assessments, interviews, and rejections.
- Updated email views to show match reasoning, matched job labels, and status actions.

### 2.3.4

- Improved LinkedIn job imports when LinkedIn omits the old top-card selectors from rendered HTML.
- Added rendered visible-text fallback parsing for job title, company, location, posted date, and job type context.
- Trimmed LinkedIn job descriptions to the actual job and company sections instead of surrounding LinkedIn UI.

### 2.3.3

- Removed the per-row Trash button from the jobs list so list deletion flows through checkbox selection and the bulk Trash action.

### 2.3.2

- Expanded LinkedIn profile caching to capture the main profile plus supported detail pages for collapsed profile sections.
- Switched LinkedIn profile text capture to the profile main content instead of full-page navigation chrome.
- Kept captured LinkedIn profile sections structured by source URL for review in the Core Profile tab.

### 2.3.1

- Added explicit LinkedIn browser overlay buttons for authenticated profile capture.
- Increased LinkedIn login/capture windows and added profile auto-scroll/expand before capture.
- Prevented empty LinkedIn profile captures from being saved as successful cache updates.

### 2.3.0

- Added LinkedIn job import mode backed by a persistent local browser profile.
- Added LinkedIn profile cache endpoints and Core Profile UI controls.
- Added cached LinkedIn profile context to analysis, package generation, and supplemental answer prompts.

### 2.2.0

- Added active-list status filtering.
- Added checked-row bulk selection with bulk trash and bulk status updates.
- Changed Analyze to generate tailored resume and cover-letter artifacts automatically.
- Renamed manual package generation actions to regenerate actions.

### 2.1.0

- Added Active, Archived, and Trash Bin job lifecycle views.
- Added 24-hour trash recovery and purge behavior.
- Added auto-archive for `not_for_me`, `job_expired`, and `rejected`.

### 2.0.0

- Moved runtime persistence from SQLite to PostgreSQL.
- Added PostgreSQL environment settings and health output.
- Added `scripts/migrate_sqlite_to_postgres.py` to safely import the existing SQLite data without changing the source file.
- Kept the legacy SQLite path only as a migration input.

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
