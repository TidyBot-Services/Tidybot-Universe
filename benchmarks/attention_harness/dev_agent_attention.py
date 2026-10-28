"""Agent help grants; original Advisor SYSTEM and request view, formal boundaries only."""
import hashlib
import json
import time
from pathlib import Path
from . import dev_advisor_proxy as advisor
from .dev_help import decide, policy_parameters, public_screen
from .dev_agent_workspace import atomic_write, encode
from .dev_agent_eval import feedback as public_feedback

VOLUNTARY = {'reactive_help', 'retry_k_then_ask'}
PROACTIVE = {'budget_matched_random_escalation', 'trace_aware_hint_only', 'full_trace_aware_attention_planner'}


def request_view(episode, config, decision, *, exec_version, policy, recording,
                 execution_seconds, diff='', review=None, question=None, hypothesis=None):
    # Same construction as one-call advisor_call, including frame localization,
    # display words, selected timeline and original review projection.
    from .v2_evaluator import build_eval_evidence
    from .moment_localization import select_moments, validate_selected_moments
    from .dev_v2_loop import events_for_moment_selection, question_for
    from .dev_loop import sha
    proxy=config['advisor_proxy']
    evidence=build_eval_evidence(episode,config)
    episode_root=Path(episode)
    if hypothesis:
        public_screen(hypothesis);evidence['agent_hypothesis']=hypothesis[:1500]
    trace=evidence.get('public_feedback',{}).get('action_trace',[])
    event_ids=decision.get('event_ids',[])
    if policy=='full_trace_aware_attention_planner' and event_ids:
        events=events_for_moment_selection(episode,trace,exec_version)
        timing=recording.get('replay_timing')
        episode_root=Path(episode)
        views=['external']+[view for view in ('left_wrist','right_wrist','side','top') if (episode_root/'recording_views'/view/'frames.jsonl').is_file()]
        def journal(path):
            try:return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
            except (OSError,ValueError):return []
        base_rows=journal(episode_root/'recording_frames'/'frames.jsonl');view_map={}
        views=['external']+[view for view in views[1:] if journal(episode_root/'recording_views'/view/'frames.jsonl')]
        if timing and len(base_rows)==len(timing.get('frame_trace_seconds',[])):
            for view in views[1:]:
                rows=journal(episode_root/'recording_views'/view/'frames.jsonl')
                if not rows:continue
                view_map[view]={}
                for index,base in enumerate(base_rows):
                    best=min(enumerate(rows),key=lambda pair:abs(pair[1].get('physical_sequence',0)-base.get('physical_sequence',0)))
                    view_map[view][index]={'frame_index':best[0],'video_seconds':timing['frame_video_seconds'][index],'trace_seconds':best[1].get('trace_seconds',timing['frame_trace_seconds'][index])}
        # A wrist-view index has no time meaning until its sample correspondence is known.
        views=['external']+[view for view in views[1:] if view in view_map]
        selected,localization=select_moments(events,event_ids,timing,available_views=views,max_frames=proxy['max_selected_frames'],view_index_map=view_map)
    else:selected,localization=[],{'status':'generic_fallback','reason':'policy_uses_full_trace' if policy!='full_trace_aware_attention_planner' else 'no_selected_events'}
    selected_frames=[]
    if localization['status']=='targeted':
        for moment in selected:
            folder=episode_root/'recording_frames' if moment['view']=='external' else episode_root/'recording_views'/moment['view']
            rows=base_rows if moment['view']=='external' else journal(folder/'frames.jsonl')
            if moment['frame_index']>=len(rows) or not isinstance(rows[moment['frame_index']].get('file'),str):
                selected=[];localization={'status':'generic_fallback','reason':'selected_frame_missing'};break
            path=folder/rows[moment['frame_index']]['file']
            if path.is_symlink() or not path.is_file() or path.parent!=folder:
                selected=[];localization={'status':'generic_fallback','reason':'selected_frame_missing'};break
            selected_frames.append({'moment_id':moment['moment_id'],'filename':str(path.relative_to(episode_root)),
                'view':moment['view'],'sha256':sha(path)})
    if localization['status']=='targeted' and proxy['mode']=='vision':
        import base64,io
        from PIL import Image
        visual=[]
        for row in selected_frames:
            path=episode_root/row['filename']
            raw=path.read_bytes()
            with Image.open(io.BytesIO(raw)) as source:
                picture=source.convert('RGB')
                if picture.width>768:picture=picture.resize((768,max(1,round(picture.height*768/picture.width))))
                output=io.BytesIO();picture.save(output,format='JPEG',quality=80)
            visual.append({**row,'data_url':'data:image/jpeg;base64,'+base64.b64encode(output.getvalue()).decode()})
        evidence['frames']=visual
    elif localization['status']=='targeted':evidence['frames']=selected_frames
    else:selected_frames=[]
    validate_selected_moments(selected,localization)
    evidence['selected_moments']=selected;evidence['localization']=localization
    if localization['status']=='targeted':
        evidence['public_feedback']['action_trace']=[row for i,row in enumerate(trace) if f'v{exec_version}:{i}' in event_ids]
    evidence['question']=question or question_for(decision.get('context',{}).get('assessment'))
    if proxy['mode']=='vision':
        from .dev_request_view import build_request_view,shown_words,READ_WPS,WRITE_WPS
        timing=recording.get('replay_timing') or {}
        video_times=timing.get('frame_video_seconds') or []
        # Views are synchronized: count the displayed timeline once, not once
        # per camera. A targeted request spans its selected samples plus one
        # sample interval; a generic request offers the whole replay timeline.
        if localization['status']=='targeted' and selected:
            sample_step=min((b-a for a,b in zip(video_times,video_times[1:])),default=0)
            shown_video_seconds=max(m['video_seconds'] for m in selected)-min(m['video_seconds'] for m in selected)+sample_step
        else:shown_video_seconds=max(video_times,default=execution_seconds)
        request_view=build_request_view(task=evidence['task'],question=evidence['question'],reasoning='',
            frames=evidence['frames'][:proxy['max_selected_frames']],events=trace,
            failure=evidence.get('public_feedback',{}).get('public_error',''),diff=diff,
            eval_review=review,localization=localization,shown_video_seconds=shown_video_seconds)
        evidence=request_view
    return evidence


class Helper:
    def __init__(self, workspace, ledger, *, policy, parameters=None, config=None, client=None,
                 demo=None, quota=None, ask_provider=None, view_builder=request_view):
        self.workspace,self.ledger,self.policy=workspace,ledger,policy
        self.parameters=policy_parameters(policy,parameters)
        self.config=config or {};self.client=client;self.demo=demo;self.quota=quota
        self.ask_provider=ask_provider or advisor.ask;self.view_builder=view_builder
        self.path=workspace.private/'attention.json'
        self.state=json.loads(self.path.read_bytes()) if self.path.exists() else dict(failures=0,prior=[],current=None,intervals={})
        self.context={}

    def _save(self):atomic_write(self.workspace.private,'attention.json',encode(self.state))

    def _publish(self,vid):
        row={k:v for k,v in self.state['intervals'][vid].items() if k not in ('context','review')}
        self.workspace.publish('versions/'+vid+'/help.json',public_feedback(row))
        self._save()
        return public_feedback(row)

    def capture(self, executor):
        self.context=dict(episode=str(getattr(executor,'episode','')),
            recording=getattr(executor,'recording',{}),execution_seconds=(getattr(executor,'execution_usage',{}) or {}).get('wall_seconds') or 0)

    def initial(self):
        if self.policy!='demo_first' or 'demo' in self.state:return None
        decision=decide(self.policy,self.parameters,1,{},0,help_remaining=self.ledger.snapshot()['remaining']['help'],demo_available=bool(self.demo))
        if decision['action']!='use_demo' or not self.demo or self.ledger.snapshot()['remaining']['help']<=0:return None
        if self.quota:self.quota.check('before_demo')
        jid='help:demo';self.ledger.reserve(jid,'help',{'help':1});self.ledger.claim(jid)
        # Same fixed demo delivery/read-time formula as one-call attention().
        seconds=0  # One-call fixed demo has no hint_human_seconds charge.
        value=dict(status='delivered',eligible=True,requested=False,not_used=False,system_injected=True,
                   delivered=True,content=self.demo['content'],help_count=1,hint_human_seconds=seconds)
        self.ledger.settle(jid,value,usage={});self.state['demo']=value;self._save()
        self.workspace.publish('events/demo.json',value)
        return value

    def boundary(self, execution, *, deadline):
        vid=execution['version_id']
        if vid in self.state['intervals']:return self._publish(vid)
        old=self.state['current']
        if old:
            previous=self.state['intervals'][old]
            if previous['eligible'] and not previous['requested']:
                previous['not_used']=True;self._publish(old)
        verdict=execution.get('eval',{})
        failed=verdict.get('passed') is False
        self.state['failures']=self.state['failures']+1 if failed else 0
        eid=execution.get('execution_id')
        evidence=json.loads((self.workspace.canonical/'executions'/eid/'trace.json').read_bytes()) if eid else []
        outcome=execution['outcome']
        diagnostic=dict(execution_status=outcome.get('status'),public_error=outcome.get('error') or '',
                        public_failure=outcome.get('status') in ('failed','timeout'),action_trace=evidence)
        review={k:verdict[k] for k in ('passed','reason') if k in verdict}
        decision=decide(self.policy,self.parameters,int(vid[1:]),diagnostic,self.state['failures'],self.state['prior'],
            help_remaining=self.ledger.snapshot()['remaining']['help'],eval_review={'passed':verdict.get('passed')},
            eval_available=type(verdict.get('passed')) is bool)
        eligible=decision['action']=='request'
        # D4 explicitly grants reactive help for unavailable Eval, without
        # counting it as a failed verdict or advancing threshold/random slots.
        if self.policy=='reactive_help' and verdict.get('status')=='unavailable' and self.ledger.snapshot()['remaining']['help']>0:
            eligible=True
        row=dict(status='eligible' if eligible else 'not_eligible',eligible=eligible,requested=False,
                 not_used=False,system_injected=False,delivered=False,decision=decision,context=self.context,
                 review=review,version_id=vid)
        # Context paths are controller-only; strip before public publication.
        self.state['intervals'][vid]=row;self.state['current']=vid;self.state['prior'].append(diagnostic);self._save()
        if eligible and self.policy in PROACTIVE:return self._deliver(vid,None,deadline,system=True)
        return self._publish(vid)

    def ask(self,payload,*,deadline):
        if self.policy not in VOLUNTARY:return dict(status='policy_denied',reason='policy_uses_no_manual_help')
        vid=self.state['current'];row=self.state['intervals'].get(vid,{})
        if not row.get('eligible') or row.get('requested') or self.ledger.snapshot()['remaining']['help']<=0:
            return dict(status='not_eligible',reason='no_unused_formal_interval')
        question=payload.get('question')
        if not isinstance(question,str) or not question.strip() or len(question)>2000:
            return dict(status='policy_denied',reason='invalid_question')
        try:public_screen(question)
        except ValueError:return dict(status='policy_denied',reason='invalid_question')
        return self._deliver(vid,question,deadline,system=False)

    def _deliver(self,vid,question,deadline,*,system):
        row=self.state['intervals'][vid]
        if self.quota:self.quota.check('before_help')
        if time.monotonic()>=deadline:return dict(status='not_eligible',reason='wall_stop')
        context=row['context'];number=int(vid[1:])
        source=(self.workspace.canonical/'versions'/vid/'bundle.py').read_text()
        previous=self.workspace.canonical/'versions'/('v%04d'%(number-1))/'bundle.py'
        import difflib
        try:
            view=self.view_builder(context['episode'],self.config,row['decision'],exec_version=number,policy=self.policy,
                recording=context['recording'],execution_seconds=context['execution_seconds'],
                diff=''.join(difflib.unified_diff((previous.read_text() if previous.exists() else '').splitlines(True),source.splitlines(True),fromfile=f'v{number-1}',tofile=f'v{number}')),review=row['review'],question=question)
        except Exception as exc:
            # No provider was dispatched, so no attention is charged. Keep a
            # concrete receipt instead of turning an unusable Advisor view into
            # a failed formal execution.
            row.update(status='unavailable',eligible=False,not_used=True,error='public_view_unavailable')
            atomic_write(self.workspace.private,'help/'+vid+'/error.json',encode(dict(error=str(exc))))
            return self._publish(vid)
        from .dev_request_view import human_seconds,shown_words,READ_WPS,WRITE_WPS
        reserve=view['shown_video_seconds']+shown_words(view)/READ_WPS+768/WRITE_WPS
        jid='help:'+vid;self.ledger.reserve(jid,'help',{'help':1});self.ledger.claim(jid)
        row.update(requested=True,system_injected=system,not_used=False,status='requested');self._save()
        atomic_write(self.workspace.private,'help/'+vid+'/request-view.json',encode(view))
        try:
            record=self.ask_provider(view,self.config['advisor_proxy'],self.client,cache_dir=self.workspace.private/'advisor-cache')
            public_screen(record['feedback'])
            cost=human_seconds(view,record['feedback'])
            row.update(status='delivered',delivered=True,content=record['feedback'],help_count=1,
                       cache_hit=record.get('cache_hit',False),**cost)
            atomic_write(self.workspace.private,'help/'+vid+'/advisor.json',encode(record))
            self.ledger.settle(jid,{k:v for k,v in row.items() if k!='context'},usage=record.get('usage'))
            if self.quota:
                for name in ('quota_before','quota_after'):self.quota.event(record.get(name) or {})
        except Exception as exc:
            row.update(status='unavailable',error=type(exc).__name__,help_count=1,hint_human_seconds=reserve)
            self.ledger.settle(jid,{k:v for k,v in row.items() if k!='context'})
        return self._publish(vid)

    def finish(self):
        vid=self.state['current']
        if vid and self.state['intervals'][vid]['eligible'] and not self.state['intervals'][vid]['requested']:
            self.state['intervals'][vid]['not_used']=True;self._publish(vid)

    def cancel(self):pass
