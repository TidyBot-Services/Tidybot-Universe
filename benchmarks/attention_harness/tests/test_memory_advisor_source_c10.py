"""C10 synthetic non-planner history, read-only preflight and in-process service."""
import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from benchmarks.attention_harness import memory_origin_evidence as origin
from benchmarks.attention_harness import memory_validation_worker as worker
from benchmarks.attention_harness.core.dev_store import DevConflict
from benchmarks.attention_harness.dev_v2_memory_import import prepare
from benchmarks.attention_harness.tests.autovalidate_fakes import AutomaticOriginFakeRunner, Fixture
from benchmarks.attention_harness.tests.r3_fixtures import r3_deployment
from benchmarks.attention_harness.tests.test_memory_origin_c9 import snapshot, synthetic_export

pytestmark = pytest.mark.usefixtures('r3_deployment')


def make_fake(tmp_path, *, planner=False, fault=None):
    write = Fixture.write; advice = Fixture.add_advice

    def write_origin(path, value):
        if not planner and isinstance(value, dict):
            if value.get('schema') == 'attentionbench.dev-experiment.v3':
                value['selection'] = {'policies': ['reactive_help']}
                for cell in value['cells']: cell['policy_id'] = 'reactive_help'
                if fault == 'planner_features': value['selection']['planner_features'] = {'memory': {}}
            if value.get('schema_version') == 'attentionbench.formal-runner-result.v1':
                value['policy_id'] = 'reactive_help'
            if path.name == 'help.json':
                if fault == 'content': value['content'] += '\n'
                if fault == 'rejected': value['rejected'] = True
            if path.name in ('advisor-input.json', 'model-input.json'):
                packet = json.loads(value['messages'][-1]['content'])
                if fault == 'previous' and path.name == 'advisor-input.json': packet['previous_complete_source'] += '\n'
                if fault == 'feedback' and path.name == 'advisor-input.json': packet['public_feedback'] = {}
                if fault == 'guidance' and path.name == 'model-input.json': packet['attention_control'].pop('help')
                value['messages'][-1]['content'] = json.dumps(packet, sort_keys=True, ensure_ascii=False)
        return write(path, value)

    def advice_origin(f, failed, *args):
        d = advice(f, failed, *args)
        if planner: return d
        with sqlite3.connect(d['ledger']['uri']) as db:
            if fault != 'existing_request': db.execute('DROP TABLE v2_planner_requests')
            else:
                request = json.loads(db.execute('SELECT payload FROM v2_planner_requests').fetchone()[0])
                request['adopted'] = False
                db.execute('UPDATE v2_planner_requests SET payload=?', (json.dumps(request),))
            if fault == 'unsettled': db.execute("UPDATE v2_jobs SET state='claimed' WHERE kind='advisor'")
            row = json.loads(db.execute("SELECT payload FROM v2_jobs WHERE kind='advisor'").fetchone()[0])
            if fault == 'cost': row['actual']['help_count'] = 0
            if fault == 'late': row['settled_at'] = 5.5
            db.execute("UPDATE v2_jobs SET payload=? WHERE kind='advisor'", (json.dumps(row),))
        from attention_memory_service.dev_v2 import digest
        d['ledger']['sha256'] = digest(Path(d['ledger']['uri']).read_bytes()); d.pop('request_id')
        f.write(f.root/'failed/execution.json', failed['descriptor'])
        f.write(f.root/'failed/source.json', d)
        return d

    with patch.object(Fixture, 'write', staticmethod(write_origin)), patch.object(Fixture, 'add_advice', advice_origin):
        fake = AutomaticOriginFakeRunner(tmp_path/'fake', tmp_path/'worker')
    if not planner: fake.f.write = write_origin  # Generated fake arms keep the origin asking policy.
    fake.deps(fake.provision.state_root)
    worker.atomic(fake.provision.state_root/'runtime.json', dict(fake.provision.config, validator_root=str(fake.f.validator)))
    return fake


@pytest.mark.parametrize('planner', [False, True])
def test_check_origin_and_source_export(tmp_path, planner, capsys):
    fake = make_fake(tmp_path, planner=planner)
    state = snapshot(fake.provision.state_root); history = snapshot(fake.f.root)
    with synthetic_export(fake):
        assert worker.main(['--state-dir', str(fake.provision.state_root), '--check-origin', fake.mid], client=fake.service) == 0
    assert capsys.readouterr().out.startswith('PASS:')
    assert snapshot(fake.provision.state_root) == state and snapshot(fake.f.root) == history
    assert fake.replays == 0 and not fake.starts
    found = origin.locate(fake.provision, {'memory_id': fake.mid})
    assert bool(found['request_id']) is planner
    with synthetic_export(fake):
        ctx, sources = origin.export_context(fake.provision, found)
        prepare(found['store'], 'failed', 2, ctx['namespace'], fake.f.root/'export', fake.f.root,
                source=origin.source_prerequisites(found), source_catalogs=sources)
    descriptor = json.loads((fake.f.root/'export/descriptor.json').read_bytes())
    assert ('request_id' in descriptor) is planner
    if not planner:
        with found['store'].tx() as db:
            assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='v2_planner_requests'").fetchone()


@pytest.mark.parametrize('fault,reason', [('unsettled', 'settled'), ('cost', 'cost'),
    ('late', 'after model'), ('content', 'guidance'), ('guidance', 'generation input'),
    ('rejected', 'guidance'), ('planner_features', 'non-planner'),
    ('existing_request', 'missing or ambiguous'), ('previous', 'failed trace'), ('feedback', 'failed trace')])
def test_origin_and_direct_export_refuse_bad_job_evidence(tmp_path, fault, reason):
    fake = make_fake(tmp_path, fault=fault)
    before = snapshot(fake.f.root)
    with pytest.raises(DevConflict, match=reason): origin.locate(fake.provision, {'memory_id': fake.mid})
    store = origin.OriginalStore(fake.failed['descriptor']['ledger']['uri'])
    with synthetic_export(fake), pytest.raises(DevConflict, match=reason):
        prepare(store, 'failed', 2, fake.failed['context']['namespace'], fake.f.root/'export', fake.f.root,
                source=dict(failed_execution_import_id='failed', validator_id='validator-failed',
                            validation_descriptor='failed/verdict.json', request_id=None))
    assert snapshot(fake.f.root) == before and fake.replays == 0


def test_reactive_origin_candidate_and_validation_plan_accepted(tmp_path):
    fake = make_fake(tmp_path); deps = fake.deps(fake.provision.state_root)
    worker.set_enabled(deps.root, True); state = {}
    for _ in range(35):
        worker.tick(state, deps); fake.phase = 'completed'
        if state.get('results'): break
    assert state.get('results'), state['status']
    assert state['results'][0]['status'] == 'trusted', state['status']
    assert fake.replays == 2 and len(fake.starts) == 10 and state['runs_today'] == 11
    provenance = fake.service.provenance(fake.candidate_id)
    source = fake.service.dev_v2.source(provenance['source_import_id'])
    assert source['advisor_evidence'] == 'advisor_job' and source['source_policy_id'] == 'reactive_help'
    assert fake.batch['plan'] and fake.service.get_memory(fake.candidate_id).status.value == 'trusted'
