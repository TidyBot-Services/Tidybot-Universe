"""In-process synthetic acceptance. No socket, provider, simulator or real keys."""
import tempfile
from pathlib import Path
from benchmarks.attention_harness.tests.autovalidate_fakes import FakeRunner,ProvisionedFakeRunner
from benchmarks.attention_harness.memory_validation_worker import tick,set_enabled


def main():
    with tempfile.TemporaryDirectory(prefix='autovalidate-fake-') as tmp:
        root=Path(tmp);runner=ProvisionedFakeRunner(root/'evidence',root/'worker');deps=runner.deps(root/'worker');state={}
        print('provisional → queued (1 use)');assert len(runner.service.validation_queue())==1
        set_enabled(deps.root,True)
        for _ in range(30):
            tick(state,deps)
            if state.get('results'):break
            if state.get('current',{}).get('active'):runner.phase='completed'
        assert state.get('results'),state.get('status')
        assert len(runner.starts)==10 and state['runs_today']==10
        assert set(runner.starts)==set(runner.batch['cells'])
        from benchmarks.attention_harness.dev_ledger import DevV2Store
        from benchmarks.attention_harness.dev_experiment import list_experiments
        store=DevV2Store(next(iter(runner.batch['cells'].values()))['ledger'])
        assert len(list_experiments(store))==1
        goal=store.goal_for_cell(runner.starts[0]);assert goal['limits']['executions']==goal['limits']['dev_calls']==10
        print('default adapter → one experiment, 10 cells, 10 Dev calls + 10 executions budgeted')
        print('5 pairs → verdict (control failure 5/5; treatment failure 0/5)')
        assert state['results'][0]['status']=='trusted'
        print('promoted → trusted on this scene')
        runner.demote();print('3 failing uses in last 5 → demoted to provisional')
        runner=FakeRunner(root/'failed-safety',failed_check=('t0','safety'));deps=runner.deps(root/'failed-worker');state={};set_enabled(deps.root,True)
        for _ in range(5):
            tick(state,deps)
            if state.get('results'):break
            runner.phase='completed'
        assert state.get('results'),state.get('status')
        assert state['results'][0]['status']=='rejected' and len(runner.starts)==2 and state['runs_today']==2
        assert state['results'][0]['decision']['failed_gates']==['safety_regression']
        assert runner.service.get_memory('m1').status.value=='rejected'
        print('second scenario: treatment independent safety failed → rejected (2 arms; 0 accepted pairs)')
        print('Notice: '+state['status'])
        print('PASS: fake runner, fake replay, real in-process service; no external calls')


if __name__=='__main__':main()
