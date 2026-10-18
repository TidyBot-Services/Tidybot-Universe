"""Simulated evidence only. Never counted as real model/native acceptance."""
import json,threading
from pathlib import Path
from benchmarks.attention_harness.dev_loop import immutable
from benchmarks.attention_harness.parcc_client import ParccResponse
class Client:
    def __init__(self,invalid_first=False):self.inputs=[];self.response_observer=None;self.invalid_first=invalid_first
    def chat(self,**kw):
        self.inputs.append(kw)
        content='not json' if self.invalid_first and len(self.inputs)==1 else json.dumps({'source':'from robot_sdk import sensors, left_arm\nobservation=sensors.get_observation()\nleft_arm.move_delta(0,0,0.01)\n' if len(self.inputs)==1 else 'from robot_sdk import sensors, left_arm\nobservation=sensors.get_observation()\nleft_arm.move_delta(0,0,0.02)\n','reason':'Repair public failure and attempt task'})
        usage={'total_tokens':100,'prompt_tokens':40,'completion_tokens':60}
        body={'model':'simulated/GLM','choices':[{'message':{'content':content}}],'usage':usage}
        if self.response_observer:self.response_observer(200,{},json.dumps(body).encode())
        return ParccResponse('simulated/GLM',content,None,.01,1,usage)
class Runner:
    native_success=False
    def __init__(self,**kw):self.cancel_event=threading.Event();self.kw=kw;self.suite='robocasa365'
    def execute(self,r):
        ep=r.artifact_root/'simulated';ep.mkdir(parents=True)
        if self.kw.get('episode_observer'):self.kw['episode_observer'](str(ep))
        ids={'run_id':r.run_id,'attempt_id':r.attempt_id}
        binding={'suite':r.suite,'task_id':r.task_id,'seed':r.seed,'policy_sha256':r.policy_sha256,'config_sha256':r.config_sha256,**ids}
        native={**ids,'status':'failed','evaluated':True,'native_success':self.native_success}
        trace={**binding,'status':'failed','sdk_events':[{'source':'left_arm','operation':'move_delta','status':'failed','arguments':{'dz':.01},'error':{'type':'RuntimeError','message':'IK planning rejected'}}],'error':'IK planning rejected'}
        rec={**ids,'elapsed_seconds':.01,'control_steps':2,'sdk_call_budget':{'limit':r.sdk_call_limit,'requested':2,'dispatched':2}}
        artifacts={n:immutable(ep/(n+'.json'),v) for n,v in [('native_result',native),('trace',trace),('sandbox_receipt',rec),('safety',ids)]}
        return {'schema_version':'attentionbench.formal-runner-result.v1',**binding,'status':'failed','native_evaluator':native,'native_success':self.native_success,'artifacts':artifacts,'sandbox':{'process_isolated':True,'sdk_rpc_only':True,'deadline_enforced':True,'action_cancellation_verified':True},'infrastructure_failure':False,'outcome_unknown':False,'artifact_dir':str(ep),'recording':{'status':'simulated_no_video'},'formal_eligible':False}

class EvalRunner(Runner):
    """Provide a tiny public recording frame so the fake vision Eval can run."""
    def execute(self,r):
        from PIL import Image
        result=super().execute(r)
        frames=Path(result['artifact_dir'])/'recording_frames'
        frames.mkdir()
        Image.new('RGB',(8,8),(1,2,3)).save(frames/'000000.jpg')
        return result

class FailedEvalClient(Client):
    """Keep Dev inputs separate while returning a failed Eval and a plain Advisor hint."""
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.role_calls=[]
    def chat(self,**kw):
        prompt=kw['messages'][0]['content']
        usage={'total_tokens':100,'prompt_tokens':40,'completion_tokens':60}
        if prompt.startswith('You are the system Eval agent'):
            self.role_calls.append('eval')
            return ParccResponse(kw['model'],'{"passed": false, "reason": "frame #1 shows the motion failed", "reference_suggestion": "Use smaller motion"}',None,.01,1,usage)
        if prompt.startswith('You are a human-like robotics advisor') or prompt.startswith('Give a concise actionable repair hint'):
            self.role_calls.append('advisor')
            self.inputs.append(kw)
            return ParccResponse(kw['model'],'Use smaller world deltas based on observations.',None,.01,1,usage)
        return super().chat(**kw)
