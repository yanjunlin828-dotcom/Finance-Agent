import {el,button,icon} from '../utils/dom.mjs';
import {href} from '../adapters/fixture.mjs';
import {creationReadiness} from '../state/creation.mjs';

export function question(ctx) {
  const s = ctx.snapshot;
  const editor = el('textarea',{id:'question',class:'question-input','data-scroll-region':'question-input','data-question-ticket':'',maxlength:ctx.remote?'2000':'2400',placeholder:'描述希望比较的财务现象，以及需要查证的解释。',value:ctx.draft??s.task.question});
  const saveDraft=()=>{ctx.draft=editor.value;ctx.onDraft?.(editor.value);};
  editor.addEventListener('input',()=>{saveDraft();editor.setCustomValidity('');});
  const baseline = el('div',{class:'baseline-choice',hidden:true},
    el('label',{for:'baseline'},'复用的研究基线'),
    el('select',{id:'baseline'},...(ctx.remote?ctx.bootstrap.tasks.filter(t=>t.workflow==='B1'&&t.execution_mode==='LIVE'&&t.report_available&&t.source_revalidation==='VERIFIED').map(t=>el('option',{value:t.task_id},(t.question||'已完成研究')+' · '+t.task_id.slice(-6))): [el('option',{value:'s4-b1-live-20260930-05'},'已有研究资料')])),
    el('p',{class:'small muted'},'补查在已有基线上处理缺口，不会重新执行 B1。'));
  let mode = 'B1';
  const modes = el('fieldset',{class:'mode-selector'},el('legend',{},'研究方式'),
    el('div',{class:'mode-options'},...[
      ['B1','比较与解释','核对财务、整理披露与候选解释'],
      ['B2','在已有研究上补查','围绕未决问题查找当前资料']
    ].map(([id,title,note])=>el('label',{class:'mode-option'},
      el('input',{type:'radio',name:'mode',value:id,checked:id==='B1',onchange:()=>{mode=id;baseline.hidden=id!=='B2';}}),
      el('span',{},el('strong',{},title),el('small',{},note))))),baseline);
  const paper = el('section',{class:'paper question-paper','aria-labelledby':'question-heading'},
    el('h1',{id:'question-heading'},'提出一个研究问题'),
    el('p',{class:'section-intro'},'从财务差异出发，沿着年报依据理解公司披露。'),
    el('label',{class:'question-label',for:'question'},'这次想弄清什么？'),editor,
    el('div',{class:'example-links'},el('span',{class:'muted'},'试试：'),
      button('收入与现金流的差异',()=>{editor.value='比较三家公司的收入与经营现金流，哪些差异有年报依据，哪些解释仍需核验？';saveDraft();editor.focus();}),
      button('应收账款的变化',()=>{editor.value='比较三家公司的年末应收账款与收入变化，并区分计算事实、公司披露和候选解释。';saveDraft();editor.focus();})),
    modes,
    ctx.remote?el('details',{class:'research-settings'},el('summary',{},'模型与费用'),
      el('p',{id:'creation-model',class:'small muted'},'研究模型：'+ctx.bootstrap.model),
      el('p',{id:'creation-budget-note',class:'small muted'}),
      el('p',{class:'small muted'},'费用为使用量估算，未确认调用继续保留预留。')):null,
    ctx.remote?el('p',{id:'creation-readiness',class:'small',role:'status'}):el('p',{class:'small muted'},'当前为离线资料预览，连接本机研究服务后可启动研究。'),
    el('div',{id:'create-feedback',role:'status'}),
    el('div',{class:'editor-actions'},el('p',{},ctx.remote?'使用本次资料范围进行研究。':'当前仅可阅读已保存资料。'),
      (()=>{const b=button([el('span',{},'开始研究'),icon()],()=>ctx.createResearch(mode,editor.value),'primary-button');b.id='create-live';b.disabled=!ctx.remote;return b;})()));
  const head=el('header',{class:'question-head'},paper.querySelector('h1'),paper.querySelector('.section-intro'));
  const actions=paper.querySelector('.editor-actions'),feedback=paper.querySelector('#create-feedback');
  const readiness=feedback.previousElementSibling;
  const footer=el('footer',{class:'question-actions'},el('div',{class:'creation-status',tabindex:'0','aria-label':'研究启动状态'},readiness,feedback),actions);
  const form=el('div',{class:'question-form pane-body','data-scroll-region':'question-form',tabindex:'0',role:'region','aria-label':'研究问题与设置'},...Array.from(paper.childNodes));
  paper.replaceChildren(head,form,footer);
  let sync=()=>{};
  if(ctx.remote){
    sync=()=>{
      const ready=creationReadiness(ctx.bootstrap,mode,'LIVE',Boolean(baseline.querySelector('option'))),submit=paper.querySelector('#create-live');
      submit.disabled=Boolean(ctx.bootstrapPending||ctx.bootstrapError||submit.hasAttribute('aria-busy')||!ready.ready);
      paper.querySelector('#creation-readiness').textContent=ctx.bootstrapPending?'正在更新研究设置，可先编辑问题…':ctx.bootstrapError?'研究设置更新失败，请点击“提出问题”重试。':ready.ready?'':ready.message;
      paper.querySelector('#creation-budget-note').textContent='本任务上限 '+ctx.bootstrap.task_caps_usd[mode]+' USD · 本轮可用 '+ctx.bootstrap.remaining_authorized_usd+' USD · 累计授权 '+ctx.bootstrap.authorized_total_usd+' USD';
    };
    for(const radio of modes.querySelectorAll('input[name="mode"]'))radio.addEventListener('change',sync);
    sync();
  }
  const scope = el('details',{class:'scope-page',open:matchMedia('(min-width: 769px)').matches},el('summary',{},'资料范围与已有研究'),el('aside',{class:'scope-body','data-scroll-region':'scope',tabindex:'0','aria-label':'资料范围与已有研究'},
    el('h2',{},'本次资料范围'),
    el('dl',{class:'scope-list'},
      el('div',{},el('dt',{},'公司'),el('dd',{},...Object.entries(s.task.company_labels).map(([id,label])=>el('div',{class:'scope-company'},label,el('small',{class:'numeric'},id))))),
      el('div',{},el('dt',{},'财年'),el('dd',{class:'numeric'},'2023、2024')),
      el('div',{},el('dt',{},'资料截止'),el('dd',{class:'numeric'},'2025-04-30')),
      el('div',{},el('dt',{},'关注指标'),el('dd',{},'营业收入、经营活动现金流量净额、年末应收账款')),
      el('div',{},el('dt',{},'资料集'),el('dd',{},'半导体设备年报 · 固定快照',el('div',{class:'record-id'},'s3-semiconductor-equipment-ar-v1')))),
    el('p',{class:'scope-note'},'目前仅使用既有年报资料与已审核财务记录。超出公司、年度或截止日的要求，需要另建研究范围。'),
    el('section',{class:'history-block'},el('h3',{},ctx.remote?'已有研究':'已有研究样本'),
      ...(ctx.remote?ctx.bootstrap.tasks.filter(t=>t.execution_mode==='LIVE').slice(0,12).map(t=>el('a',{class:'history-row','data-task':t.task_id,href:href(t.report_available?'report':'process','history-b1',{task:t.task_id,source:'live'})},el('strong',{},t.question||'已有研究'),el('small',{},(t.workflow==='B2'?'补充研究':'比较研究')+' · '+(ctx.vocabulary.task_status[t.status]||'状态待核对')))):[
      el('a',{class:'history-row',href:href('report','history-b1')},el('strong',{},'三家公司财务比较'),el('small',{},'B1 · 已发布记录 · 只读')),
      el('a',{class:'history-row',href:href('report','history-b2')},el('strong',{},'解释缺口的进一步补查'),el('small',{},'B2 · 部分成果 · 保留局限'))]))));
  const layout=el('div',{class:'question-layout'},paper,scope);
  // Refresh settings in place: the user's draft, selected mode and scroll stay put.
  layout.updateBootstrap=(fresh,{pending=false,error=''}={})=>{
    if(!ctx.remote)return;
    const changed=fresh!==ctx.bootstrap;
    ctx.bootstrap=fresh;ctx.bootstrapPending=pending;ctx.bootstrapError=error;
    if(changed){
      const select=baseline.querySelector('select'),selected=select.value;
      select.replaceChildren(...fresh.tasks.filter(t=>t.workflow==='B1'&&t.execution_mode==='LIVE'&&t.report_available&&t.source_revalidation==='VERIFIED').map(t=>el('option',{value:t.task_id},(t.question||'已完成研究')+' · '+t.task_id.slice(-6))));
      if(fresh.tasks.some(t=>t.task_id===selected)&&Array.from(select.children).some(t=>t.value===selected))select.value=selected;
      paper.querySelector('#creation-model').textContent='研究模型：'+fresh.model;
      const history=scope.querySelector('.history-block'),title=history.querySelector('h3');
      history.replaceChildren(title,...fresh.tasks.filter(t=>t.execution_mode==='LIVE').slice(0,12).map(t=>el('a',{class:'history-row','data-task':t.task_id,href:href(t.report_available?'report':'process','history-b1',{task:t.task_id,source:'live'})},el('strong',{},t.question||'已有研究'),el('small',{},(t.workflow==='B2'?'补充研究':'比较研究')+' · '+(ctx.vocabulary.task_status[t.status]||'状态待核对')))));
    }
    sync();
  };
  return layout;
}
