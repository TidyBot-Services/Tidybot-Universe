"""Small three-attempt recording, plus optional read-only checks on the supplied captures."""
import copy
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import sqlite3
import shutil
import subprocess

import pytest

from benchmarks.attention_harness.demo_engine import DemoEngine, redact_for_other, slice_view, source_timing
from benchmarks.attention_harness.lobby import LobbyError
from benchmarks.attention_harness.tests.test_lobby import Clock

EID = 'devexp-'+'a'*24
KEY = 'b'*64
DEV = 'DEV_TEXT_SENTINEL'
ANSWER = 'SOURCE_REPLY_SENTINEL'


TRIM_VIEWS = ('overview','side','top','left_wrist','right_wrist')


def trim_ref(n):
    return dict(schema='attentionbench.replay-trim.v1',original_duration_seconds=90.,trimmed_duration_seconds=20.,skipped_seconds=70.,params=dict(gap_s=1.,hold_s=1.),
        replay_timing=dict(frame_trace_seconds=[0.,1.],frame_video_seconds=[0.,1.],frame_orig_video_seconds=[0.,40.]),
        breaks=[dict(kind='wait',orig_start=1.,orig_end=41.,trace_start=1.,trace_end=41.,skipped_seconds=40.,trim_at=1.)],
        videos={v:{'uri':f'agent-view/replays/e{n:06d}/'+('d'*64)+f'/trim/{v}.mp4','sha256':'d'*64} for v in TRIM_VIEWS})


def recording():
    versions, jobs, intervals, timeline = [],[],[],[]
    for n in range(1,4):
        version = dict(version=n,version_id=f'v{n:04d}',execution_id=f'e{n:06d}',native_success=n==3,config_sha256='d'*64,valid=True,
            source_text=DEV,plan=dict(summary=DEV,note=DEV,source='summary'),model_input=None,prompt=DEV,
            source={'uri':f'agent-view/compat/v{n:04d}/policy.py','sha256':'c'*64},
            bundle={'uri':f'agent-view/compat/v{n:04d}/bundle.json','sha256':'c'*64},
            trace={'uri':f'agent-view/compat/v{n:04d}/trace.json','sha256':'c'*64},
            frames={'uri':f'agent-view/executions/e{n:06d}/frames.json','sha256':'c'*64},
            replays={'advisor_replay.mp4':{'uri':f'agent-view/replays/e{n:06d}/'+('c'*64)+'/advisor_replay.mp4','sha256':'c'*64}},
            recording={'trim':trim_ref(n)},
            story=dict(short='solved' if n==3 else 'out of reach',sentence=DEV,failure_class='Out of reach'),
            evaluator_review=dict(passed=n==3,reason=DEV,reference_suggestion=ANSWER),
            help_content={'delivered':True,'content':ANSWER},after_help={'content':ANSWER},
            request_view={'items':[{'label':'Question','value':DEV}]})
        versions.append(version)
        jobs.append(dict(version=n,kind='execute',state='settled',reserved_at=10000+n*60-20,settled_at=10000+n*60,
                         receipt={'bundle':version['bundle'],'trace':version['trace'],'execution_status':'completed'}))
        timeline.append(dict(kind='formal_version',op='submit',version_id=f'v{n:04d}',at=10000+n*60,result={'text':DEV}))
        if n<3:
            intervals.append(dict(after_version=n,version=n+1,reserved_at=10000+n*60,settled_at=10010+n*60,
                help_content={'delivered':True,'status':'delivered','content':ANSWER},request_view={'text':DEV},decision_content={'reason':ANSWER}))
    compat = dict(experiment_id=EID,key=KEY,state='completed',reason='solved',cancelled=False,
        versions=versions,jobs=jobs,help_intervals=intervals,used={'executions':3,'provider_tokens':900},budget={'executions':5,'provider_tokens':1000},
        ledger={'goal':{'wall_used':180,'wall_limit':1000,'wall_remaining':820,'limits':{'wall_seconds':1000}}},lifecycle=None,
        run_controls=dict(state='ended',can_pause=False,can_resume=False),artifact_dir='agent-view')
    agent = dict(versions=copy.deepcopy(versions),timeline=timeline,counts={'formal_versions':3},held={},
                 limits={'formal_versions':5,'provider_tokens':1000},remaining={'formal_versions':2},wall_seconds=180,phase='stopped',
                 pending_ask=DEV,active_help={'content':ANSWER},history=[{'url':DEV}],errors=[{'error':DEV}],
                 future_private_field=DEV)
    return dict(experiment_id=EID,key=KEY,dev_mode='agent',state='completed',reason='solved',created_at=10000,updated_at=10180,
                versions=copy.deepcopy(versions),jobs=copy.deepcopy(jobs),agent=agent,dev_v2_compat=compat)


class Bridge:
    def __init__(self):
        self.view = recording()
        self.experiment = dict(experiment_id=EID,state='completed',created_at=10000,updated_at=10180,
                               selection={'dev_mode':'agent','prompt':DEV},cells=[dict(key=KEY,experiment_id=EID,state='completed')])
        self.requests = []

    def list(self):
        return [dict(experiment_id=EID,key=KEY,session_id='v2-'+KEY,channel='dev_v2',state='completed',created_at=10000,
            locked={'config':{'task_id':'lift','task_prompt':'Lift the cube.','dev_notes':DEV,'seed':101,'suite':'robocasa365'}},model='recorded-model')]

    def get(self,path):
        self.requests.append(path)
        if path == '/api/dev-v2/experiments/'+EID:return copy.deepcopy(self.experiment),'application/json'
        if path == f'/api/dev-v2/experiments/{EID}/cells/{KEY}/view':return copy.deepcopy(self.view),'application/json'
        if '/files/' in path:
            if '..' in path or '%' in path:raise FileNotFoundError('unsafe')
            if path.endswith('policy.py'):return DEV,'text/plain'
            if path.endswith('.mp4'):return b'fake-video','video/mp4'
            if path.endswith('frames.json'):return [],'application/json'
            if path.endswith('help.json'):return dict(content=ANSWER),'application/json'
            number = re.search(r'/v(\d+)/',path)
            number = int(number[1]) if number else 1
            attempt = 'attempt:'+KEY+':'+str(number-1)
            return dict(run_id='run:'+KEY,attempt_id=attempt,dev_version=number,formal_eligible=False,config_sha256='d'*64,policy_sha256='c'*64,
                        native_evaluator={'run_id':'run:'+KEY,'attempt_id':attempt,'evaluated':True},
                        recording={'run_id':'run:'+KEY,'attempt_id':attempt},sdk_events=[],error=DEV),'application/json'
        raise FileNotFoundError('missing')


def engine(tmp_path, clock=None, count=0):
    return DemoEngine(dict(mode='demo',state_dir=str(tmp_path),speed=1,seed=1,
        sources={EID:dict(task_key='lift',title='Lift cup')},invitees=[{'count':count}]),Bridge(),clock=clock or Clock())


@pytest.mark.parametrize('k',[0,1,2,3])
@pytest.mark.parametrize('phase',['queued','running','waiting_human','finished','cancelled','failed'])
def test_slice_view_all_cuts_and_phases_are_pure(k,phase):
    source = recording();before = copy.deepcopy(source)
    eid,key = 'devexp-'+'1'*24,'2'*64
    result = slice_view(source,k,dict(state=phase,experiment_id=eid,key=key,created_at=123,updated_at=456))
    assert source == before
    raw = json.dumps(result)
    assert EID not in raw and KEY not in raw
    for obj in (result,result['agent'],result['dev_v2_compat']):
        assert len(obj['versions']) == k
        if 'jobs' in obj:assert len(obj['jobs']) == k
    assert len(result['dev_v2_compat']['help_intervals']) == min(k,2)
    assert len(result['agent']['timeline']) == k
    assert result['state'] == ('completed' if phase=='finished' else 'planned' if phase=='queued' else phase)
    assert result['created_at'] == 123 and result['updated_at'] == 456
    if phase != 'finished':assert result['dev_v2_compat']['used']['executions'] == k
    else:assert result['dev_v2_compat']['used'] == source['dev_v2_compat']['used']


def test_redaction_fails_closed_preserves_shape_and_labels():
    view = recording();redacted = redact_for_other(view)
    assert set(redacted) == set(view)
    raw = json.dumps(redacted)
    assert DEV not in raw and ANSWER not in raw
    assert redacted['agent']['future_private_field'] is None
    assert redacted['dev_v2_compat']['versions'][0]['story']['short'] == 'out of reach'
    assert redacted['dev_v2_compat']['versions'][2]['native_success'] is True
    assert redacted['agent']['limits']['formal_versions'] == 5
    assert redacted['agent']['counts']['formal_versions'] is None
    assert redact_for_other(redacted) == redacted


def test_source_timing_and_missing_timestamps():
    source = recording()
    timing = source_timing(source,{'created_at':10000,'updated_at':10180})
    assert timing['attempt_done_at'] == [60,120,180]
    assert set(timing['helps']) == {'1','2'} and timing['helps']['1']['delay'] == 10
    assert timing['result'] == 'solved'
    source['dev_v2_compat']['jobs'] = []
    assert source_timing(source,{'created_at':10000,'updated_at':10180})['attempt_done_at'] == [60,120,180]


@pytest.mark.parametrize('state,native,passed,expected',[
    ('completed',False,True,'failed'),('budget_exhausted',False,False,'limit'),
    ('completed',None,True,'solved'),('completed',True,False,'solved')])
def test_recorded_result_uses_native_outcome_and_limit(state,native,passed,expected):
    source = recording()
    last = source['dev_v2_compat']['versions'][-1]
    last['native_success'] = native;last['evaluator_review']['passed'] = passed
    assert source_timing(source,{'state':state,'created_at':10000,'updated_at':10180})['result'] == expected


def test_slice_experiment_and_unknown_indexed_arrays():
    source = recording()
    source['agent']['custom_attempt_rows'] = [{'version':n,'state':'settled'} for n in (1,2,3)]
    exp = dict(experiment_id=EID,cells=[source],state='completed')
    cut = slice_view(exp,1,'running')
    assert EID not in json.dumps(cut) and KEY not in json.dumps(cut)
    assert len(cut['cells'][0]['agent']['custom_attempt_rows']) == 1


@pytest.mark.skipif(shutil.which('node') is None,reason='optional JS runtime unavailable')
def test_existing_ui_workspace_adapter_accepts_all_cuts(tmp_path):
    demo = engine(tmp_path)
    owner,_ = demo.lobby.identify();other,_ = demo.lobby.identify()
    card = demo.lobby.enqueue(owner,task_key='lift');demo.tick()
    run = demo.lobby.find(card['experiment_id'])
    source = Path(__file__).parents[1].joinpath('ui/live.js').read_text()
    markers = [('async function devWorkspaceSnapshot(', '// End Dev workspace projection.'),
               ('async function agentWorkspaceSnapshot(', 'function devV2State('),
               ('function devV2State(', 'async function devV2WorkspaceSnapshot('),
               ('async function devV2WorkspaceSnapshot(', 'async function updateV2Snapshot(')]
    functions = '\n'.join(source[source.index(start):source.index(end,source.index(start))] for start,end in markers)
    cases = []
    for k in range(4):
        for state in ('queued','running','waiting_human','finished'):
            run.update(current=k,state=state)
            for viewer in (owner,other):
                view = demo.view(run,viewer)
                cases.append(dict(k=k,cell=view,experiment=demo.get(run,'',viewer)[0],
                    session=dict(experiment_id=run['experiment_id'],key=demo.key(run),session_id='v2-'+demo.key(run),
                                 locked={'config':{}},model=None),
                    artifacts={uri:demo._asset(run,'agent/files/'+uri.removeprefix('agent-view/'),viewer)[0]
                        for version in view['dev_v2_compat']['versions'] for uri in
                        (version['bundle']['uri'],version['trace']['uri'],version['frames']['uri'])}))
    script = """
const input=JSON.parse(require('fs').readFileSync(0,'utf8'));
let current;const devEvidenceCache=new Map(),v2MonitorIdentities=new Map(),launchOptions={};
const readCameraViews=async()=>null,agentReplayEvents=events=>events,devHelpResponder=()=> 'advisor_proxy';
const devFile=(s,uri)=>uri;
const readDevEvidence=async(s,ref)=>{const value=current.artifacts[ref.uri];if(!value)throw Error('missing artifact');return value;};
"""+functions+"""
(async()=>{for(current of input){devEvidenceCache.clear();const snapshot=await agentWorkspaceSnapshot(current.session,current.cell,current.experiment);
 if(snapshot.attempts.length!==current.k)throw Error('attempt count');
 if(snapshot.dev_evidence_gaps.length)throw Error(snapshot.dev_evidence_gaps.join(';'));
 if(!snapshot.resources||!snapshot.station||!snapshot.dev_session)throw Error('missing workspace region');
}process.stdout.write('ok');})().catch(error=>{process.stderr.write(String(error));process.exit(1);});
"""
    result = subprocess.run(['node','-e',script],input=json.dumps(cases),text=True,capture_output=True,timeout=20)
    assert result.returncode == 0,result.stderr
    assert result.stdout == 'ok'


def test_engine_routes_rewrite_identity_and_block_future_private_files(tmp_path):
    clock = Clock();demo = engine(tmp_path,clock)
    owner,_ = demo.lobby.identify();other,_ = demo.lobby.identify()
    card = demo.lobby.enqueue(owner,task_key='lift');demo.tick()
    clock.step(60);demo.tick()
    run = demo.lobby.find(card['experiment_id']);key = demo.key(run)
    assert re.fullmatch(r'devexp-[a-f0-9]{24}',run['experiment_id'])
    own = demo.get(run,f'/cells/{key}/view',owner)[0]
    assert len(own['dev_v2_compat']['versions']) == 1
    assert EID not in json.dumps(own) and KEY not in json.dumps(own)
    public = demo.get(run,f'/cells/{key}',other)[0]
    assert DEV not in json.dumps(public) and ANSWER not in json.dumps(public)
    suffix = f'/cells/{key}/agent/files/compat/v0001/bundle.json'
    evidence = demo.get(run,suffix,other)[0]
    assert evidence['run_id'] == 'run:'+key and evidence['attempt_id'] == 'attempt:'+key+':0'
    for file in ('compat/v0002/bundle.json','compat/v0001/policy.py','compat/v0001/help.json','../secret','%2e%2e/secret'):
        with pytest.raises(LobbyError):demo.get(run,f'/cells/{key}/agent/files/'+file,other)
    session = demo.sessions(owner)[0]
    assert session['lobby']['mine'] is True and session['experiment_id'] == run['experiment_id']
    assert DEV not in json.dumps(demo.sessions(other))
    assert len(demo.bridge.requests) < 10


def test_config_requires_explicit_directory_and_valid_sources(tmp_path):
    config = dict(mode='demo',state_dir=str(tmp_path),sources={EID:dict(task_key='lift',title='Lift cup')})
    for patch in ({'state_dir':None},{'state_dir':'relative'},{'mode':'real'},{'sources':{}},{'key':'TEST_KEY'},{'speed':2}):
        with pytest.raises(ValueError):DemoEngine({**config,**patch},Bridge())


# Optional recorded Dev v2 runs kept outside the repository (one directory per experiment id).
CAPTURES = Path(os.environ.get('ATTENTIONBENCH_DEMO_CAPTURES', '/nonexistent'))


@pytest.mark.parametrize('eid',['devexp-2656176e6fa79c9bbee569bf','devexp-6f072cb01d5a056c1bf3b384'])
def test_supplied_recording_read_only(eid,tmp_path):
    if 'ATTENTIONBENCH_DEMO_CAPTURES' not in os.environ:pytest.skip('ATTENTIONBENCH_DEMO_CAPTURES not set')
    path = CAPTURES/eid/'ledger.sqlite3'
    if not path.is_file():pytest.skip('optional supplied capture unavailable')
    from benchmarks.attention_harness.dev_ledger import DevV2Store
    # Do not instantiate DailyDevV2/DevV2Store: constructors migrate/recover.
    class ReadOnly(DevV2Store):
        def __init__(self,path):self.path=path
        @contextmanager
        def tx(self):
            with sqlite3.connect(self.path.as_uri()+'?mode=ro&immutable=1',uri=True) as db:yield db
    store = ReadOnly(path)
    with store.tx() as db:
        key = db.execute('SELECT id FROM v2_cells').fetchone()[0]
        exp = json.loads(db.execute('SELECT payload FROM v2_experiments').fetchone()[0])
    cell = store.cell(key)
    # The cell's archived host paths may refer to another checkout. Resolve only
    # the supplied copy; never follow those paths or write a migrated ledger.
    cell['artifact_dir'] = str(CAPTURES/eid/'artifacts'/eid/'cells'/key)
    from benchmarks.attention_harness.dev_agent_view import projection
    view = projection(store,cell,now=exp.get('updated_at',exp['created_at']))
    assert len(view['dev_v2_compat']['versions']) >= 1
    for k in range(len(view['versions'])+1):
        cut = slice_view(view,k,'running')
        assert len(cut['dev_v2_compat']['versions']) == k
        assert eid not in json.dumps(cut) and key not in json.dumps(cut)
    assert source_timing(view,exp)['attempt_done_at']
    from benchmarks.attention_harness.dev_agent_view import artifact
    class CaptureBridge:
        def get(self,route):
            if route.endswith('/view'):return view,'application/json'
            if '/agent/files/' in route:return artifact(cell,route.split('/agent/files/',1)[1])
            return {**exp,'cells':[cell]},'application/json'
        def list(self):return [dict(experiment_id=eid,key=key,session_id='v2-'+key,channel='dev_v2',locked={'config':{}})]
    clock = Clock()
    demo = DemoEngine(dict(mode='demo',state_dir=str(tmp_path),sources={eid:dict(task_key='recording',title='Recording')}),
                      CaptureBridge(),clock=clock)
    owner,_ = demo.lobby.identify();other,_ = demo.lobby.identify()
    card = demo.lobby.enqueue(owner,task_key='recording');demo.tick()
    run = demo.lobby.find(card['experiment_id']);run['current'] = len(view['versions']);run['state'] = 'finished'
    # Verify the actual renderer's immutable identity and digest contract after
    # both simulation rewriting and observer redaction, without weakening checks.
    import hashlib
    for viewer in (owner,other):
        result = demo.view(run,viewer)
        for job in result['dev_v2_compat']['jobs']:
            if job['kind'] != 'execute':continue
            version = next(v for v in result['dev_v2_compat']['versions'] if v['version']==job['version'])
            for name in ('bundle','trace'):
                ref = job['receipt'][name]
                payload = demo.get(run,f'/cells/{demo.key(run)}/agent/files/'+ref['uri'].removeprefix('agent-view/'),viewer)[0]
                raw = json.dumps(payload,ensure_ascii=False).encode()
                assert hashlib.sha256(raw).hexdigest() == ref['sha256']
                assert payload['run_id'] == 'run:'+demo.key(run)
                assert payload['attempt_id'] == f'attempt:{demo.key(run)}:{job["version"]-1}'
                assert payload['config_sha256'] == version['config_sha256']
                assert payload['policy_sha256'] == version['source']['sha256']


def test_trimmed_replay_reaches_observers_for_settled_attempts_only(tmp_path):
    clock = Clock();demo = engine(tmp_path,clock)
    owner,_ = demo.lobby.identify();other,_ = demo.lobby.identify()
    card = demo.lobby.enqueue(owner,task_key='lift');demo.tick()
    clock.step(60);demo.tick()
    run = demo.lobby.find(card['experiment_id']);key = demo.key(run)
    base = f'/cells/{key}/agent/files/'
    for viewer in (owner,other):
        view = demo.get(run,f'/cells/{key}/view',viewer)[0]
        trim = view['dev_v2_compat']['versions'][0]['recording']['trim']
        assert trim['schema'] == 'attentionbench.replay-trim.v1' and len(trim['videos']) == 5
        assert len(view['dev_v2_compat']['versions']) == 1
    # Observers: the schema survives redaction, the settled attempt's trim videos are served.
    for name in TRIM_VIEWS:
        assert demo.get(run,base+f'replays/e000001/{"d"*64}/trim/{name}.mp4',other)[1] == 'video/mp4'
    # A later attempt's trim files stay unreachable, for the owner and for observers.
    for viewer in (owner,other):
        for name in TRIM_VIEWS:
            with pytest.raises(LobbyError) as error:demo.get(run,base+f'replays/e000002/{"d"*64}/trim/{name}.mp4',viewer)
            assert error.value.status in (403,404)
    # Nothing else is opened up: other file types and paths under trim/, other names, odd digests.
    for name in (f'replays/e000001/{"d"*64}/trim/overview.png',f'replays/e000001/{"d"*64}/trim/segments.json',f'replays/e000001/{"d"*64}/trim/other.mp4',
                 f'replays/e000001/{"d"*64}/trim/../trim/overview.mp4',f'replays/e000001/{"d"*63}/trim/overview.mp4',f'replays/e000001/{"d"*64}/trim/overview.mp4.bak',
                 f'replays/e000001/{"d"*64}/trim/sub/overview.mp4',f'replays/e000001/{"d"*64}/replay_trim/overview.mp4'):
        with pytest.raises(LobbyError):demo.get(run,base+name,other)


def test_redaction_keeps_only_the_trim_schema_name():
    view = recording();trim = view['dev_v2_compat']['versions'][0]['recording']['trim']
    assert redact_for_other(view)['dev_v2_compat']['versions'][0]['recording']['trim']['schema'] == 'attentionbench.replay-trim.v1'
    trim['schema'] = 'attentionbench.replay-trim.v1\nsecret'
    assert redact_for_other(view)['dev_v2_compat']['versions'][0]['recording']['trim']['schema'] is None
    view['dev_v2_compat']['versions'][0]['recording']['other'] = dict(schema='DEV_TEXT_SENTINEL')
    assert DEV not in json.dumps(redact_for_other(view))


def test_repository_lobby_config_lists_valid_sources_with_a_microwave_recording():
    config = json.loads((Path(__file__).parents[1]/'config'/'lobby-config.8769.json').read_text())
    assert config['mode'] == 'demo'
    # An example: no machine path. The runtime file adds an absolute state_dir of its own.
    assert 'state_dir' not in config and '/home/' not in json.dumps(config)
    tasks = {entry['task_key'] for entry in config['sources'].values()}
    assert tasks == {'pick_place_counter_to_sink','turn_on_microwave'}
    for eid, entry in config['sources'].items():
        assert re.fullmatch(r'devexp-[a-f0-9]{24}',eid) and set(entry) == {'task_key','title'}


def test_redaction_keeps_the_clock_basis_names_that_event_times_depend_on():
    view = recording();version = view['dev_v2_compat']['versions'][0]
    version['recording']['trim']['replay_timing'].update(basis='relative',event_clock='sdk_completion')
    event = dict(operation='open',timestamp=58.2,timestamp_basis='relative',status='completed',result=None,arguments={'args':[],'kwargs':{}})
    redacted = redact_for_other(dict(view,events=[event,dict(event,timestamp_basis='bad\nvalue'*20)]))
    timing = redacted['dev_v2_compat']['versions'][0]['recording']['trim']['replay_timing']
    assert timing['basis'] == 'relative' and timing['event_clock'] == 'sdk_completion'
    assert redacted['events'][0]['timestamp_basis'] == 'relative' and redacted['events'][0]['timestamp'] == 58.2
    assert redacted['events'][1]['timestamp_basis'] is None
