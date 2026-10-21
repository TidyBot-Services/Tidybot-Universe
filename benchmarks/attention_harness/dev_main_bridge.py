"""Opt-in main-workbench adapter for the current Dev v3 implementation.

One existing goal ledger, no daily source switch, budget renewal or forked loop.
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
from pathlib import Path
from urllib.parse import urlsplit, parse_qs, unquote

from . import dev_experiment as experiments
from .core.dev_store import DevConflict, DevBudgetExhausted
from .dev_capabilities import execution_environment
from .dev_configuration import derive_cell_budget, normalize_configuration
from .dev_ledger import DevV2Store, RESOURCES, complete_resources, fill_optional
from .dev_loop import sha, process_identity, owner_alive
from .dev_planner import PlannerControl, POLICY
from .dev_tasks import TASKS
from openarm365_dev_v2.dev_config import ROOT
from openarm365_dev_v2.diag_budget import task_limits


class DevMainBridge:
    def __init__(self, ledger, artifacts, goal_key, memory=None, ui_url=None):
        if not Path(ledger).is_file():raise FileNotFoundError('existing authorized goal ledger required')
        self.store=DevV2Store(ledger);self.artifacts=Path(artifacts).resolve()
        self.goal_key=goal_key;self.goal_id=hashlib.sha256(('goal|'+goal_key).encode()).hexdigest()[:32]
        self.store.goal(self.goal_id)  # Must exist; never provision a replacement.
        self.memory=memory;self.ui_url=ui_url;self.workers={};self.mutex=threading.Lock()

    def options(self):
        goal=self.store.goal(self.goal_id)
        tier=goal.get('budget_tier','legacy')
        from .eval_agent import default_config as eval_default
        default=derive_cell_budget(normalize_configuration(dict(max_versions=5,target_depth=5,target_valid_executions=5,help_budget=5,
            planner_features={'human_wait_seconds':300},policies=[POLICY],budget_tier=tier,eval_agent=eval_default(True))))
        without_eval=derive_cell_budget(normalize_configuration(dict(max_versions=5,target_depth=5,target_valid_executions=5,help_budget=5,
            planner_features={'human_wait_seconds':300},policies=[POLICY],budget_tier=tier,eval_agent=eval_default(False))))
        from .free_models import eval_is_unmetered,is_unmetered_model
        from .dev_experiment import MODEL
        from .dev_advisor_proxy import MODEL as ADVISOR_MODEL
        from .daily_dev_v2 import registered_scene_seeds
        return dict(enabled=True,bridge_schema='main-dev-v3',budget_tier=tier,model_reserve_seconds=360 if tier=='dev' else 120,goal_key=self.goal_key,registered_scene_seeds=registered_scene_seeds(),tasks=[t.public() for t in TASKS.values()],default_cell_budget=default,
            default_no_eval_cell_budget=without_eval,goal_limits=fill_optional(goal['limits']),default_max_iterations=5,policies=[POLICY],eval_agent_supported=True,
            eval_accounting='不计限额，仅记录' if eval_is_unmetered(eval_default(True)) else '计入限额',
            model_token_accounting={role:('不计限额，仅记录' if is_unmetered_model(model) else '计入限额') for role,model in {'Dev':MODEL['model'],'Advisor':ADVISOR_MODEL,'Eval':eval_default(True)['model']}.items()},
            authorization=dict(mode='existing_goal',deadline_epoch=goal.get('authorized_until')),
            memory=self.memory,profiles=[dict(id='dev-v2-'+t.task_id,suite='robocasa365',task_id=t.task_id,
                seed=101,robot='OpenArm365',execution_arm='both',channel='dev_v2',enabled=True,
                execution_target='robocasa365_sim',attempt_wall_seconds=task_limits(tier)['attempt_seconds'],sdk_call_limit=task_limits(tier)['sdk_calls'],control_step_limit=task_limits(tier)['control_steps'],startup_cleanup_seconds=60,wall_seconds=min(21600,goal['wall_limit']),layout_id=11,style_id=34) for t in TASKS.values()])

    def experiments(self):
        return [e for e in experiments.list_experiments(self.store) if e.get('goal_id')==self.goal_id]

    def experiment(self,eid):
        e=experiments.snapshot(self.store,eid)
        if e.get('goal_id')!=self.goal_id:raise DevConflict('outside authorized goal')
        return e

    def list(self):
        rows=[]
        for e in self.experiments():
            e=self.experiment(e['experiment_id']);m=json.loads(Path(e['manifest']['uri']).read_bytes())
            for c in e['cells']:
                rows.append(dict(c,channel='dev_v2',session_id='v2-'+c['key'],created_at=e['created_at'],
                    locked={'config':dict(m['configs'][f"{c['task_id']}:{c['seed']}"],suite='robocasa365')},model=m['model']['model'],experiment_state=e['state'],lifecycle=self.lifecycle(c,e)))
        return rows

    def lifecycle(self,c,e=None):
        """Evidence projection only. Reading it never acquires a worker or clock."""
        e=e or self.experiment(c['experiment_id']);g=e['resources']
        cells=e['cells'];jobs=[j for row in cells for j in row['jobs']]
        goal_experiments=[e if row['experiment_id']==e['experiment_id'] else self.experiment(row['experiment_id']) for row in self.experiments()]
        active=False;own_active=False;unknown_owner=False
        for row in goal_experiments:
            if row.get('owner') and owner_alive(row['owner']):
                active=True
                if row['experiment_id']==e['experiment_id']:own_active=True
            for cell in row['cells']:
                if cell.get('owner') and owner_alive(cell['owner']):
                    active=True
                    if row['experiment_id']==e['experiment_id']:own_active=True
        for eid,worker in self.workers.items():
            if worker.poll() is None:
                active=True
                if eid==e['experiment_id']:own_active=True
        for receipt in self.artifacts.glob('devexp-*/scheduler-process-*.json'):
            try:
                if owner_alive(json.loads(receipt.read_bytes())):
                    active=True
                    if receipt.parent.name==e['experiment_id']:own_active=True
            except (OSError,ValueError,KeyError,TypeError):unknown_owner=True
        owned=bool(c.get('owner') and owner_alive(c['owner']))
        # A cell owner also blocks dispatch during acquisition/release windows.
        for row in cells:
            if row.get('owner') and owner_alive(row['owner']):active=True;own_active=True
        claims=[j for j in c['jobs'] if j['state']=='claimed']
        recovery=[];recovery_error=None
        if not active and any(j['kind']=='human' and (j['state']=='claimed' or j['state']=='settled' and (row['state']=='running' or row['state']=='planned' and row.get('planner_wait'))) for row in cells for j in row['jobs']):
            try:
                with self.store.tx() as db:recovery=experiments.wait_recovery(self.store,db,e['experiment_id'])
            except (DevConflict,OSError,ValueError,KeyError,TypeError) as exc:recovery_error=str(exc)
        recoverable={r['job_id'] for r in recovery}
        unresolved=any(j['state']=='needs_review' or j['state']=='claimed' and not (j.get('owner') and owner_alive(j['owner'])) and j['job_id'] not in recoverable for row in goal_experiments for cell in row['cells'] for j in cell['jobs'])
        terminal=c['state'] in {'completed','failed','cancelled','condition_invalid','not_started','budget_exhausted','stopped'} or e['cancelled']
        requests=(c.get('planner') or {}).get('requests',[])
        prepared=e['state']=='planned' and all(row['state']=='planned' and not row.get('owner') and not row['jobs'] and not row['versions'] and not any(row['used'].values()) for row in cells) and g.get('real_started_at') is None and not any(g['used'].values()) and not any(g['reserved'].values())
        if terminal:phase='terminal'
        elif unresolved or claims and not owned and not recovery or c['state']=='needs_review' or unknown_owner:phase='review'
        elif any(r['state']=='waiting' and r['request_type']=='approval' for r in requests):phase='approval'
        elif owned and any(j['kind']=='advisor' for j in claims):phase='advisor'
        elif owned and any(j['kind']=='model' for j in claims):phase='model'
        elif owned and any(j['kind']=='execute' for j in claims):phase='sdk'
        elif prepared and not active:phase='prepared'
        elif owned:phase='preparing'  # Memory/public work, not simulator startup.
        else:phase='queued'
        enough=g['wall_remaining']>0 and not g.get('real_finished_at') and g['remaining'].get('dev_calls',0)>0
        can_start=(bool(recovery) or e['state']=='planned' and any(row['state']=='planned' for row in cells) and enough) and not e['cancelled'] and not active and not unresolved and not unknown_owner and not recovery_error
        from .eval_agent import default_config as eval_default
        minimum=derive_cell_budget(normalize_configuration(dict(max_versions=1,target_depth=1,target_valid_executions=1,help_budget=0,
            planner_features={'human_wait_seconds':1},policies=[POLICY],budget_tier=g.get('budget_tier','legacy'),
            eval_agent=eval_default(e['selection']['mode']=='experiment'))))
        allocated={k:0 for k in RESOURCES}
        for other in goal_experiments:
            for row in other['cells']:
                for k,v in row['budget'].items():allocated[k]+=v
        from .free_models import limited_resources
        from .dev_experiment import MODEL
        from .dev_advisor_proxy import MODEL as ADVISOR_MODEL
        limited=limited_resources(eval_config=eval_default(e['selection']['mode']=='experiment'),model_names=(MODEL['model'],ADVISOR_MODEL))
        can_create=not recovery and not active and not unresolved and not unknown_owner and enough and all(allocated[k]+minimum[k]<=g['limits'].get(k,0) and minimum[k]<=g['remaining'][k] for k in limited)
        scope=[dict(task_id=row['task_id'],seed=row['seed'],state=row['state']) for row in cells]
        return dict(phase=phase,can_start=can_start,can_create_new=can_create,can_stop=own_active and not terminal,
            start_label='Start prepared experiment' if prepared else 'Continue experiment',
            experiment_id=e['experiment_id'],scope=scope,scope_count=len(scope),scheduler_active=own_active,
            recovery_available=bool(recovery) and can_start,recovery_error=recovery_error,waiting_owner=c.get('owner') if owned else None,
            basis='persisted experiment/cells/jobs/requests/goal clock and observed process identity')

    def create(self,body):
        if not isinstance(body,dict) or set(body)!={'selection','start'} or type(body['start']) is not bool:raise ValueError('selection and explicit start required')
        v=body['selection'];configuration=v['configuration'];g=self.store.goal(self.goal_id)
        from .dev_configuration import require_eval_gate, require_scene_gate
        require_eval_gate(v['mode'],v)
        if v.get('human_proxy',{}).get('enabled'):raise ValueError('HumanProxy requests are not configured in this isolated bridge')
        from .eval_agent import default_config as eval_default
        review=v.get('eval_agent')
        source=v.get('model_source','parcc')
        if source not in ('parcc','codex') or (source=='codex' and g.get('budget_tier','legacy')!='dev'):
            raise ValueError('Codex model source requires dev tier')
        if source=='codex' and (v.get('model','gpt-6-astra')!='gpt-6-astra' or v.get('reasoning_effort','high')!='high'):
            raise ValueError('Codex requires gpt-6-astra at high reasoning effort')
        if v['mode']=='experiment':
            fixed=eval_default(True,source)
            if review not in ({'enabled':True,'model':fixed['model']},fixed):
                raise ValueError('fixed Qwen Eval selection required')
        elif isinstance(review,dict) and review.get('enabled'):
            raise ValueError('Eval requests are not configured for interactive/diagnostic bridge runs')
        if v.get('budget_tier',g.get('budget_tier','legacy'))!=g.get('budget_tier','legacy'):
            raise ValueError('existing goal budget tier cannot change')
        if configuration['goal_id']!=self.goal_key:raise ValueError('this UI is bound to the existing goal; no new budget')
        if v['policies']!=[POLICY]:raise ValueError('this acceptance bridge only dispatches the full planner')
        if not self.memory:raise ValueError('isolated Memory authority required for three-feature run')
        limits=configuration['experiment_budget'];budget=configuration['cell_budget'];n=len(v['tasks'])*len(v['seeds'])
        if complete_resources(limits):limits=fill_optional(limits)  # goals authorized before eval_calls existed allow none
        if set(limits)!=set(RESOURCES):raise ValueError('complete experiment resource limits required')
        if not 0<configuration['wall_seconds']<=g['wall_limit']:raise ValueError('wall exceeds original authorization')
        selection={k:v[k] for k in ('request_key','tasks','seeds','policies','mode','help_budget')}
        selection['budget_tier']=g.get('budget_tier','legacy')
        if source=='codex':selection['model_source']='codex'
        depth=configuration['max_iterations']
        selection.update(goal_key=self.goal_key,goal_limits=g['limits'],wall_limit=g['wall_limit'],cell_budget=budget,
            coverage='budget_limited',target_depth=depth,target_valid_executions=depth,max_versions=depth,
            reproducible_reset=True,planner_features=dict(human_wait_seconds=v.get('human_wait_seconds',60),memory=self.memory))
        # The bridge owns the fixed Eval configuration; UI input only chooses the role.
        from .dev_advisor_proxy import default_config as advisor_default
        selection.update(eval_agent=eval_default(v['mode']=='experiment',source),advisor_proxy=advisor_default(False,source))
        from .free_models import limited_resources
        from .dev_experiment import MODEL
        limited=limited_resources(eval_config=selection['eval_agent'],model_names=(('gpt-6-astra' if source=='codex' else MODEL['model']),selection['advisor_proxy']['model']),model_source=source)
        if v['mode']=='experiment':
            required=derive_cell_budget(normalize_configuration(selection))
            actual=fill_optional(budget) if complete_resources(budget) else budget
            missing={k:required[k]-actual.get(k,0) for k in limited if actual.get(k,0)<required[k]}
            if missing:raise ValueError('Eval cell budget shortfall: '+', '.join(f'{k} +{amount}' for k,amount in missing.items()))
        authorization_shortfall={k:max(limits[k],n*budget.get(k,0))-g['limits'].get(k,0) for k in limited
                                 if max(limits[k],n*budget.get(k,0))>g['limits'].get(k,0)}
        experiment_shortfall={k:n*budget.get(k,0)-limits[k] for k in limited if n*budget.get(k,0)>limits[k]}
        if authorization_shortfall or experiment_shortfall:
            parts=[]
            if authorization_shortfall:parts.append('authorized goal shortfall: '+', '.join(f'{k} +{v}' for k,v in authorization_shortfall.items()))
            if experiment_shortfall:parts.append('experiment limit shortfall: '+', '.join(f'{k} +{v}' for k,v in experiment_shortfall.items()))
            raise ValueError('; '.join(parts))
        if selection['mode']=='experiment':
            # Experiment mode starts every cell from the registered physical scene; seeds without a reference are refused at creation.
            from openarm365_dev_v2.scene_contract import registered_contract_ref
            selection['scene_contract']=registered_contract_ref()
        require_scene_gate(selection['mode'],selection,selection['tasks'],selection['seeds'])
        # An operator may lower the wall for this particular experiment, without
        # changing the persistent goal deadline or the immutable old goal row.
        e=experiments.create(self.store,self.artifacts,selection)
        from .dev_v2_loop import immutable
        immutable(Path(e['artifact_dir'])/'ui-selection.json',v)
        if body['start']:self.start(e['experiment_id'],wall_seconds=configuration['wall_seconds'])
        return self.experiment(e['experiment_id'])

    def start(self,eid,wall_seconds=None):
        from .dev_wait_recovery import reconcile_waits
        # Cleanup is independent of dispatch permission and source validity.
        self.experiment(eid)  # Verify this bridge's goal before any mutation.
        reconcile_waits(self.store,eid)
        e=self.experiment(eid)
        if e['cancelled']:raise DevConflict('cancelled experiment remains cancelled')
        experiments.verify_manifest(e)
        selection_path=Path(e['artifact_dir'])/'ui-selection.json'
        if selection_path.is_file():
            frozen_wall=json.loads(selection_path.read_bytes())['configuration']['wall_seconds']
            if wall_seconds is not None and wall_seconds!=frozen_wall:raise DevConflict('dispatch wall differs from frozen UI selection')
            wall_seconds=frozen_wall
        with self.mutex:
            e=self.experiment(eid)
            lifecycle=self.lifecycle(e['cells'][0],e)
            if not lifecycle['can_start']:raise DevConflict('experiment is not startable; inspect lifecycle, owner, claims and original resources')
            if any(p.poll() is None for p in self.workers.values()):raise DevConflict('serial worker already active')
            for old in self.experiments():
                if old.get('owner') and owner_alive(old['owner']):raise DevConflict('external scheduler already owns goal')
            for receipt in self.artifacts.glob('devexp-*/scheduler-process-*.json'):
                if owner_alive(json.loads(receipt.read_bytes())):raise DevConflict('external scheduler process still active')
            if any((j['state']=='needs_review' or j['state']=='claimed' and j['kind']!='human') for c in e['cells'] for j in c['jobs']):raise DevConflict('unresolved claim requires independent review')
            goal=self.store.goal(self.goal_id)
            if goal['wall_remaining']<=0 or goal.get('real_finished_at'):raise DevBudgetExhausted('original goal wall is exhausted/closed')
            # run_cell atomically starts the original clock before Memory. This
            # optional shorter deadline is monotone and cannot extend on retry.
            if wall_seconds is not None and not lifecycle['recovery_available']:
                import time
                with self.store.tx() as db:
                    raw=self.store.get(db,'goals',self.goal_id);deadline=time.time()+wall_seconds
                    raw['authorized_until']=min(raw.get('authorized_until') or deadline,deadline)
                    self.store.put(db,'goals',self.goal_id,raw)
            directory=Path(e['artifact_dir'])
            with (directory/'scheduler.log').open('ab') as log:
                p=subprocess.Popen([sys.executable,'-B','-u','-m','benchmarks.attention_harness.dev_experiment','run',
                    '--store',str(self.store.path),'--artifacts',str(self.artifacts),'--experiment',eid,'--max-cells',str(len(e['cells']))],
                    cwd=ROOT,env=execution_environment(ROOT,{'ATTENTION_PLANNER_UI_URL':self.ui_url or '', 'ATTENTION_PLANNER_APPROVAL_API_PREFIX':'/api/dev-v2'}),stdout=log,stderr=log,start_new_session=True)
            self.workers[eid]=p
            from .dev_v2_loop import immutable
            immutable(directory/f'scheduler-process-{p.pid}.json',process_identity(p.pid))
        return dict(experiment_id=eid,worker_started=True)

    def view(self,c):
        from .codex_quota import run_quota_usage
        def read(ref,text=False):
            if not ref:return None
            if sha(ref['uri'])!=ref['sha256']:raise DevConflict('display evidence drift')
            return Path(ref['uri']).read_text() if text else json.loads(Path(ref['uri']).read_bytes())
        rows=[]
        from .dev_request_view import display_eval_review,display_request_view,request_view_ref
        for v in c['versions']:
            j=next((j for j in c['jobs'] if j['version']==v['version'] and j['kind']=='execute'),None)
            receipt=(j.get('receipt') or j.get('review_evidence') or {}) if j else {};bundle=read(receipt.get('bundle'))
            help_content=read(v.get('help'))
            request_ref=request_view_ref(help_content,read)
            request_view=display_request_view(read(request_ref)) if request_ref else None
            rows.append(dict(v,evaluator_review=display_eval_review(c,v['version']),request_view=request_view,source_text=read(v['source'],True),diff_text=read(v.get('diff'),True),decision_content=read(v.get('decision')),
                help_content=help_content,public_feedback=read(receipt.get('public_feedback')),
                independent_result=read(bundle['artifacts']['native_result']) if bundle else None,recording=bundle.get('recording') if bundle else None,
                execution_state=j['state'] if j else 'not_dispatched'))
        planner=PlannerControl(self.store).snapshot(c['key']) if c.get('planner_features') else None
        from .dev_memory_view import projection as memory_projection
        return dict(c,versions=rows,planner=planner,ledger=self.store.export_ledger(self.goal_id),lifecycle=self.lifecycle({**c,'planner':planner}),planner_memory=memory_projection(self.store,c),quota_usage=run_quota_usage(c,read))

    def get(self,path):
        parsed=urlsplit(path)
        if parsed.path=='/api/dev-v2/options':return self.options(),'application/json'
        if parsed.path=='/api/dev-v2/experiments':return self.list(),'application/json'
        m=re.fullmatch(r'/api/dev-v2/experiments/(devexp-[a-f0-9]{24})(?:/(.*))?',parsed.path)
        if not m:raise FileNotFoundError('unknown Dev resource')
        eid,suffix=m.groups();e=self.experiment(eid)
        if not suffix:return e,'application/json'
        if suffix in {'report','export.json','export.csv','ledger.json'}:
            from .dev_report import report,report_csv
            if suffix=='ledger.json':return self.store.export_ledger(self.goal_id),'application/json'
            r=report(self.store,eid);return (report_csv(r),'text/csv') if suffix=='export.csv' else (r,'application/json')
        m=re.fullmatch(r'cells/([a-f0-9]{64})/(view|live|live/frame|files/(.+))',suffix or '')
        if not m:raise FileNotFoundError('unknown Dev cell resource')
        key,kind,name=m.groups();c=self.store.cell(key)
        if c['experiment_id']!=eid:raise DevConflict('cell outside experiment')
        if kind=='view':return self.view(c),'application/json'
        if kind.startswith('live'):
            from .dev_live_monitor import response
            return response(self.store,eid,key,image=kind.endswith('/frame'),query=parse_qs(parsed.query))
        root=Path(c.get('artifact_dir','/missing')).resolve();p=(root/unquote(name)).resolve()
        allowed={'policy.py','diff.patch','version.json','public-feedback.json','decision.json','help.json','bundle.json','model-input.json','model-output.json','advisor-input.json','advisor-output.json','trace.json','native_result.json','recording.json','sandbox_receipt.json','advisor_replay.mp4','simulation.mp4','config.json','session-lock.json','human-review-needed.json','human-decision-receipt.json','memory-input.json','memory-result.json','memory-candidate-proposal.json','advisor-independent-preparation.json','human-independent-preparation.json'}
        if not p.is_relative_to(root) or any(n.startswith('.') for n in p.relative_to(root).parts) or p.name not in allowed or not p.is_file():raise FileNotFoundError('unavailable public UI artifact')
        return p,'video/mp4' if p.suffix=='.mp4' else 'application/json' if p.suffix=='.json' else 'text/plain; charset=utf-8'

    def post(self,path,body,*,human_authorized=False):
        if path=='/api/dev-v2/experiments':return self.create(body)
        m=re.fullmatch(r'/api/dev-v2/experiments/(devexp-[a-f0-9]{24})/(run|cancel|switch|approvals/(planner-[a-f0-9]{64})/decision)',path)
        if not m:raise FileNotFoundError('unknown command')
        eid,command,rid=m.groups();self.experiment(eid)
        if rid:
            if not human_authorized:raise PermissionError('separate human capability required')
            ctl=PlannerControl(self.store);r=ctl.get(rid)
            if self.store.cell(r['cell'])['experiment_id']!=eid:raise DevConflict('request outside experiment')
            if set(body)!={'binding','approved','response_id'}:raise ValueError('exact decision required')
            return ctl.reply(rid,scope=body['binding'],approved=body['approved'],actor='authenticated_human',response_id=body['response_id'])
        if command=='switch':
            if set(body)!={'cell','policy'} or self.store.cell(body['cell'])['experiment_id']!=eid:raise ValueError('exact cell/policy required')
            return experiments.switch(self.store,body['cell'],body['policy'])
        if body:raise ValueError('empty command body required')
        return experiments.cancel(self.store,eid) if command=='cancel' else self.start(eid)
