/** Display readiness only. Server enforces the grant inside its creation transaction. */
export function creationReadiness(bootstrap,workflow,execution,hasBaseline=true){
  if(workflow==='B2'&&!hasBaseline)return {ready:false,message:'先完成一项与当前版本兼容的 B1 研究，再进行补查。'};
  if(execution!=='LIVE')return {ready:true,message:'此方式不新增模型调用。'};
  if(!(bootstrap.model_ready??bootstrap.live_enabled))return {ready:false,message:'服务端模型尚未就绪，请检查模型配置。'};
  const remaining=Number(bootstrap.remaining_authorized_usd);
  const cap=Number(bootstrap.task_caps_usd?.[workflow]);
  if(!Number.isFinite(remaining)||!Number.isFinite(cap)||cap<=0||remaining+1e-12<cap)return {ready:false,message:'当前可用授权不足以覆盖本任务上限，未创建或调用模型。'};
  return {ready:true,message:'模型与授权已就绪，可开始真实研究。'};
}
