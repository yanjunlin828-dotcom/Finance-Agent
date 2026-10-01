import test from 'node:test';
import assert from 'node:assert/strict';
import {api,requestId,clearCreateRequest} from '../../web/app/adapters/service.mjs';

test('uncertain write acknowledgement never retries or falls back to fixtures',async()=>{
  let calls=0;const old=globalThis.fetch;
  globalThis.fetch=async()=>{calls++;throw new TypeError('connection lost');};
  try{await assert.rejects(api('tasks',{body:{request_id:'one'},token:'test-token'}),e=>e.code==='DISCONNECTED'&&e.message.includes('不会自动重发'));assert.equal(calls,1);}
  finally{globalThis.fetch=old;}
});

test('API preserves server rejection and uses same-origin token for writes',async()=>{
  const old=globalThis.fetch;let seen;
  globalThis.fetch=async(path,options)=>{seen={path,options};return {ok:false,status:422,json:async()=>({code:'BUDGET_LIMIT',message:'预算不足'})};};
  try{await assert.rejects(api('tasks',{body:{request_id:'one'},token:'test-token'}),e=>e.code==='BUDGET_LIMIT'&&e.status===422);assert.equal(seen.path,'/api/tasks');assert.equal(seen.options.headers['X-FinResearch-Token'],'test-token');assert.equal(seen.options.method,'POST');}
  finally{globalThis.fetch=old;}
});

test('invalid service payload cannot become a sample success',async()=>{
  const old=globalThis.fetch;
  globalThis.fetch=async()=>({ok:true,status:200,json:async()=>{throw new SyntaxError();}});
  try{await assert.rejects(api('tasks/one'),e=>e.code==='UNSUPPORTED_RESPONSE');}finally{globalThis.fetch=old;}
});

test('pending creation keeps one request identity without caching the question',async()=>{
  const values=new Map();const storage={getItem:k=>values.get(k),setItem:(k,v)=>values.set(k,v),removeItem:k=>values.delete(k)};
  const payload={workflow:'B1',execution_mode:'LIVE',question:'尚未提交的私人问题正文'};
  const id=await requestId(payload,storage);
  assert.equal(await requestId({...payload},storage),id);
  assert.ok(![...values.values()].join('').includes(payload.question));
  assert.notEqual(await requestId({...payload,question:'另一个问题'},storage),id);
  clearCreateRequest(storage);
  assert.equal(values.size,0);
  assert.notEqual(await requestId(payload,storage),id);
});
