import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {question} from '../../web/app/components/question.mjs';
import {process} from '../../web/app/components/process.mjs';
import {report} from '../../web/app/components/report.mjs';
import {initialState,reportAvailable} from '../../web/app/state/workspace.mjs';

// Minimal tree model for component construction, NOT browser/visual acceptance.
// It models move semantics so wrapping cannot duplicate or lose source nodes.
class TreeNode {
  constructor(tag,text=''){this.tag=tag;this.childNodes=[];this.attrs={};this.parent=null;this.className='';this.text=text;this.dataset={};}
  get children(){return this.childNodes.filter(node=>node.tag!=='#text');}
  get textContent(){return this.text+this.childNodes.map(node=>node.textContent).join('');}
  set textContent(value){this.replaceChildren();this.text=String(value);}
  get previousElementSibling(){const siblings=this.parent?.children||[];return siblings[siblings.indexOf(this)-1];}
  get nextElementSibling(){const siblings=this.parent?.children||[];return siblings[siblings.indexOf(this)+1];}
  get classList(){return {add:value=>{this.className+=' '+value;},contains:value=>this.className.split(' ').includes(value)};}
  setAttribute(key,value){this.attrs[key]=value;if(key==='id')this.id=value;}
  hasAttribute(key){return key in this.attrs || key.startsWith('data-')&&key.slice(5).replace(/-([a-z])/g,(_,x)=>x.toUpperCase()) in this.dataset;}
  addEventListener(){}
  append(...nodes){for(const value of nodes){const node=value instanceof TreeNode?value:new TreeNode('#text',String(value));if(node.parent)node.parent.childNodes.splice(node.parent.childNodes.indexOf(node),1);node.parent=this;this.childNodes.push(node);}}
  prepend(node){this.append(node);this.childNodes.unshift(this.childNodes.pop());}
  replaceChildren(...nodes){for(const node of this.childNodes)node.parent=null;this.childNodes=[];this.text='';this.append(...nodes);}
  matches(selectors){return selectors.split(',').some(raw=>{const selector=raw.trim();if(selector.startsWith('.'))return this.classList.contains(selector.slice(1));if(selector.startsWith('#'))return this.id===selector.slice(1);const match=/^([a-z][a-z0-9]*)?(?:\[([\w-]+)(?:="([^"]*)")?\])?$/.exec(selector);return Boolean(match&&(!match[1]||match[1]===this.tag)&&(!match[2]||(match[3]===undefined?this.hasAttribute(match[2]):this.attrs[match[2]]===match[3])));});}
  querySelectorAll(selector){return this.children.flatMap(node=>[...(node.matches(selector)?[node]:[]),...node.querySelectorAll(selector)]);}
  querySelector(selector){return this.querySelectorAll(selector)[0]||null;}
}
globalThis.Node=TreeNode;
globalThis.document={createElement:tag=>new TreeNode(tag),createTextNode:text=>new TreeNode('#text',text),createElementNS:(_,tag)=>new TreeNode(tag)};
globalThis.matchMedia=()=>({matches:true});
const directory=new URL('../../web/fixtures/',import.meta.url);
const vocabulary=JSON.parse(fs.readFileSync(new URL('../../web/contracts/vocabulary.json',import.meta.url)));
const fixtures=JSON.parse(fs.readFileSync(new URL('manifest.json',directory))).fixtures;
function context(snapshot){return {snapshot,vocabulary,state:initialState(snapshot,'process'),route:{scenario:'history-b1',step:'calculate'},remote:false,navigate(){},openRecord(){},createResearch(){}};}

test('every B1/B2 fixture constructs bounded content; headings remain outside the scrolling body',()=>{
  for(const entry of fixtures)for(const frame of JSON.parse(fs.readFileSync(new URL(entry.scenario_id+'.json',directory))).frames){
    const ctx=context(frame.snapshot);
    for(const node of frame.snapshot.task.workflow==='B1'?vocabulary.b1_stations:[{node_id:null}]){
      ctx.route.step=node.node_id;const view=process(ctx),body=view.querySelector('[data-scroll-region="process-content"]');
      assert.ok(body,entry.scenario_id);assert.equal(body.attrs.tabindex,'0');assert.equal(body.attrs.role,'region');assert.equal(body.querySelector('h1'),null);
      assert.ok(body.parent.querySelector('.pane-head').querySelector('h1'));assert.ok(view.querySelector('[data-scroll-region="process-index"]'));
    }
  }
});
test('all report gates render with a bounded body; valid title folding stays next to its toggle',()=>{
  for(const entry of fixtures)for(const frame of JSON.parse(fs.readFileSync(new URL(entry.scenario_id+'.json',directory))).frames){
    const ctx=context(frame.snapshot),view=report(ctx),body=view.querySelector('[data-scroll-region="report-content"]');assert.ok(body);assert.equal(body.querySelector('h1'),null);
    if(reportAvailable(ctx.snapshot)){const title=view.querySelector('h1');assert.ok(title.nextElementSibling.hasAttribute('data-fold-toggle'));assert.equal(title.parent,body.parent.querySelector('.pane-head'));assert.ok(view.querySelector('[data-scroll-region="report-index"]'));}
    else assert.equal(view.querySelector('#findings'),null);
  }
});
test('question keeps the start action and creation errors outside the form scroll area',()=>{
  const snapshot=JSON.parse(fs.readFileSync(new URL('history-b1.json',directory))).frames[0].snapshot;
  const ctx=context(snapshot);ctx.remote=true;ctx.bootstrap={tasks:[],model:'configured',model_ready:true,remaining_authorized_usd:'0.02',authorized_total_usd:'0.30',task_caps_usd:{B1:'0.10',B2:'0.05'}};
  const view=question(ctx),form=view.querySelector('[data-scroll-region="question-form"]'),footer=view.querySelector('.question-actions');
  assert.ok(form.querySelector('#question'));assert.equal(form.querySelector('#create-live'),null);assert.ok(footer.querySelector('#create-feedback'));assert.equal(footer.querySelector('#create-live').disabled,true);assert.ok(footer.textContent.includes('授权不足'));assert.ok(view.querySelector('[data-scroll-region="scope"]'));
});
