import {el} from '../utils/dom.mjs';

/** Stable heading with a separately focusable, bounded reading surface. */
export function readingPane(paper,headerSelector,region,label) {
  const headers=Array.from(paper.children).filter(node=>node.matches(headerSelector));
  const body=el('div',{class:'pane-body '+region,'data-scroll-region':region,tabindex:'0',role:'region','aria-label':label});
  body.append(...Array.from(paper.childNodes).filter(node=>!headers.includes(node)));
  const head=el('header',{class:'pane-head'},...headers);
  paper.replaceChildren(head,body);return paper;
}
