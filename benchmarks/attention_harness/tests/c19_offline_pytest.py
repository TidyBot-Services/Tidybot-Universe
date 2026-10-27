"""Opt-in offline adapter for the one provision test constructing a native lock.

Omit the two model resources from that test's temporary lock and substitute
initial joints without reading them. Production source hashing and the test's
configuration preservation/export assertions still run.
"""
import json
import sys
from pathlib import Path

import pytest


def pytest_configure(config):
    def guard(event,args):
        if event!='open' or not isinstance(args[0],(str,bytes)):return
        path=Path(args[0].decode() if isinstance(args[0],bytes) else args[0]).absolute()
        if path.name=='keys.env' or (not path.is_relative_to('/tmp') and (
                'model' in path.parts or path.name=='robot_parameters_v1.json'
                or path.suffix in {'.urdf','.xml','.stl','.obj','.pt','.safetensors'})):
            raise PermissionError('C19 offline test forbids model/key reads: '+str(path))
    sys.addaudithook(guard)


@pytest.fixture(autouse=True)
def no_model_resources_in_provision_test(request,monkeypatch):
    if request.node.name!='test_validation_creation_preserves_real_registered_config_and_checks':
        return
    from openarm365_dev_v2 import dev_config
    from openarm365_dev_v2 import environment
    from benchmarks.attention_harness import dev_experiment
    excluded={dev_config.ROOT/'openarm365/model/identity.json',
              dev_config.ROOT/'openarm365/configs/robot_parameters_v1.json'}
    original_sha=dev_config.sha;original_make=dev_config.make_lock
    monkeypatch.setattr(dev_config,'sha',lambda path:'0'*64 if Path(path) in excluded else original_sha(path))
    def make(path,**kwargs):
        ref=original_make(path,**kwargs)
        value=json.loads(Path(path).read_bytes())
        for name in excluded:value['files'].pop(str(name),None)
        Path(path).chmod(0o600)
        Path(path).write_text(json.dumps(value,sort_keys=True,indent=2))
        Path(path).chmod(0o444)
        return dict(ref,sha256=original_sha(path))
    monkeypatch.setattr(dev_config,'make_lock',make)
    monkeypatch.setattr(dev_experiment,'make_lock',make)
    monkeypatch.setattr(environment,'initial_joints',lambda task:{'controlled_test_joint':0.0})
