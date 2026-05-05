const state = {
  startup: null,
  settings: null,
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

function showTab(tabName) {
  document.querySelectorAll(".tab").forEach((node) => node.classList.remove("active"));
  document.querySelectorAll(".view").forEach((node) => node.classList.remove("active"));
  const tab = document.querySelector(`[data-tab="${tabName}"]`);
  const view = $(`#${tabName}-view`);
  if (tab) tab.classList.add("active");
  if (view) view.classList.add("active");
}

async function api(path, options = {}) {
  const isFormData = options.body instanceof FormData;
  const headers = isFormData
    ? { ...(options.headers || {}) }
    : { "Content-Type": "application/json", ...(options.headers || {}) };
  const response = await fetch(path, {
    headers,
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

async function loadStartup() {
  state.startup = await api("/api/startup");
  renderSetup();
  return state.startup;
}

function providerLine(name, info) {
  const label = info.active ? "active" : info.configured ? "configured" : "missing";
  return `<p><strong>${escapeHtml(name)}</strong>: ${escapeHtml(label)} · ${escapeHtml(info.model || "")}</p>`;
}

function renderSetup() {
  const startup = state.startup;
  if (!startup) return;
  const setupTab = $("#setup-tab");
  if (setupTab) setupTab.classList.toggle("hidden", !startup.setup_required);
  const content = $("#setup-content");
  if (!content) return;
  $("#setup-status").textContent = startup.setup_required ? "action needed" : "ready";
  const dbMessage = startup.database_ready
    ? `<p>PostgreSQL is connected to <code>${escapeHtml(startup.postgres_database || "")}</code>.</p>`
    : `<p>${escapeHtml(startup.database_error || "PostgreSQL is not ready.")}</p>`;
  const llmMessage = startup.llm_ready
    ? `<p>Using <code>${escapeHtml(startup.active_llm_provider)}:${escapeHtml(startup.active_llm_model)}</code>.</p>`
    : `<p>Add at least one LLM API key in <code>.env</code>.</p>`;
  const providers = Object.entries(startup.llm_providers || {})
    .map(([name, info]) => providerLine(name, info))
    .join("");
  const issues = (startup.issues || []).map((issue) => `<p>${escapeHtml(issue)}</p>`).join("");
  content.innerHTML = `
    <article class="setup-card">
      <h2>PostgreSQL</h2>
      ${dbMessage}
      <p><code>POSTGRES_HOST</code>, <code>POSTGRES_PORT</code>, <code>POSTGRES_USER</code>, <code>POSTGRES_PASS</code>, <code>POSTGRES_DB_NAME</code></p>
    </article>
    <article class="setup-card">
      <h2>LLM Providers</h2>
      ${llmMessage}
      <p>Priority: <code>${escapeHtml((startup.llm_provider_priority || []).join(","))}</code></p>
      ${providers}
    </article>
    <article class="setup-card">
      <h2>First Run</h2>
      <p>${startup.onboarding_required ? "Add the candidate profile before importing jobs." : "Core profile is initialized."}</p>
      <button id="setup-open-profile">Core Profile</button>
    </article>
    ${issues ? `<article class="setup-card"><h2>Issues</h2>${issues}</article>` : ""}
  `;
  const openProfile = $("#setup-open-profile");
  if (openProfile) {
    openProfile.addEventListener("click", async () => {
      showTab("profile");
      await loadProfile();
      await loadLinkedInProfile();
    });
  }
}

async function openSettings() {
  $("#settings-modal").classList.remove("hidden");
  $("#settings-status").textContent = "loading...";
  state.settings = await api("/api/settings");
  renderSettings();
}

function closeSettings() {
  $("#settings-modal").classList.add("hidden");
}

function renderSettingsField(field) {
  const value = field.value || "";
  const configured = field.secret && field.has_value ? "Configured. Leave blank to keep current value." : "";
  const help = field.help || configured;
  if (field.kind === "boolean") {
    const normalized = String(value || "").toLowerCase();
    return `
      <label class="settings-field">
        <span>${escapeHtml(field.label)}</span>
        <select data-setting-name="${escapeHtml(field.name)}">
          <option value=""></option>
          <option value="true" ${normalized === "true" || normalized === "1" || normalized === "yes" ? "selected" : ""}>true</option>
          <option value="false" ${normalized === "false" || normalized === "0" || normalized === "no" ? "selected" : ""}>false</option>
        </select>
        ${help ? `<span class="settings-help">${escapeHtml(help)}</span>` : ""}
      </label>`;
  }
  const type = field.secret ? "password" : field.kind === "number" ? "number" : "text";
  const placeholder = field.secret && field.has_value ? "configured" : "";
  return `
    <label class="settings-field">
      <span>${escapeHtml(field.label)}</span>
      <input data-setting-name="${escapeHtml(field.name)}" type="${type}" value="${escapeHtml(value)}" placeholder="${escapeHtml(placeholder)}" autocomplete="off" />
      ${help ? `<span class="settings-help">${escapeHtml(help)}</span>` : ""}
    </label>`;
}

function renderSettings() {
  const settings = state.settings;
  if (!settings) return;
  $("#settings-status").textContent = settings.restart_required ? "changes require restart" : "";
  $("#settings-save-status").textContent = "";
  $("#settings-body").innerHTML = settings.groups
    .map((group) => `
      <section class="settings-group">
        <h2>${escapeHtml(group.name)}</h2>
        <div class="settings-fields">
          ${group.fields.map(renderSettingsField).join("")}
        </div>
      </section>`)
    .join("");
}

async function saveSettings() {
  const values = {};
  document.querySelectorAll("[data-setting-name]").forEach((field) => {
    values[field.dataset.settingName] = field.value;
  });
  $("#settings-save-status").textContent = "saving...";
  state.settings = await api("/api/settings", {
    method: "PUT",
    body: JSON.stringify({ values }),
  });
  renderSettings();
  $("#settings-save-status").textContent = "saved; restart JESSA to apply changes";
  toast("Settings saved. Restart JESSA to apply them.");
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

function formatPercent(value) {
  const number = Number(value || 0);
  if (!Number.isFinite(number) || number <= 0) return "";
  return `${Math.round(number * 100)}%`;
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
        : "";
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

function isLinkedInUrl(value) {
  try {
    const host = new URL(value).hostname.toLowerCase();
    return host === "linkedin.com" || host.endsWith(".linkedin.com");
  } catch {
    return false;
  }
}

function syncImportMethodForUrl() {
  const method = $("#import-method");
  if (isLinkedInUrl($("#job-url").value) && method.value === "http") {
    method.value = "linkedin";
  }
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
          <div>
            <span class="badge ${badgeClass(message.classification)}">${escapeHtml(message.classification)}</span>
            ${message.status_action ? `<div class="job-metadata">status ${escapeHtml(message.status_action)}</div>` : ""}
          </div>
          <div>
            <strong>${escapeHtml(message.subject)}</strong>
            <div class="job-metadata">${escapeHtml(message.sender || "")}</div>
            <div class="job-metadata">${escapeHtml(message.summary)}</div>
            ${message.match_reason ? `<div class="job-metadata">match ${escapeHtml(formatPercent(message.match_confidence))} · ${escapeHtml(message.match_reason)}</div>` : ""}
          </div>
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
  const panel = $("#profile-import-panel");
  if (panel) panel.classList.toggle("hidden", !(state.startup?.onboarding_required || state.startup?.profile_is_default));
  const mode = $("#profile-import-mode");
  if (mode && state.startup?.profile_is_default) mode.value = "replace";
}

async function loadLinkedInProfile() {
  const profile = await api("/api/linkedin-profile");
  $("#linkedin-profile-url").value = profile.url || "";
  $("#linkedin-profile-title").value = profile.title || "";
  $("#linkedin-profile-cache").value = profile.content || "";
  $("#linkedin-profile-status").textContent = profile.content
    ? `cached ${profile.fetched_at || profile.updated_at || ""}`
    : "not cached";
}

async function saveProfile() {
  const profile = await api("/api/profile", {
    method: "PUT",
    body: JSON.stringify({ content: $("#profile-editor").value }),
  });
  $("#profile-version").textContent = `version ${profile.version} · ${profile.updated_at}`;
  state.startup = await api("/api/startup");
  renderSetup();
  toast("Core profile saved.");
}

async function importProfile() {
  const form = new FormData();
  form.append("resume_text", $("#profile-import-text").value);
  form.append("mode", $("#profile-import-mode").value);
  const file = $("#profile-import-file").files[0];
  if (file) form.append("resume_file", file);
  $("#profile-import-status").textContent = "importing...";
  const profile = await api("/api/profile/import", {
    method: "POST",
    headers: {},
    body: form,
  });
  $("#profile-editor").value = profile.content;
  $("#profile-version").textContent = `version ${profile.version} · ${profile.updated_at}`;
  $("#profile-import-text").value = "";
  $("#profile-import-file").value = "";
  state.startup = await api("/api/startup");
  renderSetup();
  $("#profile-import-status").textContent = "imported";
  toast("Profile imported.");
}

async function saveLinkedInProfile() {
  const profile = await api("/api/linkedin-profile", {
    method: "PUT",
    body: JSON.stringify({
      url: $("#linkedin-profile-url").value,
      title: $("#linkedin-profile-title").value,
      content: $("#linkedin-profile-cache").value,
    }),
  });
  $("#linkedin-profile-status").textContent = `cached ${profile.fetched_at || profile.updated_at || ""}`;
  toast("LinkedIn profile cache saved.");
}

async function fetchLinkedInProfileCache() {
  $("#linkedin-profile-status").textContent = "waiting for browser capture...";
  toast("Use the LinkedIn browser buttons: sign in, continue, then capture.");
  const profile = await api("/api/linkedin-profile/fetch", {
    method: "POST",
    body: JSON.stringify({ url: $("#linkedin-profile-url").value }),
  });
  $("#linkedin-profile-url").value = profile.url || "";
  $("#linkedin-profile-title").value = profile.title || "";
  $("#linkedin-profile-cache").value = profile.content || "";
  $("#linkedin-profile-status").textContent = `cached ${profile.fetched_at || profile.updated_at || ""}`;
  toast("LinkedIn profile cached.");
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
          ${message.status_action ? `<div class="job-metadata">status ${escapeHtml(message.status_action)}</div>` : ""}
        </div>
        <div>
          <strong>${escapeHtml(message.subject)}</strong>
          <div class="job-metadata">${escapeHtml(message.sender)}</div>
          ${message.match_reason ? `<div class="job-metadata">match ${escapeHtml(formatPercent(message.match_confidence))} · ${escapeHtml(message.match_reason)}</div>` : ""}
          <p>${escapeHtml(message.summary)}</p>
        </div>
        <div>
          ${message.job_id
            ? `<strong>Job #${message.job_id}</strong><div class="job-metadata">${escapeHtml([message.job_company, message.job_title].filter(Boolean).join(" · "))}</div>`
            : "Unmatched"}
        </div>
      </div>
    `).join("")
    : `<div class="empty-state">No email imported.</div>`;
}

async function syncEmail() {
  $("#email-status").textContent = "Syncing...";
  const result = await api("/api/email/sync", { method: "POST" });
  renderEmails(result.emails);
  $("#email-status").textContent = `${result.inserted} new / ${result.fetched} fetched / ${result.status_updates || 0} status updates`;
}

async function testEmail() {
  $("#email-status").textContent = "Testing SMTP...";
  await api("/api/email/test", { method: "POST" });
  $("#email-status").textContent = "SMTP login OK";
}

function setupTabs() {
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", async () => {
      showTab(tab.dataset.tab);
      if (tab.dataset.tab === "profile") {
        await loadProfile();
        await loadLinkedInProfile();
      }
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
  $("#job-url").addEventListener("input", syncImportMethodForUrl);
  $("#job-url").addEventListener("change", syncImportMethodForUrl);
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
  $("#import-profile").addEventListener("click", () => importProfile().catch((error) => {
    $("#profile-import-status").textContent = "import failed";
    toast(error.message);
  }));
  $("#save-linkedin-profile").addEventListener("click", () => saveLinkedInProfile().catch((error) => toast(error.message)));
  $("#fetch-linkedin-profile").addEventListener("click", () => fetchLinkedInProfileCache().catch((error) => {
    $("#linkedin-profile-status").textContent = "cache failed";
    toast(error.message);
  }));
  $("#open-settings").addEventListener("click", () => openSettings().catch((error) => toast(error.message)));
  $("#close-settings").addEventListener("click", closeSettings);
  $("#settings-modal").addEventListener("click", (event) => {
    if (event.target.id === "settings-modal") closeSettings();
  });
  $("#save-settings").addEventListener("click", () => saveSettings().catch((error) => {
    $("#settings-save-status").textContent = "save failed";
    toast(error.message);
  }));
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
(async function init() {
  try {
    const startup = await loadStartup();
    if (!startup.database_ready || !startup.llm_ready) {
      showTab("setup");
      return;
    }
    if (startup.onboarding_required) {
      showTab("profile");
      await loadProfile();
      await loadLinkedInProfile();
      return;
    }
    await loadJobs();
  } catch (error) {
    toast(error.message);
  }
})();
