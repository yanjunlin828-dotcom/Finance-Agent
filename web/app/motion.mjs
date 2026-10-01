/** Independent channels: telemetry cannot cancel scenes, nodes or overlays. */
export function createMotion({document:doc=globalThis.document,media=()=>globalThis.matchMedia?.('(prefers-reduced-motion: reduce)').matches||false,styles=globalThis.getComputedStyle}={}) {
  const channels=new Map();
  const easing='cubic-bezier(.16,1,.3,1)';
  function cancel(channel) {
    const names=channel?[channel]:[...channels.keys()];
    for(const name of names){const run=channels.get(name);if(!run)continue;channels.delete(name);run.animations.forEach(a=>a.cancel());run.cleanup();}
  }
  function run(channel,entries,cleanup=()=>{},options={}) {
    cancel(channel);
    let cleaned=false;
    const finish=()=>{if(!cleaned){cleaned=true;cleanup();}};
    if(options.noAnimation || doc?.hidden || entries.some(([node])=>!node?.animate)){finish();return {finished:Promise.resolve(),animation:null};}
    const animations=[];
    try {for(const [node,frames,timing] of entries){const a=node.animate(frames,{easing,fill:'both',...timing});a.finished.catch(()=>{});animations.push(a);}}
    catch {animations.forEach(a=>a.cancel());finish();return {finished:Promise.resolve(),animation:null};}
    const token={animations,cleanup:finish};channels.set(channel,token);
    const finished=Promise.allSettled(animations.map(a=>a.finished)).then(()=>{
      if(channels.get(channel)!==token)return;
      channels.delete(channel);animations.forEach(a=>a.cancel());finish();
    });
    return {finished,animation:{cancel:()=>{if(channels.get(channel)===token)cancel(channel);}}};
  }
  function reduced(options){return options.reduced||media();}
  /** ID/name removal prevents an outgoing form or anchor from shadowing the live scene. */
  function retire(node) {
    node.inert=true;node.setAttribute('aria-hidden','true');node.classList.add('is-departing');
    for(const item of [node,...node.querySelectorAll('*')]){
      for(const name of ['id','name','data-view','data-question-ticket','data-fold-text','data-fold-toggle','data-scroll-region'])item.removeAttribute(name);
      if(item.matches('a,button,input,textarea,select,summary,[tabindex]'))item.setAttribute('tabindex','-1');
    }
  }
  function pair(channel,oldNode,newNode,{direction=1,kind='scene',...options}={}) {
    const pose=oldNode&&channels.has(channel)?styles?.(oldNode):null;
    const sampled=pose?{opacity:Number(pose.opacity),transform:pose.transform}:null;
    cancel(channel);
    if(oldNode)retire(oldNode);
    const gentle=reduced(options),duration=gentle?120:kind==='scene'?460:280;
    const enter=gentle?[{opacity:.35},{opacity:1}]:kind==='scene'?
      [{opacity:.25,transform:`perspective(1200px) translateX(${direction*24}px) rotateY(${-direction*2.5}deg)`},{opacity:1,transform:'perspective(1200px) translateX(0) rotateY(0)'}]:
      [{opacity:.25,transform:`translateY(${direction*10}px)`},{opacity:1,transform:'translateY(0)'}];
    const exit=gentle?[{opacity:1},{opacity:0}]:kind==='scene'?
      [{opacity:1,transform:'perspective(1200px) translateX(0) rotateY(0)'},{opacity:0,transform:`perspective(1200px) translateX(${-direction*18}px) rotateY(${direction*2}deg)`}]:
      [{opacity:1,transform:'translateY(0)'},{opacity:0,transform:`translateY(${-direction*6}px)`}];
    if(sampled&&Number.isFinite(sampled.opacity)){exit[0].opacity=sampled.opacity;if(!gentle)exit[0].transform=sampled.transform;}
    return run(channel,[...(oldNode?[[oldNode,exit,{duration:gentle?120:kind==='scene'?320:200}]]:[]),[newNode,enter,{duration}]],()=>oldNode?.remove(),options);
  }
  function flip(node,previous,channel,options={}) {
    const next=node?.getBoundingClientRect();if(!node||!previous||!next?.width||!next.height)return;
    const gentle=reduced(options),dx=previous.left-next.left,dy=previous.top-next.top;
    return run(channel,[[node,gentle?[{opacity:.6},{opacity:1}]:[
      {transform:`translate(${dx}px,${dy}px) scale(${previous.width/next.width},${previous.height/next.height})`},{transform:'translate(0,0) scale(1,1)'}
    ],{duration:gentle?120:220}]],()=>{},options);
  }
  function feedback(node,channel='feedback',options={}) {
    return run(channel,[[node,[{opacity:.45},{opacity:1}],{duration:reduced(options)?120:160}]],()=>{},options);
  }
  function reader(node,opening,options={}) {
    const gentle=reduced(options);
    node.classList.toggle('is-closing',!opening);
    return run('overlay:'+node.id,[[node,gentle?[{opacity:opening?.4:1},{opacity:opening?1:0}]:
      opening?[{transform:'translateX(32px)',opacity:.5},{transform:'translateX(0)',opacity:1}]:[{transform:'translateX(0)',opacity:1},{transform:'translateX(24px)',opacity:0}],{duration:gentle?120:opening?260:180}]],()=>{},options);
  }
  const visibility=()=>{if(doc?.hidden)cancel();};
  doc?.addEventListener('visibilitychange',visibility);
  return {pair,flip,feedback,reader,cancel,retire,active:()=>[...channels.keys()],dispose:()=>{cancel();doc?.removeEventListener('visibilitychange',visibility);}};
}
