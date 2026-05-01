const state = {
  jobs: [],
  selectedJobId: null,
};

const $ = (selector) => document.querySelector(selector);

function toast(message) {
  const node = $("#toast");
  node.textContent = message;
  node.classList.remove("hidden");
  window.setTimeout(() => node.classList.add("hidden"), 4200);
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = body.detail || detail;
    } catch {
      detail = await response.text();
    }
    throw new Error(detail);
  }
  return response.json();
}

function badgeClass(value) {
  const text = String(value || "").toLowerCase();
  if (text.includes("apply") || text.includes("right") || text.includes("high")) return "good";
  if (text.includes("skip") || text.includes("under") || text.includes("low")) return "bad";
  if (text.includes("maybe") || text.includes("stretch") || text.includes("over")) return "warn";
  return "";
}

function renderJobs() {
  const list = $("#job-list");
  if (!state.jobs.length) {
    list.innerHTML = `<div class="empty-state">No jobs yet.</div>`;
    return;
  }
  list.innerHTML = state.jobs
    .map((job) => {
      const active = job.id === state.selectedJobId ? "active" : "";
      const score = job.match_score === null || job.match_score === undefined ? "--" : `${job.match_score}%`;
      return `
        <button class="job-item ${active}" data-job-id="${job.id}">
          <div class="job-title">${escapeHtml(job.title || "Untitled job")}</div>
          <div class="job-subtitle">${escapeHtml([job.company, job.location].filter(Boolean).join(" · "))}</div>
          <div class="job-badges">
            <span class="badge ${badgeClass(job.recommendation)}">${escapeHtml(job.recommendation || job.status || "new")}</span>
            <span class="badge">${score}</span>
            <span class="badge ${badgeClass(job.qualification_band)}">${escapeHtml(job.qualification_band || "unscored")}</span>
            <span class="badge">${escapeHtml(job.resume_base || "resume")}</span>
          </div>
        </button>`;
    })
    .join("");
  list.querySelectorAll(".job-item").forEach((item) => {
    item.addEventListener("click", () => selectJob(Number(item.dataset.jobId)));
  });
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

async function loadJobs() {
  state.jobs = await api("/api/jobs");
  renderJobs();
}

async function selectJob(id) {
  state.selectedJobId = id;
  renderJobs();
  const job = await api(`/api/jobs/${id}`);
  renderJobDetail(job);
}

function analysisList(job, key) {
  const items = job.analysis_json?.[key] || [];
  if (!items.length) return `<p class="job-metadata">None recorded.</p>`;
  return `<ul>${items.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>`;
}

function renderJobDetail(job) {
  const urlButton = job.apply_url || job.url
    ? `<button id="open-apply">Open</button>`
    : "";
  const statuses = [
    ["new", "New"],
    ["not_applied", "Not Applied"],
    ["tailor", "Tailor"],
    ["ready", "Ready"],
    ["applied", "Applied"],
    ["follow-up", "Follow-up"],
    ["interview", "Interview"],
    ["on_hold", "On Hold"],
    ["job_expired", "Job Expired"],
    ["not_for_me", "Not For Me"],
    ["rejected", "Rejected"],
  ];
  $("#job-detail").innerHTML = `
    <div class="detail-header">
      <div>
        <h1>${escapeHtml(job.title || "Untitled job")}</h1>
        <div class="job-metadata">${escapeHtml([job.company, job.location, job.source].filter(Boolean).join(" · "))}</div>
      </div>
      <div class="detail-actions">
        ${urlButton}
        <button id="analyze-job" class="primary">Analyze</button>
        <button id="generate-package">Generate Docs</button>
        <button id="save-job">Save</button>
      </div>
    </div>

    <div class="analysis-grid">
      <div class="metric"><span>Match</span><strong>${job.match_score ?? "--"}${job.match_score === null || job.match_score === undefined ? "" : "%"}</strong></div>
      <div class="metric"><span>Fit</span><strong>${escapeHtml(job.qualification_band || "--")}</strong></div>
      <div class="metric"><span>Interview</span><strong>${escapeHtml(job.interview_odds || "--")}</strong></div>
      <div class="metric"><span>Resume</span><strong>${escapeHtml(job.resume_base || "--")}</strong></div>
    </div>

    <div class="field-grid">
      <label>Title <input id="edit-title" value="${escapeHtml(job.title)}"></label>
      <label>Company <input id="edit-company" value="${escapeHtml(job.company)}"></label>
      <label>Location <input id="edit-location" value="${escapeHtml(job.location)}"></label>
      <label>Salary <input id="edit-salary" value="${escapeHtml(job.salary)}"></label>
      <label>Status
        <select id="edit-status">
          ${statuses.map(([value, label]) => (
            `<option value="${value}" ${job.status === value ? "selected" : ""}>${label}</option>`
          )).join("")}
        </select>
      </label>
      <label>Ask Range <input readonly value="${escapeHtml(job.salary_ask_range || "")}"></label>
      <label>Posted <input id="edit-posted-date" value="${escapeHtml(job.posted_date)}"></label>
      <label>Apply URL <input id="edit-apply-url" value="${escapeHtml(job.apply_url || job.url || "")}"></label>
    </div>

    <div class="full-field">
      <label>Description <textarea id="edit-description">${escapeHtml(job.description)}</textarea></label>
    </div>

    <section class="section">
      <h2>Analysis</h2>
      <p>${escapeHtml(job.analysis_summary || "No analysis yet.")}</p>
      <div class="generated">
        <div>
          <h2>Resume Notes</h2>
          <textarea readonly>${escapeHtml(job.resume_notes || "")}</textarea>
        </div>
        <div>
          <h2>Cover Letter</h2>
          <textarea readonly>${escapeHtml(job.cover_letter || "")}</textarea>
        </div>
      </div>
    </section>

    <section class="section">
      <h2>Reasons</h2>
      ${analysisList(job, "top_reasons")}
      <h2>Risks</h2>
      ${analysisList(job, "risks")}
      <h2>Keyword Gaps</h2>
      ${analysisList(job, "keyword_gaps")}
    </section>

    <section class="section">
      <div class="section-title-row">
        <h2>Application Materials</h2>
        <button id="generate-package-inline">Generate Resume + Cover Letter</button>
      </div>
      <div class="artifact-list">
        ${renderArtifacts(job.artifacts || [])}
      </div>
    </section>

    <section class="section">
      <div class="section-title-row">
        <h2>Supplemental Questions</h2>
        <button id="generate-supplemental">Generate Answers</button>
      </div>
      <textarea id="supplemental-questions" class="question-box" placeholder="Paste supplemental/application questions here. The generated answers will be saved as an artifact and can be marked submitted."></textarea>
    </section>

    <section class="section">
      <h2>Email</h2>
      ${(job.emails || []).map((message) => `
        <div class="email-row">
          <div>${escapeHtml(message.classification)}</div>
          <div><strong>${escapeHtml(message.subject)}</strong><br><span class="job-metadata">${escapeHtml(message.summary)}</span></div>
          <div>${escapeHtml(message.received_at || "")}</div>
        </div>
      `).join("") || `<p class="job-metadata">No matched email yet.</p>`}
    </section>

    <section class="section">
      <h2>Status Log</h2>
      ${renderEvents(job.events || [])}
    </section>
  `;

  $("#analyze-job").addEventListener("click", analyzeSelectedJob);
  $("#generate-package").addEventListener("click", generatePackage);
  $("#generate-package-inline").addEventListener("click", generatePackage);
  $("#generate-supplemental").addEventListener("click", generateSupplemental);
  $("#save-job").addEventListener("click", saveSelectedJob);
  const open = $("#open-apply");
  if (open) {
    open.addEventListener("click", () => window.open(job.apply_url || job.url, "_blank", "noopener"));
  }
  document.querySelectorAll("[data-save-artifact]").forEach((button) => {
    button.addEventListener("click", () => saveArtifact(Number(button.dataset.saveArtifact), false));
  });
  document.querySelectorAll("[data-submit-artifact]").forEach((button) => {
    button.addEventListener("click", () => saveArtifact(Number(button.dataset.submitArtifact), true));
  });
  document.querySelectorAll("[data-download-artifact]").forEach((button) => {
    button.addEventListener("click", () => {
      window.open(`/api/artifacts/${button.dataset.downloadArtifact}/download.pdf`, "_blank", "noopener");
    });
  });
}

function artifactTypeLabel(type) {
  return {
    resume: "Resume",
    cover_letter: "Cover Letter",
    supplemental_answers: "Supplemental Answers",
  }[type] || type;
}

function renderArtifacts(artifacts) {
  if (!artifacts.length) {
    return `<p class="job-metadata">No generated materials yet.</p>`;
  }
  return artifacts.map((artifact) => `
    <article class="artifact-card">
      <div class="artifact-header">
        <div>
          <strong>${escapeHtml(artifactTypeLabel(artifact.artifact_type))}</strong>
          <span class="job-metadata">v${artifact.version} · ${escapeHtml(artifact.created_at || "")}</span>
          ${artifact.is_submitted ? `<span class="badge good">submitted ${escapeHtml(artifact.submitted_at || "")}</span>` : `<span class="badge">draft</span>`}
        </div>
        <div class="artifact-actions">
          <button data-download-artifact="${artifact.id}">PDF</button>
          <button data-save-artifact="${artifact.id}">Save</button>
          <button data-submit-artifact="${artifact.id}">Mark Submitted</button>
        </div>
      </div>
      <input class="artifact-title" id="artifact-title-${artifact.id}" value="${escapeHtml(artifact.title || "")}">
      <textarea class="artifact-content" id="artifact-content-${artifact.id}">${escapeHtml(artifact.content || "")}</textarea>
    </article>
  `).join("");
}

function renderEvents(events) {
  if (!events.length) {
    return `<p class="job-metadata">No status changes yet.</p>`;
  }
  return `
    <div class="event-list">
      ${events.map((event) => `
        <div class="event-row">
          <div><strong>${escapeHtml(event.event_type)}</strong><br><span class="job-metadata">${escapeHtml(event.created_at)}</span></div>
          <div>${escapeHtml(event.note || "")}</div>
        </div>
      `).join("")}
    </div>`;
}

async function importJob(useText) {
  const payload = useText
    ? { text: $("#job-text").value }
    : { url: $("#job-url").value, method: $("#import-method").value, text: $("#job-text").value };
  const job = await api("/api/jobs/import", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  await loadJobs();
  await selectJob(job.id);
  toast("Job imported.");
}

async function analyzeSelectedJob() {
  if (!state.selectedJobId) return;
  toast("Analyzing job...");
  const job = await api(`/api/jobs/${state.selectedJobId}/analyze`, { method: "POST" });
  await loadJobs();
  renderJobDetail(job);
  toast("Analysis complete.");
}

async function generatePackage() {
  if (!state.selectedJobId) return;
  toast("Generating resume and cover letter...");
  const job = await api(`/api/jobs/${state.selectedJobId}/generate-package`, { method: "POST" });
  await loadJobs();
  renderJobDetail(job);
  toast("Application materials generated.");
}

async function generateSupplemental() {
  if (!state.selectedJobId) return;
  const questions = $("#supplemental-questions").value;
  if (!questions.trim()) {
    toast("Paste supplemental questions first.");
    return;
  }
  toast("Generating supplemental answers...");
  const job = await api(`/api/jobs/${state.selectedJobId}/supplemental`, {
    method: "POST",
    body: JSON.stringify({ questions_text: questions }),
  });
  await loadJobs();
  renderJobDetail(job);
  toast("Supplemental answers saved.");
}

async function saveArtifact(id, markSubmitted) {
  const payload = {
    title: $(`#artifact-title-${id}`).value,
    content: $(`#artifact-content-${id}`).value,
  };
  if (markSubmitted) payload.is_submitted = true;
  await api(`/api/artifacts/${id}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
  const job = await api(`/api/jobs/${state.selectedJobId}`);
  renderJobDetail(job);
  toast(markSubmitted ? "Artifact marked submitted." : "Artifact saved.");
}

async function saveSelectedJob() {
  if (!state.selectedJobId) return;
  const payload = {
    title: $("#edit-title").value,
    company: $("#edit-company").value,
    location: $("#edit-location").value,
    salary: $("#edit-salary").value,
    status: $("#edit-status").value,
    posted_date: $("#edit-posted-date").value,
    apply_url: $("#edit-apply-url").value,
    description: $("#edit-description").value,
  };
  const job = await api(`/api/jobs/${state.selectedJobId}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
  await loadJobs();
  renderJobDetail(job);
  toast("Job saved.");
}

async function loadProfile() {
  const profile = await api("/api/profile");
  $("#profile-editor").value = profile.content;
  $("#profile-version").textContent = `version ${profile.version} · ${profile.updated_at}`;
}

async function saveProfile() {
  const profile = await api("/api/profile", {
    method: "PUT",
    body: JSON.stringify({ content: $("#profile-editor").value }),
  });
  $("#profile-version").textContent = `version ${profile.version} · ${profile.updated_at}`;
  toast("Core profile saved.");
}

async function loadEmails() {
  const emails = await api("/api/emails");
  renderEmails(emails);
}

function renderEmails(emails) {
  $("#email-list").innerHTML = emails.length
    ? emails.map((message) => `
      <div class="email-row">
        <div>
          <span class="badge ${badgeClass(message.classification)}">${escapeHtml(message.classification)}</span>
          <div class="job-metadata">${escapeHtml(message.received_at || "")}</div>
        </div>
        <div>
          <strong>${escapeHtml(message.subject)}</strong>
          <div class="job-metadata">${escapeHtml(message.sender)}</div>
          <p>${escapeHtml(message.summary)}</p>
        </div>
        <div>${message.job_id ? `Job #${message.job_id}` : "Unmatched"}</div>
      </div>
    `).join("")
    : `<div class="empty-state">No email imported.</div>`;
}

async function syncEmail() {
  $("#email-status").textContent = "Syncing...";
  const result = await api("/api/email/sync", { method: "POST" });
  renderEmails(result.emails);
  $("#email-status").textContent = `${result.inserted} new / ${result.fetched} fetched`;
}

async function testEmail() {
  $("#email-status").textContent = "Testing SMTP...";
  await api("/api/email/test", { method: "POST" });
  $("#email-status").textContent = "SMTP login OK";
}

function setupTabs() {
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", async () => {
      document.querySelectorAll(".tab").forEach((node) => node.classList.remove("active"));
      document.querySelectorAll(".view").forEach((node) => node.classList.remove("active"));
      tab.classList.add("active");
      $(`#${tab.dataset.tab}-view`).classList.add("active");
      if (tab.dataset.tab === "profile") await loadProfile();
      if (tab.dataset.tab === "email") await loadEmails();
    });
  });
}

function setupActions() {
  $("#toggle-text-import").addEventListener("click", () => $("#text-import").classList.toggle("hidden"));
  $("#import-url").addEventListener("click", () => importJob(false).catch((error) => toast(error.message)));
  $("#import-text").addEventListener("click", () => importJob(true).catch((error) => toast(error.message)));
  $("#save-profile").addEventListener("click", () => saveProfile().catch((error) => toast(error.message)));
  $("#sync-email").addEventListener("click", () => syncEmail().catch((error) => {
    $("#email-status").textContent = "Sync failed";
    toast(error.message);
  }));
  $("#test-email").addEventListener("click", () => testEmail().catch((error) => {
    $("#email-status").textContent = "SMTP failed";
    toast(error.message);
  }));
}

setupTabs();
setupActions();
loadJobs().catch((error) => toast(error.message));
