"""C12 variant: motion source, fake native receipts and replay, real import gates."""
import json
import sqlite3
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest.mock import patch

from benchmarks.attention_harness.tests import autovalidate_fakes as af, cross_seed_fakes as cf
from benchmarks.attention_harness.memory_independent_validator import validate_arm
from benchmarks.attention_harness.memory_motion_permission import ACTION, PURPOSE, validation_scope
from benchmarks.attention_harness.dev_ledger import encoded
from benchmarks.attention_harness.dev_loop import sha
from benchmarks.attention_harness.memory_validation_worker import tick

CODE = 'from robot_sdk import left_arm, left_gripper\ndef policy(sdk):\n    left_arm.move_delta([0, 0, 0.001])\n    left_gripper.close()\n'


class MotionSceneFixture(af.SceneFixture):
    def write(self, path, value):
        if path.name == 'config.json' and isinstance(value, dict):
            value.update(budget_tier='dev', runner_boundary='dev_loop')
        if path.name == 'bundle.json':
            value.update(runner_boundary='dev_loop')
        return super().write(path, value)

    def make(self, *args, **kwargs):
        kwargs['code'] = CODE + '# ' + str(args[0]) + '\n' if 'from robot_sdk' not in kwargs.get('code', '') else kwargs['code']
        r = super().make(*args, **kwargs)
        d = r['descriptor']
        d['import_purpose'] = PURPOSE
        d['permission_scope'] = validation_scope(r['name'], 1, Path(r['record']['source']['uri']).read_text(), r['config'], r['bundle'], PURPOSE)
        r['verdict']['permission_scope'] = d['permission_scope']
        # Native motion receipts are still explicitly synthetic.
        trace_ref = r['bundle']['artifacts']['trace']; trace = json.loads(Path(trace_ref['uri']).read_bytes())
        for event in trace.get('sdk_events', []):
            if event['source'] == 'sensors':event.update(source='left_gripper', operation='close')
        r['bundle']['artifacts']['trace'] = self.write(Path(trace_ref['uri']), trace)
        r['verdict']['trace'] = r['bundle']['artifacts']['trace']
        bref = self.write(self.root/r['name']/'bundle.json', r['bundle']); r['verdict']['bundle'] = bref
        with sqlite3.connect(d['ledger']['uri']) as db:
            job = json.loads(db.execute("SELECT payload FROM v2_jobs WHERE kind='execute'").fetchone()[0])
            job['receipt'].update(bundle=bref, trace=r['bundle']['artifacts']['trace'])
            job['receipt']['public_feedback'] = self.write(Path(job['receipt']['public_feedback']['uri']), {**af.public_trace_projection(trace), 'evidence_kind':'controlled_test'})
            db.execute("UPDATE v2_jobs SET payload=? WHERE kind='execute'", (encoded(job),))
        d['ledger']['sha256'] = sha(d['ledger']['uri'])
        self.write(self.root/r['name']/'execution.json', d)
        self.write(self.validator/r['name']/'verdict.json', r['verdict'])
        return r


@contextmanager
def motion_fakes():
    # Existing fake adapters omit the newly sealed purpose when calling the
    # validator; the production LedgerRunner supplies it from its descriptor.
    def validate(**kwargs):
        return validate_arm(**dict(kwargs, import_purpose=PURPOSE))
    with ExitStack() as stack:
        stack.enter_context(patch.object(af, 'SceneFixture', MotionSceneFixture))
        stack.enter_context(patch.object(af, 'validate_arm', validate))
        stack.enter_context(patch.object(cf, 'validate_arm', validate))
        yield


def lifecycle(root):
    with motion_fakes():
        fake = cf.AutomaticCrossFakeRunner(root/'fake', root/'worker')
        note = fake.promote_origin()
        assert fake.replays == 2
        fake.enable_cross(); state = {}; deps = fake.deps(root/'worker')
        af.atomic(root/'worker'/'state.json', state)
        for _ in range(60):
            tick(state, deps); fake.phase = 'completed'
            if fake.service.decision(note['memory_id'])['trust_scope'] == 'task_scoped':break
        assert fake.service.decision(note['memory_id'])['trust_scope'] == 'task_scoped', state.get('status')
        assert state['runs_today'] == 18
        with sqlite3.connect(fake.service.store.path) as db:
            executions = [json.loads(p) for p, in db.execute("SELECT payload FROM dev_v2_imports WHERE kind='execution'")]
        assert len(executions) == 29  # origin + 10 same-scene arms + 18 cross-seed runs
        assert all(e['permission']['action'] == ACTION and e['permission']['actor'] == ACTION for e in executions)
        row = fake.service.get_dev_v2_memory_public(note['memory_id'])
        assert row['motion_permission'] == dict(action=ACTION, actor=ACTION)
        fake.demote_task(note['memory_id'])
        return fake, state
