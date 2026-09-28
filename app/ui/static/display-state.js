/* Shared presentation only. Never changes persisted workflow state or action authority. */
(function (root) {
  'use strict';
  const terminal = new Set(['completed', 'failed', 'cancelled', 'canceled', 'rejected', 'expired']);
  const labels = {pending:'待你决定',waiting_approval:'待你决定',approved:'已批准',completed:'已完成',failed:'未通过',expired:'已过期',cancelled:'已取消',canceled:'已取消',rejected:'已驳回',repair_queued:'修改排队中',running:'进行中',active:'进行中',ready:'待执行',queued:'排队中',created:'已创建'};
  function deriveDisplayState(run = {}, delivery, now = Date.now()) {
    const d = delivery || run.metadata?.delivery || {state:run.delivery_state, expires_at:run.expires_at};
    if (terminal.has(run.status)) return run.status === 'completed' && d.state === 'approved' ? 'approved' : run.status;
    if (d.state === 'approved') return 'approved';
    const expiry = Date.parse(d.expires_at || '');
    if ((d.state === 'pending' || run.status === 'waiting_approval') && Number.isFinite(expiry) && expiry <= now) return 'expired';
    return d.state || run.status || 'unknown';
  }
  function tone(status) {
    return status === 'approved' ? 'seal' : ['failed','rejected','timeout','manual_required','pending','waiting_approval','blocked'].includes(status) ? 'warn' : ['running','active','queued','ready','repair_queued'].includes(status) ? 'live' : ['completed','passed','pass','success','connected'].includes(status) ? 'good' : 'neutral';
  }
  const reasons = {review_evidence_not_in_patch:'模型复核引用未能定位到补丁原文，任务未通过。',budget_or_policy_denied:'预算或策略检查未通过。具体原因以本次记录为准。',repair_budget_exhausted:'当前修改预算不足，修改请求未受理。',repair_attempts_exhausted:'修改尝试次数已用完，修改请求未受理。',no_progress:'修改未产生可交付的新进展。',cancelled:'任务已取消。',rejected:'此版本已被驳回。',expired:'未在有效期内作出决定。',tests_failed:'检查未通过。',review_failed:'模型复核未通过。',execution_failed:'执行未完成，请查看经过中的详细记录。'};

  Object.assign(reasons, {test_gate_blocked:'检查未通过，未生成可批准版本。',independent_review_gate_blocked:'模型复核未通过，未生成可批准版本。',review_location_not_in_patch:'模型复核引用的位置不在本次补丁中。',invalid_model_output_or_execution_failed:'模型输出无效或执行未完成。',checks_failed:'检查未通过。',timeout:'执行超时，任务已停止。',repair_interrupted_restart:'修改因服务重启中断，已有候选保留。',engineering_recovery_not_supported:'此工程任务不支持自动恢复。',review_evidence_not_substantive:'模型复核引用缺少实质性依据。',review_evidence_missing_file:'模型复核缺少文件依据。'});
  const importantEvents = new Set(['run.created','task.completed','task.failed','engineering.delivery_pending','engineering.approve','engineering.reject','engineering.feedback','engineering.cancel','engineering.cancelled','engineering.no_progress']);
  function eventLabel(event = {}) {
    const kind = event.event_type || event.type || event.kind || event.name;
    const revision = event.details?.revision;
    const version = Number.isInteger(revision) && revision > 0 ? '第 ' + revision + ' 版' : '当前版本';
    const names = {'run.created':'任务已创建','task.created':'执行步骤已创建','run.status_changed':'任务状态更新','task.status_changed':'步骤状态更新','policy.allowed':'策略检查允许执行','task.claimed':'开始执行步骤','artifact.registered':'保存产物证据','task.completed':'步骤已完成','task.failed':'步骤未通过','run.metadata_updated':'任务记录更新','engineering.delivery_pending':version + '待你决定','engineering.approve':'本人批准' + version,'engineering.reject':'已驳回' + version,'engineering.feedback':'已提交批注修改','engineering.cancel':'已请求取消','engineering.cancelled':'任务已取消','engineering.no_progress':'修改未产生新进展'};
    return names[kind] || '事件记录（' + (kind || '未知') + '）';
  }
  function reason(run = {}, delivery) {
    const status = deriveDisplayState(run, delivery);
    if (status === 'expired') return reasons.expired;
    const code = run.metadata?.termination_reason || run.termination_reason;
    return reasons[code] || (code ? '未识别的结束原因（代码 ' + code + '）' : status === 'failed' ? '任务未通过，详细原因请查看记录。' : status === 'approved' ? '此版本已经本人批准，可导出交付记录。' : '');
  }
  root.CofounderState = {deriveDisplayState, label: status => labels[status] || status, tone, reason, terminal, eventLabel, importantEvents};
  if (typeof module !== 'undefined') module.exports = root.CofounderState;
})(typeof window === 'undefined' ? globalThis : window);
