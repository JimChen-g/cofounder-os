"use strict";

const ACTIVE_RUN_KEY = "cofounder-os.active-run-id";

const state = {
  runId: null,
  snapshot: null,
  artifacts: [],
  events: [],
  evaluation: null,
  pendingAttachments: [],
  evidencePackage: null,
  routingPlan: null,
  conflicts: [],
  selectedArtifactId: null,
  activeView: "mission",
  requestEpoch: 0,
  evaluationEpoch: 0,
};

const viewTitles = {
  mission: "Mission",
  approvals: "Approvals",
  artifacts: "Artifacts",
  audit: "Audit trail",
  evaluation: "Evaluation",
};

const agentDefinitions = {
  "product-agent": {
    className: "product",
    label: "Product Agent",
    monogram: "P",
    discipline: "Product intelligence",
  },
  "finance-agent": {
    className: "finance",
    label: "Finance Agent",
    monogram: "F",
    discipline: "Financial intelligence",
  },
  "executive-orchestrator": {
    className: "executive",
    label: "Executive Orchestrator",
    monogram: "E",
    discipline: "Decision synthesis",
  },
  "evidence-extractor": {
    className: "evidence",
    label: "Evidence Extractor",
    monogram: "V",
    discipline: "Multimodal evidence",
  },
  "engineering-agent": {
    className: "engineering",
    label: "Engineering Agent",
    monogram: "G",
    discipline: "Executable delivery",
  },
  "risk-agent": {
    className: "risk",
    label: "Risk Agent",
    monogram: "R",
    discipline: "Authority & privacy",
  },
  "artifact-synthesizer": {
    className: "synthesis",
    label: "Artifact Synthesizer",
    monogram: "S",
    discipline: "Delivery package",
  },
  verifier: {
    className: "verifier",
    label: "Independent Verifier",
    monogram: "✓",
    discipline: "Consistency & revision",
  },
};

const selectors = {
  alert: document.querySelector("#global-alert"),
  alertTitle: document.querySelector("#alert-title"),
  alertMessage: document.querySelector("#alert-message"),
  approvalCount: document.querySelector("#approval-count"),
  approvalList: document.querySelector("#approval-list"),
  artifactCount: document.querySelector("#artifact-count"),
  artifactList: document.querySelector("#artifact-list"),
  artifactViewer: document.querySelector("#artifact-viewer"),
  auditCount: document.querySelector("#audit-count"),
  auditList: document.querySelector("#audit-list"),
  composer: document.querySelector("#mission-composer"),
  conflictGrid: document.querySelector("#conflict-grid"),
  conflictSection: document.querySelector("#conflict-section"),
  conflictSummary: document.querySelector("#conflict-summary"),
  downloadSelected: document.querySelector("#download-selected"),
  emptyOverview: document.querySelector("#empty-overview"),
  evaluationAgents: document.querySelector("#evaluation-agents"),
  evaluationLatest: document.querySelector("#evaluation-latest"),
  evaluationProviders: document.querySelector("#evaluation-providers"),
  evaluationRuns: document.querySelector("#evaluation-runs"),
  evidenceBoard: document.querySelector("#evidence-board"),
  evidenceBoardGrid: document.querySelector("#evidence-board-grid"),
  evidenceBoardSummary: document.querySelector("#evidence-board-summary"),
  evidenceFiles: document.querySelector("#evidence-files"),
  evidencePackageId: document.querySelector("#evidence-package-id"),
  evidenceSourceStrip: document.querySelector("#evidence-source-strip"),
  evidenceWarning: document.querySelector("#evidence-warning"),
  formHint: document.querySelector("#form-hint"),
  launchButton: document.querySelector("#launch-mission"),
  loadPocFixture: document.querySelector("#load-poc-fixture"),
  missionForm: document.querySelector("#mission-form"),
  newMission: document.querySelector("#new-mission"),
  previewEvidence: document.querySelector("#preview-evidence"),
  routingBoard: document.querySelector("#routing-board"),
  routingBoardSummary: document.querySelector("#routing-board-summary"),
  routingDisclosure: document.querySelector("#routing-disclosure"),
  routingGrid: document.querySelector("#routing-grid"),
  simulateRouteFallback: document.querySelector("#simulate-route-fallback"),
  refreshRun: document.querySelector("#refresh-run"),
  refreshEvaluation: document.querySelector("#refresh-evaluation"),
  retryRun: document.querySelector("#retry-run"),
  runWorkspace: document.querySelector("#run-workspace"),
  systemState: document.querySelector("#system-state"),
  systemStateLabel: document.querySelector("#system-state-label"),
  systemStateDetail: document.querySelector("#system-state-detail"),
  toastRegion: document.querySelector("#toast-region"),
  attachmentList: document.querySelector("#attachment-list"),
  viewResult: document.querySelector("#view-result"),
  viewTitle: document.querySelector("#view-title"),
};

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) {
    node.className = className;
  }
  if (text !== undefined && text !== null) {
    node.textContent = String(text);
  }
  return node;
}

function append(parent, ...children) {
  children.filter(Boolean).forEach((child) => parent.append(child));
  return parent;
}

function statusClass(status) {
  return `status-${String(status || "pending").replace(/[^a-z_]/g, "")}`;
}

function labelize(value) {
  return String(value || "unknown")
    .replaceAll("_", " ")
    .replaceAll("-", " ")
    .replaceAll(".", " · ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function shortId(value) {
  if (!value) {
    return "—";
  }
  return String(value).split("-")[0].toUpperCase();
}

function formatTime(value, includeDate = false) {
  if (!value) {
    return "—";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "—";
  }
  const options = includeDate
    ? {
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      }
    : { hour: "2-digit", minute: "2-digit", second: "2-digit" };
  return new Intl.DateTimeFormat(undefined, options).format(date);
}

function safeFilename(value) {
  const fallback = "cofounder-artifact.txt";
  if (!value) {
    return fallback;
  }
  const cleaned = String(value).replace(/[^a-zA-Z0-9._-]/g, "-");
  return cleaned.slice(0, 160) || fallback;
}

function bytesToBase64(buffer) {
  const bytes = new Uint8Array(buffer);
  const chunks = [];
  for (let offset = 0; offset < bytes.length; offset += 32768) {
    chunks.push(
      String.fromCharCode(...bytes.subarray(offset, offset + 32768)),
    );
  }
  return btoa(chunks.join(""));
}

async function fileToAttachment(file) {
  if (!["application/pdf", "image/png"].includes(file.type)) {
    throw new Error(`${file.name} must be a PDF or PNG file.`);
  }
  if (file.size > 8 * 1024 * 1024) {
    throw new Error(`${file.name} exceeds the 8 MiB demo boundary.`);
  }
  return {
    filename: file.name,
    content_type: file.type,
    base64_content: bytesToBase64(await file.arrayBuffer()),
    privacy_level: "restricted",
  };
}

function attachmentSize(attachment) {
  return Math.floor((attachment.base64_content.length * 3) / 4);
}

function renderAttachmentList() {
  selectors.attachmentList.replaceChildren();
  if (!state.pendingAttachments.length) {
    selectors.attachmentList.append(
      element("span", null, "No evidence files selected."),
    );
    selectors.previewEvidence.disabled = true;
    return;
  }
  state.pendingAttachments.forEach((attachment) => {
    const item = element("span", "attachment-chip");
    append(
      item,
      element(
        "strong",
        null,
        attachment.content_type === "application/pdf" ? "PDF" : "PNG",
      ),
      document.createTextNode(attachment.filename),
      element(
        "small",
        null,
        `${Math.max(1, Math.round(attachmentSize(attachment) / 1024))} KB`,
      ),
    );
    selectors.attachmentList.append(item);
  });
  selectors.previewEvidence.disabled = state.pendingAttachments.length < 3;
}

async function handleEvidenceFiles(event) {
  hideAlert();
  try {
    const files = [...(event.target.files || [])];
    state.pendingAttachments = await Promise.all(files.map(fileToAttachment));
    state.evidencePackage = null;
    state.routingPlan = null;
    selectors.evidenceBoard.classList.add("is-hidden");
    selectors.routingBoard.classList.add("is-hidden");
    renderAttachmentList();
  } catch (error) {
    state.pendingAttachments = [];
    renderAttachmentList();
    showAlert("Evidence files could not be read", error);
  }
}

function renderRoutingBoard() {
  selectors.routingGrid.replaceChildren();
  selectors.routingDisclosure.replaceChildren();
  const plan = state.routingPlan;
  if (!plan) {
    selectors.routingBoard.classList.add("is-hidden");
    return;
  }
  selectors.routingBoard.classList.remove("is-hidden");
  const fallbackCount = plan.decisions.filter(
    (decision) => decision.fallback_used,
  ).length;
  selectors.routingBoardSummary.textContent =
    `${plan.decisions.length} task routes · ${fallbackCount} fallback${fallbackCount === 1 ? "" : "s"} · ${plan.live_model_calls} live calls during decision`;

  plan.decisions.forEach((decision) => {
    const card = element(
      "article",
      `routing-card ${decision.fallback_used ? "is-fallback" : ""}`,
    );
    const heading = element("div", "routing-card-heading");
    const headingCopy = element("div");
    append(
      headingCopy,
      element("span", "route-task-key", decision.task_key),
      element("h3", null, decision.task_title),
    );
    append(
      heading,
      headingCopy,
      element(
        "span",
        `route-provider ${decision.fallback_used ? "fallback" : ""}`,
        decision.fallback_used ? "Fallback" : labelize(decision.provider),
      ),
    );
    const selected = element("div", "route-selection");
    append(
      selected,
      element("span", null, "Selected"),
      element("strong", null, decision.selected_model),
      decision.requested_model !== decision.selected_model
        ? element("small", null, `Requested ${decision.requested_model}`)
        : null,
    );
    const facts = element("div", "route-facts");
    [
      ["Privacy", labelize(decision.privacy_level)],
      ["Complexity", labelize(decision.complexity)],
      ["Context", `${decision.context_length} est. tokens`],
      ["Latency", `${decision.estimated_latency_ms} / ${decision.latency_budget_ms} ms`],
      ["Cost", `$${decision.estimated_cost_usd.toFixed(2)} / $${decision.cost_budget_usd.toFixed(2)}`],
      ["Verifier", decision.validation_required ? "Required" : "Not required"],
    ].forEach(([label, value]) => {
      const row = element("div");
      append(row, element("span", null, label), element("strong", null, value));
      facts.append(row);
    });
    const capabilities = element("div", "route-capabilities");
    decision.required_capabilities.forEach((capability) =>
      capabilities.append(element("span", null, labelize(capability))),
    );
    const exclusions = Object.entries(decision.excluded_models || {});
    const exclusionBlock = exclusions.length
      ? element("div", "route-exclusions")
      : null;
    if (exclusionBlock) {
      exclusions.forEach(([model, reason]) => {
        exclusionBlock.append(
          element("p", null, `Excluded ${model}: ${reason}`),
        );
      });
    }
    append(
      card,
      heading,
      selected,
      element("p", "route-reason", decision.reason),
      capabilities,
      facts,
      element("p", "route-privacy", decision.privacy_decision),
      exclusionBlock,
      element(
        "p",
        "route-validation",
        `Validation: ${decision.validation_requirement}`,
      ),
    );
    selectors.routingGrid.append(card);
  });
  selectors.routingDisclosure.append(
    element("strong", null, "Decision-only routing evidence"),
    element("p", null, plan.simulation_disclosure),
  );
}

async function loadRoutingDecisions(unavailableModels = []) {
  if (!state.evidencePackage) {
    throw new Error("Build the Evidence Package before routing work.");
  }
  const plan = await apiRequest("/api/insurance-poc/routing", {
    method: "POST",
    body: JSON.stringify({
      evidence_package: state.evidencePackage,
      unavailable_models: unavailableModels,
    }),
  });
  state.routingPlan = plan;
  renderRoutingBoard();
  return plan;
}

async function simulateRouteFallback() {
  hideAlert();
  setButtonLoading(selectors.simulateRouteFallback, true);
  try {
    const plan = await loadRoutingDecisions(["cofounder-step"]);
    const fallbackCount = plan.decisions.filter(
      (decision) => decision.fallback_used,
    ).length;
    toast(
      `${fallbackCount} Step routes moved to declared fallbacks; no model call was claimed.`,
    );
  } catch (error) {
    showAlert("Fallback simulation could not run", error);
  } finally {
    setButtonLoading(selectors.simulateRouteFallback, false);
  }
}

function renderEvidenceBoard() {
  const packageValue = state.evidencePackage;
  selectors.evidenceBoardGrid.replaceChildren();
  selectors.evidenceSourceStrip.replaceChildren();
  selectors.evidenceWarning.replaceChildren();
  if (!packageValue) {
    selectors.evidenceBoard.classList.add("is-hidden");
    return;
  }

  selectors.evidenceBoard.classList.remove("is-hidden");
  selectors.evidencePackageId.textContent =
    `Package ${shortId(packageValue.package_id)} · ${packageValue.evidence.length} facts`;
  selectors.evidenceBoardSummary.textContent =
    `${packageValue.sources.length} normalized sources · ${packageValue.synthetic ? "synthetic demo" : "submitted evidence"} · non-authoritative`;

  packageValue.sources.forEach((source) => {
    const chip = element("article", "evidence-source");
    append(
      chip,
      element("strong", null, source.source_file),
      element(
        "span",
        null,
        `${labelize(source.modality)} · ${labelize(source.privacy_level)}`,
      ),
      element(
        "small",
        null,
        `${labelize(source.processing_status)} · ${labelize(source.adapter_mode)}`,
      ),
    );
    selectors.evidenceSourceStrip.append(chip);
  });

  const byCategory = new Map();
  packageValue.evidence.forEach((item) => {
    const category = item.category;
    const existing = byCategory.get(category) || [];
    existing.push(item);
    byCategory.set(category, existing);
  });
  byCategory.forEach((items, category) => {
    const column = element("section", "evidence-category-card");
    append(
      column,
      element("h3", null, labelize(category)),
      element("span", "subtle-label", `${items.length} source-linked facts`),
    );
    items.forEach((item) => {
      const fact = element("article", "evidence-fact");
      append(
        fact,
        element("strong", null, item.evidence_id),
        element("p", null, item.content),
        element(
          "span",
          null,
          `${item.source_file} · ${labelize(item.modality)} · ${Math.round(item.confidence * 100)}% · ${labelize(item.privacy_level)}`,
        ),
        element(
          "small",
          null,
          `Used by ${item.used_by_agents.map(labelize).join(", ")}`,
        ),
      );
      column.append(fact);
    });
    selectors.evidenceBoardGrid.append(column);
  });
  packageValue.warnings.forEach((warning) => {
    selectors.evidenceWarning.append(element("p", null, warning));
  });
}

async function buildEvidencePackage({ quiet = false } = {}) {
  const mission = document.querySelector("#objective").value.trim();
  if (!mission) {
    throw new Error("Enter the Founder Mission before building evidence.");
  }
  if (state.pendingAttachments.length < 3) {
    throw new Error("Select one PDF and at least two PNG images.");
  }
  setButtonLoading(selectors.previewEvidence, true);
  try {
    const response = await apiRequest("/api/insurance-poc/evidence", {
      method: "POST",
      body: JSON.stringify({
        mission,
        attachments: state.pendingAttachments,
      }),
    });
    state.evidencePackage = response.evidence_package;
    renderEvidenceBoard();
    await loadRoutingDecisions();
    if (!quiet) {
      toast("Evidence Package built with source, privacy, and Agent-use links.");
      selectors.evidenceBoard.scrollIntoView({ behavior: "smooth", block: "start" });
    }
    return state.evidencePackage;
  } finally {
    setButtonLoading(selectors.previewEvidence, false);
  }
}

async function previewEvidence() {
  hideAlert();
  try {
    await buildEvidencePackage();
  } catch (error) {
    showAlert("Evidence extraction needs attention", error);
  }
}

async function loadPocFixture() {
  hideAlert();
  setButtonLoading(selectors.loadPocFixture, true);
  try {
    const fixture = await apiRequest("/api/insurance-poc/fixture");
    document.querySelector("#objective").value = fixture.mission;
    document.querySelector("#owner").value = "Founder";
    state.pendingAttachments = fixture.attachments;
    state.evidencePackage = null;
    state.routingPlan = null;
    selectors.evidenceFiles.value = "";
    renderAttachmentList();
    await buildEvidencePackage();
  } catch (error) {
    showAlert("Stable demo evidence could not be loaded", error);
  } finally {
    setButtonLoading(selectors.loadPocFixture, false);
  }
}

async function apiRequest(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (options.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const response = await fetch(path, {
    ...options,
    headers,
  });
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    const validationDetail = Array.isArray(payload?.detail)
      ? payload.detail.map((item) => item.msg).join("; ")
      : null;
    const error = new Error(
      validationDetail ||
        payload?.detail ||
        "The system could not complete the request.",
    );
    error.code = payload?.error || `http_${response.status}`;
    error.requestId =
      payload?.request_id || response.headers.get("X-Request-ID") || null;
    throw error;
  }
  return payload;
}

function setButtonLoading(button, loading) {
  button.disabled = loading;
  button.classList.toggle("is-loading", loading);
  button.setAttribute("aria-busy", loading ? "true" : "false");
}

function showAlert(title, error) {
  const requestSuffix = error?.requestId
    ? ` Reference: ${error.requestId}.`
    : "";
  selectors.alertTitle.textContent = title;
  selectors.alertMessage.textContent = `${error?.message || error}${requestSuffix}`;
  selectors.alert.classList.remove("is-hidden");
}

function hideAlert() {
  selectors.alert.classList.add("is-hidden");
}

function toast(message, kind = "success") {
  const node = element("div", `toast ${kind}`, message);
  selectors.toastRegion.append(node);
  window.setTimeout(() => node.remove(), 3600);
}

function switchView(view) {
  const target = viewTitles[view] ? view : "mission";
  state.activeView = target;
  document.querySelectorAll("[data-view]").forEach((node) => {
    node.classList.toggle("is-active", node.dataset.view === target);
  });
  document.querySelectorAll("[data-view-target]").forEach((button) => {
    const active = button.dataset.viewTarget === target;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-current", active ? "page" : "false");
  });
  selectors.viewTitle.textContent = viewTitles[target];
  window.scrollTo({ top: 0, behavior: "smooth" });
  if (target === "evaluation") {
    loadEvaluation();
  }
}

async function checkHealth() {
  try {
    const health = await apiRequest("/api/health");
    selectors.systemState.classList.remove("is-error");
    selectors.systemState.classList.add("is-healthy");
    selectors.systemStateLabel.textContent = "System ready";
    selectors.systemStateDetail.textContent = `Product API v${health.version}`;
  } catch (error) {
    selectors.systemState.classList.remove("is-healthy");
    selectors.systemState.classList.add("is-error");
    selectors.systemStateLabel.textContent = "API unavailable";
    selectors.systemStateDetail.textContent = "Check runtime";
  }
}

async function createMission(event) {
  event.preventDefault();
  const requestEpoch = ++state.requestEpoch;
  hideAlert();
  const data = new FormData(selectors.missionForm);
  const genericPayload = {
    objective: String(data.get("objective") || "").trim(),
    max_cycles: 100,
  };
  let context = String(data.get("context") || "").trim();
  const owner = String(data.get("owner") || "").trim();
  const insuranceMission = state.pendingAttachments.length > 0;
  if (state.pendingAttachments.length) {
    try {
      if (!state.evidencePackage) {
        await buildEvidencePackage({ quiet: true });
      }
    } catch (error) {
      showAlert("Mission evidence is not ready", error);
      return;
    }
  }
  if (context) {
    genericPayload.context = context;
  }
  if (owner) {
    genericPayload.owner = owner;
  }

  setButtonLoading(selectors.launchButton, true);
  selectors.formHint.textContent =
    "Planning the mission and evaluating approval gates…";
  const slowMessageTimer = window.setTimeout(() => {
    selectors.formHint.textContent =
      "Agents are executing; bounded validation and repair may take several minutes.";
  }, 12000);
  try {
    const endpoint = insuranceMission
      ? "/api/insurance-poc/runs"
      : "/api/runs";
    const requestBody = insuranceMission
      ? {
          mission: context
            ? `${genericPayload.objective}\n\nFounder context: ${context}`
            : genericPayload.objective,
          attachments: state.pendingAttachments,
          owner: owner || "Founder",
        }
      : genericPayload;
    const created = await apiRequest(endpoint, {
      method: "POST",
      body: JSON.stringify(requestBody),
    });
    if (requestEpoch !== state.requestEpoch) {
      return;
    }
    state.runId = created.run_id;
    window.localStorage.setItem(ACTIVE_RUN_KEY, state.runId);
    state.snapshot = insuranceMission
      ? created.snapshot
      : created.workflow.snapshot;
    state.conflicts = created.conflicts || [];
    if (insuranceMission) {
      state.evidencePackage = created.evidence_package;
      state.routingPlan = created.routing_plan;
    }
    state.artifacts = [];
    state.events = state.snapshot.events || [];
    state.selectedArtifactId = null;
    await loadRun({ useCurrentSnapshot: true });
    if (state.runId === created.run_id && state.snapshot) {
      toast("Mission created and workflow evidence loaded.");
    }
  } catch (error) {
    if (requestEpoch === state.requestEpoch) {
      showAlert("Mission could not start", error);
    }
  } finally {
    window.clearTimeout(slowMessageTimer);
    selectors.formHint.textContent =
      "Generic missions remain available · Insurance POC is the primary demo";
    setButtonLoading(selectors.launchButton, false);
  }
}

async function loadRun({ useCurrentSnapshot = false } = {}) {
  if (!state.runId) {
    return;
  }
  const requestedRunId = state.runId;
  const requestEpoch = ++state.requestEpoch;
  hideAlert();
  selectors.refreshRun.disabled = true;
  try {
    const requests = [
      useCurrentSnapshot
        ? Promise.resolve(state.snapshot)
        : apiRequest(`/api/runs/${requestedRunId}`),
      apiRequest(`/api/runs/${requestedRunId}/artifacts`),
      apiRequest(`/api/runs/${requestedRunId}/events?limit=200`),
    ];
    const [snapshot, artifactResponse, eventResponse] =
      await Promise.all(requests);
    if (
      requestEpoch !== state.requestEpoch ||
      requestedRunId !== state.runId
    ) {
      return;
    }
    state.snapshot = snapshot;
    state.artifacts = artifactResponse.artifacts || [];
    state.events = eventResponse.events || [];
    hydrateInsuranceRunState();
    if (
      state.selectedArtifactId &&
      !state.artifacts.some(
        (item) => item.artifact.id === state.selectedArtifactId,
      )
    ) {
      state.selectedArtifactId = null;
    }
    renderAll();
  } catch (error) {
    if (
      requestEpoch === state.requestEpoch &&
      requestedRunId === state.runId
    ) {
      showAlert("Run evidence could not be refreshed", error);
    }
  } finally {
    if (
      requestEpoch === state.requestEpoch &&
      requestedRunId === state.runId
    ) {
      selectors.refreshRun.disabled = false;
    }
  }
}

async function retryRun() {
  if (!state.runId) {
    return;
  }
  const requestedRunId = state.runId;
  const requestEpoch = ++state.requestEpoch;
  hideAlert();
  setButtonLoading(selectors.retryRun, true);
  try {
    const result = await apiRequest(`/api/runs/${requestedRunId}/retry`, {
      method: "POST",
      body: JSON.stringify({ max_cycles: 100 }),
    });
    if (
      requestEpoch !== state.requestEpoch ||
      requestedRunId !== state.runId
    ) {
      return;
    }
    state.snapshot = result.snapshot;
    await loadRun({ useCurrentSnapshot: true });
    if (state.runId === requestedRunId && state.snapshot) {
      toast(
        result.terminal_failure
          ? "Recovery stopped safely. Review the failed task and audit evidence."
          : result.replayed
          ? "Completed evidence verified; no additional model calls were made."
          : "Bounded recovery completed.",
        result.terminal_failure ? "error" : "success",
      );
    }
  } catch (error) {
    if (
      requestEpoch === state.requestEpoch &&
      requestedRunId === state.runId
    ) {
      showAlert("Recovery could not continue", error);
    }
  } finally {
    setButtonLoading(selectors.retryRun, false);
  }
}

async function resolveApproval(approvalId, decision, card) {
  const reviewer = card.querySelector("[data-approval-reviewer]").value.trim();
  const reason = card.querySelector("[data-approval-reason]").value.trim();
  if (!reviewer || !reason) {
    showAlert(
      "Decision needs evidence",
      new Error("Enter the reviewer and a decision reason before continuing."),
    );
    return;
  }
  const requestedRunId = state.runId;
  const requestEpoch = ++state.requestEpoch;

  const buttons = card.querySelectorAll("button");
  buttons.forEach((button) => {
    button.disabled = true;
  });
  card.setAttribute("aria-busy", "true");
  const progress = element(
    "p",
    "approval-progress",
    decision === "approved"
      ? "Decision is being recorded. The controller will resume the workflow."
      : "Decision is being recorded. The controller will stop the workflow safely.",
  );
  card.querySelector(".approval-form").append(progress);
  const slowMessageTimer = window.setTimeout(() => {
    progress.textContent =
      "Agents are executing through bounded validation, repair, and artifact synthesis…";
  }, 12000);
  hideAlert();
  try {
    const response = await apiRequest(
      `/api/runs/${requestedRunId}/approvals/${approvalId}`,
      {
        method: "POST",
        body: JSON.stringify({
          decision,
          decided_by: reviewer,
          reason,
          max_cycles: 100,
        }),
      },
    );
    if (
      requestEpoch !== state.requestEpoch ||
      requestedRunId !== state.runId
    ) {
      return;
    }
    state.snapshot = response.workflow.snapshot;
    await loadRun({ useCurrentSnapshot: true });
    if (state.runId === requestedRunId && state.snapshot) {
      toast(
        decision === "approved"
          ? "Approval recorded. Workflow resumed through the controller."
          : "Rejection recorded. The workflow stopped with audit evidence.",
      );
    }
  } catch (error) {
    if (
      requestEpoch === state.requestEpoch &&
      requestedRunId === state.runId
    ) {
      showAlert("Approval could not be resolved", error);
      progress.remove();
      card.removeAttribute("aria-busy");
      buttons.forEach((button) => {
        button.disabled = false;
      });
    }
  } finally {
    window.clearTimeout(slowMessageTimer);
  }
}

function renderAll() {
  if (!state.snapshot) {
    renderEmptyDataViews();
    return;
  }
  selectors.composer.classList.add("is-hidden");
  selectors.emptyOverview.classList.add("is-hidden");
  selectors.runWorkspace.classList.remove("is-hidden");
  selectors.refreshRun.classList.remove("is-hidden");
  renderEvidenceBoard();
  renderRoutingBoard();
  renderRunSummary();
  renderWorkflow();
  renderPolicy();
  renderConflicts();
  renderAgents();
  renderApprovals();
  renderArtifacts();
  renderAudit();
}

function renderRunSummary() {
  const { run, tasks, approvals, route_decisions: routeDecisions } =
    state.snapshot;
  const completed = tasks.filter((task) => task.status === "completed").length;
  const pendingApprovals = approvals.filter(
    (approval) => approval.status === "pending",
  );
  const latestRoute = routeDecisions.at(-1);

  const runStatus = document.querySelector("#run-status");
  runStatus.textContent = labelize(run.status);
  runStatus.className = `status-pill ${statusClass(run.status)}`;
  document.querySelector("#run-short-id").textContent =
    `Run ${shortId(run.id)} · ${formatTime(run.created_at, true)}`;
  document.querySelector("#run-objective").textContent = run.objective;
  document.querySelector("#run-owner").textContent =
    `Owned by ${run.owner || "Founder"} · ${tasks.length} governed tasks`;
  document.querySelector("#progress-metric").textContent =
    `${completed} / ${tasks.length || 3}`;
  document.querySelector("#progress-detail").textContent =
    completed === tasks.length && tasks.length
      ? "workflow complete"
      : "tasks completed";
  document.querySelector("#artifact-metric").textContent =
    String(state.artifacts.length);
  document.querySelector("#approval-metric").textContent =
    String(pendingApprovals.length);
  document.querySelector("#approval-detail").textContent = pendingApprovals.length
    ? "decision required"
    : "no pending decisions";
  document.querySelector("#route-metric").textContent =
    latestRoute?.provider || "—";
  document.querySelector("#route-detail").textContent =
    latestRoute?.selected_model || "awaiting evidence";

  selectors.artifactCount.textContent = String(state.artifacts.length);
  selectors.artifactCount.classList.toggle(
    "is-hidden",
    state.artifacts.length === 0,
  );
  selectors.approvalCount.textContent = String(pendingApprovals.length);
  selectors.approvalCount.classList.toggle(
    "is-hidden",
    pendingApprovals.length === 0,
  );

  const notice = document.querySelector("#run-notice");
  const noticeTitle = document.querySelector("#notice-title");
  const noticeBody = document.querySelector("#notice-body");
  const noticeAction = document.querySelector("#notice-action");
  notice.classList.remove("is-hidden");
  if (run.status === "waiting_approval") {
    noticeTitle.textContent = "Founder decision required";
    noticeBody.textContent =
      `${pendingApprovals.length} controlled action awaits review before execution can continue.`;
    noticeAction.textContent = "Review";
    noticeAction.onclick = () => switchView("approvals");
  } else if (run.status === "failed") {
    noticeTitle.textContent = "Workflow stopped safely";
    noticeBody.textContent =
      "Inspect the failed task and audit evidence, then run bounded recovery if eligible.";
    noticeAction.textContent = "View audit";
    noticeAction.onclick = () => switchView("audit");
  } else if (run.status === "completed") {
    noticeTitle.textContent = "Decision bundle ready";
    noticeBody.textContent =
      `${state.artifacts.length} artifacts are available with checksum evidence.`;
    noticeAction.textContent = "Open bundle";
    noticeAction.onclick = () => switchView("artifacts");
  } else {
    notice.classList.add("is-hidden");
  }

  selectors.retryRun.textContent =
    run.status === "completed" ? "Verify replay" : "Retry / recover";
  selectors.retryRun.disabled = run.status === "waiting_approval";
  selectors.viewResult.disabled = state.artifacts.length === 0;
}

function taskAgent(task) {
  return (
    agentDefinitions[task.assigned_agent] || {
      className: "product",
      label: labelize(task.assigned_agent || "Unassigned agent"),
      monogram: "?",
      discipline: "Governed execution",
    }
  );
}

function tasksInStageOrder() {
  return [...state.snapshot.tasks].sort((left, right) => {
    const stageDifference =
      Number(left.metadata?.stage || 999) - Number(right.metadata?.stage || 999);
    if (stageDifference) {
      return stageDifference;
    }
    return String(left.metadata?.task_key || left.title).localeCompare(
      String(right.metadata?.task_key || right.title),
    );
  });
}

function renderWorkflow() {
  const track = document.querySelector("#workflow-track");
  track.replaceChildren();
  const tasksById = new Map(
    state.snapshot.tasks.map((task) => [task.id, task]),
  );
  const tasks = tasksInStageOrder();
  const stageCounts = new Map();
  tasks.forEach((task) => {
    const stage = Number(task.metadata?.stage || 0);
    stageCounts.set(stage, (stageCounts.get(stage) || 0) + 1);
  });
  tasks.forEach((task) => {
    const definition = taskAgent(task);
    const stage = Number(task.metadata?.stage || 0);
    const parallel = (stageCounts.get(stage) || 0) > 1;
    const dependencies = task.dependency_ids
      .map((id) => tasksById.get(id)?.title)
      .filter(Boolean);
    const step = element(
      "article",
      `workflow-step ${
        task.status === "completed"
          ? "is-completed"
          : ["running", "waiting_approval", "ready"].includes(task.status)
            ? "is-current"
            : ""
      }`,
    );
    const stepIndex = element(
      "span",
      "step-index",
      task.status === "completed" ? "✓" : String(stage).padStart(2, "0"),
    );
    const copy = element("div");
    append(
      copy,
      element("h3", null, `${definition.label} · ${task.title}`),
      element(
        "p",
        null,
        `${parallel ? `Stage ${String(stage).padStart(2, "0")} · parallel · ` : ""}${
          dependencies.length
            ? `Depends on ${dependencies.join(" + ")}`
            : "Ready at workflow start"
        }`,
      ),
    );
    const badge = element(
      "span",
      `task-status ${statusClass(task.status)}`,
      labelize(task.status),
    );
    append(step, stepIndex, copy, badge);
    track.append(step);
  });
  document.querySelector("#workflow-updated").textContent =
    `Updated ${formatTime(state.snapshot.run.updated_at)}`;
}

function hydrateInsuranceRunState() {
  const metadata = state.snapshot?.run?.metadata || {};
  if (metadata.evidence_package) {
    state.evidencePackage = metadata.evidence_package;
  }
  if (metadata.routing_plan) {
    state.routingPlan = metadata.routing_plan;
  }
  const resource = state.artifacts.find(
    (item) => item.artifact?.name === "conflict-resolution-log",
  );
  if (resource?.content) {
    try {
      const value = JSON.parse(resource.content);
      state.conflicts = Array.isArray(value.conflicts) ? value.conflicts : [];
    } catch (_error) {
      state.conflicts = [];
    }
  }
}

function conflictValue(conflict, side) {
  if (conflict.conflict_type === "scope_budget") {
    const proposal = conflict[side] || {};
    const cost = Number(proposal.planned_cost_cny || 0).toLocaleString();
    return side === "proposal_before"
      ? `CNY ${cost} scope request`
      : `CNY ${cost}; deferred ${proposal.deferred || "optional work"}`;
  }
  return labelize(conflict[side]?.decision_mode || "unknown");
}

function renderConflicts() {
  selectors.conflictGrid.replaceChildren();
  if (!state.conflicts.length) {
    selectors.conflictSection.classList.add("is-hidden");
    return;
  }
  selectors.conflictSection.classList.remove("is-hidden");
  selectors.conflictSummary.textContent =
    `${state.conflicts.length} resolved from structured outputs`;
  state.conflicts.forEach((conflict) => {
    const card = element("article", "conflict-card");
    const head = element("div", "conflict-head");
    append(
      head,
      element("span", "conflict-id", conflict.conflict_id),
      element("span", "task-status status-completed", "Resolved"),
    );
    const transition = element("div", "conflict-transition");
    append(
      transition,
      element("div", "conflict-before", conflictValue(conflict, "proposal_before")),
      element("span", "conflict-arrow", "→"),
      element("div", "conflict-after", conflictValue(conflict, "proposal_after")),
    );
    append(
      card,
      head,
      element("h3", null, labelize(conflict.conflict_type)),
      element(
        "p",
        "conflict-agents",
        `${labelize(conflict.raised_by)} challenged ${conflict.affected_agents.map(labelize).join(", ")}`,
      ),
      transition,
      element("p", "conflict-rule", `Rule: ${labelize(conflict.resolution_rule)}`),
      element(
        "small",
        null,
        `Evidence ${conflict.source_evidence.join(", ")} · accepted by ${conflict.accepted_by.map(labelize).join(", ")}`,
      ),
    );
    selectors.conflictGrid.append(card);
  });
}

function policyEvidence() {
  const pending = state.snapshot.approvals.filter(
    (approval) => approval.status === "pending",
  );
  const failed = state.snapshot.tasks.some((task) => task.status === "failed");
  const actions = state.snapshot.tasks
    .map((task) => task.metadata?.policy_action)
    .filter((action) => action && typeof action === "object");
  const rules = pending.flatMap(
    (approval) => [
      ...(approval.metadata?.policy_rule_ids || []),
      ...(approval.metadata?.blocked_policy_rule_ids || []),
    ],
  );
  const reviewer = pending
    .map((approval) => approval.metadata?.reviewer_required)
    .find(Boolean);

  let risk = "low";
  if (failed) {
    risk = "critical";
  } else if (pending.length && ["security", "finance"].includes(reviewer)) {
    risk = "high";
  } else if (pending.length) {
    risk = "moderate";
  }
  return { actions, pending, reviewer, risk, rules: [...new Set(rules)] };
}

function renderPolicy() {
  const summary = document.querySelector("#policy-summary");
  summary.replaceChildren();
  const evidence = policyEvidence();
  const riskBadge = document.querySelector("#risk-badge");
  riskBadge.textContent = evidence.risk.toUpperCase();
  riskBadge.className = `risk-badge risk-${evidence.risk}`;

  const control = element("div", "policy-state");
  append(
    control,
    element(
      "strong",
      null,
      evidence.pending.length
        ? "Controlled action paused"
        : "No active policy blocker",
    ),
    element(
      "p",
      null,
      evidence.pending.length
        ? `${evidence.pending.length} decision awaits ${evidence.reviewer || "founder"} review.`
        : "Current actions are allowed or have already been resolved by the workflow authority.",
    ),
  );

  const boundary = element("div", "policy-state");
  append(
    boundary,
    element("strong", null, "Execution boundary"),
    element(
      "p",
      null,
      evidence.actions.length
        ? `${evidence.actions.length} deterministic policy action${evidence.actions.length === 1 ? "" : "s"} recorded across the task graph.`
        : "Agents propose results; only the Workflow Controller changes authoritative state.",
    ),
  );
  if (evidence.rules.length) {
    const rules = element("div", "policy-rules");
    evidence.rules.forEach((rule) =>
      rules.append(element("span", "policy-rule", rule)),
    );
    boundary.append(rules);
  }
  append(summary, control, boundary);
}

function renderAgents() {
  const grid = document.querySelector("#agent-grid");
  grid.replaceChildren();
  const completed = state.snapshot.tasks.filter(
    (task) => task.status === "completed",
  ).length;

  tasksInStageOrder().forEach((task) => {
    const definition = taskAgent(task);
    const route = state.snapshot.route_decisions
      .filter(
        (decision) =>
          decision.task_id === task.id ||
          (!decision.task_id &&
            task.assigned_agent === "executive-orchestrator"),
      )
      .at(-1);
    const artifactCount = state.snapshot.artifacts.filter(
      (artifact) => artifact.task_id === task.id,
    ).length;
    const card = element("article", `agent-card ${definition.className}`);
    const bar = element("div", "agent-card-bar");
    const body = element("div", "agent-card-body");
    const head = element("div", "agent-card-head");
    const identity = element("div", "agent-identity");
    const identityCopy = element("div");
    append(
      identityCopy,
      element("h3", null, definition.label),
      element("p", null, definition.discipline),
    );
    append(
      identity,
      element("span", "agent-monogram", definition.monogram),
      identityCopy,
    );
    append(
      head,
      identity,
      element(
        "span",
        `task-status ${statusClass(task.status)}`,
        labelize(task.status),
      ),
    );
    const evidence = element("div", "agent-evidence");
    [
      ["Route", route?.provider || "Awaiting route"],
      ["Model", route?.selected_model || "—"],
      ["Attempts", `${task.attempt_count} / ${task.max_attempts}`],
      ["Artifacts", String(artifactCount)],
    ].forEach(([label, value]) => {
      const row = element("div", "evidence-row");
      append(row, element("span", null, label), element("strong", null, value));
      evidence.append(row);
    });
    append(
      body,
      head,
      element("p", "agent-task", task.description || task.title),
      evidence,
    );
    append(card, bar, body);
    grid.append(card);
  });
  document.querySelector("#agent-summary").textContent =
    `${completed} of ${state.snapshot.tasks.length} complete`;
}

function renderApprovals() {
  selectors.approvalList.replaceChildren();
  const approvals = [...state.snapshot.approvals].sort((left, right) => {
    if (left.status === right.status) {
      return new Date(right.created_at) - new Date(left.created_at);
    }
    return left.status === "pending" ? -1 : 1;
  });

  if (!approvals.length) {
    selectors.approvalList.append(
      emptyCard(
        "✓",
        "No approval requests",
        "The policy gate has not paused this workflow. Controlled actions will appear here.",
      ),
    );
    return;
  }

  approvals.forEach((approval) => {
    const pending = approval.status === "pending";
    const reviewer =
      approval.metadata?.reviewer_required ||
      approval.decided_by ||
      "founder";
    const card = element(
      "article",
      `approval-card ${pending ? "" : "is-resolved"}`,
    );
    const copy = element("div");
    const kicker = element("div", "approval-kicker");
    append(
      kicker,
      element(
        "span",
        `task-status ${statusClass(approval.status)}`,
        labelize(approval.status),
      ),
      element("span", "subtle-label", `Approval ${shortId(approval.id)}`),
    );
    const meta = element("div", "approval-meta");
    [
      ["Required reviewer", reviewer],
      ["Requested by", approval.requested_by],
      ["Expires", formatTime(approval.expires_at, true)],
      ["Policy rules", (approval.metadata?.policy_rule_ids || []).join(", ") || "Plan gate"],
    ].forEach(([label, value]) => {
      const block = element("div");
      append(
        block,
        element("span", null, label),
        element("strong", null, value),
      );
      meta.append(block);
    });
    append(
      copy,
      kicker,
      element(
        "h3",
        null,
        approval.task_id
          ? "Controlled task action"
          : "Executive plan review",
      ),
      element("p", "approval-reason", approval.reason),
      meta,
    );
    card.append(copy);

    if (pending) {
      const form = element("div", "approval-form");
      const reviewerLabel = element("label", null, "Reviewer");
      reviewerLabel.setAttribute("for", `reviewer-${approval.id}`);
      const reviewerInput = element("input");
      reviewerInput.id = `reviewer-${approval.id}`;
      reviewerInput.value = reviewer;
      reviewerInput.maxLength = 200;
      reviewerInput.dataset.approvalReviewer = "";
      const reasonLabel = element("label", null, "Decision reason");
      reasonLabel.setAttribute("for", `reason-${approval.id}`);
      const reasonInput = element("textarea");
      reasonInput.id = `reason-${approval.id}`;
      reasonInput.rows = 3;
      reasonInput.maxLength = 2000;
      reasonInput.placeholder =
        "Record why this action should continue or stop.";
      reasonInput.dataset.approvalReason = "";
      const actions = element("div", "approval-actions");
      const reject = element("button", "button button-danger", "Reject");
      reject.type = "button";
      reject.addEventListener("click", () =>
        resolveApproval(approval.id, "rejected", card),
      );
      const approve = element(
        "button",
        "button button-primary",
        "Approve & resume",
      );
      approve.type = "button";
      approve.addEventListener("click", () =>
        resolveApproval(approval.id, "approved", card),
      );
      append(actions, reject, approve);
      append(
        form,
        reviewerLabel,
        reviewerInput,
        reasonLabel,
        reasonInput,
        actions,
      );
      card.append(form);
    } else {
      const resolution = element("div", "approval-form");
      append(
        resolution,
        element("strong", null, `Resolved by ${approval.decided_by || "—"}`),
        element(
          "p",
          "approval-reason",
          approval.decision_reason || "No decision reason recorded.",
        ),
        element(
          "span",
          "subtle-label",
          formatTime(approval.decided_at, true),
        ),
      );
      card.append(resolution);
    }
    selectors.approvalList.append(card);
  });
}

function artifactName(resource) {
  return (
    resource.artifact.metadata?.filename ||
    resource.artifact.name ||
    "Artifact"
  );
}

function renderArtifacts() {
  selectors.artifactList.replaceChildren();
  if (!state.artifacts.length) {
    selectors.artifactList.append(
      emptyCard(
        "▱",
        "No artifacts yet",
        "Validated Product, Finance, and Executive outputs will appear after execution.",
      ),
    );
    renderArtifactViewer();
    return;
  }

  const head = element("div", "artifact-list-head");
  append(
    head,
    element("strong", null, "Decision bundle"),
    element(
      "span",
      null,
      `${state.artifacts.length} integrity-checked files`,
    ),
  );
  selectors.artifactList.append(head);

  state.artifacts.forEach((resource) => {
    const button = element(
      "button",
      `artifact-item ${
        state.selectedArtifactId === resource.artifact.id ? "is-active" : ""
      }`,
    );
    button.type = "button";
    button.dataset.artifactId = resource.artifact.id;
    const copy = element("span");
    append(
      copy,
      element("strong", null, artifactName(resource)),
      element(
        "small",
        null,
        `${labelize(resource.artifact.kind)} · ${resource.artifact.size_bytes || 0} bytes`,
      ),
    );
    append(
      button,
      element("span", "artifact-icon", "▱"),
      copy,
      element(
        "span",
        resource.content_available ? "integrity-dot" : "",
        resource.content_available ? "" : "—",
      ),
    );
    button.addEventListener("click", () => {
      state.selectedArtifactId = resource.artifact.id;
      renderArtifacts();
    });
    selectors.artifactList.append(button);
  });

  if (!state.selectedArtifactId) {
    const preferred =
      state.artifacts.find((resource) =>
        artifactName(resource).toLowerCase().includes("decision"),
      ) || state.artifacts[0];
    state.selectedArtifactId = preferred.artifact.id;
  }
  renderArtifactViewer();
}

function renderArtifactViewer() {
  selectors.artifactViewer.replaceChildren();
  const resource = state.artifacts.find(
    (item) => item.artifact.id === state.selectedArtifactId,
  );
  selectors.downloadSelected.disabled = !resource?.content_available;
  if (!resource) {
    const empty = element("div", "artifact-empty");
    append(
      empty,
      element("span", null, "▱"),
      element("h3", null, "Select an artifact"),
      element("p", null, "The verified content will appear here."),
    );
    selectors.artifactViewer.append(empty);
    return;
  }

  const head = element("div", "viewer-head");
  const copy = element("div");
  append(
    copy,
    element("h3", null, artifactName(resource)),
    element(
      "p",
      null,
      `SHA-256 ${resource.artifact.checksum_sha256 || "not available"}`,
    ),
  );
  append(
    head,
    copy,
    element(
      "span",
      "verified-label",
      resource.content_available ? "● Checksum verified" : "Content unavailable",
    ),
  );
  const content = element(
    "pre",
    "viewer-content",
    resource.content ||
      `Content omitted: ${resource.content_omitted_reason || "not available"}`,
  );
  append(selectors.artifactViewer, head, content);
}

function downloadSelectedArtifact() {
  const resource = state.artifacts.find(
    (item) => item.artifact.id === state.selectedArtifactId,
  );
  if (!resource?.content_available || resource.content === null) {
    return;
  }
  const blob = new Blob([resource.content], {
    type: resource.artifact.content_type || "text/plain;charset=utf-8",
  });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = safeFilename(artifactName(resource));
  document.body.append(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
  toast(`${artifactName(resource)} downloaded.`);
}

function renderAudit() {
  selectors.auditList.replaceChildren();
  selectors.auditCount.textContent =
    `${state.events.length} event${state.events.length === 1 ? "" : "s"}`;
  if (!state.events.length) {
    selectors.auditList.append(
      emptyCard(
        "≋",
        "No audit events yet",
        "Workflow state transitions and decisions will appear here.",
      ),
    );
    return;
  }

  [...state.events].reverse().forEach((event) => {
    const item = element("article", "audit-item");
    const marker = element(
      "span",
      `audit-marker ${event.outcome || "pending"}`,
    );
    marker.setAttribute("aria-hidden", "true");
    const time = element("time", "audit-time", formatTime(event.created_at, true));
    time.dateTime = event.created_at;
    const copy = element("div", "audit-main");
    const target = [
      event.actor,
      event.target_type,
      event.target_id ? shortId(event.target_id) : null,
    ]
      .filter(Boolean)
      .join(" · ");
    append(
      copy,
      element("strong", null, labelize(event.event_type)),
      element("p", null, `${event.action} · ${target}`),
    );
    append(
      item,
      marker,
      time,
      copy,
      element(
        "span",
        `outcome-badge ${event.outcome || "pending"}`,
        labelize(event.outcome),
      ),
    );
    selectors.auditList.append(item);
  });
}

function formatPercent(value) {
  const number = Number(value);
  return Number.isFinite(number) ? `${number.toFixed(1)}%` : "—";
}

async function loadEvaluation() {
  const evaluationEpoch = ++state.evaluationEpoch;
  hideAlert();
  setButtonLoading(selectors.refreshEvaluation, true);
  try {
    const summary = await apiRequest("/api/evaluation/summary?limit=50");
    if (evaluationEpoch !== state.evaluationEpoch) {
      return;
    }
    state.evaluation = summary;
    renderEvaluation();
  } catch (error) {
    if (evaluationEpoch === state.evaluationEpoch) {
      showAlert("Evaluation evidence could not be loaded", error);
      renderEvaluationEmpty();
    }
  } finally {
    if (evaluationEpoch === state.evaluationEpoch) {
      setButtonLoading(selectors.refreshEvaluation, false);
    }
  }
}

function renderEvaluationEmpty() {
  document.querySelector("#evaluation-run-count").textContent = "0";
  document.querySelector("#evaluation-run-detail").textContent =
    "no persisted runs";
  document.querySelector("#evaluation-completion").textContent = "—";
  document.querySelector("#evaluation-average").textContent = "—";
  document.querySelector("#evaluation-integrity").textContent = "—";
  document.querySelector("#evaluation-grade").textContent = "—";
  document.querySelector("#evaluation-grade").className =
    "evaluation-grade grade-attention";
  document.querySelector("#evaluation-retries").textContent = "0 retries";
  document.querySelector("#evaluation-updated").textContent =
    "Awaiting persisted evidence";
  selectors.evaluationLatest.replaceChildren(
    emptyCard(
      "◫",
      "No evaluated runs",
      "Launch a founder mission to create deterministic execution evidence.",
    ),
  );
  selectors.evaluationAgents.replaceChildren(
    element("p", "evaluation-empty-copy", "No agent performance evidence yet."),
  );
  selectors.evaluationRuns.replaceChildren(
    element("p", "evaluation-empty-copy", "No Run history is available."),
  );
  selectors.evaluationProviders.replaceChildren(
    element("p", "evaluation-empty-copy", "No provider routes are recorded."),
  );
}

function renderEvaluation() {
  const summary = state.evaluation;
  if (!summary || !summary.recent_runs?.length) {
    renderEvaluationEmpty();
    return;
  }

  document.querySelector("#evaluation-run-count").textContent =
    String(summary.run_count);
  document.querySelector("#evaluation-run-detail").textContent =
    `${summary.task_success_rate.toFixed(1)}% task success`;
  document.querySelector("#evaluation-completion").textContent =
    formatPercent(summary.completion_rate);
  document.querySelector("#evaluation-average").textContent =
    Number(summary.average_score).toFixed(1);
  document.querySelector("#evaluation-integrity").textContent =
    formatPercent(summary.artifact_integrity_rate);
  document.querySelector("#evaluation-retries").textContent =
    `${summary.total_retries} ${summary.total_retries === 1 ? "retry" : "retries"}`;
  document.querySelector("#evaluation-updated").textContent =
    `Updated ${formatTime(summary.generated_at, true)}`;

  renderLatestEvaluation(summary.recent_runs[0]);
  renderEvaluationAgents(summary.agent_performance || []);
  renderEvaluationRuns(summary.recent_runs);
  renderEvaluationProviders(
    summary.provider_distribution || {},
    summary.run_count,
  );
}

function renderLatestEvaluation(run) {
  selectors.evaluationLatest.replaceChildren();
  const grade = String(run.grade || "attention").replace(/[^a-z]/g, "");
  const gradeBadge = document.querySelector("#evaluation-grade");
  gradeBadge.textContent = labelize(grade);
  gradeBadge.className = `evaluation-grade grade-${grade}`;

  const hero = element("div", "evaluation-score-hero");
  const score = element(
    "strong",
    `evaluation-score grade-ring-${grade}`,
    run.overall_score,
  );
  score.setAttribute("aria-label", `${run.overall_score} out of 100`);
  const copy = element("div");
  append(
    copy,
    element("h3", null, run.objective),
    element(
      "p",
      null,
      `${labelize(run.status)} · Run ${shortId(run.run_id)} · ${formatTime(run.updated_at, true)}`,
    ),
  );
  append(hero, score, copy);

  const dimensions = element("div", "evaluation-dimensions");
  run.dimensions.forEach((dimension) => {
    const item = element("article", "evaluation-dimension");
    const heading = element("div", "evaluation-dimension-head");
    append(
      heading,
      element("strong", null, dimension.label),
      element("span", null, `${dimension.score.toFixed(1)} / 100`),
    );
    const track = element("div", "evaluation-track");
    const fill = element(
      "span",
      `evaluation-fill dimension-${dimension.status}`,
    );
    fill.style.width = `${Math.max(0, Math.min(100, dimension.score))}%`;
    track.append(fill);
    append(
      item,
      heading,
      track,
      element("p", null, dimension.evidence.join(" ")),
    );
    dimensions.append(item);
  });
  append(selectors.evaluationLatest, hero, dimensions);
}

function renderEvaluationAgents(agents) {
  selectors.evaluationAgents.replaceChildren();
  if (!agents.length) {
    selectors.evaluationAgents.append(
      element("p", "evaluation-empty-copy", "No governed tasks are available."),
    );
    return;
  }
  agents.forEach((agent) => {
    const row = element("article", "evaluation-bar-row");
    const heading = element("div", "evaluation-bar-head");
    append(
      heading,
      element("strong", null, labelize(agent.agent_id)),
      element("span", null, `${agent.success_rate.toFixed(1)}% success`),
    );
    const track = element("div", "evaluation-track");
    const fill = element("span", "evaluation-fill dimension-pass");
    fill.style.width = `${Math.max(0, Math.min(100, agent.success_rate))}%`;
    track.append(fill);
    append(
      row,
      heading,
      track,
      element(
        "p",
        null,
        `${agent.completed}/${agent.tasks} complete · ${agent.retries} retries · ${agent.average_attempts.toFixed(2)} avg attempts`,
      ),
    );
    selectors.evaluationAgents.append(row);
  });
}

function renderEvaluationRuns(runs) {
  selectors.evaluationRuns.replaceChildren();
  runs.forEach((run) => {
    const row = element("article", "evaluation-run-row");
    const copy = element("div", "evaluation-run-copy");
    append(
      copy,
      element("strong", null, run.objective),
      element(
        "p",
        null,
        `Run ${shortId(run.run_id)} · ${formatTime(run.updated_at, true)} · ${run.completed_tasks}/${run.task_count} tasks`,
      ),
    );
    const evidence = element("div", "evaluation-run-evidence");
    append(
      evidence,
      element(
        "span",
        `task-status ${statusClass(run.status)}`,
        labelize(run.status),
      ),
      element("strong", "evaluation-run-score", run.overall_score),
    );
    const inspect = element(
      "button",
      "button button-small",
      "Inspect Run",
    );
    inspect.type = "button";
    inspect.addEventListener("click", () => openEvaluatedRun(run.run_id));
    append(row, copy, evidence, inspect);
    selectors.evaluationRuns.append(row);
  });
}

function renderEvaluationProviders(distribution, evaluatedRunCount) {
  selectors.evaluationProviders.replaceChildren();
  const entries = Object.entries(distribution);
  if (!entries.length) {
    selectors.evaluationProviders.append(
      element("p", "evaluation-empty-copy", "No provider routes are recorded."),
    );
    return;
  }
  entries.forEach(([provider, count]) => {
    const row = element("div", "provider-row");
    append(
      row,
      element("strong", null, labelize(provider)),
      element(
        "span",
        null,
        `${count} / ${evaluatedRunCount} evaluated runs`,
      ),
    );
    selectors.evaluationProviders.append(row);
  });
}

function openEvaluatedRun(runId) {
  state.requestEpoch += 1;
  state.runId = runId;
  window.localStorage.setItem(ACTIVE_RUN_KEY, state.runId);
  state.snapshot = null;
  state.artifacts = [];
  state.events = [];
  state.evidencePackage = null;
  state.routingPlan = null;
  state.conflicts = [];
  state.selectedArtifactId = null;
  selectors.composer.classList.add("is-hidden");
  selectors.emptyOverview.classList.add("is-hidden");
  selectors.runWorkspace.classList.add("is-hidden");
  switchView("mission");
  toast(`Loading Run ${shortId(runId)} evidence.`);
  loadRun();
}

function emptyCard(icon, title, message) {
  const empty = element("div", "empty-card");
  append(
    empty,
    element("span", null, icon),
    element("h3", null, title),
    element("p", null, message),
  );
  return empty;
}

function renderEmptyDataViews() {
  selectors.approvalList.replaceChildren(
    emptyCard(
      "✓",
      "No active mission",
      "Launch a founder mission to review controlled actions and policy evidence.",
    ),
  );
  selectors.artifactList.replaceChildren(
    emptyCard(
      "▱",
      "No active mission",
      "The synthesized decision bundle will appear after a workflow runs.",
    ),
  );
  renderArtifactViewer();
  selectors.auditList.replaceChildren(
    emptyCard(
      "≋",
      "No active mission",
      "The append-only audit trace will appear after a workflow starts.",
    ),
  );
}

function startNewMission() {
  state.requestEpoch += 1;
  state.runId = null;
  state.snapshot = null;
  state.artifacts = [];
  state.events = [];
  state.selectedArtifactId = null;
  state.pendingAttachments = [];
  state.evidencePackage = null;
  state.routingPlan = null;
  state.conflicts = [];
  window.localStorage.removeItem(ACTIVE_RUN_KEY);
  selectors.composer.classList.remove("is-hidden");
  selectors.emptyOverview.classList.remove("is-hidden");
  selectors.runWorkspace.classList.add("is-hidden");
  selectors.refreshRun.classList.add("is-hidden");
  selectors.missionForm.reset();
  renderAttachmentList();
  renderEvidenceBoard();
  renderRoutingBoard();
  selectors.approvalCount.classList.add("is-hidden");
  selectors.artifactCount.classList.add("is-hidden");
  hideAlert();
  renderEmptyDataViews();
  switchView("mission");
  document.querySelector("#objective").focus();
}

document.querySelectorAll("[data-view-target]").forEach((button) => {
  button.addEventListener("click", () => switchView(button.dataset.viewTarget));
});
selectors.missionForm.addEventListener("submit", createMission);
selectors.evidenceFiles.addEventListener("change", handleEvidenceFiles);
selectors.previewEvidence.addEventListener("click", previewEvidence);
selectors.loadPocFixture.addEventListener("click", loadPocFixture);
selectors.simulateRouteFallback.addEventListener("click", simulateRouteFallback);
selectors.refreshRun.addEventListener("click", () => loadRun());
selectors.refreshEvaluation.addEventListener("click", loadEvaluation);
selectors.retryRun.addEventListener("click", retryRun);
selectors.newMission.addEventListener("click", startNewMission);
selectors.viewResult.addEventListener("click", () => switchView("artifacts"));
selectors.downloadSelected.addEventListener("click", downloadSelectedArtifact);
document
  .querySelector("#dismiss-alert")
  .addEventListener("click", hideAlert);

renderEmptyDataViews();
renderAttachmentList();
checkHealth();
const persistedRunId = window.localStorage.getItem(ACTIVE_RUN_KEY);
if (
  persistedRunId &&
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(
    persistedRunId,
  )
) {
  state.runId = persistedRunId;
  selectors.composer.classList.add("is-hidden");
  selectors.emptyOverview.classList.add("is-hidden");
  loadRun();
}
