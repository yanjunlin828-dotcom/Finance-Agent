import test from 'node:test';
import assert from 'node:assert/strict';
import {createNodeTour} from '../../web/app/state/node-tour.mjs';
import {creationReadiness} from '../../web/app/state/creation.mjs';

function fixture(){
  let held=false,id=0;const timers=new Map(),frames=[];let completed=0;
  const clock={setTimeout:f=>{timers.set(++id,f);return id;},clearTimeout:id=>timers.delete(id)};
  const tour=createNodeTour({clock,canPresent:()=>!held,onFrame:s=>frames.push(s.node_id),onIdle:()=>completed++});
  const source={task:{task_id:'actual',origin:'CURRENT_EXECUTION',workflow:'B1'},steps:Array.from({length:9},(_,i)=>({instance_id:'source-'+i,node_id:'node-'+i,identity_kind:'EXECUTION',occurred_at:'2026-10-01',lifecycle:'FUNCTION_RETURNED'}))};
  const tick=()=>{const [key,fn]=timers.entries().next().value||[];if(fn){timers.delete(key);fn();}};
  return {tour,source,frames,timers,tick,hold:value=>held=value,completed:()=>completed};
}

test('fast real nodes are all viewable, deduplicated, without altering source state',()=>{
  const f=fixture(),before=JSON.stringify(f.source);f.tour.start('actual');f.tour.offer(f.source);f.tour.offer(f.source);
  assert.equal(f.tour.pending(),true);assert.deepEqual(f.frames,[]);
  for(let i=0;i<10;i++)f.tick();
  assert.deepEqual(f.frames,f.source.steps.map(s=>s.node_id));assert.equal(f.completed(),1);assert.equal(f.tour.pending(),false);
  assert.equal(JSON.stringify(f.source),before);
});
test('reader pause holds remaining records and resumes without losing or replaying nodes',()=>{
  const f=fixture();f.tour.start('actual');f.tour.offer(f.source);f.tick();f.hold(true);f.tick();
  assert.deepEqual(f.frames,['node-0']);assert.equal(f.tour.pending(),true);assert.equal(f.timers.size,0);
  f.hold(false);f.tour.resume();for(let i=0;i<9;i++)f.tick();assert.equal(f.frames.length,9);assert.equal(f.completed(),1);
});
test('wrong task, historical replay and invented index entries never produce a node tour',()=>{
  const f=fixture();f.tour.start('actual');
  for(const task of [{...f.source.task,task_id:'old'},{...f.source.task,origin:'MODEL_REPLAY'},{...f.source.task,workflow:'B2'}])f.tour.offer({...f.source,task});
  f.tour.offer({...f.source,steps:f.source.steps.map(s=>({...s,identity_kind:'READING_INDEX'}))});
  assert.equal(f.tour.pending(),false);assert.equal(f.timers.size,0);assert.deepEqual(f.frames,[]);
});
test('cancel/manual report/new task invalidates all queued callbacks',()=>{
  const f=fixture();f.tour.start('actual');f.tour.offer(f.source);f.tour.stop();f.tick();
  assert.equal(f.tour.pending(),false);assert.deepEqual(f.frames,[]);
  f.tour.start('next');f.tour.offer(f.source);assert.equal(f.timers.size,0);
});
const bootstrap={model_ready:true,remaining_authorized_usd:'0.05',task_caps_usd:{B1:'0.10',B2:'0.05'}};
test('default clock calls browser timers without an incompatible object receiver',async()=>{
  const original=globalThis.setTimeout;let calls=0;
  globalThis.setTimeout=function(fn,ms){assert.equal(this,undefined);calls++;return original(fn,ms);};
  try{
    const f=fixture();let resolve;const done=new Promise(r=>resolve=r);
    const tour=createNodeTour({initialDelay:0,dwell:1,canPresent:()=>true,onFrame:()=>{},onIdle:resolve});
    tour.start('actual');tour.offer(f.source);await done;
    assert.equal(calls,10);assert.equal(tour.pending(),false);
  }finally{globalThis.setTimeout=original;}
});
test('UI checks the full task cap and keeps LIVE distinct from free modes',()=>{
  assert.equal(creationReadiness(bootstrap,'B1','LIVE').ready,false);
  assert.equal(creationReadiness(bootstrap,'B2','LIVE').ready,true);
  assert.equal(creationReadiness(bootstrap,'B1','REPLAY').ready,true);
  assert.equal(creationReadiness({...bootstrap,remaining_authorized_usd:'0.28022490'},'B1','LIVE').ready,true);
  assert.equal(creationReadiness({...bootstrap,model_ready:false},'B1','LIVE').ready,false);
  assert.equal(creationReadiness(bootstrap,'B2','RULES',false).ready,false);
  assert.equal(creationReadiness({...bootstrap,remaining_authorized_usd:'NaN'},'B1','LIVE').ready,false);
});
