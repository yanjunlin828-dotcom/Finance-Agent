import {el,button,notice,empty} from '../utils/dom.mjs';
import {financialTable,calculationList,claimItem,gapList} from './shared.mjs';
import {currentStation,reportAvailable} from '../state/workspace.mjs';
import {readingPane} from './pane.mjs';

export function process(ctx) {
  return ctx.snapshot.task.workflow === 'B2' ? supplement(ctx) : baseline(ctx);
}

function evidenceList(ctx) {
  if (!ctx.snapshot.research.evidence.length) return empty('没有原文记录','这个画面尚未返回可阅读的原文窗口。');
  return el('div',{},ctx.snapshot.research.evidence.map(e=>el('div',{class:'evidence-entry'},
    button(`${ctx.snapshot.task.company_labels[e.record.company_id]||e.record.company_id} · PDF 第 ${e.record.pdf_page} 页`,()=>ctx.openRecord([e.record.evidence_id])),
    el('p',{class:'evidence-preview'},e.record.text.slice(0,100)+'…'))));
}

function baseline(ctx) {
  const {snapshot:s,vocabulary:v} = ctx;
  const selected = v.b1_stations.some(x=>x.node_id===ctx.route.step) ? ctx.route.step : 'load_financials';
  const groups = [...new Set(v.b1_stations.map(x=>x.group))];
  const index = el('details',{class:'process-index',open:matchMedia('(min-width: 769px)').matches},
    el('summary',{},'研究路径 · 九个步骤'),
    el('div',{class:'process-index-body','data-scroll-region':'process-index',tabindex:'0',role:'region','aria-label':'研究步骤列表'},el('div',{class:'station-cursor','aria-hidden':'true'}),groups.map(group=>el('section',{class:'station-group'},el('h3',{},group),
      el('ol',{class:'station-list'},v.b1_stations.filter(x=>x.group===group).map(station=>{
        const n=v.b1_stations.indexOf(station)+1;
        return el('li',{},el('button',{type:'button',class:'station-button','data-node':station.node_id,'aria-pressed':station.node_id===selected?'true':'false',onclick:()=>ctx.navigate('process',ctx.route.scenario,{step:station.node_id})},
          el('span',{class:'station-number'},String(n).padStart(2,'0')),station.label,
          currentStation(s)===station.node_id && s.task.task_status==='RUNNING' ? el('span',{class:'running-marker'},'执行中') : s.steps.some(x=>x.node_id===station.node_id&&x.lifecycle==='FUNCTION_RETURNED')?el('span',{class:'completed-marker'},'已返回'):null));
      }))))));
  const station=v.b1_stations.find(x=>x.node_id===selected);
  const heading=el('div',{class:'process-heading'},el('h1',{},station.label));
  const main = el('section',{class:'paper process-paper'},heading);
  const step = [...s.steps].reverse().find(x=>x.node_id===selected);
  if(ctx.watchingNodes)main.prepend(el('p',{class:'node-view-label'},'正在观看真实步骤 · '+(v.b1_stations.indexOf(station)+1)+' / 9'));
  if (!step) main.append(notice(s.publication.result_available ? '当前记录未保存该步骤的独立成果。下方回看发布记录中的相关内容，不代表该步骤的独立执行历史。' : '当前记录未保存该步骤的独立成果。仅展示本画面已有数据，不补造节点输出。'));
  else main.append(notice((step.lifecycle==='RUNNING'?'该步骤正在实际执行。':'该步骤已返回，正在回看本任务的真实记录。')+' 成果接纳：'+step.acceptance+'。下方展示当前已保存内容。'));
  if (selected==='validate_context') main.append(el('div',{class:'paper-section'},el('h2',{},'记录中的研究范围'),
    el('p',{},Object.values(s.task.company_labels).join('、')),
    el('p',{class:'muted'},s.task.context.fiscal_years.join(' / ')+' 财年 · 资料截止 '+s.task.context.as_of_date),
    el('p',{class:'read-provenance'},'固定快照：'+s.task.context.corpus_snapshot_id)));
  else if (['load_financials','calculate'].includes(selected)) main.append(el('div',{class:'paper-section'},el('h2',{},'财务记录比较'),financialTable(ctx),selected==='calculate'?calculationList(ctx):null));
  else if (selected==='collect_fixed_evidence') main.append(el('div',{class:'paper-section'},el('h2',{},'年报原文窗口'),evidenceList(ctx)));
  else if (selected==='review_claims') main.append(el('div',{class:'paper-section'},el('h2',{},'保留的未决问题'),gapList(ctx)));
  else if (['detect_phenomena','propose_hypotheses'].includes(selected)) {
    const kinds=selected==='detect_phenomena'?['COMPUTED']:['INFERENCE','UNRESOLVED'];
    const claims=s.research.claims.filter(c=>kinds.includes(c.kind));
    main.append(el('div',{class:'paper-section'},el('h2',{},selected==='detect_phenomena'?'记录中的计算事实':'记录中的候选解释'),
      claims.length ? claims.map(c=>claimItem(c,ctx)) : empty('尚无相关成果','当前画面没有该类结论，不能从其他样本补造。')));
  } else main.append(el('div',{class:'paper-section'},el('h2',{},'报告发布记录'),
    el('p',{},s.publication.result_available?'已有可阅读的发布记录。发布核验仅验证记录与范围，不证明候选解释的因果关系。':'当前没有可阅读的报告。'),
    el('p',{class:'read-provenance'},'发布核验：'+s.publication.verification),
    reportAvailable(s)?button('打开文章式报告',()=>ctx.navigate('report',ctx.route.scenario),'secondary-button'):null));
  return el('div',{class:'process-layout'},index,readingPane(main,'.process-heading, .node-view-label','process-content','当前研究步骤正文'));
}

function supplement(ctx) {
  const {snapshot:s,vocabulary:v} = ctx;
  const supplement=s.research.supplement;
  const count=supplement?.rounds||0;
  const selected=Math.min(count, Math.max(1,Number(ctx.route.round)||count));
  const side=el('details',{class:'process-index',open:matchMedia('(min-width: 769px)').matches},el('summary',{},'补查路径'),el('div',{class:'process-index-body','data-scroll-region':'process-index',tabindex:'0',role:'region','aria-label':'补查路径索引'},el('h3',{},'补查路径'),
    el('div',{class:'scope-note'},'基线来自已有研究',el('p',{class:'record-id'},s.task.baseline?.label||'当前样本未提供基线说明')),
    el('ol',{class:'station-list'},v.b2_stations.map((st,i)=>el('li',{},el('div',{class:'station-button'},el('span',{class:'station-number'},i+1),st.label)))),
    el('p',{class:'read-provenance'},'路径是阅读索引，不是当前执行进度。')));
  const paper=el('section',{class:'paper process-paper'},
    el('div',{class:'process-heading'},el('h1',{},'围绕缺口，进一步补查')),
    el('p',{class:'section-intro'},'在已有基线上查看保留的轮次总览、动作与工具返回。定位原文，仍需保留解释的边界。'));
  if (!supplement) {paper.append(empty('尚无补查轮次','这个画面没有提供工具动作记录。'));return el('div',{class:'process-layout'},side,readingPane(paper,'.process-heading','process-content','补查记录正文'));}
  if (supplement.stop_reason) paper.append(notice('补查停止：'+(v.stop_reason[supplement.stop_reason]||supplement.stop_reason),'warning'));
  paper.append(el('div',{class:'round-selector','aria-label':'选择补查轮次'},...Array.from({length:count},(_,i)=>el('button',{type:'button','aria-pressed':selected===i+1?'true':'false',onclick:()=>ctx.navigate('process',ctx.route.scenario,{round:String(i+1)})},'第 '+(i+1)+' 轮'))));
  // Core action records do not carry a round number. Never infer two actions/round.
  paper.append(notice('第 '+selected+' 轮的独立映射未保存。当前记录共 '+count+' 轮、'+supplement.action_count+' 个动作；下方保留完整列表，不按轮次猜分。'));
  const history=supplement.history;
  paper.append(el('section',{class:'paper-section'},el('h2',{},'已保存的工具动作')));
  if (!history.length) paper.append(empty('尚无独立动作记录','不根据总轮数或最终报告推算工具结果。'));
  for (const h of history) {
    const gap=supplement.gaps.find(g=>g.gap_id===h.action.gap_id);
    const tools={QUERY_METRIC:'查询已审核财务指标',SEARCH_DISCLOSURE:'查找公司披露',VERIFY_QUOTE:'核对引用窗口',RECOMPUTE:'重新计算指标'};
    const title = tools[h.action.tool]||h.action.tool_name||h.action.tool||h.action.action_type||'补查动作';
    const text = h.action.reason||h.action.rationale||gap?.description||'当前动作未提供独立理由。';
    paper.append(el('section',{class:'round-action'},el('h3',{},title),el('p',{},text),
      el('p',{class:'read-provenance'},'工具返回：'+({FOUND:'已找到记录',NOT_FOUND:'未找到',REVIEW_REQUIRED:'需要复核',REQUIRES_NEW_SNAPSHOT:'需要新快照',FAILED:'执行失败'}[h.result?.status]||h.result?.status||'未保存')+' · 缺口状态：'+(v.gap_status_labels[h.changed_gap_status]||h.changed_gap_status||'无变化记录')),
      gap?el('p',{class:'small muted'},gap.resolution_note||gap.description):null,
      el('details',{class:'read-details'},el('summary',{},'查看完整动作与工具返回'),el('pre',{class:'raw-report'},JSON.stringify(h,null,2)))));
  }
  paper.append(el('section',{class:'paper-section'},el('h2',{},'本次仍保留的局限'),gapList(ctx,supplement.gaps.filter(g=>g.status!=='RESOLVED'))));
  return el('div',{class:'process-layout'},side,readingPane(paper,'.process-heading','process-content','补查记录正文'));
}
