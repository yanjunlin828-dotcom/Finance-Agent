import {sanitizePosition} from '../utils/scroll.mjs';
/** Source snapshots are authority; UI intent and mock revision are separate.
 * Fixture frames are independent snapshots, NOT a live monotonic event stream.
 * W3 must supply a trusted revision and validate its cursor reconciliation.
 */
export function reportAvailable(s) {
  const real = s.publication.verification === 'VERIFIED';
  const mock = s.publication.verification === 'SIMULATED_VALID' && s.task.access_mode === 'MOCK' && s.provenance.kind === 'SYNTHETIC';
  return Boolean(s.publication.result_available && (real || mock) && ['COMPLETED','PARTIAL'].includes(s.task.task_status) && !s.runtime.cancel_requested);
}
export function capabilities(s) {
  const writes = s.task.access_mode !== 'HISTORY_READ_ONLY' && ['CONNECTED','NOT_APPLICABLE'].includes(s.runtime.connection_state);
  const waiting = s.task.workflow === 'B2' && s.task.task_status === 'WAITING_INPUT';
  return {
    VIEW_REPORT:reportAvailable(s), VIEW_STEPS:Boolean(s.steps.length || s.research.supplement), VIEW_EVIDENCE:Boolean(s.research.evidence.length),
    FOLLOWUP:writes && reportAvailable(s), NEW_TASK:writes,
    CANCEL:writes && !['COMPLETED','PARTIAL','CANCELLED'].includes(s.task.task_status) && !s.runtime.cancel_requested,
    CLARIFY:writes && waiting && !s.runtime.cancel_requested && s.runtime.clarification_status === 'NONE',
    RESUME:writes && s.runtime.resume_readiness === 'CONFIRMED_SAFE' && s.runtime.worker_state === 'STOPPED' && !s.runtime.cancel_requested && ['CREATED','RUNNING','FAILED','WAITING_INPUT'].includes(s.task.task_status) && (!waiting || s.runtime.clarification_status === 'READY_TO_RESUME')
  };
}
export function currentStation(s) {
  const id=s.runtime.current_step_id;
  return s.steps.find(x=>x.step_id===id)?.node_id || (s.steps.some(x=>x.node_id===id)?id:null);
}
export function initialState(snapshot, view, saved={}) {
  return {snapshot, revision:0, events:[], eventKeys:[], positions:saved.positions||{}, ui:{
    panel:view, follow:saved.follow ?? snapshot.ui.follow_progress, manual:saved.manual ?? snapshot.ui.manual_browsing,
    reader:snapshot.ui.reader_open, selection:snapshot.ui.text_selection_active, editing:snapshot.ui.editing_followup, step:saved.step||null, round:saved.round||null,
    reduced:saved.reduced||false, noAnimation:saved.noAnimation||false, followupDraft:'', followupResult:null
  }};
}
export function canAutoReport(state) {
  const u=state.ui;
  return reportAvailable(state.snapshot) && state.snapshot.task.origin!=='HISTORICAL_VIEW' && u.panel==='process' && u.follow && ![u.manual,u.reader,u.selection,u.editing].some(Boolean);
}
export function reduce(state, action) {
  switch(action.type) {
    case 'SOURCE': {
      if(action.revision<=state.revision || action.snapshot.task.task_id!==state.snapshot.task.task_id) return state;
      const keys=new Set(state.eventKeys), events=[...state.events];
      for(const e of action.events||[]) {
        if(e.task_id!==state.snapshot.task.task_id || e.event_stream_id!==action.snapshot.runtime.event_stream_id) continue;
        const key=JSON.stringify(e);
        if(!keys.has(key)){keys.add(key);events.push(e);}
      }
      return {...state,snapshot:action.snapshot,revision:action.revision,events,eventKeys:[...keys]};
    }
    case 'UI': return {...state,ui:{...state.ui,...action.patch}};
    case 'NAVIGATE': return {...state,ui:{...state.ui,panel:action.panel,manual:action.manual ?? true,step:action.step ?? state.ui.step,round:action.round ?? state.ui.round}};
    case 'FOLLOW': return {...state,ui:{...state.ui,follow:action.value,manual:!action.value,step:action.value?currentStation(state.snapshot):state.ui.step,round:action.value?(state.snapshot.research.supplement?.rounds||null):state.ui.round}};
    case 'POSITION': return {...state,positions:{...state.positions,[action.key]:action.position}};
    default: return state;
  }
}
export function positionKey(ui) {return [ui.panel,ui.panel==='process'?ui.step||'':'',ui.panel==='process'?ui.round||'':''].join(':');}
/** Allowlisted UI storage only. No question, source content, task status or budget. */
export function readingPreferences(state) {
  const {follow,manual,step,round,reduced,noAnimation}=state.ui;
  return {task_id:state.snapshot.task.task_id,motion_version:2,follow,manual,step,round,reduced,noAnimation,positions:state.positions};
}
export function readPreferences(storage,taskId) {
  try {
    const x=JSON.parse(storage.getItem('finresearch:reading:'+taskId)||'null');
    if(x?.task_id!==taskId)return {};
    const positions={};
    for(const [key,p] of Object.entries(x.positions||{}).slice(0,40)) {
      const position=sanitizePosition(p);
      if(key.length<180 && /^(question|process|report):[a-z_]*:[0-9]*$/.test(key) && position)positions[key]=position;
    }
    return {positions,follow:typeof x.follow==='boolean'?x.follow:undefined,manual:typeof x.manual==='boolean'?x.manual:undefined,
      step:typeof x.step==='string' && /^[a-z_]+$/.test(x.step)?x.step:null,round:Number.isInteger(x.round)&&x.round>0?x.round:null,
      reduced:x.motion_version===2&&x.reduced===true,noAnimation:x.motion_version===2&&x.noAnimation===true};
  }catch{return {};}
}
