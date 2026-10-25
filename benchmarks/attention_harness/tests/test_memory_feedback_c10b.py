"""C10b read-only origin and export with the real-shaped feedback additions."""
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from benchmarks.attention_harness import memory_origin_evidence as origin
from benchmarks.attention_harness import memory_validation_worker as worker
from benchmarks.attention_harness.core.dev_store import DevConflict
from benchmarks.attention_harness.dev_v2_memory_import import prepare
from benchmarks.attention_harness.tests.autovalidate_fakes import Fixture
from benchmarks.attention_harness.tests.r3_fixtures import r3_deployment
from benchmarks.attention_harness.tests.test_memory_advisor_source_c10 import make_fake
from benchmarks.attention_harness.tests.test_memory_origin_c9 import snapshot, synthetic_export
from tests.test_feedback_projection_c10b import projected_writer, with_eval

pytestmark = pytest.mark.usefixtures('r3_deployment')


def enriched_fake(tmp_path, fault=None, target='advisor', planner=False):
    with patch.object(Fixture, 'write', staticmethod(projected_writer(Fixture.write, fault, target))), \
         patch.object(Fixture, 'add_advice', with_eval(Fixture.add_advice, fault)):
        return make_fake(tmp_path, planner=planner)


@pytest.mark.parametrize('planner', [False, True])
def test_check_origin_exports_sealed_eval_and_projection(tmp_path, planner, capsys):
    fake = enriched_fake(tmp_path, planner=planner)
    history, state = snapshot(fake.f.root), snapshot(fake.provision.state_root)
    with synthetic_export(fake):
        assert worker.main(['--state-dir', str(fake.provision.state_root), '--check-origin', fake.mid], client=fake.service) == 0
    assert capsys.readouterr().out.startswith('PASS:')
    assert snapshot(fake.f.root) == history and snapshot(fake.provision.state_root) == state
    assert fake.replays == 0 and not fake.starts
    found = origin.locate(fake.provision, {'memory_id': fake.mid})
    assert found['feedback_projection'] == 'with_eval_review'
    with synthetic_export(fake):
        ctx, sources = origin.export_context(fake.provision, found)
        for number in (1, 2):
            output = fake.f.root/f'export-{number}'
            prepare(found['store'], 'failed', number, ctx['namespace'], output, fake.f.root,
                    source=origin.source_prerequisites(found) if number == 2 else None, source_catalogs=sources)
            catalog = json.loads((output/'catalog.json').read_bytes())
            assert 'failed/eval-review.json' in catalog['files']
            if number == 2:
                assert json.loads((output/'descriptor.json').read_bytes())['feedback_projection'] == 'with_eval_review'


@pytest.mark.parametrize('target', ['advisor', 'model'])
@pytest.mark.parametrize('fault', ['extra', 'reason', 'suggestion', 'suggestion_reason', 'classification',
                                   'numeric_bool', 'rule', 'base', 'missing_eval', 'incomplete', 'runtime', 'unsettled_eval'])
def test_origin_and_export_refuse_unverified_additions(tmp_path, fault, target):
    fake = enriched_fake(tmp_path, fault, target)
    history = snapshot(fake.f.root)
    with pytest.raises(DevConflict): origin.locate(fake.provision, {'memory_id': fake.mid})
    store = origin.OriginalStore(fake.failed['descriptor']['ledger']['uri'])
    with synthetic_export(fake), pytest.raises(DevConflict):
        prepare(store, 'failed', 2, fake.failed['context']['namespace'], fake.f.root/'export', fake.f.root,
                source=dict(failed_execution_import_id='failed', validator_id='validator-failed',
                            validation_descriptor='failed/verdict.json', request_id=None))
    assert snapshot(fake.f.root) == history and fake.replays == 0


def test_old_exact_and_same_loop_functions(tmp_path):
    from benchmarks.attention_harness import eval_agent, dev_feedback_evidence, dev_v2_loop
    from attention_memory_service import feedback_evidence
    fake = make_fake(tmp_path)
    assert origin.locate(fake.provision, {'memory_id': fake.mid})['feedback_projection'] == 'exact'
    assert eval_agent.visible_review is dev_feedback_evidence.visible_review
    assert dev_v2_loop.public_failure is dev_feedback_evidence.public_failure
    assert Path(dev_feedback_evidence.__file__).read_bytes() == Path(feedback_evidence.__file__).read_bytes()


@pytest.mark.parametrize('fault', ['eval-review.json', 'dev_failure_rules.json'])
def test_origin_rejects_digest_drift(tmp_path, fault):
    fake = enriched_fake(tmp_path)
    root = fake.f.root if fault == 'eval-review.json' else fake.root/'sources'
    path = next(root.rglob(fault)); path.write_text('{}')
    with pytest.raises(DevConflict, match='digest'):
        origin.locate(fake.provision, {'memory_id': fake.mid})
