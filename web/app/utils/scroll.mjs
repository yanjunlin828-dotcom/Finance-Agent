/** Only named panes scroll. Version 2 deliberately ignores old window offsets. */
export const scrollRegions = new Set(['question-form','question-input','scope','process-index','process-content','financial-table','report-index','report-content']);

export function sanitizePosition(value) {
  if(value?.version!==2)return null;
  const regions={};
  for(const [name,p] of Object.entries(value.regions||{})) {
    if(!scrollRegions.has(name)||!Number.isFinite(p?.top)||p.top<0)continue;
    regions[name]={top:Math.min(p.top,1000000),left:Number.isFinite(p.left)&&p.left>=0?Math.min(p.left,1000000):0};
  }
  return {version:2,regions,anchor:typeof value.anchor==='string'&&/^[a-z-]+$/.test(value.anchor)?value.anchor:null};
}
export function capturePosition(root,anchor=null) {
  const regions={};
  for(const pane of root.querySelectorAll('[data-scroll-region]')) {
    if(scrollRegions.has(pane.dataset.scrollRegion))regions[pane.dataset.scrollRegion]={top:pane.scrollTop,left:pane.scrollLeft};
  }
  return sanitizePosition({version:2,regions,anchor});
}
export function restorePosition(root,value) {
  const position=sanitizePosition(value);
  for(const pane of root.querySelectorAll('[data-scroll-region]')) {
    const saved=position?.regions[pane.dataset.scrollRegion];
    pane.scrollTop=Math.min(saved?.top||0,Math.max(0,pane.scrollHeight-pane.clientHeight));
    pane.scrollLeft=Math.min(saved?.left||0,Math.max(0,pane.scrollWidth-pane.clientWidth));
  }
}
/** Move just this container, never scrollIntoView (which also moves ancestors). */
export function revealInPane(pane,target,{align='start',behavior='instant'}={}) {
  if(!pane||!target||!pane.contains(target)||!pane.clientHeight)return false;
  const viewport=pane.getBoundingClientRect(),rect=target.getBoundingClientRect();
  const start=viewport.top+(pane.clientTop||0),end=start+pane.clientHeight;
  if(align==='nearest'&&rect.top>=start&&rect.bottom<=end)return true;
  const offset=align==='nearest'&&rect.top>=start?rect.bottom-end:rect.top-start;
  const top=Math.min(Math.max(0,pane.scrollTop+offset),Math.max(0,pane.scrollHeight-pane.clientHeight));
  pane.scrollTo({top,behavior});return true;
}
export function scrollToAnchor(root,id,options={}) {
  // Restrict lookup to this scene: neither a dialog nor another pane may jump.
  const pane=root.querySelector('[data-scroll-region="report-content"]');
  const target=pane&&Array.from(pane.querySelectorAll('[id]')).find(node=>node.id===id);
  return revealInPane(pane,target,options);
}
