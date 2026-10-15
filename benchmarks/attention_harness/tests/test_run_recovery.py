from dataclasses import replace
import os

import pytest

from benchmarks.attention_harness.attention_modes import RequestState
from benchmarks.attention_harness.core.models import RunStatus
from benchmarks.attention_harness.core.store import AttentionStore
from benchmarks.attention_harness.run_recovery import recover_lost_runs, process_alive
from benchmarks.attention_harness.tests.test_attention_store import populated_store


def launched(tmp_path, fallback=False):
    store, request = populated_store(tmp_path/'store.sqlite')
    store.transition_run(request.run_id, RunStatus.RUNNING, event_key='start')
    store.reserve_assistance(request.run_id, credits=1, reservation_id='credit')
    store.reserve_resource(request.run_id, resource='tokens', amount=10, reservation_id='tokens')
    store.begin_waiting_window(request.request_id, started_at=5)
    if fallback:
        store.transition_request(request.request_id, RequestState.FALLBACK, event_key='fallback')
    store.record_launch('launch', locked={}, log_path='not-used')
    store.record_launch_started('launch', pid=99999999)
    store._insert_launch_binding('launch', request.run_id)
    return store, request


@pytest.mark.parametrize('fallback', [False, True])
def test_dead_scheduler_cleanup_is_persistent_and_idempotent(tmp_path, fallback):
    store, request = launched(tmp_path, fallback)
    assert recover_lost_runs(store, alive=lambda pid: False, clock=lambda: 10)==[request.run_id]
    restarted=AttentionStore(store.path)
    assert restarted.get_run(request.run_id)['status']=='failed'
    assert restarted.get_request(request.request_id).state is RequestState.CANCELLED
    assert restarted.budget_status(request.run_id)=={'limit':2,'used':0,'reserved':0,'remaining':2}
    assert restarted.resource_reservations(request.run_id)=={}
    assert restarted.waiting_windows(request.run_id)[request.request_id]['ended_at']==10
    assert restarted.list_launches()[0]['reason']=='scheduler_lost'
    before=restarted.events()
    assert recover_lost_runs(restarted, alive=lambda pid: False, clock=lambda: 20)==[]
    assert restarted.events()==before


def test_live_scheduler_untouched_and_spent_credit_retained(tmp_path):
    store, request=launched(tmp_path)
    before=store.events()
    assert recover_lost_runs(store, alive=lambda pid: True)==[]
    assert store.events()==before
    store.settle_assistance('credit', commit=True)
    recover_lost_runs(store, alive=lambda pid: False, clock=lambda: 10)
    assert store.budget_status(request.run_id)['used']==1


def test_unknown_pid_and_permissions_are_not_death(monkeypatch):
    assert process_alive(None) and process_alive(-1) and process_alive(True)
    assert process_alive(os.getpid())
    def denied(pid, signal):raise PermissionError('probe denied')
    monkeypatch.setattr(os,'kill',denied)
    assert process_alive(123)
    def missing(pid, signal):raise ProcessLookupError('dead')
    monkeypatch.setattr(os,'kill',missing)
    assert not process_alive(123)


def test_cleanup_rolls_back_on_failure(tmp_path, monkeypatch):
    store, request=launched(tmp_path)
    before=store.events()
    def fail(*args):raise RuntimeError('disk write failed')
    monkeypatch.setattr(store,'_update_with_event',fail)
    with pytest.raises(RuntimeError):recover_lost_runs(store,alive=lambda pid:False)
    assert store.events()==before
    assert store.budget_status(request.run_id)['reserved']==1
    assert store.resource_reservations(request.run_id)['tokens']==10


def test_foreign_or_legacy_pid_namespace_is_not_recovered(tmp_path, monkeypatch):
    store, request=launched(tmp_path)
    from benchmarks.attention_harness import run_recovery
    monkeypatch.setattr(run_recovery, 'process_namespace', lambda: {'pid_namespace':'foreign','boot_id':'foreign'})
    assert recover_lost_runs(store, alive=lambda pid:False)==[]
    assert store.get_run(request.run_id)['status']=='running'


def test_list_and_custom_server_start_recover_before_serving(tmp_path, monkeypatch):
    from benchmarks.attention_harness import ui_server
    store,request=launched(tmp_path)
    assert ui_server.list_runs(store)[0]['status']=='failed'
    other=tmp_path/'other';other.mkdir();store,request=launched(other)
    monkeypatch.setattr(ui_server,'ThreadingHTTPServer',lambda address,handler:address)
    assert ui_server.create_server(store=store,port=0)==('127.0.0.1',0)
    assert store.get_run(request.run_id)['status']=='failed'
