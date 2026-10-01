import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {TreeNode,createDocument} from './helpers/dom.mjs';

const snapshot=JSON.parse(fs.readFileSync(new URL('../../web/fixtures/history-b1.json',import.meta.url))).frames[0].snapshot;
const vocabulary=JSON.parse(fs.readFileSync(new URL('../../web/contracts/vocabulary.json',import.meta.url)));
const settings={tasks:[],model:'configured',model_ready:true,remaining_authorized_usd:'0.30',authorized_total_usd:'0.30',task_caps_usd:{B1:'0.10',B2:'0.05'},default_task_id:snapshot.task.task_id,scope:{}};
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};};
const settle=async()=>{for(let i=0;i<12;i++)await new Promise(resolve=>setImmediate(resolve));};
const storage=()=>{const values=new Map();return {getItem:key=>values.get(key)||null,setItem:(key,value)=>values.set(key,value),removeItem:key=>values.delete(key)};};

test('live workspace navigation renders the question before refreshing the service',async t=>{
  // Execute the production entry point with controlled HTTP and a tree model.
  // Pending bootstrap responses deliberately never resolve until the test says so.
  const doc=createDocument(),app=new TreeNode('div'),reader=new TreeNode('dialog');app.id='app';reader.id='reader';doc.append(app,reader);
  const win=new TreeNode('#window');
  let url=new URL('http://localhost/?view=process&source=live&task='+snapshot.task.task_id),nextBootstrap=null,sourceSnapshot=snapshot;
  const writes=[],historyWrites=[],timers=[];
  const globals={document:doc,window:win,Node:TreeNode,location:{},history:{},localStorage:storage(),sessionStorage:storage(),
    matchMedia:()=>({matches:false,addEventListener(){}}),requestAnimationFrame:fn=>{queueMicrotask(fn);return 1;},cancelAnimationFrame(){},
    setTimeout:fn=>{timers.push(fn);return timers.length;},clearTimeout(){},getSelection:()=>({isCollapsed:true}),
    fetch:async(path,options={})=>{
      if(options.method==='POST'){writes.push(path);throw new Error('Unexpected write');}
      const value=path==='/api/bootstrap'?(nextBootstrap?await nextBootstrap.promise:settings):path.startsWith('/api/tasks/')?{snapshot:sourceSnapshot,events:[]}:path.includes('vocabulary')?vocabulary:null;
      if(!value)throw new Error('Unexpected read: '+path);
      return {ok:true,json:async()=>structuredClone(value)};
    }
  };
  for(const key of ['search','hash','pathname'])Object.defineProperty(globals.location,key,{get:()=>url[key]});
  for(const method of ['pushState','replaceState'])globals.history[method]=(_state,_title,path)=>{historyWrites.push(method);url=new URL(path,url);};
  for(const [key,value] of Object.entries(globals)){const old=Object.getOwnPropertyDescriptor(globalThis,key);Object.defineProperty(globalThis,key,{value,writable:true,configurable:true});t.after(()=>{if(old)Object.defineProperty(globalThis,key,old);else delete globalThis[key];});}
  const current=()=>app.querySelector('[data-current-scene]');
  const click=view=>app.dispatchEvent({type:'click',target:app.querySelector('[data-view="'+view+'"]')});
  await import('../../web/app/main.mjs?navigation-regression');await settle();
  assert.ok(current()?.querySelector('.process-paper'),'production entry point loaded the process');

  await t.test('process → question is immediate while the service is slow',async()=>{
    nextBootstrap=deferred();click('question');
    assert.ok(current().querySelector('#question'),'return must not wait for bootstrap');
    assert.equal(url.searchParams.get('view'),'question');
    assert.equal(doc.querySelector('#main').inert,false);
    assert.equal(current().querySelector('#create-live').disabled,true);
    nextBootstrap.resolve(settings);await settle();
    assert.equal(current().querySelector('#create-live').disabled,false);
  });
  await t.test('report → question preserves the editor and its reading position on refresh',async()=>{
    click('report');assert.ok(current().querySelector('[data-scroll-region="report-content"]'));
    nextBootstrap=deferred();click('question');
    const editor=current().querySelector('#question'),form=current().querySelector('.question-form');
    editor.value='我的新问题';editor.dispatchEvent({type:'input'});editor.focus();form.scrollTop=17;
    const fresh={...settings,model:'updated',remaining_authorized_usd:'0.20',tasks:[{task_id:'baseline-live',workflow:'B1',execution_mode:'LIVE',report_available:true,source_revalidation:'VERIFIED',question:'新研究',status:'COMPLETED'}]};
    nextBootstrap.resolve(fresh);await settle();
    assert.equal(current().querySelector('#question'),editor);assert.equal(editor.value,'我的新问题');assert.equal(doc.activeElement,editor);assert.equal(form.scrollTop,17);
    assert.ok(current().querySelector('#creation-model').textContent.includes('updated'));
    assert.equal(current().querySelector('#baseline').children.length,1);
    assert.ok(current().querySelector('.history-block').textContent.includes('新研究'));
    click('process');click('question');assert.equal(current().querySelector('#question').value,'我的新问题');
    await settle();
  });
  await t.test('failed refresh leaves the question editable and creation disabled',async()=>{
    click('report');nextBootstrap=deferred();click('question');
    const editor=current().querySelector('#question');nextBootstrap.reject(new Error('offline'));await settle();
    assert.equal(current().querySelector('#question'),editor);assert.equal(doc.querySelector('#main').inert,false);
    assert.equal(current().querySelector('#create-live').disabled,true);
    assert.ok(current().querySelector('#creation-readiness').textContent.includes('重试'));
    nextBootstrap=deferred();click('question');nextBootstrap.resolve(settings);await settle();
    assert.equal(current().querySelector('#create-live').disabled,false);
  });
  await t.test('source polling cannot replace the latest typed draft on navigation',async()=>{
    const editor=current().querySelector('#question');
    editor.value='状态更新之前';editor.dispatchEvent({type:'input'});
    sourceSnapshot=structuredClone(snapshot);sourceSnapshot.runtime.event_cursor++;
    assert.ok(timers.length);timers.shift()();await settle();
    editor.value='状态更新之后的最终问题';editor.dispatchEvent({type:'input'});
    click('report');click('question');assert.equal(current().querySelector('#question').value,'状态更新之后的最终问题');
    await settle();
  });
  await t.test('late response cannot override a later stage or overwrite a newer settings response',async()=>{
    nextBootstrap=deferred();const old=nextBootstrap;click('question');click('report');
    nextBootstrap=deferred();const latest=nextBootstrap;click('question');latest.resolve({...settings,model:'latest'});await settle();
    old.resolve({...settings,model:'stale'});await settle();
    assert.ok(current().querySelector('#creation-model').textContent.includes('latest'));
    nextBootstrap=deferred();click('question');click('process');nextBootstrap.resolve(settings);await settle();
    assert.ok(current().querySelector('.process-paper'));assert.equal(url.searchParams.get('view'),'process');
    assert.equal(app.querySelectorAll('[data-current-scene]').length,1);
  });
  await t.test('browser Back within a task returns immediately without adding a history entry',async()=>{
    nextBootstrap=null;click('question');await settle();const questionURL=new URL(url);
    click('report');const before=historyWrites.length;
    nextBootstrap=deferred();url=questionURL;win.dispatchEvent({type:'popstate'});
    assert.ok(current().querySelector('#question'));assert.equal(historyWrites.length,before);
    nextBootstrap.resolve(settings);await settle();assert.equal(historyWrites.length,before);
  });
  await t.test('returning remains available when the refreshed grant is exhausted',async()=>{
    click('process');nextBootstrap=deferred();click('question');nextBootstrap.resolve({...settings,remaining_authorized_usd:'0.00'});await settle();
    assert.ok(current().querySelector('#question'));assert.equal(current().querySelector('#create-live').disabled,true);
    assert.ok(current().querySelector('#creation-readiness').textContent.includes('授权不足'));
  });
  await t.test('refresh keeps the chosen B2 mode and compatible baseline',async()=>{
    const tasks=[{task_id:'baseline-live',workflow:'B1',execution_mode:'LIVE',report_available:true,source_revalidation:'VERIFIED',question:'已完成研究',status:'COMPLETED'}];
    const configured={...settings,tasks};
    nextBootstrap=deferred();click('question');nextBootstrap.resolve(configured);await settle();
    const mode=current().querySelectorAll('input').find(input=>input.value==='B2');mode.checked=true;mode.dispatchEvent({type:'change'});
    const baseline=current().querySelector('#baseline');baseline.value='baseline-live';
    nextBootstrap=deferred();click('question');nextBootstrap.resolve({...configured,remaining_authorized_usd:'0.10'});await settle();
    assert.equal(current().querySelector('#baseline'),baseline);assert.equal(baseline.value,'baseline-live');assert.equal(mode.checked,true);
    assert.equal(current().querySelector('.baseline-choice').hidden,false);assert.equal(current().querySelector('#create-live').disabled,false);
    assert.ok(current().querySelector('#creation-budget-note').textContent.includes('0.05 USD'));
  });
  assert.deepEqual(writes,[],'navigation never creates tasks or calls models');
});
