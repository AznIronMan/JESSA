const state = {
  jobs: [],
  selectedJobId: null,
  jobView: "active",
  jobStatusFilter: "all",
  selectedJobIds: new Set(),
};

const $ = (selector) => document.querySelector(selector);

const JOB_STATUSES = [
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

function jobViewLabel(view = state.jobView) {
  return {
    active: "active jobs",
    archived: "archived jobs",
    trash: "Trash Bin",
  }[view] || "jobs";
}

function updateJobViewTabs() {
  document.querySelectorAll("[data-job-view]").forEach((tab) => {
    tab.classList.toggle("active", tab.dataset.jobView === state.jobView);
  });
  const statusShell = $("#status-filter-shell");
  if (statusShell) statusShell.classList.toggle("hidden", state.jobView !== "active");
  const bulkStatusRow = $("#bulk-status-row");
  if (bulkStatusRow) bulkStatusRow.classList.toggle("hidden", state.jobView === "trash");
  const bulkTrash = $("#bulk-trash");
  if (bulkTrash) bulkTrash.classList.toggle("hidden", state.jobView === "trash");
  const bulkRecover = $("#bulk-recover");
  if (bulkRecover) bulkRecover.classList.toggle("hidden", state.jobView !== "trash");
  updateBulkActions();
}

function formatTimestamp(value) {
  if (!value) return "";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString([], {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function detailEmptyText() {
  return state.jobView === "trash"
    ? "Select a trashed job to recover it."
    : `Select or import one of the ${jobViewLabel()}.`;
}

function setEmptyDetail() {
  $("#job-detail").innerHTML = `<div class="empty-state">${escapeHtml(detailEmptyText())}</div>`;
}

function selectedJobIds() {
  return Array.from(state.selectedJobIds);
}

function pruneSelectedJobs() {
  const visible = new Set(state.jobs.map((job) => job.id));
  state.selectedJobIds = new Set(selectedJobIds().filter((id) => visible.has(id)));
}

function updateBulkActions() {
  const count = state.selectedJobIds.size;
  const label = count === 1 ? "1 selected" : `${count} selected`;
  const countNode = $("#bulk-count");
  if (countNode) countNode.textContent = label;
  ["#bulk-update-status", "#bulk-trash", "#bulk-recover"].forEach((selector) => {
    const button = $(selector);
    if (button) button.disabled = count === 0;
  });
}

function renderStatusOptions(select, includeAll = false) {
  if (!select) return;
  const options = includeAll
    ? [["all", "All Statuses"], ...JOB_STATUSES]
    : JOB_STATUSES;
  select.innerHTML = options
    .map(([value, label]) => `<option value="${value}">${escapeHtml(label)}</option>`)
    .join("");
}

function renderJobs() {
  const list = $("#job-list");
  if (!state.jobs.length) {
    list.innerHTML = `<div class="empty-state">No ${escapeHtml(jobViewLabel())}.</div>`;
    updateBulkActions();
    return;
  }
  list.innerHTML = state.jobs
    .map((job) => {
      const active = job.id === state.selectedJobId ? "active" : "";
      const score = job.match_score === null || job.match_score === undefined ? "--" : `${job.match_score}%`;
      const lifecycle = job.lifecycle_state || "active";
      const lifecycleBadge = lifecycle === "active" ? "" : `<span class="badge">${escapeHtml(lifecycle === "trash" ? "trash" : lifecycle)}</span>`;
      const rowAction = lifecycle === "trash"
        ? `<button class="job-row-action" data-recover-job="${job.id}">Recover</button>`
        : `<button class="job-row-action danger" data-trash-job="${job.id}">Trash</button>`;
      const purgeMeta = lifecycle === "trash" && job.purge_after
        ? `<div class="job-subtitle">Purges ${escapeHtml(formatTimestamp(job.purge_after))}</div>`
        : "";
      const checked = state.selectedJobIds.has(job.id) ? "checked" : "";
      return `
        <article class="job-item ${active}" data-job-id="${job.id}">
          <label class="job-check" aria-label="Select job">
            <input type="checkbox" data-check-job="${job.id}" ${checked}>
          </label>
          <button class="job-select" data-select-job="${job.id}">
            <div class="job-title">${escapeHtml(job.title || "Untitled job")}</div>
            <div class="job-subtitle">${escapeHtml([job.company, job.location].filter(Boolean).join(" · "))}</div>
            ${purgeMeta}
            <div class="job-badges">
              <span class="badge ${badgeClass(job.recommendation)}">${escapeHtml(job.recommendation || job.status || "new")}</span>
              <span class="badge">${score}</span>
              <span class="badge ${badgeClass(job.qualification_band)}">${escapeHtml(job.qualification_band || "unscored")}</span>
              <span class="badge">${escapeHtml(job.resume_base || "resume")}</span>
              ${lifecycleBadge}
            </div>
          </button>
          ${rowAction}
        </article>`;
    })
    .join("");
  list.querySelectorAll("[data-select-job]").forEach((item) => {
    item.addEventListener("click", () => selectJob(Number(item.dataset.selectJob)));
  });
  list.querySelectorAll("[data-check-job]").forEach((checkbox) => {
    checkbox.addEventListener("change", () => {
      const id = Number(checkbox.dataset.checkJob);
      if (checkbox.checked) {
        state.selectedJobIds.add(id);
      } else {
        state.selectedJobIds.delete(id);
      }
      updateBulkActions();
    });
  });
  list.querySelectorAll("[data-trash-job]").forEach((button) => {
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      trashJob(Number(button.dataset.trashJob)).catch((error) => toast(error.message));
    });
  });
  list.querySelectorAll("[data-recover-job]").forEach((button) => {
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      recoverJob(Number(button.dataset.recoverJob)).catch((error) => toast(error.message));
    });
  });
  updateBulkActions();
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

async function loadJobs() {
  updateJobViewTabs();
  const params = new URLSearchParams({ view: state.jobView });
  if (state.jobView === "active" && state.jobStatusFilter !== "all") {
    params.set("status", state.jobStatusFilter);
  }
  state.jobs = await api(`/api/jobs?${params.toString()}`);
  pruneSelectedJobs();
  if (state.selectedJobId && !state.jobs.some((job) => job.id === state.selectedJobId)) {
    state.selectedJobId = null;
    setEmptyDetail();
  }
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
  const lifecycle = job.lifecycle_state || "active";
  const locked = lifecycle === "trash";
  const formDisabled = locked ? "disabled" : "";
  const textLocked = locked ? "readonly" : "";
  const urlButton = !locked && (job.apply_url || job.url)
    ? `<button id="open-apply">Open</button>`
    : "";
  const workActions = locked
    ? ""
    : `
        ${urlButton}
        <button id="analyze-job" class="primary">Analyze</button>
        <button id="generate-package">Regenerate Docs</button>
        <button id="save-job">Save</button>
      `;
  const lifecycleActions = lifecycle === "trash"
    ? `<button id="recover-job" class="primary">Recover</button>`
    : lifecycle === "archived"
      ? `<button id="restore-job">Restore Active</button><button id="trash-job" class="danger">Trash</button>`
      : `<button id="archive-job">Archive</button><button id="trash-job" class="danger">Trash</button>`;
  const lifecycleNotice = lifecycle === "trash"
    ? `<div class="lifecycle-notice bad">Trash Bin · purges ${escapeHtml(formatTimestamp(job.purge_after) || "after the recovery window")}</div>`
    : lifecycle === "archived"
      ? `<div class="lifecycle-notice">Archived · ${escapeHtml(formatTimestamp(job.archived_at) || "terminal status")}</div>`
      : "";
  const generateMaterialsButton = locked ? "" : `<button id="generate-package-inline">Regenerate Resume + Cover Letter</button>`;
  const generateSupplementalButton = locked ? "" : `<button id="generate-supplemental">Generate Answers</button>`;
  const supplementalPlaceholder = locked ? "" : "Paste supplemental/application questions here. The generated answers will be saved as an artifact and can be marked submitted.";
  const artifactActionsDisabled = locked ? "disabled" : "";
  const artifactContentLocked = locked ? "readonly" : "";
  $("#job-detail").innerHTML = `
    <div class="detail-header">
      <div>
        <h1>${escapeHtml(job.title || "Untitled job")}</h1>
        <div class="job-metadata">${escapeHtml([job.company, job.location, job.source].filter(Boolean).join(" · "))}</div>
        ${lifecycleNotice}
      </div>
      <div class="detail-actions">
        ${workActions}
        ${lifecycleActions}
      </div>
    </div>

    <div class="analysis-grid">
      <div class="metric"><span>Match</span><strong>${job.match_score ?? "--"}${job.match_score === null || job.match_score === undefined ? "" : "%"}</strong></div>
      <div class="metric"><span>Fit</span><strong>${escapeHtml(job.qualification_band || "--")}</strong></div>
      <div class="metric"><span>Interview</span><strong>${escapeHtml(job.interview_odds || "--")}</strong></div>
      <div class="metric"><span>Resume</span><strong>${escapeHtml(job.resume_base || "--")}</strong></div>
    </div>

    <div class="field-grid">
      <label>Title <input id="edit-title" value="${escapeHtml(job.title)}" ${formDisabled}></label>
      <label>Company <input id="edit-company" value="${escapeHtml(job.company)}" ${formDisabled}></label>
      <label>Location <input id="edit-location" value="${escapeHtml(job.location)}" ${formDisabled}></label>
      <label>Salary <input id="edit-salary" value="${escapeHtml(job.salary)}" ${formDisabled}></label>
      <label>Status
        <select id="edit-status" ${formDisabled}>
          ${JOB_STATUSES.map(([value, label]) => (
            `<option value="${value}" ${job.status === value ? "selected" : ""}>${label}</option>`
          )).join("")}
        </select>
      </label>
      <label>Ask Range <input readonly value="${escapeHtml(job.salary_ask_range || "")}"></label>
      <label>Posted <input id="edit-posted-date" value="${escapeHtml(job.posted_date)}" ${formDisabled}></label>
      <label>Apply URL <input id="edit-apply-url" value="${escapeHtml(job.apply_url || job.url || "")}" ${formDisabled}></label>
    </div>

    <div class="full-field">
      <label>Description <textarea id="edit-description" ${textLocked}>${escapeHtml(job.description)}</textarea></label>
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
        ${generateMaterialsButton}
      </div>
      <div class="artifact-list">
        ${renderArtifacts(job.artifacts || [], artifactActionsDisabled, artifactContentLocked)}
      </div>
    </section>

    <section class="section">
      <div class="section-title-row">
        <h2>Supplemental Questions</h2>
        ${generateSupplementalButton}
      </div>
      <textarea id="supplemental-questions" class="question-box" placeholder="${escapeHtml(supplementalPlaceholder)}" ${textLocked}></textarea>
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

  const analyze = $("#analyze-job");
  if (analyze) analyze.addEventListener("click", analyzeSelectedJob);
  const generate = $("#generate-package");
  if (generate) generate.addEventListener("click", generatePackage);
  const generateInline = $("#generate-package-inline");
  if (generateInline) generateInline.addEventListener("click", generatePackage);
  const supplemental = $("#generate-supplemental");
  if (supplemental) supplemental.addEventListener("click", generateSupplemental);
  const save = $("#save-job");
  if (save) save.addEventListener("click", saveSelectedJob);
  const archive = $("#archive-job");
  if (archive) archive.addEventListener("click", () => archiveJob(job.id).catch((error) => toast(error.message)));
  const restore = $("#restore-job");
  if (restore) restore.addEventListener("click", () => restoreJob(job.id).catch((error) => toast(error.message)));
  const trash = $("#trash-job");
  if (trash) trash.addEventListener("click", () => trashJob(job.id).catch((error) => toast(error.message)));
  const recover = $("#recover-job");
  if (recover) recover.addEventListener("click", () => recoverJob(job.id).catch((error) => toast(error.message)));
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

function renderArtifacts(artifacts, actionsDisabled = "", contentLocked = "") {
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
          <button data-save-artifact="${artifact.id}" ${actionsDisabled}>Save</button>
          <button data-submit-artifact="${artifact.id}" ${actionsDisabled}>Mark Submitted</button>
        </div>
      </div>
      <input class="artifact-title" id="artifact-title-${artifact.id}" value="${escapeHtml(artifact.title || "")}" ${actionsDisabled}>
      <textarea class="artifact-content" id="artifact-content-${artifact.id}" ${contentLocked}>${escapeHtml(artifact.content || "")}</textarea>
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

async function switchJobView(view) {
  state.jobView = view;
  state.selectedJobId = null;
  state.selectedJobIds.clear();
  updateJobViewTabs();
  setEmptyDetail();
  await loadJobs();
}

async function archiveJob(id) {
  const job = await api(`/api/jobs/${id}/archive`, { method: "POST" });
  state.jobView = job.lifecycle_state || "archived";
  state.selectedJobId = job.id;
  await loadJobs();
  renderJobDetail(job);
  toast("Job archived.");
}

async function restoreJob(id) {
  const job = await api(`/api/jobs/${id}/restore`, { method: "POST" });
  state.jobView = job.lifecycle_state || "active";
  state.selectedJobId = job.id;
  await loadJobs();
  renderJobDetail(job);
  toast("Job restored.");
}

async function trashJob(id) {
  const job = await api(`/api/jobs/${id}`, { method: "DELETE" });
  state.jobView = "trash";
  state.selectedJobId = job.id;
  state.selectedJobIds.delete(id);
  await loadJobs();
  renderJobDetail(job);
  toast("Moved to Trash Bin.");
}

async function recoverJob(id) {
  const job = await api(`/api/jobs/${id}/recover`, { method: "POST" });
  state.jobView = job.lifecycle_state || "active";
  state.selectedJobId = job.id;
  state.selectedJobIds.delete(id);
  await loadJobs();
  renderJobDetail(job);
  toast("Job recovered.");
}

async function bulkTrashSelectedJobs() {
  const ids = selectedJobIds();
  if (!ids.length) return;
  const result = await api("/api/jobs/bulk/trash", {
    method: "POST",
    body: JSON.stringify({ job_ids: ids }),
  });
  state.selectedJobIds.clear();
  state.selectedJobId = null;
  await loadJobs();
  setEmptyDetail();
  toast(`Moved ${result.updated} job${result.updated === 1 ? "" : "s"} to Trash Bin.`);
}

async function bulkUpdateSelectedStatus() {
  const ids = selectedJobIds();
  if (!ids.length) return;
  const status = $("#bulk-status").value;
  const result = await api("/api/jobs/bulk/status", {
    method: "PUT",
    body: JSON.stringify({ job_ids: ids, status }),
  });
  state.selectedJobIds.clear();
  state.selectedJobId = null;
  if (result.archived) state.jobView = "archived";
  await loadJobs();
  setEmptyDetail();
  toast(`Updated ${result.updated} job${result.updated === 1 ? "" : "s"}.`);
}

async function bulkRecoverSelectedJobs() {
  const ids = selectedJobIds();
  if (!ids.length) return;
  let recovered = 0;
  for (const id of ids) {
    await api(`/api/jobs/${id}/recover`, { method: "POST" });
    recovered += 1;
  }
  state.selectedJobIds.clear();
  state.selectedJobId = null;
  state.jobView = "active";
  await loadJobs();
  setEmptyDetail();
  toast(`Recovered ${recovered} job${recovered === 1 ? "" : "s"}.`);
}

async function importJob(useText) {
  const payload = useText
    ? { text: $("#job-text").value }
    : { url: $("#job-url").value, method: $("#import-method").value, text: $("#job-text").value };
  const job = await api("/api/jobs/import", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  state.jobView = job.lifecycle_state || "active";
  await loadJobs();
  await selectJob(job.id);
  toast("Job imported.");
}

async function analyzeSelectedJob() {
  if (!state.selectedJobId) return;
  toast("Analyzing and generating materials...");
  const job = await api(`/api/jobs/${state.selectedJobId}/analyze`, { method: "POST" });
  await loadJobs();
  renderJobDetail(job);
  toast("Analysis and materials complete.");
}

async function generatePackage() {
  if (!state.selectedJobId) return;
  toast("Regenerating resume and cover letter...");
  const job = await api(`/api/jobs/${state.selectedJobId}/generate-package`, { method: "POST" });
  await loadJobs();
  renderJobDetail(job);
  toast("Application materials regenerated.");
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
  state.jobView = job.lifecycle_state || state.jobView;
  await loadJobs();
  renderJobDetail(job);
  toast(job.lifecycle_state === "archived" ? "Job saved and archived." : "Job saved.");
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
  renderStatusOptions($("#job-status-filter"), true);
  renderStatusOptions($("#bulk-status"));
  $("#job-status-filter").value = state.jobStatusFilter;
  $("#job-status-filter").addEventListener("change", async () => {
    state.jobStatusFilter = $("#job-status-filter").value;
    state.selectedJobId = null;
    state.selectedJobIds.clear();
    setEmptyDetail();
    await loadJobs();
  });
  $("#select-visible-jobs").addEventListener("click", () => {
    state.jobs.forEach((job) => state.selectedJobIds.add(job.id));
    renderJobs();
  });
  $("#clear-selected-jobs").addEventListener("click", () => {
    state.selectedJobIds.clear();
    renderJobs();
  });
  $("#bulk-trash").addEventListener("click", () => bulkTrashSelectedJobs().catch((error) => toast(error.message)));
  $("#bulk-update-status").addEventListener("click", () => bulkUpdateSelectedStatus().catch((error) => toast(error.message)));
  $("#bulk-recover").addEventListener("click", () => bulkRecoverSelectedJobs().catch((error) => toast(error.message)));
  document.querySelectorAll("[data-job-view]").forEach((tab) => {
    tab.addEventListener("click", () => switchJobView(tab.dataset.jobView).catch((error) => toast(error.message)));
  });
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
