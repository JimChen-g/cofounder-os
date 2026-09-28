'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('tools/local_portal/static/portal.js','utf8');
const code = source.slice(source.indexOf('  function recoveryEligible('),source.indexOf('\n  async function refreshStatus('));
let calls=[], stored, confirmed=true, inventory, detail, fail=false;
const ctx=vm.createContext({Date,Number,Boolean,JSON,encodeURIComponent,
 state:{},window:{confirm:()=>confirmed}, localStorage:{getItem:()=>stored},
 isActive:r=>['running','ready','queued','created','active'].includes(r.status),
 api:async(path,options)=>{assert.equal(options,undefined,'recovery must only read');calls.push(path);if(fail)throw Error('offline');return path==='/local/runs'?inventory:detail;},
 renderPending:()=>{},notify:()=>{},keepPending:()=>{ctx.state.uncertain=null;}
});
vm.runInContext(code,ctx);
function reset(age=181000){calls=[];fail=false;confirmed=true;inventory={runs:[],complete:true};detail={snapshot:{run:{id:'run',status:'failed',metadata:{}}}};ctx.state={uncertain:{request_id:'request',action:'创建',created_at:new Date(Date.now()-age).toISOString()},connected:true};stored=JSON.stringify(ctx.state.uncertain);}
(async()=>{
 reset();await ctx.recoverPending();assert.equal(ctx.state.uncertain,null);
 reset(1000);await ctx.recoverPending();assert.ok(ctx.state.uncertain);
 reset();inventory.complete=false;await ctx.recoverPending();assert.ok(ctx.state.uncertain);
 reset();inventory.runs=[{status:'running'}];await ctx.recoverPending();assert.ok(ctx.state.uncertain);
 reset();fail=true;await ctx.recoverPending();assert.ok(ctx.state.uncertain);
 reset();confirmed=false;await ctx.recoverPending();assert.ok(ctx.state.uncertain);
 reset();stored=JSON.stringify({request_id:'other-tab'});await ctx.recoverPending();assert.ok(ctx.state.uncertain);
 reset();stored=null;await ctx.recoverPending();assert.ok(ctx.state.uncertain);
 reset();stored='invalid';await ctx.recoverPending();assert.ok(ctx.state.uncertain);
 reset();ctx.state.readFailed=true;await ctx.recoverPending();assert.ok(ctx.state.uncertain);
 reset();ctx.state.uncertain.run_id='run';detail.snapshot.run.metadata.delivery={state:'repair_queued'};await ctx.recoverPending();assert.ok(ctx.state.uncertain);
 reset();ctx.state.uncertain.run_id='run';detail.snapshot.run.id='wrong';await ctx.recoverPending();assert.ok(ctx.state.uncertain);
 reset();ctx.state.uncertain.run_id='run';detail.snapshot.run.metadata.delivery={receipts:{request:{response:{}}}};await ctx.reconcilePending();assert.equal(ctx.state.uncertain,null);
 reset();inventory.runs=[{request_id:'request',status:'completed'}];await ctx.reconcilePending();assert.equal(ctx.state.uncertain,null);
 console.log('14 recovery scenarios passed; zero write calls');
})().catch(e=>{console.error(e);process.exitCode=1;});
