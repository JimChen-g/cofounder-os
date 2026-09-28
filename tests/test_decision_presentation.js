'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync('app/ui/static/app.js','utf8');
const get=(a,b)=>source.slice(source.indexOf('function '+a+'('),source.indexOf('function '+b+'('));
const ctx=vm.createContext({});vm.runInContext(get('executionSource','renderAgents'),ctx);
assert.equal(ctx.executionSource({},null),'来源未提供');
assert.equal(ctx.executionSource({},{execution_status:'decision_only',metadata:{execution_backend:'gateway_llm_agent'}}),'计划路由');
assert.equal(ctx.executionSource({},{execution_status:'executed',metadata:{execution_backend:'gateway_llm_agent'}}),'实时模型');
assert.equal(ctx.executionSource({},{execution_status:'executed',metadata:{execution_backend:'local_rule'}}),'规则控制');
assert.equal(ctx.executionSource({},{execution_status:'executed',fallback_used:true,metadata:{execution_backend:'local_rule'}}),'本地回退');
let messages=0;ctx.showAlert=()=>messages++;ctx.Date=Date;ctx.Error=Error;ctx.Number=Number;
vm.runInContext(source.slice(source.indexOf('async function resolveApproval('),source.indexOf('\nfunction renderAll(')),ctx);
(async()=>{
 for(const approval of [undefined,{id:'a',status:'approved',expires_at:'2099-01-01'}, {id:'a',status:'pending',expires_at:'2000-01-01'}, {id:'a',status:'pending'}]) {
  ctx.state={snapshot:{approvals:approval?[approval]:[]}};
  await ctx.resolveApproval('a','approved',{querySelector:()=>{throw Error('must block before reading form or writing')}});
 }
 assert.equal(messages,4);console.log('9 decision provenance and expiry checks passed; zero writes');
})().catch(e=>{console.error(e);process.exitCode=1;});
