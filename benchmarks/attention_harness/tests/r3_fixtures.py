"""Explicit offline deployment substitute; never modifies the runtime registry."""
import hashlib
import pytest
from curobo_v2 import execution_settings as es


def fake_deployment(monkeypatch,tmp_path):
    path=tmp_path/'r3-test-deployment-06'/'runtime-lock.json'
    path.parent.mkdir(exist_ok=True);path.write_text('{"controlled_test": "deployment-06"}\n')
    pin=dict(lock_path=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    monkeypatch.setattr(es,'_deployment_pin',lambda:dict(pin))
    return es.from_config(es.R3_20261025.to_dict())


@pytest.fixture
def r3_deployment(monkeypatch,tmp_path):
    return fake_deployment(monkeypatch,tmp_path)
