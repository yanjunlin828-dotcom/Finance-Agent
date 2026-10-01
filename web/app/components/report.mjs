import {el,button,notice,empty} from '../utils/dom.mjs';
import {financialTable,claimItem,gapList,publicationNotice} from './shared.mjs';
import {reportAvailable} from '../state/workspace.mjs';
import {questionTicket} from './question-ticket.mjs';
import {readingPane} from './pane.mjs';

export function report(ctx) {
  const s=ctx.snapshot;
  if (!reportAvailable(s)) return readingPane(el('section',{class:'paper report-paper'},
    el('h1',{},'报告尚不可用'),publicationNotice(ctx),empty('先保留当前阅读位置','任务终态不代表发布核验通过。这里不会显示未获准发布的报告内容。'),
    button('返回研究过程',()=>ctx.navigate('process',ctx.route.scenario),'secondary-button')),'h1','report-content','报告发布状态');
  const sections=[['findings','财务比较'],['interpretation','事实与解释'],['gaps','未决问题'],['source-report','完整记录']];
  const paper=el('article',{class:'paper report-paper'},
    questionTicket('h1',{class:'report-heading','data-question-ticket':''},ctx.ticket||s.task.question,{expanded:ctx.state.ui.questionExpanded,onIntent:ctx.onQuestionIntent}),
    el('div',{class:'report-meta'},el('span',{},'2023—2024 财年'),el('span',{},'资料截止 2025-04-30'),el('span',{},s.task.workflow==='B2'?'已有基线上的补查':'三家公司 · 合并口径')),
    publicationNotice(ctx),
    el('p',{class:'read-provenance'},(s.provenance.kind==='LIVE_PROJECTION'?'当前任务发布结果':s.provenance.kind==='SYNTHETIC'?'合成展示样本':'历史资料展示')+' · 发布核验不等于解释语义或因果已被独立认证。'),
    el('section',{id:'findings',class:'report-section'},el('h2',{},'先看财务事实'),
      el('p',{},'在同一资料范围内，分别比较收入变化、经营现金流与年末应收占比。计算事实与解释分开阅读。'),financialTable(ctx)));
  const interpretation=el('section',{id:'interpretation',class:'report-section'},el('h2',{},'事实、披露与候选解释'),
    el('p',{class:'muted'},'公司披露保留归属；候选解释保留局限。记录审核通过，不代表因果关系成立。'));
  for(const id of s.task.context.company_ids) {
    const claims=s.research.claims.filter(c=>c.company_id===id);
    const section=el('section',{class:'company-section'},el('h3',{},s.task.company_labels[id]||id,el('small',{},id)));
    const lead=claims.filter(c=>c.kind==='COMPUTED'&&c.status==='APPROVED').slice(0,2);
    const reading=claims.filter(c=>c.kind!=='COMPUTED').slice(0,2);
    section.append(...lead.map(c=>claimItem(c,ctx)),...reading.map(c=>claimItem(c,ctx)));
    const shown=new Set([...lead,...reading].map(c=>c.claim_id));
    const remaining=claims.filter(c=>!shown.has(c.claim_id));
    if(remaining.length) section.append(el('details',{class:'read-details'},el('summary',{},'阅读这家公司的其余 '+remaining.length+' 条记录'),remaining.map(c=>claimItem(c,ctx))));
    if(!claims.length) section.append(empty('未提供结论记录','当前样本不能支持这家公司的解释展示。'));
    interpretation.append(section);
  }
  const gapSource=s.research.supplement?.gaps.filter(g=>g.status!=='RESOLVED')||s.research.gaps;
  paper.append(interpretation,
    el('section',{id:'gaps',class:'report-section'},el('h2',{},'仍需保留的未决问题'),gapList(ctx,gapSource)),
    el('section',{id:'source-report',class:'report-section'},el('h2',{},'完整记录与来源'),
      el('p',{class:'muted'},'上方按阅读结构展示已有事实与结论；下方保留发布报告原文，未进行前端改写。'),
      el('details',{class:'read-details'},el('summary',{},'展开完整发布报告'),el('pre',{class:'raw-report'},s.research.report_markdown||'当前样本未保存报告正文。')),
      el('details',{class:'read-details'},el('summary',{},s.provenance.kind==='LIVE_PROJECTION'?'查看任务来源与发布核验':'查看样本来源与发布核验'),
        el('p',{class:'small muted'},s.provenance.label),el('p',{class:'small muted'},s.provenance.notes.join('；')),
        el('pre',{class:'raw-report'},JSON.stringify({task_id:s.task.task_id,publication:s.publication,source_artifacts:s.provenance.source_artifacts},null,2)))));
  return el('div',{class:'report-layout'},
    el('nav',{class:'report-index','data-scroll-region':'report-index',tabindex:'0','aria-label':'报告段落'},el('h3',{},'阅读目录'),sections.map(([id,label])=>el('a',{href:'#'+id},label))),
    readingPane(paper,'.report-heading, [data-fold-toggle], .report-meta','report-content','研究报告正文'));
}
