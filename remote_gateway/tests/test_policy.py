"""The allow-list on its own: no sockets, no harness."""
import pytest

from remote_gateway import policy
from remote_gateway.config import Config, Limits

CELL = 'cells/' + 'a' * 64
LIMITS = Limits()
OPTIONS = dict(dev_v2=dict(profiles=[dict(task_id='open_cabinet', enabled=True), dict(task_id='turn_on_microwave', rules_profile='r5', enabled=True), dict(task_id='hidden', enabled=False)],
                           agent_policies=dict(policies=[dict(policy_id='autonomous'), dict(policy_id='reactive_help')]), authorization=dict(secret='x'), memory=dict(store='y'),
                           tasks=[dict(task_id='open_cabinet', uri='/home/someone/x')]), profiles=[dict(id='formal')], dev_loop=dict(model='m'))
MINE = [dict(credential_id='mine', provider='deepseek', base_url_host='api.deepseek.com')]
PARCC = lambda model='parcc/GLM': dict(credential='lab', provider='parcc', model=model)


def body(**changes):
    selection = dict(request_key='client-key-1', tasks=['open_cabinet'], seeds=[102], policies=['autonomous'], mode='interactive', help_budget=2,
                     agent_limits=dict(versions=2, wall_seconds=3600), agent_provider=dict(roles=dict(dev=PARCC(), eval=PARCC(), advisor=PARCC('parcc/Qwen'))))
    selection.update(changes)
    return dict(selection=selection, start=True)


def rebuilt(value=None, user='u-1'):
    return policy.rebuild(value or body(), user=user, options=OPTIONS, limits=LIMITS, credentials=MINE)


@pytest.mark.parametrize('path', ['/a/../b', '/a/%2e%2e/b', '/a/%2E%2E/b', '/a/%252e%252e/b', '/a//b', '/a/%2f/b', '/a\\b', '/a/%5cb', '/a%00b', '/a\nb'])
def test_traversal_in_any_encoding_is_refused(path):
    assert not policy.safe_target(path)


def test_ordinary_paths_pass():
    assert policy.safe_target('/api/dev-v2/experiments/devexp-' + 'a' * 24 + '/cells/' + 'b' * 64 + '/view')


@pytest.mark.parametrize('suffix', [None, 'report', 'export.json', CELL + '/view', CELL + '/agent/view', CELL + '/live', CELL + '/live/frame',
                                    CELL + '/agent/files/replays/e1/abc/trim/overview.mp4', CELL + '/agent/files/compat/v1/trace.json'])
def test_what_an_owner_may_read(suffix):
    assert policy.readable(suffix)


@pytest.mark.parametrize('suffix', ['ledger.json', 'export.csv', 'fork-preview', 'approvals/x/decision', 'switch', CELL, CELL + '/files/policy.py', CELL + '/private/x',
                                    'cells/short/view', CELL + '/agent/files/.hidden/x', CELL + '/agent/files/a/.git/x', CELL + '/agent/filesx'])
def test_everything_else_is_not_readable(suffix):
    assert not policy.readable(suffix)


def test_a_selection_is_rebuilt_not_copied():
    extra = dict(goal_id='mine', goal_key='someone-elses', execution_target='real_robot', quota_stop_percent=1, wall_seconds=999999, wall_limit=999999, fork=dict(experiment_id='x'),
                 configuration=dict(cell_budget={}), planner_features=dict(memory=True), memory_authority='z', trace_visible=False, dev_reasoning_effort='high', agent_policy_version='agent-policy-v1')
    result = rebuilt(body(**extra))
    assert not set(extra) - {'goal_key', 'quota_stop_percent', 'wall_limit', 'agent_policy_version'} & set(result)
    assert result['agent_policy_version'] == 'agent-policy-v2'
    assert result['goal_key'].startswith('gw-u-1-') and result['quota_stop_percent'] == 90 and result['wall_limit'] == 21600
    assert result['model_source'] == 'codex' and result['dev_mode'] == 'agent' and result['budget_tier'] == 'dev'
    assert (result['dev_model'], result['eval_model'], result['advisor_model']) == ('parcc/GLM', 'parcc/GLM', 'parcc/Qwen')


def test_two_people_never_share_a_request_key():
    assert rebuilt(user='u-1')['request_key'] != rebuilt(user='u-2')['request_key']
    assert rebuilt()['request_key'] == rebuilt()['request_key']


def test_limits_default_to_the_ceiling_and_cannot_be_raised():
    # No time limit on Remote: the wall field is the nominal value whatever is asked, and asking for more is not refused.
    assert rebuilt(body(agent_limits=None))['agent_limits'] == dict(versions=3, wall_seconds=21600)
    assert rebuilt(body(agent_limits=dict(versions=2, wall_seconds=7200)))['agent_limits'] == dict(versions=2, wall_seconds=21600)
    for change, code in ((dict(agent_limits=dict(versions=4, wall_seconds=3600)), 'invalid_versions'),
                         (dict(agent_limits=dict(versions=True, wall_seconds=3600)), 'invalid_versions'), (dict(help_budget=4), 'invalid_help_requests'), (dict(seeds=['1']), 'invalid_seed')):
        with pytest.raises(policy.Refused) as refused:rebuilt(body(**change))
        assert refused.value.code == code


@pytest.mark.parametrize('roles,code', [
    (dict(dev=PARCC(), eval=PARCC()), 'roles_required'),                                                       # A missing role would fall back to lab Codex.
    (dict(dev=PARCC(), eval=PARCC(), advisor=dict(credential='lab', provider='openai', model='gpt-6.1-sol')), 'lab_source_not_offered'),
    (dict(dev=PARCC(), eval=dict(credential='theirs', provider='deepseek', model='x'), advisor=PARCC()), 'key_not_found'),
    (dict(dev=dict(credential='mine', provider='openai', model='x'), eval=PARCC(), advisor=PARCC()), 'key_not_found'),
    (dict(dev=PARCC(), eval=PARCC(), advisor=dict(credential='lab', provider='parcc', model=' ')), 'invalid_role')])
def test_no_role_can_reach_codex_or_someone_elses_key(roles, code):
    with pytest.raises(policy.Refused) as refused:rebuilt(body(agent_provider=dict(roles=roles)))
    assert refused.value.code == code


def test_a_saved_key_is_routed_with_its_own_provider():
    roles = dict(dev=dict(credential='mine', provider='deepseek', model=' m '), eval=PARCC(), advisor=PARCC())
    assert rebuilt(body(agent_provider=dict(roles=roles)))['agent_provider']['roles']['dev'] == dict(credential='mine', provider='deepseek', model='m')


def test_a_saved_key_that_reads_text_only_stays_declared():
    mine = lambda **more: dict(credential='mine', provider='deepseek', model='m', **more)
    roles = rebuilt(body(agent_provider=dict(roles=dict(dev=PARCC(), eval=mine(vision=False), advisor=mine(vision=True)))))['agent_provider']['roles']
    assert roles['eval'] == mine(vision=False) and roles['advisor'] == mine()


@pytest.mark.parametrize('change,code', [(dict(tasks=['hidden']), 'task_not_offered'), (dict(tasks=['nope']), 'task_not_offered'), (dict(tasks=['a', 'b']), 'single_run_required'),
                                         (dict(policies=['all']), 'policy_not_offered'), (dict(mode='diagnostic'), 'invalid_mode'), (dict(tasks=['turn_on_microwave']), 'task_not_offered')])
def test_only_what_is_offered_can_be_chosen(change, code):
    with pytest.raises(policy.Refused) as refused:rebuilt(body(**change))
    assert refused.value.code == code


def test_a_fixture_task_keeps_its_rules():
    assert rebuilt(body(tasks=['turn_on_microwave'], rules_profile='r5'))['rules_profile'] == 'r5'


def test_answers_lose_host_details_but_keep_the_pages_own_links():
    value = policy.scrub(dict(artifact_dir='/home/a/b', owner='x', runtime=dict(a=1), cells=[dict(key='k', uri='/tmp/x', error='failed at /home/someone/run/x.py: bad'), dict(uri='/api/dev-v2/y')]))
    assert value == dict(cells=[dict(key='k', error='failed at [host path]: bad'), dict(uri='/api/dev-v2/y')])
    # The agent view names its evidence relative to itself, and the page needs those names to show results and video.
    view = dict(artifact_dir='agent-view', versions=[dict(eval=dict(uri='agent-view/compat/v0001/eval.json'), trace=dict(uri='agent-view/compat/v0001/trace.json'))])
    assert policy.scrub(view) == view


def test_options_offer_only_agent_runs():
    value = policy.filter_options(OPTIONS, LIMITS)
    assert set(value) == {'dev_v2', 'hosted'} and 'authorization' not in value['dev_v2'] and 'memory' not in value['dev_v2']
    assert value['hosted']['limits']['versions'] == 3 and value['dev_v2']['goal_key'] == 'set-by-host'
    assert 'uri' not in value['dev_v2']['tasks'][0]


def test_lists_keep_only_ones_own_runs():
    listing = dict(runs=[dict(run_id='r')], launches=[1], dev_sessions=[dict(experiment_id='a', artifact_dir='/x'), dict(experiment_id='b')])
    assert policy.filter_runs(listing, {'a'}) == dict(runs=[], launches=[], dev_sessions=[dict(experiment_id='a')])


@pytest.mark.parametrize('state,active', [('running', 1), ('paused', 1), ('pausing', 1), ('planned', 0), ('completed', 0), ('failed', 0), ('needs_review', 0)])
def test_what_counts_as_a_run_in_progress(state, active):
    assert policy.active_runs(dict(dev_sessions=[dict(experiment_id='a', experiment_state=state), dict(experiment_id='b', experiment_state='running')]), {'a'}) == active


def test_the_log_shows_no_identifiers():
    path = '/api/dev-v2/experiments/devexp-' + 'a' * 24 + '/cells/' + 'b' * 64 + '/agent/files/replays/e1/x.mp4'
    assert policy.audit_route(path) == '/api/dev-v2/experiments/{experiment}/cells/{cell}/agent/files/{file}'
    assert policy.audit_route('/api/provider/credentials/my-key/test') == '/api/provider/credentials/{key}/test'


def test_settings_refuse_what_could_never_be_honoured(tmp_path):
    for bad in (dict(versions=11), dict(wall_seconds=1199), dict(wall_seconds=3601), dict(help_requests=0)):
        with pytest.raises(ValueError):Limits(**bad)
    with pytest.raises(ValueError, match='loopback'):Config(state_dir=tmp_path, upstream='http://127.0.0.1:1', ui_dir=tmp_path, listen_host='0.0.0.0')


def test_a_help_reply_is_rebuilt_from_named_fields():
    cell = 'a' * 64
    assert policy.help_reply(dict(cell=cell, execution_id='e000010', content='Lower it.', extra=1)) == dict(cell=cell, execution_id='e000010', content='Lower it.')
    assert policy.help_reply(dict(cell=cell, execution_id='e000010', skip=True, content='x')) == dict(cell=cell, execution_id='e000010', skip=True)
    for bad in (dict(cell='x', execution_id='e000010', content='a'), dict(cell=cell, execution_id='../x', content='a'), dict(cell=cell, execution_id='e000010', content=' '),
                dict(cell=cell, execution_id='e000010', content='a' * 2001), dict(cell=cell, execution_id='e000010'), []):
        with pytest.raises(policy.Refused):policy.help_reply(bad)


def test_only_an_interactive_run_waits_for_its_owner():
    made = lambda mode: policy.rebuild(body(mode=mode), user='u-1', options=OPTIONS, limits=LIMITS, credentials=MINE)
    assert made('interactive')['human_reply_seconds'] == LIMITS.human_reply_seconds
    assert 'human_reply_seconds' not in made('experiment')


def test_new_run_says_there_is_no_time_limit_whatever_an_old_config_says():
    limits = Limits(wall_seconds=1200, run_slots=3).public()
    assert limits['noTimeLimit'] is True and limits['wall'] == 21600 and limits['wallRange'].startswith('No time limit')
    assert 'no time limit' in limits['note'] and 'Up to 3 runs are open at once' in limits['note'] and '2 hours' in limits['note']
    assert policy.rebuild(body(agent_limits=dict(versions=2, wall_seconds=1200)), user='u-1', options=OPTIONS, limits=Limits(wall_seconds=1200), credentials=MINE)['agent_limits']['wall_seconds'] == 21600


@pytest.mark.parametrize('slots', [0, 17, True])
def test_run_slots_are_a_small_positive_number(slots):
    with pytest.raises(ValueError):Limits(run_slots=slots)


def test_the_cover_leaves_out_this_machine():
    value = policy.filter_home(dict(running=[], readiness=dict(codex=dict(state='ready'), disk=dict(value='160G'), glm=dict(state='ready'), qwen=dict(state='down')),
                                    recent=[dict(experiment_id='e', planner_lock_sha256='a', deployment_kind='b', execution_profile='c', why='ok')]))
    assert set(value['readiness']) == {'glm', 'qwen'} and value['recent'] == [dict(experiment_id='e', why='ok')]


@pytest.mark.parametrize('query', ['run=x', 'run=devexp-' + 'a' * 24 + '&view=../x', 'run=devexp-' + 'a' * 24 + '&refresh=1', ''])
def test_a_cover_picture_is_asked_for_by_name_only(query):
    with pytest.raises(policy.Refused):policy.home_scene(query)
    

def test_a_cover_picture_keeps_only_its_run_and_view():
    assert policy.home_scene('run=devexp-' + 'a' * 24 + '&view=top') == '/api/home/scene?run=devexp-' + 'a' * 24 + '&view=top'


def _board(row, **options):
    return policy.board(dict(dev_sessions=[row]), owners={row['experiment_id']: 'u-1'}, nicknames={'u-1': 'Fox-1'}, queue=[], limits=Limits(), **{'viewer': 'u-1', **options})


def test_a_run_waiting_for_its_owner_says_so_in_the_lobby():
    wait = dict(deadline_at=1100, seconds=120, question='What next?', execution_id='e000001')
    row = dict(experiment_id='devexp-' + 'a' * 24, state='running', experiment_state='running', human_wait=wait)
    board = _board(row, now=1040)
    card = board['robot']['open'][0]
    assert board['robot']['running'] is None  # Waiting for a person is not using the robot.
    assert card['state'] == 'waiting_human' and card['help_waiting_seconds'] == 60 and card['waiting'] == dict(question='What next?')
    assert board['counts']['needs_you'] == 1
    assert _board(row, now=1040, viewer='u-2')['counts']['needs_you'] == 0  # Everyone sees the state; it needs only its owner.


def test_a_finished_run_is_not_waiting_whatever_the_row_still_says():
    row = dict(experiment_id='devexp-' + 'a' * 24, state='failed', experiment_state='failed', human_wait=dict(deadline_at=1, seconds=1))
    assert _board(row)['recent'][0]['state'] == 'finished'


def test_a_run_that_could_not_start_carries_the_reason():
    eid = 'devexp-' + 'a' * 24
    card = _board(dict(experiment_id=eid, state='planned', experiment_state='planned'), start_errors={eid: 'no GPU'})['recent'][0]
    assert card['state'] == 'created' and card['start_error'] == 'no GPU'


def test_the_lobby_shows_every_open_run_and_which_one_is_on_the_robot():
    rows = [dict(experiment_id='devexp-' + c * 24, state='running', experiment_state='running', created_at=n, robot=robot)
            for n, (c, robot) in enumerate([('a', dict(state='executing')), ('b', dict(state='waiting', ahead=1, since=5)), ('c', None)])]
    board = policy.board(dict(dev_sessions=rows), owners={r['experiment_id']: 'u-1' for r in rows}, nicknames={'u-1': 'Fox-1'}, queue=[], limits=Limits(run_slots=3), viewer='u-1')
    assert [c['robot'] for c in board['robot']['open']] == [dict(state='executing'), dict(state='waiting', ahead=1), dict(state='thinking')]
    assert board['robot']['running']['experiment_id'] == 'devexp-' + 'a' * 24 and board['robot']['slots'] == 3 and board['robot']['waiting'] == 1
    assert board['counts']['running'] == 3


def test_a_place_in_line_has_no_time_estimate_because_runs_have_no_time_limit():
    rows = [dict(experiment_id='devexp-' + 'a' * 24, state='planned', experiment_state='planned')]
    board = policy.board(dict(dev_sessions=rows), owners={rows[0]['experiment_id']: 'u-1'}, nicknames={}, queue=[dict(experiment_id=rows[0]['experiment_id'], user_id='u-1')], limits=Limits(), viewer='u-1')
    assert board['queue'][0]['queue_position'] == 1 and board['queue'][0]['eta_start_seconds'] is None
