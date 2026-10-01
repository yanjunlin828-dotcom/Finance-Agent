import {el,icon,button,notice} from './utils/dom.mjs';
import {route,href,readFixture} from './adapters/fixture.mjs';
import {question} from './components/question.mjs';
import {process} from './components/process.mjs';
import {report} from './components/report.mjs';
import {createReader} from './components/reader.mjs';
import {initialState,reduce,reportAvailable,capabilities,canAutoReport,positionKey,readingPreferences,readPreferences,currentStation} from './state/workspace.mjs';
import {createNodeTour} from './state/node-tour.mjs';
import {createMotion} from './motion.mjs';
import {questionTicket,createQuestionFolds} from './components/question-ticket.mjs';
import {api,requestId,clearCreateRequest} from './adapters/service.mjs';
import {capturePosition,restorePosition,revealInPane,scrollToAnchor} from './utils/scroll.mjs';
import {contentFingerprint,transitionDirection} from './state/presentation.mjs';

const app=document.querySelector('#app');
let data,state,request,visibleContext,draft=null,ticket=null,loadVersion=0,scheduled=0,frameRevision=0;
let main,ribbon,statusSlot,followupSlot,live,followupInput,followupSubmit,followupOutput,lastStatus='';
let toolbar,taskDialog,followupDialog,workbenchQuestion,renderedKey=null;
let scene=null,renderedTask=null,renderedUI=null,renderedFingerprint=null,navCursor=null,navigationPending=false,navigationVersion=0;
let remote=false,bootstrap=null,bootstrapPending=false,bootstrapError='',pollTimer=null,creating=false,operationBusy=false,serviceNotice='';
const motion=createMotion();
const folds=createQuestionFolds();
const nodeTour=createNodeTour({
  canPresent:()=>state && state.ui.panel==='process' && state.ui.follow && state.snapshot.runtime.connection_state==='CONNECTED' && !document.hidden && !taskDialog?.open && !followupDialog?.open && ![state.ui.reader,state.ui.selection,state.ui.editing,state.ui.manual].some(Boolean),
  onFrame:step=>{dispatch({type:'UI',patch:{step:step.node_id}});urlState(true);paint({restore:true});},
  onIdle:()=>{if(!checkAuto())refreshStatus();}
});
const reader=createReader(()=>visibleContext,{motion,onOpen:()=>dispatch({type:'UI',patch:{reader:true}}),onClose:()=>{dispatch({type:'UI',patch:{reader:false}});nodeTour.resume();checkAuto();}});
const headings={question:'提出研究问题',process:'研究过程',report:'研究报告'};
function persist(){try{localStorage.setItem('finresearch:reading:'+state.snapshot.task.task_id,JSON.stringify(readingPreferences(state)));}catch{}}
function dispatch(action){state=reduce(state,action);persist();}
function remember(){if(!state||!scene||!renderedKey||renderedTask!==state.snapshot.task.task_id)return;dispatch({type:'POSITION',key:renderedKey,position:capturePosition(scene,location.hash.slice(1)||null)});}
function rememberDraft(){const editor=scene?.querySelector('#question');if(editor)draft=editor.value;}
function questionIntent(expanded){dispatch({type:'UI',patch:{questionExpanded:expanded,manual:true}});const note=statusSlot.querySelector('.follow-row .small');if(note)note.textContent='已固定当前阅读 · 后台状态仍同步';}
function context(){return {...data,snapshot:state.snapshot,route:{...request,view:state.ui.panel,step:state.ui.step,round:state.ui.round},draft,ticket,navigate,createResearch:createLive,remote,bootstrap,bootstrapPending,bootstrapError,onDraft:value=>{draft=value;},watchingNodes:Boolean(nodeTour.viewing()),openRecord:ids=>reader.open(ids),onQuestionIntent:questionIntent,state};}
function urlState(replace=false){
  const extra={frame:String(request.frame),task:state.snapshot.task.task_id};
  if(remote)extra.source='live';else if(request.demo)extra.demo='1';
  if(state.ui.step)extra.step=state.ui.step;if(state.ui.round)extra.round=String(state.ui.round);
  const anchor=replace && route(location.search).view===state.ui.panel?location.hash:'';
  history[replace?'replaceState':'pushState'](null,'',href(state.ui.panel,request.scenario,extra)+anchor);
}
async function navigate(view,scenario=request.scenario,extra={},{fromHistory=false}={}){
  const intent=++navigationVersion;rememberDraft();
  if(navigationPending){++loadVersion;clearTimeout(pollTimer);navigationPending=false;if(main)main.inert=false;if(remote)watch(loadVersion);}
  if(view==='question'||view==='report'||extra.step||extra.round)nodeTour.stop();
  if(remote && view==='question' && !extra.task){
    // Returning to the editor is local navigation, independent of service latency.
    remember();bootstrapPending=true;bootstrapError='';
    dispatch({type:'NAVIGATE',panel:'question'});if(!fromHistory)urlState();paint({focus:true,restore:true});
    updateQuestionSettings();
    const version=loadVersion;
    try{
      const fresh=await api('bootstrap');
      if(intent!==navigationVersion||version!==loadVersion)return;
      bootstrap=fresh;bootstrapPending=false;updateQuestionSettings();
    }catch(error){
      if(intent!==navigationVersion||version!==loadVersion)return;
      bootstrapPending=false;bootstrapError=error.message;updateQuestionSettings();
    }
    return;
  }
  if(remote && extra.task){remember();history.pushState(null,'',href(view,scenario,{task:extra.task,source:'live'}));load(true);return;}
  if(scenario!==request.scenario || extra.frame!==undefined){remember();history.pushState(null,'',href(view,scenario,extra));load(true);return;}
  if(state.ui.reader)return;
  remember();dispatch({type:'NAVIGATE',panel:view,step:extra.step,round:extra.round});if(!fromHistory)urlState();paint({focus:true,restore:true});
}
function updateQuestionSettings(){
  if(state.ui.panel!=='question')return;
  scene.querySelector('.question-layout')?.updateBootstrap?.(bootstrap,{pending:bootstrapPending,error:bootstrapError});
  renderedFingerprint=contentFingerprint(state.snapshot,state.ui,{bootstrap});
}
function shell(){
  if(main?.isConnected)return;
  workbenchQuestion=el('p',{class:'workbench-question',hidden:true});
  const header=el('header',{class:'topbar',id:'workspace-top'},el('a',{class:'brand',href:href('question')},icon('brand'),'FinResearch',el('span',{},'研究工作台')),workbenchQuestion,el('div',{class:'preview-label'},remote?'研究服务已连接':'离线预览 · 不执行研究'));
  const nav=el('div',{class:'workspace-head'},el('nav',{class:'primary-nav','aria-label':'研究工作台'},[['question','提出问题'],['process','研究过程'],['report','阅读报告']].map(([view,label],i)=>el('a',{'data-view':view,href:href(view,request.scenario)},el('span',{class:'nav-index'},String(i+1).padStart(2,'0')),label))),el('span',{class:'workspace-note'},'财务事实 / 披露 / 候选解释'));
  navCursor=el('span',{class:'nav-cursor','aria-hidden':'true'});nav.querySelector('.primary-nav').append(navCursor);
  renderedKey=null;
  main=el('main',{id:'main',tabindex:'-1'});ribbon=el('div',{id:'task-ribbon-slot'});statusSlot=el('div',{id:'status-slot'});followupSlot=el('div',{id:'followup-slot'});
  toolbar=el('div',{class:'workspace-toolbar','aria-label':'当前研究状态与操作'});
  taskDialog=drawer('task-details','任务详情与操作',el('div',{class:'drawer-body',tabindex:'0',role:'region','aria-label':'任务详情'},ribbon,statusSlot));
  followupDialog=drawer('followup-dialog','围绕已有记录追问',el('div',{class:'drawer-body',tabindex:'0',role:'region','aria-label':'追问编辑与回答'},followupSlot));
  followupDialog.addEventListener('close',()=>{dispatch({type:'UI',patch:{editing:false}});nodeTour.resume();if(!checkAuto())refreshStatus();});
  live=el('p',{class:'sr-only',role:'status','aria-live':'polite','aria-atomic':'true'});
  app.replaceChildren(el('div',{class:'page'},header,nav,toolbar,main,taskDialog,followupDialog,live));
}
function drawer(id,title,body){
  const dialog=el('dialog',{id,class:'workspace-drawer','aria-labelledby':id+'-title'});
  let opener=null,openerId=null,generation=0;
  const close=()=>{if(!dialog.open)return;const turn=++generation;motion.reader(dialog,false,state.ui).finished.then(()=>{if(turn===generation&&dialog.open)dialog.close();});};
  dialog.replaceChildren(el('header',{class:'reader-head'},el('h2',{id:id+'-title'},title),button(icon('close'),close,'icon-button')),body);
  dialog.querySelector('.icon-button').setAttribute('aria-label','关闭'+title);
  dialog.addEventListener('close',()=>{const target=opener?.isConnected?opener:openerId?document.getElementById(openerId):null;(target||main).focus({preventScroll:true});nodeTour.resume();checkAuto();});
  dialog.addEventListener('cancel',event=>{event.preventDefault();close();});dialog.closeDrawer=close;
  dialog.openDrawer=()=>{if(navigationPending)return;generation++;if(!dialog.open){opener=document.activeElement;openerId=opener?.id;dialog.showModal();}motion.reader(dialog,true,state.ui);};
  return dialog;
}
function updateNavigation(animate=true){
  const previous=navCursor?.getBoundingClientRect();
  main.closest('.page').querySelectorAll('[data-view]').forEach(a=>{a.setAttribute('href',href(a.dataset.view,request.scenario,{frame:String(request.frame),task:state.snapshot.task.task_id, ...(remote?{source:'live'}:{})}));if(a.dataset.view===state.ui.panel)a.setAttribute('aria-current','page');else a.removeAttribute('aria-current');});
  const target=main.closest('.page').querySelector('[data-view="'+state.ui.panel+'"]');
  if(!target||!navCursor)return;const nav=target.parentElement.getBoundingClientRect(),rect=target.getBoundingClientRect();
  Object.assign(navCursor.style,{left:rect.left-nav.left+'px',width:rect.width+'px'});
  if(animate&&renderedUI?.panel!==state.ui.panel)motion.flip(navCursor,previous,'nav',state.ui);
}
function updateProcessIndex(animate=true){
  if(state.ui.panel!=='process'||!scene)return;
  const pane=scene.querySelector('[data-scroll-region="process-index"]'),cursor=scene.querySelector('.station-cursor');
  if(!cursor)return;
  const previous=cursor.getBoundingClientRect();
  for(const b of pane.querySelectorAll('[data-node]')){
    const selectedNode=data.vocabulary.b1_stations.some(x=>x.node_id===state.ui.step)?state.ui.step:'load_financials';
    b.setAttribute('aria-pressed',b.dataset.node===selectedNode?'true':'false');
    const running=currentStation(state.snapshot)===b.dataset.node&&state.snapshot.task.task_status==='RUNNING';
    const done=state.snapshot.steps.some(x=>x.node_id===b.dataset.node&&x.lifecycle==='FUNCTION_RETURNED');
    let marker=b.querySelector('.running-marker, .completed-marker');
    if(!running&&!done){marker?.remove();continue;}
    if(!marker){marker=el('span');b.append(marker);}
    marker.className=running?'running-marker':'completed-marker';marker.textContent=running?'执行中':'已返回';
  }
  const selected=pane.querySelector('[aria-pressed="true"]');if(!selected)return;
  if(renderedKey!==positionKey(state.ui))revealInPane(pane,selected,{align:'nearest'});
  const outer=pane.getBoundingClientRect(),rect=selected.getBoundingClientRect();
  Object.assign(cursor.style,{top:rect.top-outer.top+pane.scrollTop+'px',left:rect.left-outer.left+pane.scrollLeft+'px',width:rect.width+'px',height:rect.height+'px'});
  if(animate&&renderedUI?.step!==state.ui.step)motion.flip(cursor,previous,'index',state.ui);
}
function paint({focus=false,restore=false,authored=false,animate=true}={}){
  const previousKey=renderedKey;remember();visibleContext=context();
  const fingerprint=contentFingerprint(state.snapshot,state.ui,{watchingNodes:visibleContext.watchingNodes,bootstrap:remote?bootstrap:null});
  updateNavigation(animate);
  if(scene&&renderedTask===state.snapshot.task.task_id&&previousKey===positionKey(state.ui)&&fingerprint===renderedFingerprint){refreshStatus();updateProcessIndex(false);if(focus)main.focus({preventScroll:true});return;}
  const next=state.ui.panel==='question'?question(visibleContext):state.ui.panel==='process'?process(visibleContext):report(visibleContext);
  const direction=transitionDirection(renderedUI,state.ui,data.vocabulary.b1_stations.map(x=>x.node_id));
  const sameProcess=scene&&renderedTask===state.snapshot.task.task_id&&renderedUI?.panel==='process'&&state.ui.panel==='process';
  if(sameProcess){
    motion.cancel('node');const paper=scene.querySelector('.process-paper'),oldBody=paper.querySelector('[data-scroll-region="process-content"]');
    const rect=oldBody.getBoundingClientRect(),parent=paper.getBoundingClientRect();
    const newHead=next.querySelector('.pane-head'),newBody=next.querySelector('[data-scroll-region="process-content"]');
    Object.assign(oldBody.style,{position:'absolute',top:rect.top-parent.top+'px',left:rect.left-parent.left+'px',width:rect.width+'px',height:rect.height+'px'});
    motion.retire(oldBody);paper.querySelector('.pane-head').replaceChildren(...newHead.childNodes);paper.append(newBody);
    // Restore this node's body without moving the retained index/disclosure.
    const indexPosition=capturePosition(scene).regions['process-index'];
    const saved=!authored&&(restore||previousKey===positionKey(state.ui))?state.positions[positionKey(state.ui)]:null;
    restorePosition(scene,{version:2,regions:{...(saved?.regions||{}),'process-index':indexPosition}});
    motion.pair('node',oldBody,newBody,{...state.ui,direction,kind:'node',noAnimation:state.ui.noAnimation||!animate});
    motion.feedback(paper.querySelector('.pane-head'),'heading',{...state.ui,noAnimation:state.ui.noAnimation||!animate});
  }else{
    motion.cancel('node');motion.cancel('heading');motion.cancel('index');
    const previous=scene;
    // Remove names/IDs before insertion: radio groups must not affect the old form.
    if(previous){previous.removeAttribute('data-current-scene');motion.retire(previous);}
    scene=el('div',{class:'workspace-scene','data-current-scene':''},next);main.append(scene);
    if(previous)motion.pair('scene',previous,scene,{...state.ui,direction,kind:'scene',noAnimation:state.ui.noAnimation||!animate});
    restorePosition(scene,!authored&&(restore||previousKey===positionKey(state.ui))?state.positions[positionKey(state.ui)]:null);
  }
  if(restore&&previousKey!==positionKey(state.ui)&&location.hash)scrollToAnchor(scene,location.hash.slice(1));
  updateProcessIndex(animate&&sameProcess);
  renderedKey=positionKey(state.ui);renderedTask=state.snapshot.task.task_id;renderedUI={...state.ui};renderedFingerprint=fingerprint;
  document.title=headings[state.ui.panel]+' · FinResearch';refreshStatus();if(focus)main.focus({preventScroll:true});
}
function refreshStatus(){
  const s=state.snapshot,u=state.ui,cap=capabilities(s);
  visibleContext=context();
  workbenchQuestion.hidden=u.panel==='question';if(workbenchQuestion.textContent!==s.task.question){workbenchQuestion.textContent=s.task.question;workbenchQuestion.title=s.task.question;}
  const connection=main.closest('.page').querySelector('.preview-label');
  const disconnected=remote&&s.runtime.connection_state==='DISCONNECTED';
  connection.textContent=disconnected?'连接中断 · 保留记录':remote?'研究服务已连接':'离线预览 · 不执行研究';connection.classList.toggle('is-disconnected',disconnected);
  ribbon.hidden=u.panel==='question';statusSlot.hidden=u.panel==='question';
  const label=s.runtime.cancel_requested && s.task.task_status!=='CANCELLED'?'取消请求已受理 · 等待边界确认':s.task.task_status==='COMPLETED'&&!reportAvailable(s)?'任务已结束 · 报告未获准发布':data.vocabulary.task_status[s.task.task_status]||s.task.task_status;
  const issue=serviceNotice || (s.runtime.connection_state==='DISCONNECTED'?'连接已断开，保留最后记录':s.task.task_status==='WAITING_INPUT'?'等待资料决定':['FAILED','BLOCKED'].includes(s.task.task_status)?'任务无法继续':s.runtime.worker_state==='STOPPED'&&s.task.task_status==='RUNNING'?'执行器已停止':!reportAvailable(s)&&['INVALID','UNSUPPORTED'].includes(s.publication.verification)?'发布核验未通过':'');
  const actual=data.vocabulary.b1_stations.find(x=>x.node_id===currentStation(s));
  const line=el('div',{class:'workbench-status'},el('span',{class:'status-text'+(issue||s.task.task_status==='PARTIAL'?' partial':'')},u.panel==='question'?'准备研究':issue||label),el('span',{class:'small muted workbench-caption'},u.panel==='question'?'在当前资料范围内提出问题':s.task.task_status==='RUNNING'?'实际执行：'+(actual?.label||'当前补查节点'):nodeTour.pending()?'正在依次展示真实步骤记录':u.manual?'已固定当前阅读 · 状态仍同步':s.task.workflow+' · '+s.task.context.fiscal_years.join('—')+' 财年'));
  const actions=el('div',{class:'workbench-actions'});
  if(u.panel==='process'){const following=u.follow&&!u.manual;const follow=button(following?'暂停跟随':'继续跟随',()=>{dispatch({type:'FOLLOW',value:!following});urlState(true);if(state.ui.follow){nodeTour.resume();if(!checkAuto())paint({focus:true,restore:true});}else refreshStatus();},'text-button');follow.id='toggle-follow';actions.append(follow);}
  if(u.panel==='process' && reportAvailable(s)){const result=button('阅读报告',()=>navigate('report'),'secondary-button');result.id='open-report';actions.append(result);}
  if(u.panel!=='question'){const details=button(issue?'查看并处理':'任务详情',()=>taskDialog.openDrawer(),'secondary-button');details.id='open-task-details';actions.append(details);}
  if(remote && u.panel==='report' && cap.FOLLOWUP){const followup=button('追问已有记录',()=>followupDialog.openDrawer(),'secondary-button');followup.id='open-followup';actions.append(followup);}
  const focused=document.activeElement?.id;
  if(navigationPending){line.replaceChildren(el('span',{class:'small muted',role:'status'},'正在准备下一份研究画面…'));for(const b of actions.querySelectorAll('button'))b.disabled=true;}
  toolbar.replaceChildren(line,actions);toolbar.classList.toggle('has-issue',Boolean(issue)&&u.panel!=='question');
  if(['open-task-details','open-followup','toggle-follow','open-report'].includes(focused))document.getElementById(focused)?.focus({preventScroll:true});
  ribbon.replaceChildren(el('div',{class:'task-ribbon'+(u.panel==='report'?' is-report':'')},el('div',{},u.panel==='report'?null:questionTicket('p',{class:'task-question','data-question-ticket':''},ticket||s.task.question,{expanded:u.questionExpanded,onIntent:questionIntent}),el('p',{class:'task-meta small muted'},s.task.context.fiscal_years.join('—')+' 财年 · 截止 '+s.task.context.as_of_date+' · '+s.task.workflow+' · '+(s.task.access_mode==='MOCK'?'合成反馈':s.task.access_mode==='LIVE_CONTROL'?'真实执行 · '+s.task.execution_mode:'历史只读 · '+s.task.execution_mode))),el('span',{class:'status-text '+(['PARTIAL','WAITING_INPUT'].includes(s.task.task_status)?'partial':['FAILED','BLOCKED','CANCELLED'].includes(s.task.task_status)?'failed':'')},label)));
  if(lastStatus && label!==lastStatus && !u.noAnimation && !document.hidden)ribbon.querySelector('.status-text').animate?.([{opacity:.6},{opacity:1}],{duration:u.reduced||matchMedia('(prefers-reduced-motion: reduce)').matches?120:180});
  lastStatus=label;
  const row=el('div',{class:'follow-row'},el('span',{class:'small muted'},u.manual?'已固定当前阅读 · 后台状态仍同步':u.follow?'跟随状态更新 · 阅读原文时保留位置':'暂停自动定位 · 不暂停任务'));
  if(reportAvailable(s))row.append(button(u.panel==='report'?'返回研究过程':'结果已就绪 · 阅读报告',()=>navigate(u.panel==='report'?'process':'report'),'secondary-button'));
  row.hidden=u.panel==='report';
  const costOpen=Boolean(statusSlot.querySelector('.task-cost-details')?.open);
  statusSlot.replaceChildren(row);
  if(remote && u.panel==='process' && s.task.workflow==='B1'){
    const actual=data.vocabulary.b1_stations.find(x=>x.node_id===currentStation(s));
    statusSlot.append(el('p',{class:'execution-caption small'},s.task.task_status==='RUNNING'?'实际执行：'+(actual?.label||'准备当前节点')+(nodeTour.pending()?' · 页面依次展示真实步骤记录':''):reportAvailable(s)&&nodeTour.pending()?'研究已完成，报告已就绪 · 正在依次展示剩余步骤记录':'步骤来自本任务的实际执行记录'));
  }
  if(remote && serviceNotice)statusSlot.append(notice(serviceNotice,'warning'));
  if(s.runtime.connection_state==='DISCONNECTED')statusSlot.append(notice('连接已断开。任务状态未被改为失败；当前保留最后可核验画面，写操作暂不可用。','warning'));
  if(s.task.task_status==='WAITING_INPUT'){
    const decisions=el('div',{class:'decision-row'});
    for(const [id,text,op] of [['b2-wait-continue','保留局限继续','CLARIFY'],['b2-new-snapshot','需要新快照','CLARIFY'],[request.scenario,'核验后恢复','RESUME']]){const b=button(text+(remote?'':'（演示）'),()=>liveOperation(op,id),'secondary-button');b.disabled=operationBusy||!cap[op];if(remote)decisions.append(b);}
    statusSlot.append(el('section',{class:'decision-surface'},el('h2',{},'等待你的资料决定'),el('p',{},'当前等待缺口：'+(s.runtime.waiting_gap_ids.join('、')||'未提供独立缺口身份')),el('p',{class:'small muted'},s.runtime.clarification_status==='NEW_RUN_REQUIRED'?'需要新研究。旧任务仍等待，'+(remote?'本服务':'本演示')+'不自动创建资料快照。':s.runtime.clarification_status==='READY_TO_RESUME'?'决定已记录，任务尚未恢复。核验安全后才能继续。':'保留局限或另建范围；提交决定与恢复执行分开。'),decisions));
  }
  if(['FAILED','BLOCKED'].includes(s.task.task_status)){
    statusSlot.append(notice(s.runtime.resume_readiness==='UNSAFE'?'恢复核验不安全。保留现有预算与预留，不通过动画重试未知调用。':'当前任务无法继续。恢复需要协调层明确确认安全。','warning'));
    const resume=button(remote?'核验后恢复':'核验后恢复（演示）',()=>liveOperation('RESUME'),'secondary-button');resume.disabled=operationBusy||!remote||!cap.RESUME;if(remote)statusSlot.append(resume);
  }
  if(s.task.task_status==='CANCELLED')statusSlot.append(notice('已确认取消。迟到结果不会触发报告入口或完成动画。'));
  if(!reportAvailable(s) && ['INVALID','UNSUPPORTED'].includes(s.publication.verification))statusSlot.append(notice('发布核验未通过：'+(s.publication.reasons.join('；')||s.publication.verification)+'。正式报告保持不可用。','warning'));
  if(remote && s.task.access_mode==='LIVE_CONTROL' && !['COMPLETED','PARTIAL','CANCELLED'].includes(s.task.task_status)){const b=button(s.runtime.cancel_requested?'取消已受理，等待边界确认':'请求取消',()=>liveOperation('CANCEL'),'text-button');b.disabled=operationBusy||!cap.CANCEL;statusSlot.append(b);}
  if(remote && cap.RESUME && s.task.task_status==='CREATED'){statusSlot.append(button('启动已登记任务',()=>liveOperation('RESUME'),'secondary-button'));}
  if(remote && s.task.access_mode==='LIVE_CONTROL' && s.task.task_status==='RUNNING' && s.runtime.worker_state==='STOPPED'){
    statusSlot.append(notice('执行器已停止，任务保留最后检查点。恢复前需核验输入版本、调用记录和待定费用。','warning'));
    const resume=button('核验后恢复',()=>liveOperation('RESUME'),'secondary-button');resume.disabled=operationBusy||!cap.RESUME;if(remote)statusSlot.append(resume);
  }
  if(followupSubmit)followupSubmit.disabled=operationBusy||!cap.FOLLOWUP;
  if(s.budget.settled_estimate!==null || s.budget.pending_reservation!==null)statusSlot.append(el('details',{class:'research-settings task-cost-details',open:costOpen},el('summary',{},'费用与调用记录'),el('p',{class:'small muted budget-record'},'使用量估算（非账单）：已结算 '+(s.budget.settled_estimate??'未提供')+' USD · 待定预留 '+(s.budget.pending_reservation??'未提供')+' USD · 调用 '+(s.budget.total_calls??'未提供')+' 次')));
  live.textContent=label+(reportAvailable(s)?'；报告可阅读':'');folds.sync();
}
function buildFollowup(){
  followupInput=null;followupSubmit=null;
  if(remote)buildLiveFollowup();else followupSlot.replaceChildren();
}
function checkAuto({authored=true,restore=false}={}){
  if(!state || navigationPending || taskDialog?.open || followupDialog?.open || nodeTour.pending() || !canAutoReport(state))return false;if(authored)remember();dispatch({type:'NAVIGATE',panel:'report',manual:false});urlState(true);paint({focus:true,restore,authored,animate:authored});return true;
}
async function createLive(mode,value){
  if(creating||!remote||bootstrapPending||bootstrapError)return;
  const editor=document.querySelector('#question');
  if(!value.trim()){editor.setCustomValidity('请写下本次研究问题');editor.reportValidity();return;}
  const intent=navigationVersion;
  creating=true;const submit=scene.querySelector('#create-live'),label=submit.querySelector('span'),fieldset=scene.querySelector('.mode-selector');submit.disabled=true;submit.setAttribute('aria-busy','true');label.textContent='正在创建研究…';editor.readOnly=true;fieldset.disabled=true;draft=value;
  const execution='LIVE';
  const watchNodes=mode==='B1';
  const payload={schema_version:'1.0.0',question:value.trim(),workflow:mode,execution_mode:execution,...bootstrap.scope,baseline_task_id:mode==='B2'?document.querySelector('#baseline').value:null};
  try{
    payload.request_id=await requestId(payload);
    const result=await api('tasks',{body:payload,token:bootstrap.csrf_token});
    clearCreateRequest();serviceNotice='';
    if(intent!==navigationVersion){serviceNotice='研究已创建，可从已有研究查看。';refreshStatus();return;}
    ticket=value.trim();
    history.pushState(null,'',href('process',request.scenario,{task:result.task_id,source:'live'}));
    await load(false,{created:true,watchNodes});
  }catch(error){if(intent===navigationVersion){scene?.querySelector('#create-feedback')?.replaceChildren(notice(error.message,'warning'));if(error.status===403){try{const fresh=await api('bootstrap');if(intent===navigationVersion)bootstrap=fresh;}catch{}}}}
  finally{creating=false;if(scene?.contains(submit)){submit.disabled=false;submit.removeAttribute('aria-busy');label.textContent='开始研究';editor.readOnly=false;fieldset.disabled=false;}}
}
async function watch(version){
  if(version!==loadVersion||!remote)return;
  try{
    const result=await api('tasks/'+encodeURIComponent(state.snapshot.task.task_id));
    if(version!==loadVersion)return;
    const before=state.snapshot,after=result.snapshot;
    if(after.task.task_id!==before.task.task_id||after.runtime.event_stream_id!==before.runtime.event_stream_id||after.runtime.event_cursor<before.runtime.event_cursor)throw new Error('任务身份或源游标冲突，请重新核对来源。');
    serviceNotice='';
    if(JSON.stringify(before)!==JSON.stringify(after)){
      dispatch({type:'SOURCE',snapshot:after,events:result.events,revision:++frameRevision});
      if(after.runtime.cancel_requested||['FAILED','BLOCKED','CANCELLED','WAITING_INPUT'].includes(after.task.task_status))nodeTour.stop();
      cancelAnimationFrame(scheduled);scheduled=requestAnimationFrame(()=>{
        refreshStatus();updateProcessIndex(false);if(state.ui.panel==='question'||navigationPending)return;
        if(state.ui.panel==='report'&&!reportAvailable(state.snapshot)){paint();return;}
        const viewing=nodeTour.viewing();nodeTour.offer(state.snapshot);
        if(nodeTour.viewing() && viewing!==nodeTour.viewing())return;
        if(checkAuto())return;
        if(!taskDialog.open && !followupDialog.open && ![state.ui.reader,state.ui.selection,state.ui.editing,state.ui.manual].some(Boolean)){if(state.ui.follow && !nodeTour.pending())dispatch({type:'FOLLOW',value:true});paint({restore:true});}
      });
    }else nodeTour.offer(after);
  }catch(error){if(version!==loadVersion)return;serviceNotice=error.message;dispatch({type:'SOURCE',snapshot:{...state.snapshot,runtime:{...state.snapshot.runtime,connection_state:'DISCONNECTED'}},events:[],revision:++frameRevision});refreshStatus();}
  finally{if(version===loadVersion)pollTimer=setTimeout(()=>watch(version),1200);}
}
async function liveOperation(op,target){
  if(operationBusy||!capabilities(state.snapshot)[op])return;
  operationBusy=true;refreshStatus();const s=state.snapshot,version=loadVersion;
  const body={request_id:'operation-'+crypto.randomUUID(),expected_cursor:s.runtime.event_cursor};
  if(op==='CLARIFY')Object.assign(body,{gap_id:s.runtime.waiting_gap_ids[0],checkpoint_sha256:s.runtime.checkpoint_sha256,decision:target==='b2-new-snapshot'?'new_snapshot_required':'continue_with_limitations'});
  try{await api('tasks/'+encodeURIComponent(s.task.task_id)+'/'+op.toLowerCase(),{body,token:bootstrap.csrf_token});if(version!==loadVersion)return;serviceNotice=op==='CANCEL'?'取消已受理，等待执行器在安全边界确认。':op==='CLARIFY'?'决定已保存，恢复执行仍需单独操作。':'';}
  catch(error){if(version===loadVersion){serviceNotice=error.message;if(error.status===403){try{const fresh=await api('bootstrap');if(version===loadVersion)bootstrap=fresh;}catch{}}}}
  finally{operationBusy=false;refreshStatus();}
}
function buildLiveFollowup(){
  followupInput=el('textarea',{id:'followup',rows:'3',maxlength:'2000',placeholder:'围绕已审核记录追问，保留原文与未决问题。',value:state.ui.followupDraft});
  followupInput.addEventListener('focus',()=>dispatch({type:'UI',patch:{editing:true}}));followupInput.addEventListener('input',()=>dispatch({type:'UI',patch:{followupDraft:followupInput.value}}));
  followupInput.addEventListener('blur',()=>dispatch({type:'UI',patch:{editing:Boolean(followupInput.value.trim())}}));
  const topic=el('select',{'aria-label':'追问专题'},[['','从问题识别专题'],['revenue','收入'],['cash_flow','现金流'],['receivables','应收账款'],['all','全部已审核记录']].map(([value,label])=>el('option',{value},label)));
  followupOutput=el('div',{role:'status'});
  followupSubmit=button('读取已有记录回答',async()=>{
    if(operationBusy||!followupInput.value.trim())return;const version=loadVersion,taskId=state.snapshot.task.task_id;operationBusy=true;refreshStatus();
    try{const result=await api('tasks/'+encodeURIComponent(taskId)+'/followup',{body:{request_id:'turn-'+crypto.randomUUID(),question:followupInput.value,topic:topic.value||null},token:bootstrap.csrf_token});
      if(version!==loadVersion||taskId!==state.snapshot.task.task_id)return;
      dispatch({type:'UI',patch:{manual:true,editing:false,followupResult:result}});
      followupOutput.replaceChildren(notice(result.message),...(result.claims||[]).map(c=>el('div',{class:'paper-section'},el('p',{},c.text),button('查看依据',()=>reader.open([...(c.evidence_ids||[]),...(c.calculation_ids||[]),...(c.observation_ids||[])])))),...(result.claims?.length?[]:[notice(result.status==='NEEDS_CLARIFICATION'?'请选择追问专题。':'当前记录没有足够证据，保留未决问题。','warning')]));
      motion.feedback(followupOutput,'followup',state.ui);
    }catch(error){if(version===loadVersion)followupOutput.replaceChildren(notice(error.message,'warning'));}finally{operationBusy=false;refreshStatus();}
  },'secondary-button');
  followupSlot.replaceChildren(el('section',{class:'followup-editor'},el('label',{for:'followup'},'追问内容'),followupInput,el('p',{class:'small muted'},'复用已审核记录，不新增模型调用或因果结论。'),topic,followupSubmit,button('结束编辑，保留草稿',()=>followupDialog.closeDrawer(),'text-button'),followupOutput));
}
async function load(focus=false,{created=false,watchNodes=false}={}){
  remember();
  const version=++loadVersion,targetRequest=route(location.search);navigationPending=true;nodeTour.stop();clearTimeout(pollTimer);cancelAnimationFrame(scheduled);motion.cancel();reader.reset();
  if(main?.isConnected){main.inert=true;for(const dialog of [taskDialog,followupDialog])if(dialog.open)dialog.close();refreshStatus();}
  try{
    let loaded,nextBootstrap=bootstrap,nextRemote=remote;
    if(!targetRequest.demo){try{nextBootstrap=await api('bootstrap');nextRemote=true;}catch(error){if(targetRequest.source==='live')throw error;nextRemote=false;}}else nextRemote=false;
    if(version!==loadVersion)return;
    if(nextRemote){const result=await api('tasks/'+encodeURIComponent(targetRequest.task||nextBootstrap.default_task_id));const vocab=await fetch('./contracts/vocabulary.json',{cache:'no-store'});loaded={...result,vocabulary:await vocab.json()};}else loaded=await readFixture(targetRequest);
    if(version!==loadVersion)return;const identity=new URLSearchParams(location.search).get('task');if(identity && identity!==loaded.snapshot.task.task_id)throw new Error('地址中的任务身份与来源记录不一致，请重新选择任务。');
    request=targetRequest;bootstrap=nextBootstrap;bootstrapPending=false;bootstrapError='';remote=nextRemote;data=loaded;frameRevision=0;state=initialState(data.snapshot,request.view,readPreferences(localStorage,data.snapshot.task.task_id));if(request.step)state.ui.step=request.step;if(request.round)state.ui.round=Number(request.round);
    if(created){state.ui.manual=false;state.ui.follow=true;if(watchNodes){state.ui.step='validate_context';nodeTour.start(data.snapshot.task.task_id);}}else ticket=null;
    if(!state.ui.step && state.ui.follow)state.ui.step=currentStation(data.snapshot);
    shell();navigationPending=false;main.inert=false;buildFollowup();urlState(true);paint({focus:focus||created,restore:!created,authored:created});
    if(remote){if(data.events?.length)dispatch({type:'SOURCE',snapshot:data.snapshot,events:data.events,revision:++frameRevision});nodeTour.offer(state.snapshot);watch(version);}
    if(data.snapshot.ui.reader_open && data.snapshot.ui.selected_evidence_id && request.view!=='question')reader.open([data.snapshot.ui.selected_evidence_id]);
    else if(!created)checkAuto({authored:false,restore:true});
  }catch(error){if(version!==loadVersion)return;navigationPending=false;if(scene&&main?.isConnected){main.inert=false;serviceNotice='下一份画面读取失败，保留当前研究：'+error.message;urlState(true);refreshStatus();if(remote)watch(version);}else app.replaceChildren(el('main',{id:'main',class:'loading-surface'},el('h1',{},'研究工作台读取失败'),notice(error.message,'error'),el('a',{class:'secondary-button',href:href('question')},'返回研究工作台')));}
}
app.addEventListener('click',e=>{if(e.target.closest('.is-departing'))return;const a=e.target.closest('a');if(!a || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey)return;if(a.dataset.view){e.preventDefault();navigate(a.dataset.view);}else if(a.classList.contains('brand')){e.preventDefault();navigate('question');}else if(a.classList.contains('history-row')){e.preventDefault();const r=route(new URL(a.href).search);navigate(r.view,r.scenario,remote?{task:a.dataset.task}:{});}else if(a.getAttribute('href')?.startsWith('#') && state && scrollToAnchor(scene,a.hash.slice(1),{behavior:state.ui.reduced||matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth'})){e.preventDefault();dispatch({type:'UI',patch:{manual:true}});history.pushState(null,'',location.pathname+location.search+a.getAttribute('href'));refreshStatus();}});
document.addEventListener('selectionchange',()=>{if(!state)return;const selection=getSelection();const held=Boolean(selection && !selection.isCollapsed && scene?.contains(selection.anchorNode));if(held!==state.ui.selection){dispatch({type:'UI',patch:{selection:held}});if(!held){nodeTour.resume();checkAuto();}}});
function holdPaneReading(event){
  if(!state || state.ui.panel!=='process' || state.ui.manual)return;
  if(event.type==='keydown'&&!['ArrowUp','ArrowDown','ArrowLeft','ArrowRight','PageUp','PageDown','Home','End',' '].includes(event.key))return;
  const pane=event.target.closest?.('[data-scroll-region]');
  if(!pane || !scene.contains(pane) || pane.scrollHeight<=pane.clientHeight&&pane.scrollWidth<=pane.clientWidth)return;
  dispatch({type:'UI',patch:{manual:true}});refreshStatus();
}
app.addEventListener('wheel',holdPaneReading,{passive:true});app.addEventListener('touchmove',holdPaneReading,{passive:true});app.addEventListener('keydown',holdPaneReading);
app.addEventListener('toggle',event=>{if(state?.ui.panel==='process'&&event.target.open&&event.target.matches('.read-details')&&scene.contains(event.target)){dispatch({type:'UI',patch:{manual:true}});refreshStatus();}},true);
document.addEventListener('visibilitychange',()=>{if(!document.hidden)nodeTour.resume();});
window.addEventListener('resize',()=>{if(!state||!scene)return;updateNavigation(false);updateProcessIndex(false);});
matchMedia('(min-width: 769px)').addEventListener('change',event=>{for(const index of scene.querySelectorAll('details.process-index, details.scope-page'))index.open=event.matches;});
window.addEventListener('popstate',()=>{
  const target=route(location.search);
  if(state&&target.task===state.snapshot.task.task_id&&target.scenario===request.scenario&&target.frame===request.frame&&target.demo===request.demo&&(remote?target.source==='live':target.source!=='live')){
    navigate(target.view,target.scenario,{step:target.step,round:target.round},{fromHistory:true});
  }else{++navigationVersion;rememberDraft();load(true);}
});window.addEventListener('pagehide',remember);load();
