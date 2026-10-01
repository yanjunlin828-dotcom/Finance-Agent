import {reportAvailable} from './workspace.mjs';

export const panelOrder=['question','process','report'];
export function transitionDirection(previous,next,stations=[]) {
  if(!previous)return 1;
  if(previous.panel!==next.panel)return Math.sign(panelOrder.indexOf(next.panel)-panelOrder.indexOf(previous.panel))||1;
  if(next.panel==='process')return Math.sign((Number(next.round)||stations.indexOf(next.step))-(Number(previous.round)||stations.indexOf(previous.step)))||1;
  return 1;
}

/** Budget, heartbeat, worker and cursor changes have no role in reading content. */
export function contentFingerprint(snapshot,ui,{watchingNodes=false,bootstrap=null}={}) {
  const {task,research:r,publication:p}=snapshot;
  const base={task:task.task_id,panel:ui.panel,question:task.question,labels:task.company_labels,context:task.context};
  if(ui.panel==='question')return JSON.stringify({...base,bootstrap});
  if(ui.panel==='report')return JSON.stringify({...base,available:reportAvailable(snapshot),publication:p,research:r,provenance:snapshot.provenance,expanded:Boolean(ui.questionExpanded)});
  if(task.workflow==='B2')return JSON.stringify({...base,round:ui.round,supplement:r.supplement,publication:p});
  const buckets={validate_context:null,load_financials:[r.observations,r.calculations,r.calculation_slots],calculate:[r.observations,r.calculations,r.calculation_slots],collect_fixed_evidence:r.evidence,detect_phenomena:r.claims?.filter(x=>x.kind==='COMPUTED'),propose_hypotheses:r.claims?.filter(x=>['INFERENCE','UNRESOLVED'].includes(x.kind)),review_claims:r.gaps};
  const node=Object.hasOwn(buckets,ui.step)||['write_report','validate_report'].includes(ui.step)?ui.step:'load_financials';
  const step=[...snapshot.steps].reverse().find(x=>x.node_id===node);
  return JSON.stringify({...base,step:node,watchingNodes,record:step?{id:step.step_id,lifecycle:step.lifecycle,acceptance:step.acceptance}:null,publication:p,content:buckets[node]??null});
}
