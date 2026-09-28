'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const shared = require('../app/ui/static/display-state.js');
const source = fs.readFileSync(path.join(__dirname, '../tools/local_portal/static/portal.js'), 'utf8');
// Evaluate the production functions verbatim; the harness supplies only dependencies.
function extract(name, next) {
  const start = source.indexOf('  ' + (name === 'createRun' ? 'async ' : '') + 'function ' + name + '(');
  const end = source.indexOf('\n  ' + (next === 'perform' ? 'async ' : '') + 'function ' + next + '(', start);
  assert.ok(start >= 0 && end > start, 'production function boundaries: ' + name);
  return source.slice(start, end);
}
let apiCalls = 0;
const context = vm.createContext({
  state: {}, shared, TERMINAL: shared.terminal, Date,
  run: () => context.state.snapshot?.run,
  attemptsInfo: () => ({used:1, max:2}),
  text: String, time: String, label: shared.label,
  api: async () => { apiCalls += 1; throw Error('unexpected write'); },
});
vm.runInContext(extract('gate', 'renderGates') + '\n' + extract('createRun', 'perform'), context);
const pending = () => ({
  connected:true, readFailed:false, busy:false, uncertain:null, loading:false,
  snapshot:{run:{status:'waiting_approval',metadata:{base_sha:'base'}}},
  delivery:{state:'pending',revision:1,base_sha:'base',patch_sha:'patch',approval_id:'approval',artifact_id:'artifact',repair_rounds:0,expires_at:new Date(Date.now()+600000).toISOString()},
  candidate:{files:{}}, candidateError:'',
});
let assertions = 0;
function check(name, mutate, expected = false) {
  context.state = pending(); mutate(context.state);
  assert.equal(Boolean(context.gate().approve), expected, name); assertions += 1;
}
check('fresh complete pending may be approved', () => {}, true);
check('expired', s => s.delivery.expires_at = '2000-01-01T00:00:00Z');
check('unknown expiry', s => delete s.delivery.expires_at);
check('failed with historical pending', s => s.snapshot.run.status = 'failed');
for (const field of ['revision','base_sha','patch_sha','approval_id','artifact_id']) check('missing version field: '+field, s => delete s.delivery[field]);
check('offline', s => s.connected = false);
check('uncertain receipt', s => s.uncertain = {request_id:'unknown'});
check('read failure with stale connected=true', s => s.readFailed = true);
check('active version read', s => s.loading = true);
check('candidate unavailable', s => s.candidate = null);
check('candidate read failed', s => s.candidateError = 'unavailable');
check('write in progress', s => s.busy = true);
(async () => {
  for (const [field, value] of [['readFailed',true],['connected',false],['busy',true],['uncertain',{request_id:'unknown'}]]) {
    context.state = pending(); context.state[field] = value;
    await context.createRun(); assert.equal(apiCalls, 0, field + ' must block creation before any API call'); assertions += 1;
  }
  const storageInit = source.split('\n').find(line => line.includes('state.uncertain = JSON.parse') && line.includes('sessionStorage.getItem'));
  assert.ok(storageInit, 'legacy pending action must be restored');
  for (const local of [null, JSON.stringify({request_id:'new'})]) {
    const legacy = JSON.stringify({request_id:'old'});
    const restored = {state:{},localStorage:{getItem:()=>local},sessionStorage:{getItem:()=>legacy}};
    vm.runInNewContext(storageInit, restored);
    assert.equal(restored.state.uncertain.request_id, local ? 'new' : 'old'); assertions += 1;
  }
  console.log(JSON.stringify({passed:assertions,source:'tools/local_portal/static/portal.js',writes:apiCalls}));
})().catch(error => { console.error(error); process.exitCode = 1; });
