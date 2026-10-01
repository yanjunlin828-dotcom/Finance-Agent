import {el,button,icon,notice} from '../utils/dom.mjs';
import {dependencies} from '../adapters/fixture.mjs';
import {calculationText,observationText} from '../utils/decimal.mjs';
import {metricNames} from './shared.mjs';

/** Native dialog provides protected source reading, Escape, and inert background. */
export function createReader(getContext,{motion,onOpen=()=>{},onClose=()=>{}}={}) {
  const dialog=document.querySelector('#reader');
  dialog.className='reader';
  let opener=null;
  let animation=null,generation=0,silent=false;
  dialog.addEventListener('close',()=>{
    if(silent){silent=false;return;}
    if(opener?.isConnected)opener.focus({preventScroll:true});else document.querySelector('#main')?.focus({preventScroll:true});
    onClose();
  });
  function close(){
    if(!dialog.open)return;
    const id=++generation;animation?.cancel();
    const transition=motion?.reader(dialog,false,getContext().state.ui);animation=transition?.animation;
    Promise.resolve(transition?.finished).then(()=>{if(id===generation && dialog.open)dialog.close();});
  }
  dialog.addEventListener('cancel',e=>{e.preventDefault();close();});
  function reset(){generation++;animation?.cancel();animation=null;if(dialog.open){silent=true;dialog.close();}opener=null;}
  function open(ids) {
    const id=++generation;animation?.cancel();
    const ctx=getContext();
    const s=ctx.snapshot;
    const refs=dependencies(s.research,ids);
    if(!dialog.open)opener=document.activeElement;
    const title=refs.calculations.length?'计算依据与原文':refs.observations.length?'财务记录与原文':'年报原文';
    const header=el('header',{class:'reader-head'},el('div',{},el('h2',{id:'reader-title'},title),el('p',{},'关闭后返回刚才的阅读位置')),
      el('button',{type:'button',class:'icon-button','aria-label':'关闭证据阅读器',onclick:close},icon('close')));
    const body=el('div',{class:'reader-body',tabindex:'0',role:'region','aria-label':'原文与计算依据'});
    for(const c of refs.calculations) body.append(el('div',{class:'reader-dependency'},el('h3',{},'已保存的计算记录'),
      el('p',{class:'numeric'},calculationText(c).text),
      el('p',{class:'muted'},'公式：'+c.expression),el('p',{class:'muted'},'结果状态：'+c.status+' · 单位：'+c.output_unit),
      el('p',{class:'record-id'},c.calculation_id),
      el('details',{class:'read-details'},el('summary',{},'原始值与可比性检查'),el('pre',{class:'raw-report'},JSON.stringify(c,null,2)))));
    for(const o of refs.observations) body.append(el('div',{class:'reader-dependency'},
      el('h3',{},(s.task.company_labels[o.company_id]||o.company_id)+' · '+(metricNames[o.metric_id]||o.metric_id)),
      el('p',{class:'numeric'},observationText(o).text+(observationText(o).valid&&o.currency==='CNY'&&o.standard_unit==='CNY_YUAN'?' 元':'')),
      el('p',{class:'muted'},o.fiscal_year+' 财年 · '+(o.statement_scope==='CONSOLIDATED'?'合并口径':o.statement_scope)+' · '+(ctx.vocabulary.observation_status[o.value_status]||o.value_status)),
      el('p',{class:'muted'},'原文数值：'+(o.raw_value_text??'未提供')+' · 披露日期：'+(o.document_published_on??'未提供')),
      el('p',{class:'record-id'},o.observation_id)));
    if(refs.missing.length) body.append(notice('以下直接引用或依赖未保存在当前样本中。缺失不自动补配：'+refs.missing.join('；'),'warning'));
    if(!refs.evidence.length) body.append(notice('当前记录没有可直接回看的完整原文窗口；不以摘要或相似文字替代。','warning'));
    for(const e of refs.evidence) {
      const r=e.record;
      const doc=s.research.documents.find(d=>d.document_id===r.document_id);
      const text=el('div',{class:'source-window'});
      const highlights=refs.observations.filter(o=>o.document_id===r.document_id).map(o=>o.raw_value_text).filter(Boolean);
      // Literal source-text matches only; never infer a semantic quote span.
      const escaped=highlights.map(x=>x.replace(/[.*+?^${}()|[\]\\]/g,'\\$&'));
      if(escaped.length) {
        const pattern=new RegExp('('+escaped.join('|')+')','g');
        const pieces=r.text.split(pattern);
        for(const piece of pieces) text.append(highlights.includes(piece)?el('mark',{},piece):document.createTextNode(piece));
      } else text.textContent=r.text;
      body.append(el('section',{class:'reader-evidence'},
        el('h3',{},(s.task.company_labels[r.company_id]||r.company_id)+' · 年报'),
        el('div',{class:'reader-meta'},el('p',{},'PDF 第 '+r.pdf_page+' 页 · 原文窗口第 '+r.line_start+'—'+r.line_end+' 行'),
          el('p',{},'印刷页码：'+(e.printed_page??'当前记录未核验')),
          el('p',{},'披露日期：'+(doc?.published_on??'当前资料未提供'))),
        text,el('p',{class:'record-id'},r.document_id),
        ctx.remote&&doc?.pdf_access==='AVAILABLE'?el('a',{class:'secondary-button',target:'_blank',rel:'noopener',href:'/api/tasks/'+encodeURIComponent(s.task.task_id)+'/documents/'+encodeURIComponent(r.document_id)+'#page='+r.pdf_page},'打开年报 PDF · 物理第 '+r.pdf_page+' 页'):el('p',{class:'small muted'},'保留完整原文窗口及表头。PDF 在线定位尚未接入当前样本。')));
    }
    dialog.replaceChildren(header,body);
    if(!dialog.open)dialog.showModal();
    onOpen();
    const transition=motion?.reader(dialog,true,ctx.state.ui);animation=transition?.animation;
    Promise.resolve(transition?.finished).then(()=>{if(id===generation)animation=null;});
    body.scrollTop=0;
  }
  return {open,close,reset};
}
