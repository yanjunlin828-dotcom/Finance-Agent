import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {initialState,reduce,canAutoReport,capabilities,reportAvailable,currentStation,readingPreferences,readPreferences} from '../../web/app/state/workspace.mjs';
const root=new URL('../../web/fixtures/',import.meta.url);
const fixture=name=>JSON.parse(fs.readFileSync(new URL(name+'.json',root),'utf8'));
const frame=(name,index=0)=>fixture(name).frames[index];
const state=(name,index=0)=>{const f=frame(name,index);return initialState(f.snapshot,f.snapshot.ui.panel.toLowerCase());};
for(const entry of JSON.parse(fs.readFileSync(new URL('manifest.json',root),'utf8')).fixtures){
  for(const f of fixture(entry.scenario_id).frames)test(entry.scenario_id+' / '+f.name+' contract gates',()=>{
    const s=initialState(f.snapshot,f.snapshot.ui.panel.toLowerCase());
    assert.equal(reportAvailable(f.snapshot),f.expected.result_available);
    assert.equal(canAutoReport(s),f.expected.auto_enter_report);
    assert.deepEqual(Object.entries(capabilities(f.snapshot)).filter(([,v])=>v).map(([k])=>k).sort(),[...f.expected.enabled_operations].sort());
  });
}
test('rapid completion accepts terminal directly without inventing RUNNING',()=>{
 let s=state('rapid-completion');s=reduce(s,{type:'NAVIGATE',panel:'process',manual:false});s=reduce(s,{type:'SOURCE',snapshot:frame('rapid-completion',1).snapshot,revision:1});
 assert.equal(s.snapshot.task.task_status,'COMPLETED');assert.equal(canAutoReport(s),true);assert.equal(s.snapshot.steps.length,0);
});
for(const guard of ['reader','selection','editing','manual'])test(guard+' retains reading while source result arrives',()=>{
 let s=state('b1-normal',1);s=reduce(s,{type:'UI',patch:{[guard]:true}});s=reduce(s,{type:'SOURCE',snapshot:frame('b1-normal',2).snapshot,revision:1});assert.equal(canAutoReport(s),false);assert.equal(s.ui.panel,'process');assert.equal(reportAvailable(s.snapshot),true);
});
test('pause does not freeze source; following locates current instance',()=>{
 let s=reduce(state('b1-normal',1),{type:'FOLLOW',value:false});const snap=structuredClone(frame('b1-normal',2).snapshot);snap.runtime.current_step_id='instance';snap.steps=[{step_id:'instance',node_id:'calculate'}];
 s=reduce(s,{type:'SOURCE',snapshot:snap,revision:1});assert.equal(s.snapshot.task.task_status,'COMPLETED');assert.equal(canAutoReport(s),false);s=reduce(s,{type:'FOLLOW',value:true});assert.equal(s.ui.step,'calculate');assert.equal(canAutoReport(s),true);
});
test('stale and foreign source responses cannot regress task identity',()=>{
 const s=state('b1-normal',1),updated=reduce(s,{type:'SOURCE',snapshot:frame('b1-normal',2).snapshot,revision:2});
 assert.equal(reduce(updated,{type:'SOURCE',snapshot:s.snapshot,revision:1}),updated);assert.equal(reduce(updated,{type:'SOURCE',snapshot:frame('hard-failure').snapshot,revision:3}),updated);
});
test('coalesced frames retain every distinct event, including independent fixture seq collision',()=>{
 let s=state('b1-normal');for(let i=1;i<3;i++){const f=frame('b1-normal',i);s=reduce(s,{type:'SOURCE',snapshot:f.snapshot,events:f.events,revision:i});}
 assert.deepEqual(s.events.map(e=>e.type),['NODE_RETURNED','REPORT_VERIFIED']);const f=frame('b1-normal',2);s=reduce(s,{type:'SOURCE',snapshot:f.snapshot,events:f.events,revision:3});assert.equal(s.events.length,2);
});
test('foreign events are rejected even inside a valid source update',()=>{
 const f=frame('b1-normal',1),events=[...f.events,{...f.events[0],task_id:'foreign'},{...f.events[0],event_stream_id:'foreign'}];assert.equal(reduce(state('b1-normal'),{type:'SOURCE',snapshot:f.snapshot,events,revision:1}).events.length,1);
});
test('simulation cannot grant publication authority to live or history origin',()=>{
 const s=structuredClone(frame('b1-normal',2).snapshot);s.task.access_mode='LIVE_CONTROL';assert.equal(reportAvailable(s),false);s.task.access_mode='MOCK';s.provenance.kind='DERIVED_HISTORICAL';assert.equal(reportAvailable(s),false);
});
test('stale publication flag cannot override cancellation or nonterminal task',()=>{
 const s=structuredClone(frame('b1-normal',2).snapshot);s.runtime.cancel_requested=true;assert.equal(reportAvailable(s),false);s.runtime.cancel_requested=false;s.task.task_status='RUNNING';assert.equal(reportAvailable(s),false);
});
test('decision acknowledgment is not a resume and new-snapshot keeps old task waiting',()=>{
 const ready=state('b2-wait-continue',1);assert.equal(ready.snapshot.task.task_status,'WAITING_INPUT');assert.equal(capabilities(ready.snapshot).RESUME,true);
 const fresh=state('b2-new-snapshot',1);assert.equal(fresh.snapshot.task.task_status,'WAITING_INPUT');assert.equal(capabilities(fresh.snapshot).RESUME,false);
});
test('unknown model remains unsafe with reservation; no presentation reset of cost',()=>{
 const s=state('unknown-model');assert.equal(capabilities(s.snapshot).RESUME,false);const u=reduce(s,{type:'FOLLOW',value:true});assert.equal(u.snapshot.budget.pending_reservation,'0.0015');assert.equal(u.snapshot.task.task_status,'BLOCKED');
});
test('historical navigation never replays execution or auto-enters report',()=>{
 let s=state('history-b2');s=reduce(s,{type:'NAVIGATE',panel:'process',round:1});s=reduce(s,{type:'FOLLOW',value:true});assert.equal(canAutoReport(s),false);assert.equal(s.snapshot.task.access_mode,'HISTORY_READ_ONLY');
});
test('persistence allowlist excludes content, budget, draft and source status',()=>{
 const s=state('history-b2');s.ui.followupDraft='private';const p=readingPreferences(s);assert.deepEqual(Object.keys(p).sort(),['task_id','motion_version','follow','manual','step','round','reduced','noAnimation','positions'].sort());assert.equal(JSON.stringify(p).includes('private'),false);assert.equal(JSON.stringify(p).includes('PARTIAL'),false);
});
test('untrusted reading cache is sanitized and cannot supply business state',()=>{
 const storage={getItem:()=>JSON.stringify({task_id:'id',snapshot:{task_status:'COMPLETED'},follow:'true',step:'../path',round:-1,positions:{bad:{y:-2},legacy:{y:640,anchor:'gaps'},'report::':{version:2,regions:{'report-content':{top:640,left:0}},anchor:'gaps'}}})};const p=readPreferences(storage,'id');assert.equal(p.follow,undefined);assert.equal(p.step,null);assert.equal(p.round,null);assert.deepEqual(p.positions,{'report::':{version:2,regions:{'report-content':{top:640,left:0}},anchor:'gaps'}});assert.equal(p.snapshot,undefined);assert.deepEqual(readPreferences(storage,'other'),{});
});
test('instance identity resolves node only from actual step mapping',()=>{
 const s=structuredClone(frame('b1-normal').snapshot);s.runtime.current_step_id='round-1-attempt-2';s.steps=[{step_id:'round-1-attempt-2',node_id:'calculate'}];assert.equal(currentStation(s),'calculate');s.runtime.current_step_id='invented';assert.equal(currentStation(s),null);
});
