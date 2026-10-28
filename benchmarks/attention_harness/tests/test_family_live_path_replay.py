"""The replay's relocation and provider substitutions must preserve real checks."""
import json
from pathlib import Path
import sqlite3

from scripts.family_live_path_replay import CopyEvidence, Provider, digest, read
from benchmarks.attention_harness.dev_v2_loop import immutable


def test_relocation_reseals_references_and_sqlite_without_editing_original(tmp_path):
    old=tmp_path/'original';new=tmp_path/'copy'
    lock=immutable(old/'template/identity-lock.json',{'frozen':'unchanged'})
    config=immutable(old/'probe/config.json',dict(seed=114,dev_lock=dict(path=lock['uri'],sha256=lock['sha256'])))
    policy=immutable(old/'probe/v1/policy.py','from robot_sdk import sensors\nsensors.get_observation()\n')
    record=immutable(old/'probe/v1/version.json',dict(config_sha256=config['sha256'],source=policy,config=config))
    ledger=old/'probe/ledger.sqlite3'
    with sqlite3.connect(ledger) as db:
        db.execute('CREATE TABLE records(id TEXT,payload TEXT)')
        db.execute('INSERT INTO records VALUES (?,?)',('version',json.dumps(read(record['uri']))))
    before={p:p.read_bytes() for p in old.rglob('*') if p.is_file()}
    copier=CopyEvidence(old,new);copier.experiment(old/'probe')
    copied=read(new/'probe/v1/version.json')
    assert copied['config']['uri']==str(new/'probe/config.json')
    assert copied['config_sha256']==copied['config']['sha256']==digest(new/'probe/config.json')
    assert copied['source']['sha256']==policy['sha256']
    assert (new/'template/identity-lock.json').read_bytes()==before[Path(lock['uri'])]
    with sqlite3.connect(new/'probe/ledger.sqlite3') as db:
        assert json.loads(db.execute('SELECT payload FROM records').fetchone()[0])==copied
    assert all(p.read_bytes()==raw for p,raw in before.items())


def test_replay_provider_passes_real_service_wire_and_usage_checks():
    from attention_memory_service.provider_evidence import verify_provider
    provider=Provider();wire=[]
    provider.response_observer=lambda status,headers,raw:wire.append(dict(http_status=status,body=json.loads(raw)))
    request=dict(model_source='codex',model='gpt-6-astra',messages=[dict(role='system',content='offline'),
        dict(role='user',content=json.dumps(dict(previous_complete_source='pass\n')))])
    response=provider.chat(**request);output=dict(response.artifact(),model_source='codex')
    def require(condition,message):assert condition,message
    assert verify_provider(request,output,wire[0],dict(usage=response.usage),
        dict(provider_tokens=200,input_tokens=100,output_tokens=100),
        dict(selection=dict(model_source='codex'),model=dict(model='gpt-6-astra')),
        dict(model_source='codex'),'model',require)=='codex_exec'


def test_native_adapter_drops_unproduced_recording_refs_preserving_costs():
    from scripts.family_live_path_replay import replay_artifact
    original = dict(camera_recording=dict(video=dict(uri='/old/video.mp4',sha256='a'*64)),
                    sdk_call_budget=dict(dispatched=74),control_steps=80,elapsed_seconds=12.5)
    value = replay_artifact('sandbox_receipt', original, lambda v:v, {})
    assert value['camera_recording'] == dict(enabled=False,test_only=True)
    assert {k:v for k,v in value.items() if k!='camera_recording'} == {k:v for k,v in original.items() if k!='camera_recording'}
    assert original['camera_recording']['video']['uri']=='/old/video.mp4'
