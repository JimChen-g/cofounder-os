"use strict";

const state = {
  runId: null,
  snapshot: null,
  artifacts: [],
  events: [],
  selectedArtifactId: null,
  activeView: "mission",
};

const viewTitles = {
  mission: "Mission",
  approvals: "Approvals",
  artifacts: "Artifacts",
  audit: "Audit trail",
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
  downloadSelected: document.querySelector("#download-selected"),
  emptyOverview: document.querySelector("#empty-overview"),
  formHint: document.querySelector("#form-hint"),
  launchButton: document.querySelector("#launch-mission"),
  missionForm: document.querySelector("#mission-form"),
  newMission: document.querySelector("#new-mission"),
  refreshRun: document.querySelector("#refresh-run"),
  retryRun: document.querySelector("#retry-run"),
  runWorkspace: document.querySelector("#run-workspace"),
  systemState: document.querySelector("#system-state"),
  systemStateLabel: document.querySelector("#system-state-label"),
  systemStateDetail: document.querySelector("#system-state-detail"),
  toastRegion: document.querySelector("#toast-region"),
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
  hideAlert();
  const data = new FormData(selectors.missionForm);
  const payload = {
    objective: String(data.get("objective") || "").trim(),
    max_cycles: 100,
  };
  const context = String(data.get("context") || "").trim();
  const owner = String(data.get("owner") || "").trim();
  if (context) {
    payload.context = context;
  }
  if (owner) {
    payload.owner = owner;
  }

  setButtonLoading(selectors.launchButton, true);
  selectors.formHint.textContent =
    "Planning the mission and evaluating approval gates…";
  const slowMessageTimer = window.setTimeout(() => {
    selectors.formHint.textContent =
      "Agents are executing; bounded validation and repair may take several minutes.";
  }, 12000);
  try {
    const created = await apiRequest("/api/runs", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    state.runId = created.run_id;
    state.snapshot = created.workflow.snapshot;
    state.artifacts = [];
    state.events = state.snapshot.events || [];
    state.selectedArtifactId = null;
    await loadRun({ useCurrentSnapshot: true });
    toast("Mission created and workflow evidence loaded.");
  } catch (error) {
    showAlert("Mission could not start", error);
  } finally {
    window.clearTimeout(slowMessageTimer);
    selectors.formHint.textContent =
      "One bounded workflow · up to three agent calls";
    setButtonLoading(selectors.launchButton, false);
  }
}

async function loadRun({ useCurrentSnapshot = false } = {}) {
  if (!state.runId) {
    return;
  }
  hideAlert();
  selectors.refreshRun.disabled = true;
  try {
    const requests = [
      useCurrentSnapshot
        ? Promise.resolve(state.snapshot)
        : apiRequest(`/api/runs/${state.runId}`),
      apiRequest(`/api/runs/${state.runId}/artifacts`),
      apiRequest(`/api/runs/${state.runId}/events?limit=200`),
    ];
    const [snapshot, artifactResponse, eventResponse] =
      await Promise.all(requests);
    state.snapshot = snapshot;
    state.artifacts = artifactResponse.artifacts || [];
    state.events = eventResponse.events || [];
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
    showAlert("Run evidence could not be refreshed", error);
  } finally {
    selectors.refreshRun.disabled = false;
  }
}

async function retryRun() {
  if (!state.runId) {
    return;
  }
  hideAlert();
  setButtonLoading(selectors.retryRun, true);
  try {
    const result = await apiRequest(`/api/runs/${state.runId}/retry`, {
      method: "POST",
      body: JSON.stringify({ max_cycles: 100 }),
    });
    state.snapshot = result.snapshot;
    await loadRun({ useCurrentSnapshot: true });
    toast(
      result.replayed
        ? "Completed evidence verified; no additional model calls were made."
        : "Bounded recovery completed.",
    );
  } catch (error) {
    showAlert("Recovery could not continue", error);
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
      `/api/runs/${state.runId}/approvals/${approvalId}`,
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
    state.snapshot = response.workflow.snapshot;
    await loadRun({ useCurrentSnapshot: true });
    toast(
      decision === "approved"
        ? "Approval recorded. Workflow resumed through the controller."
        : "Rejection recorded. The workflow stopped with audit evidence.",
    );
  } catch (error) {
    showAlert("Approval could not be resolved", error);
    progress.remove();
    card.removeAttribute("aria-busy");
    buttons.forEach((button) => {
      button.disabled = false;
    });
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
  renderRunSummary();
  renderWorkflow();
  renderPolicy();
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

function renderWorkflow() {
  const track = document.querySelector("#workflow-track");
  track.replaceChildren();
  const tasksById = new Map(
    state.snapshot.tasks.map((task) => [task.id, task]),
  );
  state.snapshot.tasks.forEach((task, index) => {
    const definition = taskAgent(task);
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
      task.status === "completed" ? "✓" : String(index + 1).padStart(2, "0"),
    );
    const copy = element("div");
    append(
      copy,
      element("h3", null, `${definition.label} · ${task.title}`),
      element(
        "p",
        null,
        dependencies.length
          ? `Depends on ${dependencies.join(" + ")}`
          : "Ready at workflow start",
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

function policyEvidence() {
  const pending = state.snapshot.approvals.filter(
    (approval) => approval.status === "pending",
  );
  const failed = state.snapshot.tasks.some((task) => task.status === "failed");
  const actions = state.snapshot.tasks
    .map((task) => task.metadata?.policy_action)
    .filter((action) => action && typeof action === "object");
  const rules = pending.flatMap(
    (approval) => approval.metadata?.policy_rule_ids || [],
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

  state.snapshot.tasks.forEach((task) => {
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
  state.runId = null;
  state.snapshot = null;
  state.artifacts = [];
  state.events = [];
  state.selectedArtifactId = null;
  selectors.composer.classList.remove("is-hidden");
  selectors.emptyOverview.classList.remove("is-hidden");
  selectors.runWorkspace.classList.add("is-hidden");
  selectors.refreshRun.classList.add("is-hidden");
  selectors.missionForm.reset();
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
selectors.refreshRun.addEventListener("click", () => loadRun());
selectors.retryRun.addEventListener("click", retryRun);
selectors.newMission.addEventListener("click", startNewMission);
selectors.viewResult.addEventListener("click", () => switchView("artifacts"));
selectors.downloadSelected.addEventListener("click", downloadSelectedArtifact);
document
  .querySelector("#dismiss-alert")
  .addEventListener("click", hideAlert);

renderEmptyDataViews();
checkHealth();
