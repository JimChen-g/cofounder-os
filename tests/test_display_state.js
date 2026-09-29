'use strict';
const assert = require('node:assert/strict');
const {deriveDisplayState: state, label, tone, reason, eventLabel} = require('../app/ui/static/display-state.js');
const now = Date.parse('2026-09-28T14:00:00Z');
const old = '2026-09-28T13:00:00Z';
const future = '2026-09-28T15:00:00Z';
const cases = [
 ['expired pending snapshot', {status:'waiting_approval',metadata:{delivery:{state:'pending',expires_at:old}}}, undefined, 'expired'],
 ['expired pending list', {status:'waiting_approval',delivery_state:'pending',expires_at:old}, undefined, 'expired'],
 ['pending future', {status:'waiting_approval'}, {state:'pending',expires_at:future}, 'pending'],
 ['expiry exact boundary', {status:'waiting_approval'}, {state:'pending',expires_at:new Date(now).toISOString()}, 'expired'],
 ['missing expiry not fabricated', {status:'waiting_approval'}, {state:'pending'}, 'pending'],
 ['invalid expiry not fabricated', {status:'waiting_approval'}, {state:'pending',expires_at:'unknown'}, 'pending'],
 ['approved stays approved after deadline', {status:'completed'}, {state:'approved',expires_at:old}, 'approved'],
 ['approved stored separately', {status:'waiting_approval'}, {state:'approved',expires_at:old}, 'approved'],
 ['failure outranks historical pending', {status:'failed'}, {state:'pending',expires_at:old}, 'failed'],
 ['failure outranks historical approval', {status:'failed'}, {state:'approved',expires_at:old}, 'failed'],
 ['cancel outranks historical pending', {status:'cancelled'}, {state:'pending',expires_at:future}, 'cancelled'],
 ['rejected preserved', {status:'rejected'}, {state:'pending',expires_at:old}, 'rejected'],
 ['completed decision stays complete', {status:'completed'}, undefined, 'completed'],
 ['running without delivery', {status:'running'}, undefined, 'running'],
 ['repair stage from stored fact', {status:'running'}, {state:'repair_queued',expires_at:old}, 'repair_queued'],
 ['unknown remains unknown', {}, undefined, 'unknown'],
];
for (const [name, run, delivery, expected] of cases) assert.equal(state(run,delivery,now),expected,name);
assert.equal(label('expired'),'已过期');
assert.equal(tone('approved'),'seal');
assert.notEqual(tone('completed'),'seal');
assert.match(reason({status:'failed',metadata:{termination_reason:'budget_or_policy_denied'}}),/预算或策略/);
assert.doesNotMatch(reason({status:'failed',metadata:{termination_reason:'budget_or_policy_denied'}}),/时间用尽|候选保留/);
assert.match(reason({status:'failed',metadata:{termination_reason:'unrecognized_failure'}}),/unrecognized_failure/);

assert.equal(eventLabel({event_type:"engineering.approve",details:{revision:2}}),"本人批准第 2 版");
assert.match(eventLabel({event_type:"engineering.reject",details:{revision:1}}),/第 1 版/);
assert.match(reason({status:'failed',metadata:{termination_reason:'feedback_no_source_change'}}),/模型未产生实际修改，原候选保留，本次修订未通过/);
console.log(JSON.stringify({passed:cases.length+9,case_names:cases.map(c=>c[0]),source:'app/ui/static/display-state.js'}));
