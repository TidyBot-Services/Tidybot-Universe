// controlFetch must refresh a stale control token for both 403 shapes the servers use.
const assert=require('assert'),fs=require('fs'),path=require('path'),vm=require('vm');
const source=fs.readFileSync(path.join(__dirname,'..','ui','live.js'),'utf8');
const start=source.indexOf('async function controlFetch'),end=source.indexOf('function connectionState');
assert(start>0&&end>start,'controlFetch not found');
async function run(rejection){
  const sent=[],root={dataset:{controlToken:'old'}};
  const reply=(status,value)=>({status,ok:status<400,url:'http://x/ui/',clone(){return this;},json:async()=>value,text:async()=>'<div id="attentionbench-clear" data-mode="live" data-control-token="new"></div>'});
  const context=vm.createContext({root,location:{origin:'http://x'},URL,
    DOMParser:class{parseFromString(){return {querySelector:()=>({dataset:{controlToken:'new'}})};}},
    fetch:async(url,options)=>{
      if(url==='/ui/')return reply(200,null);
      sent.push(options.headers['X-Attention-Token']);
      return sent.length===1?reply(403,{error:rejection}):reply(200,{ok:true});
    }});
  vm.runInContext(source.slice(start,end)+';this.controlFetch=controlFetch;',context);
  const response=await context.controlFetch('/api/provider/credentials/k',{method:'PUT',headers:{}});
  return {status:response.status,sent,token:root.dataset.controlToken};
}
(async()=>{
  for(const rejection of ['control token required',{code:'control_token_required',message:'Control token required'}]){
    const result=await run(rejection);
    assert.deepStrictEqual(result,{status:200,sent:['old','new'],token:'new'},JSON.stringify(rejection));
  }
  const other=await run({code:'provider_body_invalid'});
  assert.deepStrictEqual(other.sent,['old'],'business 403 must not retry');
  console.log('control_fetch token refresh: ok');
})();
