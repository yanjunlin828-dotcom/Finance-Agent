import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {sanitizePosition,capturePosition,restorePosition,revealInPane,scrollToAnchor} from '../../web/app/utils/scroll.mjs';
import {positionKey,initialState,reduce,readingPreferences,readPreferences} from '../../web/app/state/workspace.mjs';

function pane(name,{top=0,left=0,height=200,length=1000,width=400,contentWidth=800,y=50}={}) {
  return {dataset:{scrollRegion:name},scrollTop:top,scrollLeft:left,clientHeight:height,scrollHeight:length,clientWidth:width,scrollWidth:contentWidth,clientTop:2,
    getBoundingClientRect:()=>({top:y}),contains:node=>node.owner===name,scrollTo(options){this.scrollTop=options.top;this.lastScroll=options;}};
}
const surface=panes=>({querySelectorAll:()=>panes});

test('window-cache migration never turns an old whole-page y into a pane offset',()=>{
  assert.equal(sanitizePosition({y:800,anchor:'gaps'}),null);
  const body=pane('report-content',{top:800});restorePosition(surface([body]),{y:640});assert.equal(body.scrollTop,0);
});
test('allowlist rejects unknown panes, malformed offsets and unsafe anchor IDs',()=>{
  assert.deepEqual(sanitizePosition({version:2,regions:{'report-content':{top:1e9,left:Infinity},scope:{top:-1},unknown:{top:200}},anchor:'gaps"]'}),{version:2,regions:{'report-content':{top:1000000,left:0}},anchor:null});
});
test('each pane keeps its own vertical and horizontal position; shorter data clamps safely',()=>{
  const index=pane('process-index',{top:180,left:4}),body=pane('process-content',{top:730,left:300});
  const saved=capturePosition(surface([index,body,pane('untrusted',{top:10})]));
  const nextIndex=pane('process-index'),nextBody=pane('process-content',{length:450,contentWidth:500});
  restorePosition(surface([nextIndex,nextBody]),saved);
  assert.equal(nextIndex.scrollTop,180);assert.equal(nextIndex.scrollLeft,4);assert.equal(nextBody.scrollTop,250);assert.equal(nextBody.scrollLeft,100);
});
test('panel positions are independent of stale node/round selections; process positions are per node/round',()=>{
  assert.equal(positionKey({panel:'report',step:'calculate',round:2}),'report::');
  assert.equal(positionKey({panel:'question',step:'calculate',round:2}),'question::');
  assert.notEqual(positionKey({panel:'process',step:'calculate',round:1}),positionKey({panel:'process',step:'calculate',round:2}));
});
test('source changes preserve region positions; local storage remains task-isolated and content-free',()=>{
  const frames=JSON.parse(fs.readFileSync(new URL('../../web/fixtures/b1-normal.json',import.meta.url))).frames;
  let state=initialState(frames[1].snapshot,'process');
  const position={version:2,regions:{'process-content':{top:500,left:80}},anchor:null};
  state=reduce(state,{type:'POSITION',key:'process:calculate:',position});state=reduce(state,{type:'SOURCE',snapshot:frames[2].snapshot,revision:1});
  const storage={getItem:()=>JSON.stringify(readingPreferences(state))};
  assert.deepEqual(readPreferences(storage,state.snapshot.task.task_id).positions['process:calculate:'],position);
  assert.deepEqual(readPreferences(storage,'another-task'),{});
  assert.equal(JSON.stringify(readingPreferences(state)).includes(state.snapshot.task.question),false);
});
test('list following scrolls only the list by the minimum needed amount',()=>{
  const list=pane('process-index',{top:100});
  const target={owner:'process-index',getBoundingClientRect:()=>({top:330,bottom:380})};
  assert.equal(revealInPane(list,target,{align:'nearest'}),true);assert.equal(list.scrollTop,228);
  assert.deepEqual(list.lastScroll,{top:228,behavior:'instant'});
});
test('a visible step does not reset the list position',()=>{
  const list=pane('process-index',{top:100});
  revealInPane(list,{owner:'process-index',getBoundingClientRect:()=>({top:70,bottom:110})},{align:'nearest'});
  assert.equal(list.scrollTop,100);assert.equal(list.lastScroll,undefined);
});
test('anchor navigation stays in the report, never resolves a dialog or global ID',()=>{
  const article=pane('report-content',{top:100});
  const target={id:'gaps',owner:'report-content',getBoundingClientRect:()=>({top:352,bottom:450})};
  article.querySelectorAll=()=>[target];const root={querySelector:()=>article};
  assert.equal(scrollToAnchor(root,'gaps',{behavior:'smooth'}),true);assert.equal(article.scrollTop,400);assert.equal(article.lastScroll.behavior,'smooth');
  assert.equal(scrollToAnchor(root,'reader-title'),false);assert.equal(article.scrollTop,400);
});
test('collapsed lists and foreign targets cannot move any ancestors',()=>{
  const collapsed=pane('process-index',{height:0});assert.equal(revealInPane(collapsed,{owner:'process-index'}),false);
  assert.equal(revealInPane(pane('process-index'),{owner:'report-content'}),false);
});
test('targets beyond the last screen stop at the actual scroll boundary',()=>{
  const article=pane('report-content',{top:600});
  revealInPane(article,{owner:'report-content',getBoundingClientRect:()=>({top:700,bottom:740})});assert.equal(article.scrollTop,800);
});
