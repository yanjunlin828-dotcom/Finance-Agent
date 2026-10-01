import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {decimalText, calculationText, observationText} from '../../web/app/utils/decimal.mjs';
import {dependencies,route,href} from '../../web/app/adapters/fixture.mjs';

test('large exact decimal values retain precision past IEEE 754',()=>{
  assert.equal(decimalText('9007199254740993.125'), '9,007,199,254,740,993.13');
});
test('display yuan in hundred millions without floating arithmetic',()=>{
  assert.equal(decimalText('29838069162.26',{shift:-8}), '298.38');
});
test('ratio multiplier is display only',()=>{
  assert.equal(calculationText({status:'VALID',value:'0.3514',output_unit:'RATIO'}).text,'35.14%');
});
test('percentage points are not multiplied a second time',()=>{
  assert.equal(calculationText({status:'VALID',value:'12.345',output_unit:'PERCENTAGE_POINT'}).text,'12.35 个百分点');
});
test('round negative half magnitudes and suppress negative zero',()=>{
  assert.equal(decimalText('-0.005'),'-0.01');
  assert.equal(decimalText('-0.00001'),'0.00');
});
for(const status of ['MISSING_INPUT','ZERO_DENOMINATOR','NEGATIVE_BASE','INCOMPARABLE']) {
  test(status+' does not invent a numeric result',()=>{
    assert.equal(calculationText({status,value:null,output_unit:'RATIO'}).valid,false);
    assert.notEqual(calculationText({status,value:null,output_unit:'RATIO'}).text,'0.00%');
  });
}
test('reject floating values and exponent notation rather than silently convert',()=>{
  assert.equal(decimalText(0.1),'—');
  assert.equal(decimalText('1e6'),'—');
});
test('unknown observation differs from a real zero',()=>{
  assert.equal(observationText({value_status:'OBSERVED',standard_value:'0'}).text,'0.00');
  assert.equal(observationText({value_status:'MISSING',standard_value:null}).valid,false);
});
test('review-required observations do not acquire numeric authority',()=>{
  assert.equal(observationText({value_status:'REVIEW_REQUIRED',standard_value:'123'}).valid,false);
});
test('amount display cannot silently relabel another currency or unit',()=>{
  assert.equal(observationText({value_status:'OBSERVED',standard_value:'123',currency:'USD',standard_unit:'CNY_YUAN'},true).valid,false);
});
test('transitive calculation dependencies and missing direct IDs remain visible',()=>{
  const r={calculations:[{calculation_id:'a',input_ids:['b']},{calculation_id:'b',input_ids:['a','o','absent']}],observations:[{observation_id:'o',evidence_refs:[{evidence_id:'e'}]}],evidence:[{record:{evidence_id:'e'}}]};
  const d=dependencies(r,['a','e']);
  assert.equal(d.calculations.length,2);assert.equal(d.observations.length,1);assert.equal(d.evidence.length,1);assert.deepEqual(d.missing,['absent']);
});
test('B2 revenue observations reach their literal original-text references',()=>{
  const s=JSON.parse(readFileSync(new URL('../../web/fixtures/history-b2.json',import.meta.url))).frames[0].snapshot;
  const obs=s.research.observations.find(o=>o.metric_id==='revenue'&&o.fiscal_year===2024);
  const d=dependencies(s.research,[obs.observation_id]);
  assert.equal(d.missing.length,0);assert.equal(d.evidence.length,1);
  assert.ok(d.evidence[0].record.text.includes(obs.raw_value_text));
});
test('B1 missing direct financial reference is not repaired by fuzzy matching',()=>{
  const s=JSON.parse(readFileSync(new URL('../../web/fixtures/history-b1.json',import.meta.url))).frames[0].snapshot;
  const obs=s.research.observations.find(o=>o.metric_id==='revenue'&&o.fiscal_year===2024);
  const d=dependencies(s.research,[obs.observation_id]);
  assert.ok(d.missing.includes(obs.evidence_refs[0].evidence_id));
  assert.equal(d.evidence.length,0);
});
test('source without round attribution stays without attribution',()=>{
  const s=JSON.parse(readFileSync(new URL('../../web/fixtures/history-b2.json',import.meta.url))).frames[0].snapshot;
  assert.equal(s.research.supplement.rounds,3);assert.equal(s.research.supplement.history.length,6);
  assert.ok(s.research.supplement.history.every(h=>h.action.round===undefined));
});
test('URL defaults and encoded parameters do not create implicit task control',()=>{
  assert.equal(route('?view=unknown&frame=-3').view,'question');
  assert.equal(route('?frame=-3').frame,0);
  assert.equal(new URLSearchParams(href('report','history-b1',{step:'a&b'})).get('step'),'a&b');
});
