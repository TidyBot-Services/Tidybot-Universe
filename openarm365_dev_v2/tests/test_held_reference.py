"""Offline held-reference timing and frozen-config regression."""
import ast
import contextlib
import hashlib
import io
import json
import math
import subprocess
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import numpy as np
import pytest

from openarm365_dev import executor as ex
from openarm365_dev_v2 import dev_config


def pose(angle_deg=0, x=0):
    angle=math.radians(angle_deg)
    result=np.eye(4)
    result[:3,:3]=[[math.cos(angle),-math.sin(angle),0],
                   [math.sin(angle),math.cos(angle),0],[0,0,1]]
    result[0,3]=x
    return result


@pytest.mark.parametrize('angles,expected_wait,stable',[
    ([0]*11,10,True),
    ([i*.1 for i in range(61)],60,False),
    ([i*.1 for i in range(6)]+[.5+i*.01 for i in range(1,11)],15,True),
])
def test_pure_stability_sequences(angles,expected_wait,stable):
    previous=pose(angles[0]);streak=0;wait=0
    for angle in angles[1:]:
        current=pose(angle)
        streak,_,_=ex.held_reference_step(previous,current,streak)
        previous=current;wait+=1
        if streak==ex.HELD_REFERENCE_STABLE_STEPS:break
    assert wait==expected_wait
    assert (streak>=ex.HELD_REFERENCE_STABLE_STEPS)==stable
    assert ex.held_reference_step(pose(),pose(x=.00021),9)[0]==0
    assert ex.held_reference_step(pose(),pose(.051),9)[0]==0


class FakeExecutor:
    def __init__(self, angles, mode):
        self.angles=angles;self.sequence=0;self.events=[]
        self.journal=Path('unused');self.grasp_counts={'left':{7:12},'right':{}}
        self.held=None;self.held_transform=None;self.held_trace_enabled=False
        self.held_reference=dict(configured='fixed',source='config',mode=mode)
        self.grips={'left':0};self.planner=SimpleNamespace(ledger=SimpleNamespace(invalidate=lambda _:None))
        self.contact_event_count=0;self.contact_event_overflow=0
    def check_live(self):pass
    def control_step(self):self.sequence+=1
    def log(self,path,event):self.events.append(event)


def drive(monkeypatch,angles,mode='fixed',method=None):
    obj=FakeExecutor(angles,mode)
    monkeypatch.setattr(ex,'tcp_pose',lambda env,side:np.eye(4))
    attachment=ModuleType('openarm365_dev.attachment')
    attachment.object_pose=lambda env,bid:pose(angles[min(max(obj.sequence-20,0),len(angles)-1)])
    monkeypatch.setitem(sys.modules,'openarm365_dev.attachment',attachment)
    obj.env=SimpleNamespace()
    if method is not None:method.__globals__['tcp_pose']=ex.tcp_pose
    (method or ex.NativeExecutor.gripper)(obj,'left',1.,request_id='close')
    return obj


def head_gripper():
    source=subprocess.check_output(['git','show','HEAD:openarm365_dev/executor.py'],text=True)
    node=next(item for item in ast.parse(source).body if isinstance(item,ast.ClassDef) and item.name=='NativeExecutor')
    method=next(item for item in node.body if isinstance(item,ast.FunctionDef) and item.name=='gripper')
    module=ast.fix_missing_locations(ast.Module(body=[method],type_ignores=[]))
    namespace=dict(vars(ex))
    exec(compile(module,'HEAD:openarm365_dev/executor.py','exec'),namespace)
    return namespace['gripper']


def test_fixed_matches_head_control_and_event_sequence(monkeypatch):
    baseline=drive(monkeypatch,[0],method=head_gripper())
    current=drive(monkeypatch,[0])
    assert baseline.sequence==current.sequence==20
    assert np.array_equal(baseline.held_transform,current.held_transform)
    assert [e['event'] for e in baseline.events]==[e['event'] for e in current.events]
    assert baseline.events[0]==current.events[0]
    historical_receipt={k:current.events[-1][k] for k in baseline.events[-1]}
    assert json.dumps(historical_receipt,sort_keys=True)==json.dumps(baseline.events[-1],sort_keys=True)
    assert current.events[-1]['held_reference_wait_steps']==0


def test_settled_reference_after_stability(monkeypatch):
    angles=[0]+[i*.1 for i in range(1,6)]+[.5+i*.01 for i in range(1,11)]
    obj=drive(monkeypatch,angles,mode='settled')
    assert obj.sequence==35
    assert np.allclose(obj.held_transform,pose(.6))
    event=next(e for e in obj.events if e['event']=='held_reference')
    assert event['wait_steps']==15 and event['stable'] is True
    assert event['held_reference_unstable'] is False
    assert event['max_rotation_deg']==pytest.approx(.6)
    assert obj.events[-1]['held_reference_wait_steps']==15


def test_settled_timeout_uses_current_pose(monkeypatch):
    obj=drive(monkeypatch,[i*.1 for i in range(61)],mode='settled')
    assert obj.sequence==80
    assert np.allclose(obj.held_transform,pose(6.))
    assert next(e for e in obj.events if e['event']=='held_reference')['held_reference_unstable'] is True


def test_modes_and_original_bounds(monkeypatch):
    from curobo_v2 import attachment
    assert attachment.ATTACH_TRANSLATION_M==.004
    assert attachment.ATTACH_ROTATION_RAD==.03
    assert '>.004' in Path(ex.__file__).read_text()
    assert '>.03' in Path(ex.__file__).read_text()
    monkeypatch.delenv('ATTN_DIAG_HELD_REFERENCE_MODE',raising=False)
    assert ex.held_reference_mode('fixed')==dict(configured='fixed',source='config',mode='fixed')
    monkeypatch.setenv('ATTN_DIAG_HELD_REFERENCE_MODE','settled')
    assert ex.held_reference_mode('fixed')==dict(configured='fixed',source='ATTN_DIAG_HELD_REFERENCE_MODE',mode='settled')
    monkeypatch.setenv('ATTN_DIAG_HELD_REFERENCE_MODE','bad')
    with pytest.raises(ValueError,match='ATTN_DIAG_HELD_REFERENCE_MODE'):ex.held_reference_mode('fixed')


def test_mode_frozen_and_hashed(monkeypatch):
    monkeypatch.setattr(dev_config,'verify_lock',lambda *args,**kwargs:None)
    c=dev_config.make_dev_config('pick_place_counter_to_sink',102,{'path':'fixture','sha256':'fixture'})
    assert c['held_reference_mode']=='fixed'
    other=dict(c,held_reference_mode='settled')
    assert dev_config.validate_dev_config(other)==other
    digest=lambda value:hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()
    assert digest(c)!=digest(other)
    with pytest.raises(ValueError,match='held_reference_mode'):
        dev_config.validate_dev_config(dict(c,held_reference_mode='bad'))


def test_probe_dry_run_and_variant_flag(tmp_path):
    from benchmarks.attention_harness.diagnostics import grasp_chain_probe,grasp_variants
    args=['--base-shift','0,.25,0','--theta-deg','55','--dry-run']
    def source(extra):
        output=io.StringIO()
        with contextlib.redirect_stdout(output):grasp_chain_probe.main(args+extra)
        return output.getvalue()
    assert source([])==source(['--held-reference-mode','settled'])
    out=tmp_path/'variants'
    grasp_variants.main(['--variants','base55:theta=55','--out',str(out),'--dry-run',
                         '--held-reference-mode','settled'])
    rows=json.loads((out/'results.json').read_text())
    assert rows[0]['command'][-2:]==['--held-reference-mode','settled']
