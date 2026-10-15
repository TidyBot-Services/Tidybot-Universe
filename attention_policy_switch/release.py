"""Publish all existing startup configurations under an independent version."""
import json,hashlib,sys
from pathlib import Path
from publish import ROOT,digest,write

def release(version):
    dest=ROOT/'attention_policy_switch'/version;dest.mkdir(exist_ok=False)
    baseline=ROOT/'benchmarks/attention_harness/protocol/v2/skill_graphs/live_run_catalog.json'
    catalog=json.loads(baseline.read_text());profiles=[]
    runtime={str(p.relative_to(ROOT)) for p in (ROOT/'benchmarks/attention_harness').glob('*.py')}
    runtime|={'benchmarks/attention_harness/core/'+n for n in ['models.py','store.py','policies.py']}
    runtime|={'benchmarks/attention_harness/ui/live.js','benchmarks/attention_harness/ui/index.html'}
    runtime|={str(p.relative_to(ROOT)) for part in ['robocasa_native','robosuite_memory'] for p in (ROOT/'benchmarks/attention_harness'/part).glob('*.py')}
    for index,old in enumerate(catalog['profiles']):
        folder=dest/f'profile-{index}';folder.mkdir()
        config=json.loads(Path(old['config']).read_text())
        sources={name:digest(ROOT/name) for name in sorted(runtime|set(config.get('service_files',{})))}
        if 'service_files' in config:config['service_files']={name:sources[name] for name in sorted(set(config['service_files'])|runtime)}
        cfg=folder/'config.json';config_sha=write(cfg,config)
        graph=json.loads(Path(old['skill_graph_file']).read_text());graph['definition_version']='attention-control-'+version
        entry=graph['entries'][0]['attentionbench'];entry['formal_config_file']=str(cfg.relative_to(ROOT));entry['approved_config_sha256']=config_sha
        graph_path=folder/'graph.json';graph_sha=write(graph_path,graph)
        binding={'schema_version':'attentionbench.attention-control.v1','binding_id':f'attention-control-{version}-{index}',
            'allowed_policies':['autonomous','reactive_help','trace_aware_hint_only','full_trace_aware_attention_planner','retry_k_then_ask'],
            'source_sha256':hashlib.sha256(json.dumps(sources,sort_keys=True).encode()).hexdigest(),
            'sources':{str(ROOT/name):sha for name,sha in sources.items()},
            'config_sha256':config_sha,'action_policy_sha256':old['approved_policy_sha256'],'graph_sha256':graph_sha,
            'safe_boundary':'SDK call completion or Attention decision; in-flight actions complete normally',
            'handover':'Keep task progress, environment, budget, failure history and each policy instance; hold unresolved Hint or unassociated old responses',
            'formal_eligible':False,'pre_task_only_policies':['demo_first','budget_matched_random_escalation'],
            'source_profile':old['id'],'source_config_sha256':old['approved_config_sha256'],'source_graph_sha256':old['skill_graph_sha256']}
        if old['suite']=='robosuite':
            backend=Path(old['service_source_root'])/'robosuite_sim/backend.py'
            if 'robots="Panda"' not in backend.read_text():raise ValueError('Panda identity requires actual pinned native backend')
            binding['robot_configuration']={'robot':{'model':'Panda','execution_arm':'single'},'base_status':'fixed','identity_source':str(backend),'identity_source_sha256':digest(backend)}
            binding['sources'][str(backend)]=digest(backend)
        binding_path=folder/'attention-binding.json';binding_sha=write(binding_path,binding)
        profile={**old,'id':f'attention-control-{version}-{index}','config':str(cfg),'approved_config_sha256':config_sha,
            'skill_graph_file':str(graph_path),'skill_graph_sha256':graph_sha,
            'attention_control_binding':str(binding_path),'attention_control_binding_sha256':binding_sha}
        profiles.append(profile)
    catalog['profiles']=profiles;catalog['scope']='Engineering Attention policy switching. Independent config/source/graph bindings; original approved policy/model resources and legacy formal run bindings remain frozen.'
    path=dest/'catalog.json';write(path,catalog)
    mobile=profiles[2]
    write(ROOT/'attention_policy_switch/current.json',{'version':version,'catalog':str(path),'config':mobile['config'],'binding':mobile['attention_control_binding'],'binding_sha256':mobile['attention_control_binding_sha256']})
    print(json.dumps({'catalog':str(path),'profiles':len(profiles),'version':version}))
if __name__=='__main__':release(sys.argv[1])
