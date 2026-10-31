"""Migration negatives must fail before a legacy process or planner can run."""
from benchmarks.attention_harness.workspace_paths import get
import importlib
import json
from pathlib import Path
import pytest
from curobo_v2 import deployment
from curobo_v2.identity import IdentityError, load_lock


@pytest.mark.parametrize('module', ['openarm_robocasa.session','openarm_mobile.session','openarm_bimanual.session'])
def test_legacy_owners_cannot_spawn(module,monkeypatch):
    owner=importlib.import_module(module)
    monkeypatch.setattr(owner.subprocess,'Popen',lambda *a,**kw:pytest.fail('legacy subprocess spawned'))
    with pytest.raises(IdentityError,match='V2-only'):
        object.__new__(owner.OwnedOpenArmService).__enter__()


@pytest.mark.parametrize('module', ['openarm_robocasa.planning','openarm_mobile.planning','openarm_bimanual.planning'])
def test_rrt_and_cpu_ik_cannot_be_used_as_fallback(module):
    planner=importlib.import_module(module)
    with pytest.raises(IdentityError,match='V2-only'):planner.joint_path(None,None,None,None)
    instance=object.__new__(planner.OpenArmPlanner)
    with pytest.raises(IdentityError,match='V2-only'):instance.inverse([0,0,0])
    with pytest.raises(IdentityError,match='V2-only'):instance.plan([0,0,0])


def test_franka_owner_fails_before_source_checks_or_spawn():
    from benchmarks.attention_harness.robocasa_native.formal_services import DedicatedRobocasaServices
    with pytest.raises(IdentityError,match='V2-only'):
        object.__new__(DedicatedRobocasaServices).__enter__()


def test_365_owner_fails_before_token_file_and_spawn():
    from benchmarks.attention_harness.robocasa365.service import DedicatedService
    with pytest.raises(IdentityError,match='V2-only'):
        object.__new__(DedicatedService).start()


def test_franka_unmanaged_agent_backend_cannot_bypass_owner():
    from benchmarks.attention_harness.robocasa_native.agent_actions import AgentServerActionBackend
    with pytest.raises(IdentityError,match='V2-only'):
        AgentServerActionBackend(simulator_attested=True,transport=lambda *a:pytest.fail('unmanaged transport contacted'))


def test_unmanaged_simulator_planning_cannot_contact_old_service():
    from benchmarks.attention_harness.robocasa_native.client import RobocasaSimClient
    client=RobocasaSimClient('counter_to_sink',transport=lambda *a:pytest.fail('old service contacted'))
    for path in ['/plan','/plan/joint','/plan/ik','/plan_base','/actions','/trajectory/actions']:
        with pytest.raises(IdentityError,match='V2-only'):client._call('POST',path,{})


def test_regenerated_manifest_cannot_impersonate_official_v2(tmp_path):
    original=deployment.R12_ROOT/'runtime-lock.json'
    value=json.loads(original.read_text());value['provenance']='locally regenerated but same version'
    path=tmp_path/'forged.json';path.write_text(json.dumps(value))
    with pytest.raises(IdentityError,match='regenerated local hash'):load_lock(path)


def test_environment_cannot_select_legacy_deployment(monkeypatch):
    monkeypatch.setenv('CUROBO_V2_LOCK','/legacy/manifest.json')
    path,lock=deployment.fixed_deployment()
    assert path==deployment.MAIN_DEPLOYMENT_ROOT/'runtime-lock.json'
    assert lock['library_revision']==deployment.OFFICIAL_COMMIT


def test_retired_direct_native_runtimes_cannot_initialize():
    # These modules need SAPIEN. Their service owners and planners are tested
    # independently above in the lightweight UI runtime.
    from benchmarks.attention_harness.robocasa365.display_service.robocasa365_sim.backend import OfficialBackend
    with pytest.raises(IdentityError,match='V2-only'):
        OfficialBackend(roots={},asset_manifest='/missing',evidence_dir='/missing')


@pytest.mark.parametrize('tamper', ['waypoint','velocity','timestamp','certificate','clearance'])
def test_recorded_public_curve_tampering_never_issues_capability(tamper):
    from curobo_v2.trajectory import TrajectoryLedger
    from curobo_v2.client import PlanningRejected
    plan=json.loads((Path(__file__).parent/'fixtures/recorded_v2_micro_joint_plan.json').read_text())
    if tamper=='waypoint':plan['trajectory'][2][0]+=.01
    elif tamper=='velocity':plan['trajectory_derivatives']['velocity'][2][0]+=.01
    elif tamper=='timestamp':plan['time_from_start_seconds'][2]+=.001
    elif tamper=='certificate':plan['validation'].pop('continuous_geometry')
    else:plan['validation']['continuous_geometry']['clearance_m']=0.
    with pytest.raises(PlanningRejected):
        TrajectoryLedger().issue(plan,epoch=0,state=plan['trajectory'][0],world=[],identity={'runtime':{'generation':2},'nonce':'test'},attachment=None)


@pytest.mark.parametrize('field', ['library_root','official_files','dependencies','distributions','native_assets'])
def test_adapter_registry_cannot_relabel_official_or_environment_identity(tmp_path,monkeypatch,field):
    import hashlib
    import curobo_v2.identity as identity
    original=deployment.MAIN_DEPLOYMENT_ROOT/'runtime-lock.json'
    value=json.loads(original.read_text())
    value[field] = '/legacy/curobo' if field=='library_root' else {'legacy':'changed'}
    path=tmp_path/'forged.json';path.write_text(json.dumps(value))
    sha=hashlib.sha256(path.read_bytes()).hexdigest()
    registry=tmp_path/'registry.json';registry.write_text(json.dumps({sha:{'lock_path':str(path),
        'official_commit':deployment.OFFICIAL_COMMIT,'parent_sha256':deployment.R12_LOCK_SHA256}}))
    monkeypatch.setattr(identity,'DEPLOYMENT_REGISTRY',registry)
    monkeypatch.setattr(identity,'_registry_digest',None)
    with pytest.raises(IdentityError,match='official source/environment'):load_lock(path)


def test_declared_parameters_cannot_silently_select_worker_defaults(tmp_path):
    with pytest.raises(IdentityError,match='parameters'):
        deployment.fixed_owner(tmp_path/'owner',task='counter_to_sink',seed=101,arm='left',deadline=1e12,
            parameters_path=get('test.native_robot_config'))


@pytest.mark.parametrize('failure',['group','action','speed','held_target','missing_joint','planner_group','missing_planner'])
def test_v2_cleanup_requires_measured_hold_and_gone_process_group(failure):
    from benchmarks.attention_harness.service_recovery import service_stop_confirmed
    stop=json.loads(get('deployments.main_native_stop').read_text())
    stop.update(mechanism='authenticated_v2_native_process_group',process_group_gone=True,
                planner_process_groups=[{'confirmed':True,'reaped':True,'process_group_gone':True}],
                robot={'model':'OpenArm','execution_arm':'left'})
    assert service_stop_confirmed(stop,'robocasa')
    if failure=='group':stop['process_group_gone']=False
    elif failure=='action':stop['stop_barrier']['active_action']='0:1'
    elif failure=='speed':stop['stop_barrier']['qvel'][0]=.06
    elif failure=='held_target':stop['stop_barrier']['drive_target'][0]+=.011
    elif failure=='planner_group':stop['planner_process_groups'][0]['process_group_gone']=False
    elif failure=='missing_planner':stop.pop('planner_process_groups')
    else:stop['stop_barrier']['joint_order'][0]='wrong_joint'
    assert not service_stop_confirmed(stop,'robocasa')


def test_fixed_ui_rejects_historical_modified_solver_before_subprocess(tmp_path,monkeypatch):
    from benchmarks.attention_harness.fixed_ui import FixedUI
    monkeypatch.setattr('subprocess.Popen',lambda *a,**k:pytest.fail('historical UI subprocess started'))
    with pytest.raises(IdentityError,match='V2-only'):
        object.__new__(FixedUI).start({})


def test_independent_planner_group_must_actually_exit(tmp_path):
    import os, signal, subprocess, sys
    from curobo_v2.native_session import NativeV2Service
    owner=object.__new__(NativeV2Service);owner.evidence=tmp_path;owner.lock_sha='bound-lock'
    directory=tmp_path/'native/v2-epoch-0';directory.mkdir(parents=True)
    child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'],start_new_session=True)
    try:
        (directory/'service_spawn.json').write_text(json.dumps({'pid':child.pid,'lock_sha256':'bound-lock'}))
        # A premature/stale reaping claim cannot hide a still-live child group.
        (directory/'service_stop.json').write_text(json.dumps({'pid':child.pid,'reaped':True}))
        assert owner._planner_groups()[0]['confirmed'] is False
        os.killpg(child.pid,signal.SIGTERM);child.wait(timeout=3)
        assert owner._planner_groups()[0]['confirmed'] is True
        (directory/'service_spawn.json').write_text(json.dumps({'pid':child.pid,'lock_sha256':'another-lock'}))
        assert owner._planner_groups()[0]['confirmed'] is False
    finally:
        if child.poll() is None:os.killpg(child.pid,signal.SIGKILL);child.wait(timeout=3)


def test_old_comparison_client_cannot_contact_v1_or_unverified_v2(monkeypatch):
    from eval.curobo_v1_v2.bench._client import CuroboClient
    monkeypatch.setattr('urllib.request.urlopen',lambda *a,**k:pytest.fail('old comparison service contacted'))
    with pytest.raises(RuntimeError,match='V2-only'):CuroboClient('http://127.0.0.1:7050').warmup()


@pytest.mark.parametrize('path',['/plan','/plan/joint','/actions','/trajectory/actions','/grasp','/base/actions'])
def test_forged_generation_string_cannot_authorize_unmanaged_openarm_transport(path):
    from benchmarks.attention_harness.robocasa_native.openarm_backend import OpenArmActionBackend
    def transport(method,endpoint,body,timeout):
        assert endpoint=='/health', 'unmanaged mutation was dispatched'
        return {'ready':True,'robot':{'model':'OpenArm','execution_arm':'left'},'planner_generation':2}
    backend=OpenArmActionBackend('http://127.0.0.1:8872',transport=transport)
    with pytest.raises(IdentityError,match='V2-only'):backend._call('POST',path,{})


def test_v2_launch_config_binds_actual_execution_environment_and_rejects_old_schema():
    from types import SimpleNamespace
    from curobo_v2.prepare_config import prepare
    from benchmarks.attention_harness.robocasa_native.openarm_config import validate_config,ROOT
    source=ROOT/'openarm_robocasa/launch_configs/counter_to_sink_left_engineering6.json'
    request=SimpleNamespace(task_id='counter_to_sink',seed=101)
    with pytest.raises(ValueError,match='V2-only'):validate_config(request,json.loads(source.read_text()))
    config=prepare(source)
    assert config['v2_deployment']['official_commit']==deployment.OFFICIAL_COMMIT
    config['v2_deployment']['python']='/legacy/python'
    with pytest.raises(ValueError,match='declared runtime'):validate_config(request,config)


@pytest.mark.parametrize('script', ['eval/curobo_v1_v2/scripts/start_v1.sh','eval/curobo_v1_v2/scripts/start_v2.sh','skill-agent-setup/claude-code/scripts/start_all.sh'])
def test_shell_launchers_fail_explicitly_without_spawning_services(script):
    import subprocess
    root=Path(__file__).resolve().parents[3]
    result=subprocess.run(['bash',str(root/script),'--start'],capture_output=True,text=True,timeout=5)
    assert result.returncode==2
    assert 'V2-only' in result.stderr
