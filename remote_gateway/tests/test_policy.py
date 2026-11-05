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
    assert not set(extra) - {'goal_key', 'quota_stop_percent', 'wall_limit'} & set(result)
    assert result['goal_key'].startswith('gw-u-1-') and result['quota_stop_percent'] == 90 and result['wall_limit'] == 21600
    assert result['model_source'] == 'codex' and result['dev_mode'] == 'agent' and result['budget_tier'] == 'dev'
    assert (result['dev_model'], result['eval_model'], result['advisor_model']) == ('parcc/GLM', 'parcc/GLM', 'parcc/Qwen')


def test_two_people_never_share_a_request_key():
    assert rebuilt(user='u-1')['request_key'] != rebuilt(user='u-2')['request_key']
    assert rebuilt()['request_key'] == rebuilt()['request_key']


def test_limits_default_to_the_ceiling_and_cannot_be_raised():
    assert rebuilt(body(agent_limits=None))['agent_limits'] == dict(versions=3, wall_seconds=3600)
    for change, code in ((dict(agent_limits=dict(versions=4, wall_seconds=3600)), 'invalid_versions'), (dict(agent_limits=dict(versions=2, wall_seconds=7200)), 'invalid_wall_seconds'),
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
    for bad in (dict(versions=11), dict(wall_seconds=1800), dict(wall_seconds=5400), dict(help_requests=0)):
        with pytest.raises(ValueError):Limits(**bad)
    with pytest.raises(ValueError, match='loopback'):Config(state_dir=tmp_path, upstream='http://127.0.0.1:1', ui_dir=tmp_path, listen_host='0.0.0.0')
