"""Create a new full daily catalog without rewriting prior approved resources."""
from pathlib import Path
import argparse
import copy
import json
from .config import ROOT, SCHEMA, _REQUIRED_SOURCE, digest


def write_new(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x') as stream:json.dump(value,stream,indent=2,sort_keys=True);stream.write('\n')
    return str(path.resolve()),digest(path)


def freeze_resources(version, source_catalog):
    if not version or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789-' for c in version):
        raise ValueError('Use a lowercase named resource version')
    directory=ROOT/'openarm_bimanual/launch_configs'/version
    directory.mkdir(parents=True,exist_ok=False)
    source_catalog=Path(source_catalog).resolve()
    source=json.loads(source_catalog.read_bytes());profiles=[];provenance=[]
    originals=list(source['profiles']);seen={p['id'] for p in originals}
    legacy_catalog=ROOT/'benchmarks/attention_harness/protocol/v2/skill_graphs/live_run_catalog.json'
    if legacy_catalog.resolve()!=source_catalog:
        for item in json.loads(legacy_catalog.read_bytes())['profiles']:
            if item['id'] not in seen:originals.append(item);seen.add(item['id'])
    for index,original in enumerate(originals):
        profile=copy.deepcopy(original);folder=directory/f'compat-{index}'
        for name,key in [('config','approved_config_sha256'),('code','approved_policy_sha256')]:
            if digest(original[name])!=original[key]:raise ValueError('Source catalog identity changed: '+name)
        config=json.loads(Path(original['config']).read_bytes())
        # Refresh only declared implementation hashes in a new resource; task,
        # robot, model, tolerances, policy and all old files remain untouched.
        for name in config.get('service_files',{}):config['service_files'][name]=digest(ROOT/name)
        profile['config'],profile['approved_config_sha256']=write_new(folder/'config.json',config)
        graph=json.loads(Path(original['skill_graph_file']).read_bytes())
        if digest(original['skill_graph_file'])!=original['skill_graph_sha256']:raise ValueError('Source DAG changed')
        for entry in graph if isinstance(graph,list) else graph.get('entries',[]):
            contract=entry.get('attentionbench') or {}
            if 'approved_config_sha256' in contract:contract['approved_config_sha256']=profile['approved_config_sha256']
        profile['skill_graph_file'],profile['skill_graph_sha256']=write_new(folder/'graph.json',graph)
        profile['id']=f'openarm-bimanual-{version}-compat-{index}'
        profile.pop('attention_control_binding',None);profile.pop('attention_control_binding_sha256',None)
        profiles.append(profile)
        provenance.append({'new_profile':profile['id'],'source_profile':original['id'],
            'source_config_sha256':original['approved_config_sha256'],
            'source_graph_sha256':original['skill_graph_sha256'],'policy_unchanged':True})
    template=copy.deepcopy(json.loads((ROOT/'openarm_mobile/launch_configs/counter_to_sink_left_candidate5.json').read_bytes()))
    params=ROOT/'openarm_bimanual/configs/versions'/version/'robot_parameters.json'
    params_path,params_sha=write_new(params,json.loads((ROOT/'openarm_bimanual/configs/robot_parameters.json').read_bytes()))
    with params.with_suffix('.sha256').open('x') as stream:stream.write(params_sha+'\n')
    template.update(schema_version=SCHEMA,robot={'model':'OpenArm','execution_arm':'both'},
        model_sha256=digest(ROOT/'openarm_bimanual/model/mobile_openarm.urdf'),
        robot_parameters={'path':params_path,'sha256':params_sha},
        service_files={name:digest(ROOT/name) for name in sorted(_REQUIRED_SOURCE)})
    config_path,config_sha=write_new(directory/'both/config.json',template)
    policy=ROOT/'openarm_bimanual/policies/counter_to_sink_handoff_v1.py'
    # Freeze policy bytes as well, so subsequent development cannot rewrite an
    # already launched policy version.
    frozen_policy=directory/'both/policy.py';frozen_policy.parent.mkdir(exist_ok=True)
    with frozen_policy.open('xb') as stream:stream.write(policy.read_bytes())
    policy_sha=digest(frozen_policy)
    phases=[('left_pickup','Left pickup',['left']),('right_acquire','Right acquisition',['right']),
            ('physical_handoff','Physical handoff',['left','right']),
            ('right_transport','Receiver transport',['right']),('sink_release','Sink release',['right'])]
    entries=[]
    for index,(name,label,arms) in enumerate(phases):
        entries.append({'name':name,'display_name':label,'description':'Observed physical SDK receipts; task verdict is separate',
            'status':'implemented','dependencies':[] if not index else [phases[index-1][0]],'participating_arms':arms})
    root='counter_to_sink_bimanual_handoff'
    entries.append({'name':root,'display_name':'Dual-arm counter to sink','description':'Complete native task with genuine physical handoff',
        'status':'implemented','dependencies':[phases[-1][0]],'attentionbench':{'suite':'robocasa','task':'counter_to_sink',
        'approved_policy_sha256':policy_sha,'approved_config_sha256':config_sha}})
    graph_path,graph_sha=write_new(directory/'both/graph.json',{'graph_id':f'openarm-bimanual-{version}','entries':entries})
    profile={'id':f'openarm-bimanual-{version}-both-handoff','suite':'robocasa','task':'counter_to_sink',
        'execution_target':'robocasa_sim','sim_source_root':str(ROOT),'agent_source_root':str(ROOT),
        'task_source_root':template['task_source']['root'],'sim_python':'/home/truares/miniconda3/envs/maniskill/bin/python',
        'agent_python':'/home/truares/miniconda3/envs/maniskill/bin/python','code':str(frozen_policy),
        'approved_policy_sha256':policy_sha,'config':config_path,'approved_config_sha256':config_sha,
        'skill_graph_file':graph_path,'skill_graph_sha256':graph_sha,'skill_root':root}
    profiles.append(profile)
    from benchmarks.attention_harness.attention_control import SWITCHABLE
    for index,profile in enumerate(profiles):
        sources={str(ROOT/name):digest(ROOT/name) for name in sorted(_REQUIRED_SOURCE)}
        sources[str(ROOT/'benchmarks/attention_harness/attention_control.py')]=digest(ROOT/'benchmarks/attention_harness/attention_control.py')
        binding={'schema_version':'attentionbench.attention-control.v1','binding_id':profile['id'],
            'allowed_policies':sorted(SWITCHABLE),'pre_task_only_policies':['demo_first','budget_matched_random_escalation'],
            'config_sha256':profile['approved_config_sha256'],'action_policy_sha256':profile['approved_policy_sha256'],
            'graph_sha256':profile['skill_graph_sha256'],'sources':sources,'formal_eligible':False,
            'safe_boundary':'Completed SDK action or Attention decision; execution mode remains bound',
            'handover':'Keep environment, progress, budget, history, policy instances and unresolved Hint ownership'}
        profile['attention_control_binding'],profile['attention_control_binding_sha256']=write_new(directory/f'binding-{index}.json',binding)
    path,_=write_new(directory/'catalog.json',{'schema_version':'attentionbench.ui-run-catalog.v1',
        'profiles':profiles,'require_skill_graph':True,'formal_eligible':False,
        'scope':'Daily Robosuite/RoboCasa, Franka/OpenArm and explicit execution modes with independent engineering resources'})
    write_new(directory/'provenance.json',{'source_catalog':str(source_catalog),'source_catalog_sha256':digest(source_catalog),
        'legacy_files_written':[],'cloned_profiles':provenance,'new_bimanual_profile':profile['id']})
    from benchmarks.attention_harness.ui_launch import load_catalog
    load_catalog(Path(path))
    return path


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--version',required=True);parser.add_argument('--source-catalog',type=Path,required=True)
    args=parser.parse_args();print(freeze_resources(args.version,args.source_catalog))
