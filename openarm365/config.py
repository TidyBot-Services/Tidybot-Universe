"""Independent full-task contract, keeping the PandaOmron v1 freeze intact."""
import copy,json,hashlib
from pathlib import Path
from benchmarks.attention_harness.robocasa365.config import EXPECTED
ROOT=Path(__file__).resolve().parent
BUDGETS={'pick_place_counter_to_sink':(2400,2500,480,600),'open_cabinet':(2000,3200,420,540)}

def make_config(task):
    if task not in BUDGETS: raise ValueError('unsupported OpenArm365 task')
    original=Path('/home/truares/Documents/Codex/2026-10-09/new-chat/outputs/robocasa365/configs')/(task+'_seed101.json')
    c=json.loads(original.read_text());h,calls,attempt,whole=BUDGETS[task]
    c['camera_names']=['robot0_agentview_left','gripper0_right_eye_in_hand']
    c.update(python_hash_seed=0,schema_version='attentionbench.openarm365-config.v1',robot='OpenArm365',controller='BASIC',execution_arm='left',horizon=h,sdk_call_limit=calls,attempt_wall_seconds=attempt,whole_case_wall_seconds=whole,model_identity_sha256=hashlib.sha256((ROOT/'model/identity.json').read_bytes()).hexdigest())
    sources=[ROOT/n for n in ['model.py','controller.py','robot.py','frames.py','environment.py','config.py','budget.py','configs/robot_parameters_v1.json','service/python','service/robocasa365_sim/backend.py','service/robocasa365_sim/server.py']]
    sources += [ROOT.parent/'benchmarks/attention_harness/robocasa365'/n for n in ['adapter.py','config.py','budget.py','sandbox.py','formal_runner.py','recording.py','human_camera.py']]
    c['integration_sources']={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
    return c

def validate_config(c):
    if not isinstance(c,dict) or c!=make_config(c.get('task_id')) or any(type(c[k]) is not type(v) for k,v in make_config(c.get('task_id')).items()):
        raise ValueError('OpenArm365 complete frozen configuration mismatch')
    identity=json.loads((ROOT/'model/identity.json').read_text())
    for path,sha in identity['sources'].items():
        if hashlib.sha256(Path(path).read_bytes()).hexdigest()!=sha: raise ValueError('OpenArm365 model source drift: '+path)
    return c
