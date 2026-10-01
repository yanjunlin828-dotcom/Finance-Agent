import {el,button} from '../utils/dom.mjs';

/** Keep full text in the heading; folding depends on actual layout, not length. */
export function questionTicket(tag,attrs,text,{expanded=false,onIntent=()=>{}}={}) {
  const node=el(tag,{...attrs,class:(attrs.class||'')+(expanded?' question-expanded':''),'data-fold-text':''},text);
  const toggle=button(expanded?'收起研究问题':'展开完整研究问题',()=>{
    const open=node.classList.toggle('question-expanded');
    toggle.setAttribute('aria-expanded',String(open));toggle.textContent=open?'收起研究问题':'展开完整研究问题';onIntent(open);
  },'text-button question-fold-toggle');
  toggle.hidden=true;toggle.dataset.foldToggle='';toggle.setAttribute('aria-expanded',String(expanded));
  return [node,toggle];
}

/** One observer for all ticket elements; remove detached nodes on each paint. */
export function createQuestionFolds() {
  const watched=new Set();
  function update(node) {
    const toggle=node.nextElementSibling;
    if(!toggle?.hasAttribute('data-fold-toggle'))return;
    toggle.hidden=!node.classList.contains('question-expanded') && node.scrollHeight<=node.clientHeight+1;
  }
  const observer=typeof ResizeObserver==='function'?new ResizeObserver(entries=>entries.forEach(e=>update(e.target))):null;
  function sync() {
    for(const node of watched)if(!node.isConnected){observer?.unobserve(node);watched.delete(node);}
    for(const node of document.querySelectorAll('[data-fold-text]'))if(!watched.has(node)){watched.add(node);observer?.observe(node);}
    requestAnimationFrame(()=>watched.forEach(update));
  }
  if(!observer)window.addEventListener('resize',sync);
  return {sync,dispose:()=>{observer?.disconnect();window.removeEventListener('resize',sync);watched.clear();}};
}
