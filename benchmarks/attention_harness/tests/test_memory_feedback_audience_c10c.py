"""C10c read-only origin/export must bind Dev and Advisor projections separately."""
from unittest.mock import patch

import pytest

from benchmarks.attention_harness import memory_origin_evidence as origin
from benchmarks.attention_harness import memory_validation_worker as worker
from benchmarks.attention_harness.core.dev_store import DevConflict
from benchmarks.attention_harness.dev_v2_memory_import import prepare
from benchmarks.attention_harness.tests.autovalidate_fakes import Fixture
from benchmarks.attention_harness.tests.r3_fixtures import r3_deployment
from benchmarks.attention_harness.tests.test_memory_feedback_c10b import enriched_fake
from benchmarks.attention_harness.tests.test_memory_origin_c9 import snapshot, synthetic_export
from tests import test_feedback_projection_c10b as c10b
from tests.test_feedback_audience_c10c import audience_writer

pytestmark = pytest.mark.usefixtures('r3_deployment')


@pytest.mark.parametrize('planner', [False, True])
@pytest.mark.parametrize('suggestion', ['', c10b.REVIEW['reference_suggestion']])
@pytest.mark.parametrize('fault', [None, 'advisor_missing', 'advisor_changed', 'advisor_extra', 'dev_suggestion'])
def test_origin_and_export_use_packet_audiences(tmp_path, planner, suggestion, fault, capsys):
    review = dict(c10b.REVIEW, reference_suggestion=suggestion)
    with patch.object(c10b, 'REVIEW', review), \
         patch.object(Fixture, 'write', staticmethod(audience_writer(Fixture.write, fault))):
        fake = enriched_fake(tmp_path, planner=planner)
    history, state = snapshot(fake.f.root), snapshot(fake.provision.state_root)
    if fault is None:
        with synthetic_export(fake):
            assert worker.main(['--state-dir', str(fake.provision.state_root), '--check-origin', fake.mid],
                               client=fake.service) == 0
        assert capsys.readouterr().out.startswith('PASS:')
        assert origin.locate(fake.provision, {'memory_id': fake.mid})['feedback_projection'] == 'with_eval_review'
    else:
        with pytest.raises(DevConflict, match='failed trace mismatch'):
            origin.locate(fake.provision, {'memory_id': fake.mid})
        store = origin.OriginalStore(fake.failed['descriptor']['ledger']['uri'])
        source = dict(failed_execution_import_id='failed', validator_id='validator-failed',
                      validation_descriptor='failed/verdict.json',
                      request_id='help-failed' if planner else None)
        with synthetic_export(fake), pytest.raises(DevConflict, match='failed trace mismatch'):
            prepare(store, 'failed', 2, fake.failed['context']['namespace'], fake.f.root/'export',
                    fake.f.root, source=source)
    assert snapshot(fake.f.root) == history
    assert snapshot(fake.provision.state_root) == state
    assert fake.replays == 0 and not fake.starts
