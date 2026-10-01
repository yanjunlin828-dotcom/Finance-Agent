/** A presentation queue of actual node records. It never delays source state or executor work. */
export function createNodeTour({canPresent,onFrame,onIdle,dwell=850,initialDelay=550,clock={setTimeout:(f,ms)=>setTimeout(f,ms),clearTimeout:id=>clearTimeout(id)}}){
  let task=null,enabled=false,seen=new Set(),queue=[],timer=null,current=null,first=true;
  function clear(){if(timer!==null)clock.clearTimeout(timer);timer=null;queue=[];current=null;seen.clear();enabled=false;task=null;}
  function next(){
    timer=null;current=null;
    if(!enabled)return;
    if(!canPresent())return;
    const step=queue.shift();
    if(!step){onIdle();return;}
    current=step;onFrame(step);
    timer=clock.setTimeout(next,dwell);
  }
  function resume(){if(enabled&&timer===null&&queue.length&&canPresent()){if(first){first=false;timer=clock.setTimeout(next,initialDelay);}else next();}}
  return {
    start(id){clear();task=id;enabled=true;first=true;},stop:clear,resume,
    offer(snapshot){
      if(!enabled||snapshot.task.task_id!==task||snapshot.task.origin!=='CURRENT_EXECUTION'||snapshot.task.workflow!=='B1')return;
      for(const step of snapshot.steps){
        if(step.identity_kind!=='EXECUTION'||!step.occurred_at||seen.has(step.instance_id))continue;
        seen.add(step.instance_id);queue.push(step);
      }
      resume();
    },
    pending:()=>enabled&&Boolean(queue.length||current),
    viewing:()=>enabled?current?.node_id||null:null
  };
}
