/** Same-origin API only. Credentials never enter browser storage or request bodies. */
export async function api(path,{body,token}={}) {
  let response;
  try{response=await fetch('/api/'+path,{cache:'no-store',headers:body?{'Content-Type':'application/json','X-FinResearch-Token':token}:{},method:body?'POST':'GET',body:body?JSON.stringify(body):undefined});}
  catch{throw Object.assign(new Error(body?'未收到服务确认。请求可能已登记，请重连后核对原任务；不会自动重发。':'暂时无法连接本机服务。正在保留最后可核验画面并尝试重新读取。'),{code:'DISCONNECTED'});}
  let value;
  try{value=await response.json();}catch{throw Object.assign(new Error('服务返回无法识别，请重新读取并核对任务状态。'),{code:'UNSUPPORTED_RESPONSE',status:response.status});}
  if(!response.ok)throw Object.assign(new Error(value.message||'服务读取失败'),{code:value.code,status:response.status});
  return value;
}
export async function requestId(payload,storage=sessionStorage) {
  // Persist only a fingerprint/ID for uncertain create acknowledgements, not the question body.
  const bytes=new TextEncoder().encode(JSON.stringify(payload));
  const hash=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)),b=>b.toString(16).padStart(2,'0')).join('');
  const key='finresearch:pending-create';
  let prior;try{prior=JSON.parse(storage.getItem(key));}catch{}
  if(prior?.hash===hash && /^[A-Za-z0-9_-]+$/.test(prior.id))return prior.id;
  const id='request-'+crypto.randomUUID();storage.setItem(key,JSON.stringify({hash,id}));return id;
}
export function clearCreateRequest(storage=sessionStorage){storage.removeItem('finresearch:pending-create');}
