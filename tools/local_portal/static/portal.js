'use strict';
(() => {
  const initialView = location.hash.slice(1);
  const $ = id => document.getElementById(id);
  const TERMINAL = new Set(['completed', 'failed', 'cancelled', 'canceled', 'rejected', 'expired']);
  const TITLES = {overview: '今日', tasks: '工程', review: '审阅与决定', audit: '记录'};
  const shared = window.CofounderState;
  const LABELS = {pending: '等待批准', running: '执行中', ready: '待执行', queued: '排队中', created: '已创建', waiting_approval: '等待批准', completed: '已完成', approved: '已批准', failed: '失败', cancelled: '已取消', canceled: '已取消', rejected: '已驳回', expired: '已过期', repair_queued: '修复排队中', manual_required: '需要人工处理', passed: '通过', pass: '通过', success: '通过', blocked: '已阻止', timeout: '超时', active: '执行中', passed_checks_pending_delivery_approval: '检查通过，等待批准'};
  const state = {view: 'overview', connected: false, service: null, runs: [], runId: null, snapshot: null, delivery: null, candidate: null, candidateError: '', events: [], generation: 0, busy: false, loading: false, timer: null, detailKey: null, currentFile: null, uncertain: null, eventsGeneration: 0};
  try { state.uncertain = JSON.parse(localStorage.getItem('spark-pending-action') || sessionStorage.getItem('spark-pending-action') || 'null'); } catch (_) { /* Storage is optional. */ }
  const text = value => value === undefined || value === null || value === '' ? '—' : typeof value === 'object' ? JSON.stringify(value) : String(value);
  const pretty = value => JSON.stringify(value, null, 2);
  const label = value => shared.label(value) !== value ? shared.label(value) : LABELS[value] || text(value);
  const time = value => { const date = new Date(value); return value && Number.isFinite(date.getTime()) ? date.toLocaleString('zh-CN', {hour12: false}) : '未提供'; };
  const short = value => typeof value === 'string' && value.length > 16 ? value.slice(0, 12) + '…' : text(value);
  const run = () => state.snapshot?.run || null;
  const tasks = () => Array.isArray(state.snapshot?.tasks) ? state.snapshot.tasks : [];
  const base = () => '/api/engineering/runs/' + encodeURIComponent(state.runId);
  function node(tag, className, content) { const el = document.createElement(tag); if (className) el.className = className; if (content !== undefined) el.textContent = text(content); return el; }
  function replace(id, nodes) { $(id).replaceChildren(...nodes); }
  function badge(value) { const el = node('span', 'status-badge', label(value)); paintBadge(el, value); return el; }
  function paintBadge(el, value, custom) { const title = custom || label(value); if (el.textContent !== title) el.textContent = title; el.className = 'status-badge ' + shared.tone(value); }
  function notify(message, error = false) { $('notice').hidden = false; $('notice').className = 'notice' + (error ? ' error' : ''); $('notice-text').textContent = message; }
  function details(id, rows) { replace(id, rows.map(([key, value, mono]) => { const row = node('div'); row.append(node('dt', '', key)); const dd = node('dd'); dd.append(node(mono ? 'code' : 'span', '', value)); row.append(dd); return row; })); }
  function setView(view) { if (!TITLES[view]) view = 'overview'; const changedView = state.view !== view; state.view = view; for (const name of Object.keys(TITLES)) $('view-' + name).hidden = name !== view; document.querySelectorAll('.nav-item').forEach(el => { const active = el.dataset.view === view; el.classList.toggle('active', active); if (active) el.setAttribute('aria-current', 'page'); else el.removeAttribute('aria-current'); }); $('page-title').textContent = TITLES[view]; if (location.hash !== '#' + view) history.replaceState(null, '', location.pathname + location.search + '#' + view); if (view === 'audit') { $('history-disclosure').open = !state.runId; if (state.runId) loadEvents(); } const heading = $('view-' + view).querySelector('h1'); if (heading && changedView) { heading.tabIndex = -1; heading.focus({preventScroll:true}); } if (changedView) window.scrollTo({top:0,behavior:'instant'}); }
  class ApiError extends Error { constructor(message, status, uncertain) { super(message); this.status = status; this.uncertain = uncertain; } }
  async function api(path, options = {}) {
    const controller = new AbortController(); const timeout = setTimeout(() => controller.abort(), options.body ? 65000 : 25000);
    try {
      const response = await fetch(path, {credentials: 'same-origin', cache: 'no-store', signal: controller.signal, ...options, headers: {'Accept': 'application/json', ...(options.body ? {'Content-Type': 'application/json'} : {}), ...options.headers}});
      const raw = await response.text(); let data;
      try { data = raw ? JSON.parse(raw) : {}; } catch (_) { throw new ApiError('服务返回了无法读取的响应，请刷新核实实际状态。', response.status, Boolean(options.body)); }
      if (!response.ok) { const rawReason = data.error || data.detail || '请求失败'; const reason = ({repair_attempts_exhausted:'修改次数已用完，原候选未改变。',repair_budget_exhausted:'修改预算不足，原候选未改变。',budget_or_policy_denied:'预算或策略检查未通过，请查看任务记录。',expired:'批准有效期已结束。',version_conflict:'版本已变化，请刷新后重新审阅。'})[rawReason] || rawReason; throw new ApiError('HTTP ' + response.status + ' · ' + text(reason), response.status, Boolean(options.body) && response.status >= 500); }
      return data;
    } catch (error) {
      if (error instanceof ApiError) throw error;
      throw new ApiError(error.name === 'AbortError' ? '请求超时，请刷新核实服务与任务状态。' : '连接中断：' + error.message, 0, Boolean(options.body));
    } finally { clearTimeout(timeout); }
  }
  function keepPending(value) { state.uncertain = value; state.pendingCheck = null; try { if (value) localStorage.setItem('spark-pending-action', JSON.stringify(value)); else { localStorage.removeItem('spark-pending-action'); sessionStorage.removeItem('spark-pending-action'); } } catch (_) { /* Current-page protection still applies. */ } renderPending(); renderGates(); }
  function renderPending() {
    let banner = $('pending-banner'); if (!banner) { banner = node('div','notice'); banner.id = 'pending-banner'; banner.setAttribute('role','status'); $('notice').after(banner); }
    banner.hidden = !state.uncertain; if (!state.uncertain) { banner.dataset.renderKey = ''; return; }
    const renderKey = JSON.stringify([state.uncertain.request_id,state.uncertain.action,recoveryEligible()]);
    if (banner.dataset.renderKey === renderKey) return;
    banner.dataset.renderKey = renderKey;
    const message = node('span','','正在核对上一笔操作：' + state.uncertain.action + '。不会自动重发。若未找到记录，请查看任务列表和经过；请求可能仍在处理中。');
    const button = node('button','button secondary','只读核对'); button.addEventListener('click', async () => { await refreshRuns(); await reconcilePending(); });
    banner.replaceChildren(message,button);
    if (recoveryEligible()) {
      const recover = node('button','button secondary','人工确认后恢复操作');
      recover.addEventListener('click', recoverPending); banner.append(recover);
    }
  }
  function recoveryEligible(now = Date.now()) {
    const pending = state.uncertain, check = state.pendingCheck;
    return Boolean(pending && !state.busy && !state.reconciling && state.connected && !state.readFailed &&
      Number.isFinite(Date.parse(pending.created_at)) && now - Date.parse(pending.created_at) >= 180000 &&
      check?.request_id === pending.request_id && check.safe && now - check.checked_at < 30000);
  }
  async function recoverPending() {
    const pending = state.uncertain;
    if (!pending) return;
    await reconcilePending();
    if (!recoveryEligible() || state.uncertain?.request_id !== pending.request_id) return;
    if (!window.confirm('最新核对未找到这笔操作的记录，但缺少记录不代表一定未生效。请确认你已检查任务与经过。\n恢复只解除本地锁，不会重发旧请求。如需继续，请重新操作。')) return;
    let stored;
    try { stored = JSON.parse(localStorage.getItem('spark-pending-action') || 'null'); } catch (_) { return; }
    if (!stored || stored.request_id !== pending.request_id) return;
    if (!recoveryEligible()) return;
    keepPending(null); notify('已解除本地操作锁，未重发旧请求。原请求编号：' + pending.request_id);

  }
  async function reconcilePending() {
    const pending = state.uncertain; if (!pending || state.busy || state.reconciling) return;
    state.reconciling = true; state.pendingCheck = null;
    try {
      const inventory = await api('/local/runs');
      if (!Array.isArray(inventory.runs)) return;
      const created = inventory.runs.find(item => (item.request_id || item.engineering_request_id || item.metadata?.engineering_request_id) === pending.request_id);
      if (state.uncertain?.request_id !== pending.request_id) return;
      if (!pending.run_id && created) { keepPending(null); notify('已找到创建记录，可在任务列表继续查看。'); return; }
      let safe = inventory.complete === true && !inventory.runs.some(isActive);
      if (pending.run_id) {
        const data = await api('/api/engineering/runs/' + encodeURIComponent(pending.run_id));
        const current = data.snapshot?.run;
        if (!current || current.id !== pending.run_id) return;
        const metadata = current.metadata || {};
        const receipt = metadata.delivery?.receipts?.[pending.request_id] || metadata.cancel_receipts?.[pending.request_id];
        if (state.uncertain?.request_id !== pending.request_id) return;
        if (receipt) { keepPending(null); notify('已找到上一笔操作的服务回执。请查看当前版本与记录。'); return; }
        safe = !isActive(current) && metadata.delivery?.state !== 'repair_queued';
      }
      state.pendingCheck = {request_id:pending.request_id,checked_at:Date.now(),safe};
    } catch (_) { state.pendingCheck = null; }
    finally { state.reconciling = false; renderPending(); }
  }

  async function refreshStatus() {
    try { const data = await api('/local/status'); state.service = data; state.connected = data.connected === true; paintBadge($('connection-badge'), state.connected ? 'connected' : 'failed', state.connected ? '● Spark 已连接' : '● Spark 未连接'); $('sidebar-status').textContent = state.connected ? 'DGX Spark 在线' : 'DGX Spark 离线'; $('sidebar-dot').className = 'dot ' + (state.connected ? 'good' : 'bad'); $('service-check').textContent = time(data.checked_at); details('service-details', [['服务状态', state.connected ? '在线' : '不可用'], ['当前模型', data.model?.name || data.model?.model || data.model], ['消息桥接', data.bridge?.status || data.bridge], ['当前身份', data.owner === 'founder' ? '创始人' : data.owner], ['生产版本', data.commit, true], ['健康信息', data.health?.status || data.health]]); if (!state.connected) notify('Spark 未连接。' + text(data.error || data.health?.error || '请检查本机连接与远端服务。'), true); }
    catch (error) { state.connected = false; paintBadge($('connection-badge'), 'failed', '● 连接失败'); $('sidebar-status').textContent = '连接失败'; $('sidebar-dot').className = 'dot bad'; details('service-details', [['服务状态', '不可用'], ['实际错误', error.message]]); notify(error.message, true); }
    renderGates();
  }
  function engineering(item) { return item.engineering === true || item.metadata?.engineering === true || Boolean(item.delivery_state || item.metadata?.delivery); }
  function isActive(item) { return ['running', 'ready', 'queued', 'created', 'active'].includes(item.status); }
  function displayStatus(item) { return shared.deriveDisplayState(item); }
  function isPendingApproval(item) { return engineering(item) && ['pending','waiting_approval'].includes(displayStatus(item)); }
  function runRow(item, compact, historyRow = false) {
    const button = node('button', 'recent-row' + (item.id === state.runId ? ' selected' : '')); button.type = 'button';
    if (compact) button.append(node('span', 'run-icon', '▤'));
    const body = node('span', 'run-text'); body.append(node('span', 'run-title', engineering(item) ? '材料完整性检查' : item.objective || '决策任务')); body.append(node('span', 'run-sub', time(item.created_at) + ' · ' + String(item.id || '').slice(0,8) + (item.revision ? ' · v' + item.revision : '') + (!engineering(item) ? ' · 决策任务' : '') + (shared.reason(item) ? ' · ' + shared.reason(item) : ''))); button.append(body, badge(displayStatus(item)));
    button.addEventListener('click', () => { if (!engineering(item)) { location.assign('/ui?run=' + encodeURIComponent(item.id)); return; } selectRun(item.id).then(() => { if (historyRow) { $('history-disclosure').open = false; setView('audit'); } }); }); return button;
  }
  function renderRuns() {
    $('stat-total').textContent = String(state.runs.length); $('stat-active').textContent = String(state.runs.filter(isActive).length); $('stat-pending').textContent = String(state.runs.filter(isPendingApproval).length); $('stat-approved').textContent = String(state.runs.filter(item => displayStatus(item) === 'approved').length);
    $('today-heading').textContent = state.runs.filter(isPendingApproval).length ? '有 ' + state.runs.filter(isPendingApproval).length + ' 件工程任务，待你决定。' : '今天，专注下一件事。';
    $('today-date').textContent = new Date().toLocaleDateString('zh-CN', {month:'long',day:'numeric',weekday:'long'});
    replace('attention-runs', state.runs.filter(isPendingApproval).length ? state.runs.filter(isPendingApproval).map(item => runRow(item, true)) : [node('p','muted','暂无待你批准的工程版本。决策任务的放行请进入决策工作区查看。')]);
    replace('active-runs', state.runs.filter(isActive).length ? state.runs.filter(isActive).map(item => runRow(item, true)) : [node('p','muted','暂无进行中的任务。')]);
    state.renderedRunsKey = state.runs.map(displayStatus).join('|');
    replace('recent-runs', state.runs.length ? state.runs.filter(item => !isPendingApproval(item) && !isActive(item)).slice(0, 3).map(item => runRow(item, true)) : [node('div', 'empty small', '还没有任务。')]);
    replace('history-runs', state.runs.length ? state.runs.map(item => runRow(item,false,true)) : [node('p','muted','还没有任务记录')]);
    replace('run-list', state.runs.filter(engineering).length ? state.runs.filter(engineering).map(item => runRow(item, false)) : [node('div', 'empty small', '还没有可见任务')]);
  }
  async function refreshRuns() { try { const data = await api('/local/runs'); if (!Array.isArray(data.runs)) throw new Error('任务列表响应格式不正确'); state.runs = data.runs; renderRuns(); await reconcilePending(); } catch (error) { replace('run-list', [node('div', 'empty small', '列表读取失败：' + error.message)]); replace('recent-runs', [node('div', 'empty small', '列表读取失败：' + error.message)]); for (const id of ['stat-total', 'stat-active', 'stat-pending', 'stat-approved']) $(id).textContent = '—'; notify(error.message, true); } }
  async function refreshAll() { $('refresh-all').disabled = true; try { await Promise.allSettled([refreshStatus(), refreshRuns()]); if (state.runId) await loadSelected(true); } finally { $('refresh-all').disabled = false; } }
  function clearSelection() { state.snapshot = null; state.delivery = null; state.candidate = null; state.candidateError = ''; state.events = []; state.detailKey = null; state.currentFile = null; replace('events-list', [node('div', 'empty small', '正在读取所选任务的审计记录…')]); renderSelected(); }
  async function selectRun(id) {
    if (state.busy) { notify('请等待当前操作完成后再切换任务。', true); return; }
    if (!/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i.test(id || '')) { notify('请输入有效的 UUID 格式任务 ID。', true); return; }
    clearTimeout(state.timer); state.generation += 1; state.loading = false; state.runId = id; $('run-id').value = id;
    const selectedUrl = new URL(location.href); selectedUrl.searchParams.set('run', id); history.replaceState(null, '', selectedUrl.pathname + selectedUrl.search + selectedUrl.hash);
    clearSelection(); setView('tasks'); renderRuns(); await loadSelected(true); if (state.candidate && state.delivery) setView('review');
  }
  function schedulePoll() { clearTimeout(state.timer); const current = run(); const status = current && shared.deriveDisplayState(current,state.delivery); if (current && !TERMINAL.has(status) && status !== 'approved' && !state.busy && !state.readFailed) state.timer = setTimeout(() => loadSelected(false), ['pending','waiting_approval'].includes(status) ? 30000 : 4000); }
  async function loadSelected(force) {
    if (!state.runId || state.loading || state.busy) return; const generation = state.generation; const id = state.runId; state.loading = true; $('poll-status').textContent = '正在读取…'; renderGates();
    try {
      const data = await api('/api/engineering/runs/' + encodeURIComponent(id)); if (generation !== state.generation) return;
      if (!data.snapshot?.run) throw new Error('任务快照缺少 run 字段'); state.snapshot = data.snapshot; state.delivery = data.snapshot.run.metadata?.delivery || data.delivery || null;
      state.readFailed = false; state.lastRead = new Date().toISOString();
      const key = JSON.stringify([id, state.delivery, data.snapshot.run.status, data.snapshot.tasks]);
      const changed = key !== state.detailKey || !state.candidate;
      if (changed) {
        state.detailKey = key; state.candidate = null; state.candidateError = '';
        if (state.delivery) { try { const candidate = await api('/api/engineering/runs/' + encodeURIComponent(id) + '/candidate'); if (generation !== state.generation) return; state.candidate = candidate; } catch (error) { if (generation !== state.generation) return; state.candidateError = error.message; } }
        if (state.view === 'audit' || force) await loadEvents(generation);
      }
      if (generation !== state.generation) return; if (changed) renderSelected(); else renderGates(); await reconcilePending(); const existing = state.runs.find(item => item.id === id); if (existing) { const before = JSON.stringify(existing); Object.assign(existing, {status: run().status, delivery_state: state.delivery?.state, revision: state.delivery?.revision, expires_at: state.delivery?.expires_at}); if (before !== JSON.stringify(existing)) renderRuns(); }
      const status = shared.deriveDisplayState(run(),state.delivery); $('poll-status').textContent = TERMINAL.has(status) || status === 'approved' ? '已停止自动刷新' : ['pending','waiting_approval'].includes(status) ? '每 30 秒核对版本' : '每 4 秒刷新进度';
    } catch (error) { if (generation !== state.generation) return; state.readFailed = true; notify('任务读取失败，操作已暂停。最后成功读取：' + time(state.lastRead) + '。' + error.message, true); $('poll-status').textContent = '读取失败 · 可手动刷新'; renderGates(); }
    finally { if (generation === state.generation) { state.loading = false; renderGates(); schedulePoll(); } }
  }
  function engineeringTask() { return tasks().find(item => item.metadata?.task_type === 'engineering.materials') || tasks().find(item => Number.isInteger(item.attempt_count)) || null; }
  function attemptsInfo() { const task = engineeringTask(); return {used: task?.attempt_count, max: Number.isInteger(task?.max_attempts) ? task.max_attempts : null}; }
  function renderSelected() {
    const current = run(); $('task-empty').hidden = Boolean(current); $('task-content').hidden = !current; $('review-empty').hidden = Boolean(current); $('review-content').hidden = !current; $('audit-run-id').textContent = state.runId || '尚未选择任务'; $('raw-snapshot').textContent = state.snapshot ? pretty(state.snapshot) : '尚未加载';
    if (!current) { renderGates(); return; }
    const metadata = current.metadata || {}; const delivery = state.delivery; const attempts = attemptsInfo(); $('task-objective').textContent = '材料完整性检查'; $('task-id').textContent = current.id || state.runId; paintBadge($('task-status'), shared.deriveDisplayState(current,state.delivery));
    const result = state.candidate?.result; const status = shared.deriveDisplayState(current,delivery);
    const fact = delivery?.state === 'approved' ? '本人已批准此版本' : status === 'expired' ? '候选有效期已结束' : delivery?.state === 'pending' ? '已有待决定候选版本' : result?.review ? '已有模型复核记录' : result?.tests ? '已有检查记录' : '当前阶段尚不可确认';
    replace('progress', [node('p','phase-line', (TERMINAL.has(status) ? '任务已结束 · ' : '') + fact)]);
    const explanation = shared.reason(current,delivery); $('task-alert').hidden = !explanation; $('task-alert').textContent = explanation; $('task-alert').className = 'info-note';
    details('task-details', [['创建时间', time(current.created_at)], ['尝试', Number.isInteger(attempts.used) ? attempts.used + ' / ' + (attempts.max ?? '未知') : '未提供'], ['交付版本', delivery ? 'v' + delivery.revision : '尚未生成'], ['批准有效期', time(delivery?.expires_at)]]);
    replace('task-steps', tasks().length ? tasks().map(item => { const row = node('div', 'step-record'); const body = node('div'); body.append(node('strong', '', item.metadata?.task_type === 'engineering.materials' ? '代码实现与模型复核' : item.title || item.name || '步骤记录')); body.append(node('p', '', '次数 ' + text(item.attempt_count) + ' / ' + text(item.max_attempts) + (item.error || item.failure_reason || item.last_error ? ' · ' + text(item.error || item.failure_reason || item.last_error) : ''))); row.append(body, badge(item.status)); return row; }) : [node('p', 'muted', '暂未返回执行步骤')]); renderReview(); renderGates();
  }
  function renderFiles() {
    const files = state.candidate?.files || {}; const names = Object.keys(files); const current = names.includes(state.currentFile) ? state.currentFile : names[0]; state.currentFile = current || null;
    for (const id of ['file-select', 'feedback-path']) { const selected = id === 'feedback-path' ? $(id).value : current; replace(id, names.map(name => { const option = node('option', '', name); option.value = name; return option; })); if (names.includes(selected)) $(id).value = selected; }
    renderCode();
  }
  function renderCode() {
    const source = state.candidate?.files?.[state.currentFile]; if (typeof source !== 'string') { replace('code-lines', [node('span', 'line-text', '尚无可展示的候选文件')]); $('code-count').textContent = ''; return; }
    const lines = source.split('\n'); replace('code-lines', lines.map((line, i) => { const row = node('span', 'code-line'); row.append(node('span', 'line-number', i + 1), node('span', 'line-text', line || ' ')); return row; })); $('code-count').textContent = lines.length + ' 行'; $('file-select').value = state.currentFile;
  }
  function testSummary(value) {
    const original = text(value);
    if (!/[A-Za-z]/.test(original)) return original;
    const match = original.match(/^(\d+) passed(?:, (\d+) warnings?)? in ([\d.]+)s$/);
    return match ? '通过 ' + match[1] + ' 项' + (match[2] ? ' · 警告 ' + match[2] + ' 项' : '') + ' · 用时 ' + match[3] + ' 秒' : '详细结果见原始记录';
  }
  function checkRecord(title, value, body) { const row = node('div', 'check-row'); const heading = node('div', 'check-heading'); heading.append(node('strong', '', title), badge(value)); row.append(heading); if (body) row.append(node('p', '', body)); return row; }
  function renderReview() {
    if (!run()) return; const delivery = state.delivery; const result = state.candidate?.result;
    $('review-heading').textContent = delivery ? '第 ' + delivery.revision + ' 版 · ' + label(shared.deriveDisplayState(run(),delivery)) : '审阅'; $('review-version').textContent = delivery ? '第 ' + delivery.revision + ' 版' : '尚无交付版本'; $('review-run-id').textContent = state.runId; paintBadge($('delivery-state'), shared.deriveDisplayState(run(),delivery));
    $('candidate-notice').hidden = !state.candidateError && run().status !== 'failed'; $('candidate-notice').textContent = state.candidateError ? '候选产物暂不可读：' + state.candidateError : delivery ? (run().status === 'failed' ? '以下为保留的历史候选，检查结果不代表本次修改成功。' : '当前版本的候选代码与检查记录。') + '模型复核：同一模型 · 独立上下文。' : '执行、测试与模型复核通过后，服务才会生成待批准的候选版本。'; $('candidate-notice').className = 'info-note' + (state.candidateError ? ' error' : '');
    renderFiles(); const tests = Array.isArray(result?.tests) ? result.tests : result?.tests ? [result.tests] : [];
    replace('test-results', tests.length ? tests.map((item, index) => {
      const title = item.name || item.gate?.name || (item.harness === 'host-oracle-input-only-v1' ? '合同检查' : item.harness === 'pytest-summary-and-mutations-v2' ? '生成的测试' : item.harness === 'pytest-summary-v1' ? '回归测试' : '测试记录 ' + (index + 1));
      const summary = item.gate?.summary || item.summary || (Array.isArray(item.gate?.cases) ? item.gate.cases.filter(check => check.passed === true).length + ' / ' + item.gate.cases.length + ' 项通过' : '退出码：' + text(item.exit_code));
      const row = checkRecord(title, item.passed === true || item.gate?.passed === true ? 'passed' : item.passed === false || item.gate?.passed === false ? 'failed' : item.status || '未提供', testSummary(summary));
      if (Array.isArray(item.gate?.mutations) && item.gate.mutations.length) {
        const detected = item.gate.mutations.filter(probe => probe.detected === true).length;
        row.append(node('p','muted','能识别 ' + detected + ' / ' + item.gate.mutations.length + ' 个错误实现'));
      }
      const original = node('details'); original.append(node('summary','muted','原始输出'),node('pre','',pretty(item))); row.append(original); return row;
    }) : [node('p', 'muted', '尚无测试结果')]);
    const review = result?.review; const nodes = [];
    if (review) {
      nodes.push(checkRecord('模型复核', review.status || review.conclusion || (review.passed === true ? 'passed' : review.passed === false ? 'failed' : '未提供'), review.summary || review.reason));
      if (Array.isArray(review.findings)) for (const finding of review.findings) nodes.push(checkRecord(finding.title || finding.code || finding.severity || '审查意见', finding.blocking ? 'blocked' : '记录', finding.message || finding.description || text(finding)));
      const checks = review.evidence_checks || review.checks;
      if (checks) { const detail = node('details'); detail.append(node('summary', 'muted', '查看证据检查')); detail.append(node('pre', '', pretty(checks))); nodes.push(detail); }
      const identity = value => value && typeof value === 'object' ? [value.selected_model || value.selected_provider || '未提供模型', value.session_id ? '会话 ' + short(value.session_id) : '', value.usage?.total_tokens ? value.usage.total_tokens + ' 个词元' : ''].filter(Boolean).join(' · ') : text(value);
      const execution = node('details', 'execution-details'); execution.append(node('summary', 'muted', '模型执行明细'), node('p', 'muted', '代码实现：' + identity(result.executor)), node('p', 'muted', '代码复核：' + identity(result.reviewer))); nodes.push(execution);
    } else nodes.push(node('p', 'muted', '尚无模型复核结果'));
    replace('review-results', nodes); details('version-details', [['基础版本', delivery?.base_sha, true], ['补丁指纹', delivery?.patch_sha, true], ['批准记录', delivery?.approval_id, true], ['产物标识', delivery?.artifact_id, true]]); renderGates();
  }
  function gate() {
    const current = run(); const delivery = state.delivery; const attempts = attemptsInfo(); const expires = Date.parse(delivery?.expires_at || ''); const unexpired = Number.isFinite(expires) && expires > Date.now(); const versionComplete = delivery && ['revision', 'base_sha', 'patch_sha', 'approval_id', 'artifact_id'].every(key => delivery[key] !== undefined && delivery[key] !== null && delivery[key] !== '');
    const available = state.connected && !state.readFailed && !state.busy && !state.uncertain; const pending = Boolean(current?.status === 'waiting_approval' && delivery?.state === 'pending' && unexpired && versionComplete && state.candidate && !state.candidateError);
    const repairable = Number.isInteger(attempts.used) && Number.isInteger(attempts.max) && attempts.used < attempts.max && Number.isInteger(delivery?.repair_rounds) && delivery.repair_rounds < 2;
    let reason = !current ? '请先选择工程任务。' : !state.connected || state.readFailed ? '连接或读取中断，操作已暂停，不会自动重发。最后成功读取：' + time(state.lastRead) + '。' : state.uncertain ? '上一笔请求结果待核实，所有写入已暂停。' : state.busy ? '操作正在提交，请勿重复点击。' : delivery?.state === 'approved' ? '已由 ' + text(delivery.decided_by === 'founder' ? '创始人' : delivery.decided_by || '记录未提供批准人') + ' 于 ' + time(delivery.decided_at) + ' 批准，此版本只读保留，可导出交付记录。' : TERMINAL.has(current.status) ? shared.reason(current,delivery) || '任务已结束，现有产物和记录保留。' : !delivery ? '当前尚无可批准的交付版本。' : delivery.state !== 'pending' ? '当前交付状态为“' + label(delivery.state) + '”，请等待或查看任务记录。' : !unexpired ? '当前批准有效期已结束或缺少有效期信息，无法批准、驳回或继续反馈。' : !versionComplete || !state.candidate ? '版本信息或候选产物不完整，请刷新核实。' : '当前版本等待你的决定。请先阅读代码、测试与模型复核，再批准或提出反馈。';
    return {available, pending, repairable, reason, attempts, approve: available && !state.loading && pending, feedback: available && pending && repairable, cancel: available && !state.loading && current && !TERMINAL.has(shared.deriveDisplayState(current,state.delivery)) && state.delivery?.state !== 'approved' && Boolean(current.metadata?.base_sha || delivery?.base_sha), export: available && current?.status === 'completed' && delivery?.state === 'approved'};
  }
  function renderGates() {
    const value = gate(); document.querySelectorAll('.create-run').forEach(button => { button.disabled = !state.connected || state.readFailed || state.busy || Boolean(state.uncertain); button.textContent = state.busy && !state.runId ? '正在创建…' : '创建工程任务'; });
    const ended = run() && (TERMINAL.has(shared.deriveDisplayState(run(),state.delivery)) || shared.deriveDisplayState(run(),state.delivery) === 'approved'); $('approve-run').hidden = Boolean(ended); $('reject-run').hidden = Boolean(ended); $('decision-note').hidden = Boolean(ended); $('restart-run').hidden = !ended || shared.deriveDisplayState(run(),state.delivery) === 'approved';
    $('approve-run').disabled = !value.approve; $('reject-run').disabled = !value.approve; $('send-feedback').disabled = !value.feedback || state.loading; $('cancel-run').disabled = !value.cancel; $('export-run').disabled = !value.export; $('export-run').className = 'button ' + (value.export ? 'primary' : 'secondary'); $('refresh-selected').disabled = state.loading || state.busy; $('action-gate').textContent = value.reason; $('expiry-label').textContent = state.delivery ? (state.delivery.state === 'approved' ? '原批准期限 ' : '有效至 ') + time(state.delivery.expires_at) : '';
    $('feedback-disclosure').hidden = !value.feedback; $('feedback-form').hidden = !value.feedback; $('feedback-heading').hidden = !value.feedback; $('feedback-intro').hidden = !value.feedback;
    $('action-gate').hidden = value.approve; $('export-run').hidden = !value.export; $('review-heading').textContent = state.delivery ? '第 ' + state.delivery.revision + ' 版 · ' + label(shared.deriveDisplayState(run(),state.delivery)) : '审阅';
    $('approve-run').textContent = state.delivery ? '批准第 ' + state.delivery.revision + ' 版' : '批准当前版本';
    $('feedback-budget').hidden = state.delivery?.state === 'approved';
    $('feedback-budget').textContent = Number.isInteger(value.attempts.max) && Number.isInteger(value.attempts.used) ? '尝试 ' + value.attempts.used + ' / ' + value.attempts.max + ' · 还可尝试修改 ' + Math.max(0,value.attempts.max-value.attempts.used) + ' 次，时限与用量由服务核验' : '修改额度未知，由服务核验';

    for (const id of ['feedback-path', 'feedback-line', 'feedback-comment']) $(id).disabled = !value.feedback;
  }
  async function createRun() {
    if (!state.connected || state.readFailed || state.busy || state.uncertain) return; state.busy = true; const requestId = crypto.randomUUID(); const pending = {action: '创建材料完整性任务', request_id: requestId, created_at: new Date().toISOString()}; keepPending(pending);
    try { const data = await api('/api/engineering/runs', {method: 'POST', body: JSON.stringify({request_id: requestId, task: 'materials_completeness'})}); const id = data.run_id || data.run?.id || data.snapshot?.run?.id; if (!id) throw new ApiError('创建响应缺少任务 ID，请刷新任务列表核实。', 200, true); keepPending(null); state.busy = false; notify('工程任务已创建，正在读取执行进度。'); await refreshRuns(); await selectRun(id); }
    catch (error) { if (!error.uncertain) keepPending(null); notify((error.uncertain ? '创建结果尚未确认，未自动重试。' : '创建未成功：') + error.message + (error.uncertain ? ' 请求 ID：' + requestId : ''), true); }
    finally { state.busy = false; renderGates(); if (state.uncertain) await refreshRuns(); }
  }
  async function perform(action, extra = {}) {
    const valid = gate(); if (action === 'feedback' ? !valid.feedback || state.loading : action === 'interrupt' ? !valid.cancel : !valid.approve) { notify(valid.reason, true); return; }
    const delivery = state.delivery; const id = state.runId; const generation = state.generation; const requestId = crypto.randomUUID();
    if (action === 'approve' && !window.confirm('批准第 ' + delivery.revision + ' 版？\n有效至：' + time(delivery.expires_at) + '\n请确认已阅读检查与模型复核。批准仅对当前版本生效，修改后需重新审阅。这将记录你的批准，并允许导出此版本。')) return;
    if (action === 'reject' && !window.confirm('确认驳回当前版本 ' + delivery.revision + '？任务将以驳回状态结束。')) return;
    if (action === 'interrupt' && !window.confirm('确认取消此任务？已消耗的预算与尝试次数不会重置。')) return;
    const payload = action === 'interrupt' ? {request_id: requestId, base_sha: run().metadata?.base_sha || delivery.base_sha, revision: delivery?.revision || 0} : {request_id: requestId, revision: delivery.revision, base_sha: delivery.base_sha, patch_sha: delivery.patch_sha, approval_id: delivery.approval_id, ...extra};
    state.busy = true; clearTimeout(state.timer); keepPending({action: action, run_id: id, request_id: requestId, revision: delivery?.revision || 0, created_at: new Date().toISOString()});
    try { await api('/api/engineering/runs/' + encodeURIComponent(id) + '/' + action, {method: 'POST', body: JSON.stringify(payload)}); keepPending(null); notify({approve: '当前版本已批准，可导出真实交付记录。', reject: '当前版本已驳回。', feedback: '反馈已提交，正在读取修改进度。', interrupt: '取消请求已记录。'}[action]); if (action === 'feedback') $('feedback-comment').value = ''; }
    catch (error) { if (!error.uncertain) keepPending(null); notify((error.uncertain ? '操作结果尚未确认，未自动重发。请刷新核实。' : '操作未成功：') + error.message, true); }
    finally { state.busy = false; renderGates(); if (generation === state.generation) await loadSelected(true); await refreshRuns(); }
  }
  async function submitFeedback(event) {
    event.preventDefault(); const path = $('feedback-path').value; const line = Number($('feedback-line').value); const source = state.candidate?.files?.[path]; const comment = $('feedback-comment').value.trim();
    if (typeof source !== 'string' || !Number.isInteger(line) || line < 1 || line > source.split('\n').length || !comment || comment.length > 2000) { notify('请选择当前候选文件，填写有效行号和修改意见。', true); return; }
    await perform('feedback', {artifact_id: state.delivery.artifact_id, path, line, comment});
  }
  async function exportRun() {
    if (!gate().export) return; $('export-run').disabled = true;
    try { const data = await api(base() + '/export'); const blob = new Blob([pretty(data)], {type: 'application/json;charset=utf-8'}); const url = URL.createObjectURL(blob); const link = node('a'); link.href = url; link.download = 'spark-approved-' + state.runId + '-v' + state.delivery.revision + '.json'; document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000); notify('已导出服务返回的获批交付记录。'); } catch (error) { notify('导出失败：' + error.message, true); } finally { renderGates(); }
  }
  async function loadEvents(expectedGeneration = state.generation) {
    if (!state.runId) return; const id = state.runId; const ticket = ++state.eventsGeneration;
    try { const data = await api('/api/runs/' + encodeURIComponent(id) + '/events?limit=200'); if (expectedGeneration !== state.generation || ticket !== state.eventsGeneration) return; const events = Array.isArray(data.events) ? data.events : Array.isArray(data) ? data : []; state.events = events; const eventRows = events.map(item => { const row = node('article', 'event-row'); row.append(node('time', '', time(item.created_at || item.timestamp || item.occurred_at))); const kind = item.event_type || item.type || item.kind || item.name; row.append(node('h3', '', shared.eventLabel(item))); const detail = node('details'); detail.append(node('summary', '', '查看完整记录'), node('pre', '', pretty(item))); row.append(detail); return row; });
      const visible = [], technical = node('details', 'event-technical');
      technical.append(node('summary', '', '原始记录（' + events.length + ' 条事件与快照）'), node('pre','',pretty(state.snapshot)));
      eventRows.forEach(row => technical.append(row));
      events.forEach((item, index) => { const kind = item.event_type || item.type || item.kind || item.name; if (shared.importantEvents.has(kind)) { const row = eventRows[index].cloneNode(true); row.querySelector('details')?.remove(); visible.push(row); } });
      replace('events-list', events.length ? [...visible, technical] : [node('div', 'empty small', '服务尚未返回此任务的事件')]); }
    catch (error) { if (expectedGeneration === state.generation && ticket === state.eventsGeneration) replace('events-list', [node('div', 'empty small', '审计记录读取失败：' + error.message)]); }
  }
  document.querySelectorAll('[data-view]').forEach(button => button.addEventListener('click', () => setView(button.dataset.view)));
  document.querySelectorAll('.create-run').forEach(button => button.addEventListener('click', createRun));
  $('load-form').addEventListener('submit', event => { event.preventDefault(); if (state.busy) { notify('请等待当前操作完成。', true); return; } selectRun($('run-id').value.trim()); });
  $('notice-close').addEventListener('click', () => { $('notice').hidden = true; }); $('refresh-all').addEventListener('click', refreshAll); $('refresh-runs').addEventListener('click', refreshRuns); $('refresh-selected').addEventListener('click', () => loadSelected(true)); $('refresh-events').addEventListener('click', () => loadEvents());
  $('file-select').addEventListener('change', event => { state.currentFile = event.target.value; renderCode(); $('feedback-path').value = state.currentFile; }); $('feedback-path').addEventListener('change', event => { state.currentFile = event.target.value; renderCode(); $('feedback-line').value = '1'; });
  $('approve-run').addEventListener('click', () => perform('approve')); $('reject-run').addEventListener('click', () => perform('reject')); $('cancel-run').addEventListener('click', () => perform('interrupt')); $('feedback-form').addEventListener('submit', submitFeedback); $('export-run').addEventListener('click', exportRun);
  window.addEventListener('storage', event => { if (event.key === 'spark-pending-action') { try { state.uncertain = JSON.parse(event.newValue || 'null'); } catch (_) {} renderPending(); renderGates(); } });
  window.addEventListener('hashchange', () => { if (location.hash !== '#main-content') setView(location.hash.slice(1)); }); window.addEventListener('beforeunload', () => clearTimeout(state.timer));
  setInterval(() => { renderGates(); if (state.uncertain) renderPending(); if (state.runs.length && state.renderedRunsKey !== state.runs.map(displayStatus).join('|')) renderRuns(); if (run()) { paintBadge($('task-status'), shared.deriveDisplayState(run(),state.delivery)); paintBadge($('delivery-state'), shared.deriveDisplayState(run(),state.delivery)); if (TERMINAL.has(shared.deriveDisplayState(run(),state.delivery))) { clearTimeout(state.timer); $('poll-status').textContent = '已停止自动刷新'; } } }, 1000); renderPending(); renderGates(); setView(location.hash.slice(1) || 'overview');
  (async () => { const wantedView = initialView; await refreshAll(); const id = new URLSearchParams(location.search).get('run'); if (id) { await selectRun(id); if (TITLES[wantedView]) setView(wantedView); } })();
})();
