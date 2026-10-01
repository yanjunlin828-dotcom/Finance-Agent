export const views = ['question', 'process', 'report'];
export function route(search) {
  const params = new URLSearchParams(search);
  const requestedFrame = params.get('frame') || '0';
  return {
    view: views.includes(params.get('view')) ? params.get('view') : 'question',
    scenario: params.get('scenario') || 'history-b1',
    frame: /^\d+$/.test(requestedFrame) ? Number(requestedFrame) : 0,
    step: params.get('step'), round: params.get('round'), task:params.get('task'), source:params.get('source'), demo:params.get('demo')==='1'
  };
}

export function href(view, scenario = 'history-b1', extras = {}) {
  return '?' + new URLSearchParams({view, scenario, ...extras}).toString();
}

export async function readFixture(request) {
  const manifestResponse = await fetch('./fixtures/manifest.json', {cache:'no-store'});
  if (!manifestResponse.ok) throw new Error('展示样本目录读取失败');
  const manifest = await manifestResponse.json();
  const entry = manifest.fixtures.find(item => item.scenario_id === request.scenario);
  if (!entry || !/^[a-z0-9-]+\.json$/.test(entry.path)) throw new Error('没有找到这个展示样本，请返回默认样本。');
  const response = await fetch('./fixtures/' + entry.path, {cache:'no-store'});
  if (!response.ok) throw new Error('展示样本文件读取失败，请刷新或返回默认样本。');
  const fixture = await response.json();
  if (!fixture.frames?.[request.frame]) throw new Error('这个样本没有所选的画面。');
  const snapshot = fixture.frames[request.frame].snapshot;
  if (snapshot.schema_version !== '1.0.0') throw new Error('展示合同版本不受支持。');
  const vocabularyResponse = await fetch('./contracts/vocabulary.json', {cache:'no-store'});
  if (!vocabularyResponse.ok) throw new Error('展示词汇读取失败');
  return {manifest, fixture, snapshot, vocabulary: await vocabularyResponse.json()};
}

/** Exact IDs only. Traverse calculations for citations without guessing associations. */
export function dependencies(research, ids) {
  const calculations = new Map(research.calculations.map(x => [x.calculation_id,x]));
  const observations = new Map(research.observations.map(x => [x.observation_id,x]));
  const evidence = new Map(research.evidence.map(x => [x.record.evidence_id,x]));
  const result = {calculations:[], observations:[], evidence:[], missing:[]};
  const visited = new Set();
  function visit(id) {
    if (visited.has(id)) return;
    visited.add(id);
    if (calculations.has(id)) { const c = calculations.get(id); result.calculations.push(c); c.input_ids.forEach(visit); }
    else if (observations.has(id)) { const o = observations.get(id); result.observations.push(o); o.evidence_refs.forEach(r => visit(r.evidence_id)); }
    else if (evidence.has(id)) result.evidence.push(evidence.get(id));
    else result.missing.push(id);
  }
  ids.forEach(visit); return result;
}
