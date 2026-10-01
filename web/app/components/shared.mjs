import {el,button,notice} from '../utils/dom.mjs';
import {calculationText,observationText} from '../utils/decimal.mjs';
import {dependencies} from '../adapters/fixture.mjs';

export const metricNames = {revenue:'营业收入', operating_cash_flow_net:'经营活动现金流量净额', accounts_receivable:'年末应收账款'};
export const kindNames = {COMPUTED:'计算事实',DISCLOSED:'公司披露',INFERENCE:'候选解释',UNRESOLVED:'未决解释'};
export const claimStatus = {APPROVED:'记录审核通过',REJECTED:'记录未通过审核',REVIEW_REQUIRED:'需要复核'};

export function calculationList(ctx) {
  const roles={revenue_growth:'营业收入增长率',operating_cash_flow_net_growth:'经营现金流增长率',accounts_receivable_growth:'年末应收账款增长率',ar_revenue_ratio_2023:'2023年末应收 / 收入',ar_revenue_ratio_2024:'2024年末应收 / 收入',ocf_revenue_ratio_2023:'2023年经营现金流 / 收入',ocf_revenue_ratio_2024:'2024年经营现金流 / 收入',revenue_ocf_growth_gap_pp:'收入与经营现金流增长差'};
  return el('details',{class:'read-details'},el('summary',{},'查看完整计算记录与无效原因'),ctx.snapshot.research.calculations.map(c=>{
    const slot=Object.entries(ctx.snapshot.research.calculation_slots[c.company_id]||{}).find(([,id])=>id===c.calculation_id)?.[0];
    const value=calculationText(c);
    return el('div',{class:'calculation-row'},el('div',{},el('p',{},(ctx.snapshot.task.company_labels[c.company_id]||c.company_id)+' · '+(roles[slot]||'附加样本计算')),
      el('p',{class:'small muted'},ctx.vocabulary.calculation_status[c.status]||c.status)),
      button(value.text,()=>ctx.openRecord([c.calculation_id]),value.valid?'number-button':'text-button'));
  }));
}

export function financialTable(ctx) {
  const {snapshot:s,openRecord} = ctx;
  const r = s.research;
  if (!r.observations.length) return notice('当前画面尚无财务记录。界面不会把未返回的数值显示为零。');
  const heads = ['公司','2023收入 / 亿元','2024收入 / 亿元','收入增长率','现金流 / 收入¹','应收 / 收入²'];
  const table = el('table',{class:'financial-table'}, el('caption',{},'合并报表 · 2023—2024年度 · 点击数值查看记录与原文'),
    el('thead',{},el('tr',{},heads.map(text => el('th',{scope:'col'},text)))));
  const body = el('tbody');
  for (const id of s.task.context.company_ids) {
    const slots = r.calculation_slots[id] || {};
    const values = [2023,2024].map(year => {
      const obs = r.observations.find(o=>o.company_id===id && o.metric_id==='revenue' && o.fiscal_year===year);
      return {record:obs,id:obs?.observation_id,...observationText(obs,true)};
    });
    for (const role of ['revenue_growth','ocf_revenue_ratio_2024','ar_revenue_ratio_2024']) {
      const calc = r.calculations.find(c=>c.calculation_id===slots[role]);
      values.push({record:calc,id:calc?.calculation_id,...calculationText(calc)});
    }
    body.append(el('tr',{},el('td',{},el('strong',{},s.task.company_labels[id]||id),el('small',{},id)), values.map(v => el('td',{},
      v.valid ? button(v.text,()=>openRecord([v.id]),'number-button') : el('span',{class:'invalid-value'},v.text)))));
  }
  table.append(body);
  return el('div',{},el('div',{class:'table-scroll','data-scroll-region':'financial-table',tabindex:'0',role:'region','aria-label':'财务比较表，可横向滚动'},table),
    el('p',{class:'table-note'},'¹ 2024年经营活动现金流量净额 / 营业收入。² 2024年末应收账款 / 当年营业收入，属于存量与流量比率，不是周转率。金额仅为展示换算；原始精度保留在记录中。'));
}

export function claimItem(claim, ctx) {
  const ids = [...claim.calculation_ids,...claim.observation_ids,...claim.evidence_ids];
  const refs = dependencies(ctx.snapshot.research, ids);
  const missingDirect = claim.kind === 'DISCLOSED' && claim.evidence_ids.length === 0;
  const item = el('div',{class:'claim-item'},
    el('div',{class:'claim-labels'},el('span',{class:'kind'},kindNames[claim.kind]||claim.kind),el('span',{},claimStatus[claim.status]||claim.status)),
    el('p',{},claim.text));
  if (claim.limitations.length) item.append(el('p',{class:'claim-limitation'},claim.limitations.join('；')));
  if (missingDirect || refs.missing.length) item.append(el('p',{class:'claim-limitation'},'当前记录缺少直接引用或部分依赖；不自动匹配相似原文。'));
  if (ids.length) item.append(el('div',{class:'source-actions'},button(refs.evidence.length ? '查看依据与原文' : '查看依赖记录',()=>ctx.openRecord(ids))));
  return item;
}

export function gapList(ctx, gaps = ctx.snapshot.research.gaps) {
  if (!gaps.length) return notice('当前画面未提供未决问题记录；这不等于研究已证明所有解释。');
  return el('div',{},gaps.map(g=>el('div',{class:'gap-item'},
    el('div',{class:'claim-labels'},el('span',{},ctx.snapshot.task.company_labels[g.company_id]||g.company_id||'研究范围'),
      el('span',{},ctx.vocabulary.gap_status_labels[g.status] || (g.status==='UNRESOLVED'?'尚未解决':g.status))),
    el('p',{},g.description), (g.resolution_note||g.suggested_next_step) ? el('p',{class:'small'},g.resolution_note||g.suggested_next_step) : null)));
}

export function publicationNotice(ctx) {
  const {snapshot:s} = ctx;
  if (!s.publication.result_available) return notice('报告尚不可用：' + (s.publication.reasons.join('；') || '尚未取得可阅读的发布记录。'),'warning');
  if (s.task.task_status === 'PARTIAL') return notice('部分成果 · 仍有未决问题。当前报告保留已取得的记录；补查停止不代表因果解释已经成立。','warning');
  return null;
}
