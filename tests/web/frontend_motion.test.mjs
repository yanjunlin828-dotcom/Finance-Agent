import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {createMotion} from '../../web/app/motion.mjs';
import {contentFingerprint,transitionDirection} from '../../web/app/state/presentation.mjs';
import {readPreferences} from '../../web/app/state/workspace.mjs';

function node(id='node',children=[]) {
  const attrs=new Map([['id',id],['name','mode'],['data-view','report']]);
  const classes=new Set();const animations=[];
  return {id,attrs,children,animations,inert:false,removed:false,
    classList:{add:value=>classes.add(value),toggle(value,on){if(on)classes.add(value);else classes.delete(value);}},
    setAttribute:(key,value)=>attrs.set(key,value),removeAttribute:key=>attrs.delete(key),querySelectorAll:()=>children,matches:()=>true,
    getBoundingClientRect:()=>({left:100,top:200,width:100,height:40}),
    remove(){this.removed=true;},
    animate(frames,timing){let resolve,reject;const finished=new Promise((yes,no)=>{resolve=yes;reject=no;});const animation={frames,timing,finished,resolve,cancel(){this.cancelled=true;reject(new Error('cancelled'));}};animations.push(animation);return animation;}
  };
}
function environment(reduced=false){const listeners=new Map(),doc={hidden:false,addEventListener:(name,fn)=>listeners.set(name,fn),removeEventListener:name=>listeners.delete(name)};return {doc,listeners,motion:createMotion({document:doc,media:()=>reduced})};}
const settle=()=>new Promise(resolve=>setImmediate(resolve));
const finish=async(...nodes)=>{for(const item of nodes)for(const animation of item.animations)animation.resolve();await settle();};
const fixture=()=>JSON.parse(fs.readFileSync(new URL('../../web/fixtures/b1-normal.json',import.meta.url))).frames[2].snapshot;

test('scene departure is inert, nameless and ID-free while the new scene is immediately live',async()=>{
  const {motion}=environment(),child=node('input'),old=node('old',[child]),next=node('next');
  motion.pair('scene',old,next);
  assert.equal(old.inert,true);assert.equal(old.attrs.get('aria-hidden'),'true');assert.equal(old.attrs.has('id'),false);assert.equal(child.attrs.has('name'),false);assert.equal(child.attrs.has('data-view'),false);assert.equal(next.inert,false);assert.equal(old.removed,false);
  await finish(old,next);assert.equal(old.removed,true);assert.equal(next.removed,false);assert.deepEqual(motion.active(),[]);
});
test('forward/backward stage transitions reverse direction and exit before entry completes',async()=>{
  const {motion}=environment(),old=node(),next=node();motion.pair('scene',old,next,{direction:-1});
  assert.ok(next.animations[0].frames[0].transform.includes('translateX(-24px)'));assert.ok(old.animations[0].frames[1].transform.includes('translateX(18px)'));assert.equal(next.animations[0].timing.duration,460);assert.equal(old.animations[0].timing.duration,320);await finish(old,next);
});
test('rapid reverse navigation removes every superseded departure, leaving only the current pair',async()=>{
  const {motion}=environment();const nodes=Array.from({length:12},(_,i)=>node(String(i)));
  for(let i=0;i<11;i++)motion.pair('scene',nodes[i],nodes[i+1],{direction:i%2?-1:1});
  await settle();assert.ok(nodes.slice(0,10).every(x=>x.removed));assert.equal(nodes[10].removed,false);assert.equal(nodes[11].removed,false);assert.deepEqual(motion.active(),['scene']);
  await finish(nodes[10],nodes[11]);assert.equal(nodes[10].removed,true);assert.equal(nodes[11].removed,false);assert.deepEqual(motion.active(),[]);
});
test('interruption samples the visible pose before cancelling, avoiding a reverse-navigation snap',async()=>{
  const {doc}=environment();const motion=createMotion({document:doc,media:()=>false,styles:()=>({opacity:'.62',transform:'matrix(1,0,0,1,8,0)'})});
  const first=node(),second=node(),third=node();motion.pair('scene',first,second);motion.pair('scene',second,third,{direction:-1});
  assert.equal(second.animations[1].frames[0].opacity,.62);assert.equal(second.animations[1].frames[0].transform,'matrix(1,0,0,1,8,0)');await finish(second,third);
});
test('node, list, nav and overlay channels never cancel an ongoing stage transition',async()=>{
  const {motion}=environment(),old=node(),next=node(),body=node(),marker=node(),nav=node(),drawer=node('reader');
  motion.pair('scene',old,next);motion.pair('node',null,body,{kind:'node'});motion.flip(marker,{left:100,top:0,width:100,height:40},'index');motion.flip(nav,{left:0,top:200,width:100,height:40},'nav');motion.reader(drawer,true);
  assert.equal(next.animations[0].cancelled,undefined);assert.equal(body.animations[0].timing.duration,280);assert.equal(motion.active().length,5);motion.cancel('node');assert.equal(next.animations[0].cancelled,undefined);await finish(old,next,marker,nav,drawer);
});
test('reduced motion retains meaningful feedback with no spatial transform',async()=>{
  const {motion}=environment(true),old=node(),next=node(),drawer=node('reader');motion.pair('scene',old,next);motion.reader(drawer,true);
  for(const item of [old,next,drawer]){assert.equal(item.animations[0].timing.duration,120);assert.ok(item.animations[0].frames.every(frame=>!frame.transform));}await finish(old,next,drawer);
});
test('explicit no-animation and missing WAAPI fallbacks leave current content visible, clean and active',async()=>{
  const {motion}=environment(),old=node(),next=node();motion.pair('scene',old,next,{noAnimation:true});assert.equal(old.removed,true);assert.equal(next.animations.length,0);
  const second=node(),unsupported=node();unsupported.animate=undefined;motion.pair('node',second,unsupported);assert.equal(second.removed,true);assert.equal(unsupported.inert,false);assert.deepEqual(motion.active(),[]);await settle();
});
test('a rejected animation cannot strand an outgoing layer or reject the public finished promise',async()=>{
  const {motion}=environment(),old=node(),next=node();next.animate=()=>{throw new Error('unavailable');};await motion.pair('scene',old,next).finished;assert.equal(old.removed,true);assert.deepEqual(motion.active(),[]);await settle();
});
test('hiding the document cancels all channels and leaves only the live scene',async()=>{
  const {motion,doc,listeners}=environment(),old=node(),next=node(),drawer=node('reader');motion.pair('scene',old,next);motion.reader(drawer,true);doc.hidden=true;listeners.get('visibilitychange')();await settle();assert.equal(old.removed,true);assert.equal(next.removed,false);assert.deepEqual(motion.active(),[]);motion.dispose();assert.equal(listeners.size,0);
});
test('reverse drawer animation supersedes its close without affecting the scene channel',async()=>{
  const {motion}=environment(),drawer=node('reader'),old=node(),next=node();motion.pair('scene',old,next);motion.reader(drawer,false);motion.reader(drawer,true);assert.equal(drawer.animations[0].cancelled,true);assert.ok(motion.active().includes('scene'));await finish(drawer,old,next);
});
test('all B1 node fingerprints ignore telemetry but see their relevant output changes',()=>{
  const s=fixture(),ids=['validate_context','load_financials','calculate','detect_phenomena','propose_hypotheses','collect_fixed_evidence','review_claims','write_report','validate_report'];
  const after=structuredClone(s);after.budget.settled_estimate='99';after.runtime.event_cursor+=10;after.runtime.connection_state='DISCONNECTED';after.runtime.worker_state='STOPPED';
  for(const step of ids)assert.equal(contentFingerprint(s,{panel:'process',step}),contentFingerprint(after,{panel:'process',step}));
  after.research.observations[0].standard_value='777';assert.notEqual(contentFingerprint(s,{panel:'process',step:'calculate'}),contentFingerprint(after,{panel:'process',step:'calculate'}));assert.equal(contentFingerprint(s,{panel:'process',step:'review_claims'}),contentFingerprint(after,{panel:'process',step:'review_claims'}));
});
test('new lifecycle and approved conclusions update their own node, without moving an unrelated reading surface',()=>{
  const s=fixture(),after=structuredClone(s);after.steps.push({node_id:'calculate',step_id:'new-instance',lifecycle:'FUNCTION_RETURNED',acceptance:'ACCEPTED'});
  assert.notEqual(contentFingerprint(s,{panel:'process',step:'calculate'}),contentFingerprint(after,{panel:'process',step:'calculate'}));assert.equal(contentFingerprint(s,{panel:'process',step:'collect_fixed_evidence'}),contentFingerprint(after,{panel:'process',step:'collect_fixed_evidence'}));
  after.research.claims.push({...s.research.claims[0],kind:'COMPUTED',text:'new record'});assert.notEqual(contentFingerprint(s,{panel:'process',step:'detect_phenomena'}),contentFingerprint(after,{panel:'process',step:'detect_phenomena'}));
});
test('report fingerprints ignore costs but immediately reflect a revoked publication gate',()=>{
  const s=fixture(),after=structuredClone(s);after.budget.total_calls+=3;assert.equal(contentFingerprint(s,{panel:'report'}),contentFingerprint(after,{panel:'report'}));after.runtime.cancel_requested=true;assert.notEqual(contentFingerprint(s,{panel:'report'}),contentFingerprint(after,{panel:'report'}));
});
test('B2 round changes and actual action output changes are distinct from heartbeat updates',()=>{
  const s=JSON.parse(fs.readFileSync(new URL('../../web/fixtures/history-b2.json',import.meta.url))).frames[0].snapshot,after=structuredClone(s);after.runtime.event_cursor+=5;
  assert.equal(contentFingerprint(s,{panel:'process',round:1}),contentFingerprint(after,{panel:'process',round:1}));assert.notEqual(contentFingerprint(s,{panel:'process',round:1}),contentFingerprint(s,{panel:'process',round:2}));after.research.supplement.action_count+=1;assert.notEqual(contentFingerprint(s,{panel:'process',round:1}),contentFingerprint(after,{panel:'process',round:1}));
});
test('empty/unknown step selections still fingerprint the financial fallback actually displayed',()=>{
  const s=fixture(),after=structuredClone(s);after.research.observations[0].standard_value='123';for(const step of [null,'unknown'])assert.notEqual(contentFingerprint(s,{panel:'process',step}),contentFingerprint(after,{panel:'process',step}));
});
test('stage, node and B2 round ordering consistently distinguish forward from back navigation',()=>{
  assert.equal(transitionDirection({panel:'report'},{panel:'question'}),-1);assert.equal(transitionDirection({panel:'question'},{panel:'report'}),1);
  assert.equal(transitionDirection({panel:'process',step:'c'},{panel:'process',step:'a'},['a','b','c']),-1);assert.equal(transitionDirection({panel:'process',round:2},{panel:'process',round:3}),1);
});
test('removed demo animation switches do not silently disable the new motion; versioned preferences remain valid',()=>{
  const cache={getItem:()=>JSON.stringify({task_id:'t',reduced:true,noAnimation:true})};assert.equal(readPreferences(cache,'t').noAnimation,false);assert.equal(readPreferences(cache,'t').reduced,false);
  cache.getItem=()=>JSON.stringify({task_id:'t',motion_version:2,reduced:true,noAnimation:true});assert.equal(readPreferences(cache,'t').noAnimation,true);assert.equal(readPreferences(cache,'t').reduced,true);
});
