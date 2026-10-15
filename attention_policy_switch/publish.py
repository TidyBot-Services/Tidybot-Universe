"""Publish a new engineering version; never overwrite approved resources."""
import hashlib,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def write(p,value):p.write_text(json.dumps(value,indent=2,sort_keys=True)+'\n');return digest(p)
def publish(version):
    target=ROOT/'attention_policy_switch'/version
    target.mkdir(exist_ok=False)
    baseline=ROOT/'openarm_mobile/launch_configs'
    cfg=json.loads((baseline/'counter_to_sink_left_candidate5.json').read_text())
    extra={'benchmarks/attention_harness/attention_control.py','benchmarks/attention_harness/sim_gt_attention_run.py','benchmarks/attention_harness/formal_attention_cli.py','benchmarks/attention_harness/core/policies.py'}
    cfg['service_files']={name:digest(ROOT/name) for name in sorted(set(cfg['service_files'])|extra)}
    config=target/'config.json';config_sha=write(config,cfg)
    graph=json.loads((baseline/'openarm-mobile-counter-to-sink-left_candidate5_graph.json').read_text())
    graph['definition_version']='attention-control-'+version
    entry=graph['entries'][0]['attentionbench'];entry['formal_config_file']=str(config.relative_to(ROOT));entry['approved_config_sha256']=config_sha
    graph_path=target/'graph.json';graph_sha=write(graph_path,graph)
    source_sha=hashlib.sha256(json.dumps(cfg['service_files'],sort_keys=True).encode()).hexdigest()
    binding={'schema_version':'attentionbench.attention-control.v1','binding_id':'attention-control-'+version,
        'allowed_policies':['autonomous','reactive_help','trace_aware_hint_only','full_trace_aware_attention_planner','retry_k_then_ask'],
        'source_sha256':source_sha,'sources':{str(ROOT/name):sha for name,sha in cfg['service_files'].items()},
        'safe_boundary':'SDK call completion or Attention decision; no in-flight cancellation',
        'handover':'Preserve budgets, progress, failure history and per-policy instances; hold pending Hint and unassociated response until consumed',
        'config_sha256':config_sha,'action_policy_sha256':entry['approved_policy_sha256'],'graph_sha256':graph_sha,
        'formal_eligible':False,'pre_task_only_policies':['demo_first','budget_matched_random_escalation']}
    binding_path=target/'attention-binding.json';binding_sha=write(binding_path,binding)
    catalog=json.loads((baseline/'catalog_candidate5.json').read_text())
    profile=catalog['profiles'][0];profile['id']='attention-control-'+version+'-openarm-mobile-left'
    profile.update(config=str(config),approved_config_sha256=config_sha,skill_graph_file=str(graph_path),skill_graph_sha256=graph_sha,
        attention_control_binding=str(binding_path),attention_control_binding_sha256=binding_sha)
    catalog['scope']='Independent native OpenArm mobile engineering runtime Attention policy handover; initial action policy/model/DAG identities retained. Frozen formal resources unchanged.'
    catalog_path=target/'catalog.json';write(catalog_path,catalog)
    write(ROOT/'attention_policy_switch/current.json',{'version':version,'catalog':str(catalog_path),'config':str(config),'binding':str(binding_path),'binding_sha256':binding_sha})
    print(json.dumps({'version':version,'catalog':str(catalog_path),'config_sha256':config_sha,'binding_sha256':binding_sha}))
if __name__=='__main__':publish(sys.argv[1])
