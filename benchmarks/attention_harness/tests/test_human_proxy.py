"""Review requests, policy rules, shared request budget and the HumanProxy (fake providers only).

Nothing here calls a real model; passing these tests says nothing about real Qwen vision behaviour."""
import hashlib
import json
from pathlib import Path

import pytest
from PIL import Image

from benchmarks.attention_harness import human_proxy as hp
from benchmarks.attention_harness import v2_evaluator as ev
from benchmarks.attention_harness.core.models import RequestType
from benchmarks.attention_harness.core.policies import POLICY_IDS, build_policy, PolicyContext, DecisionAction
from benchmarks.attention_harness.dev_experiment import create, run, GOAL, verify_manifest
from benchmarks.attention_harness.dev_help import decide, policy_parameters, public_screen
from benchmarks.attention_harness.dev_ledger import DevV2Store, attention_cost, fill_optional, complete_resources, RESOURCES, OPTIONAL
from benchmarks.attention_harness.parcc_client import ParccResponse
from benchmarks.attention_harness.tests.test_v2_evaluator import evidence_dir, add_views
from openarm365_dev_v2.tests.fakes import Client, Runner

FAIL = {'execution_status': 'failed', 'public_error': 'RuntimeError: motion rejected',
        'action_trace': [{'source': 'left_arm', 'operation': 'move_delta', 'status': 'failed', 'arguments': {}, 'error': {'message': 'IK planning rejected'}}]}
PROXY = {'enabled': True, 'model': 'parcc/Qwen'}
USAGE = {'total_tokens': 100, 'prompt_tokens': 40, 'completion_tokens': 60}
REVIEW_FAIL = 'EVAL_RESULT: {"passed": false, "feedback": "frame #1 and trace event #0: the arm never reached the handle."}'
REVIEW_PASS = 'EVAL_RESULT: {"passed": true, "feedback": "frame #1 shows the door open."}'


# ---------------------------------------------------------------- policy rules (pure decisions)

def d(policy, params=None, *, version=2, failures=1, remaining=2, review=None, made=(), available=True):
    return decide(policy, policy_parameters(policy, params), version, FAIL, failures, help_remaining=remaining,
                  review_available=available, review=review, requests_made=made)


@pytest.mark.parametrize('policy', ['autonomous', 'demo_first'])
def test_non_asking_policies_never_request(policy):
    for failures in range(1, 5):
        for version in (2, 3):
            assert d(policy, version=version, failures=failures)['action'] != 'request'


def test_reactive_reviews_first_then_hints_only_after_a_failed_review():
    first = d('reactive_help')
    assert (first['action'], first['request_type']) == ('request', 'review')
    second = d('reactive_help', made=('review',), review={'passed': False, 'feedback': 'frame #1: missed'})
    assert (second['action'], second['request_type']) == ('request', 'hint')
    assert d('reactive_help', made=('review',), review={'passed': True, 'feedback': 'ok'})['action'] == 'continue'
    done = d('reactive_help', made=('review', 'hint'), review={'passed': False, 'feedback': 'x'})
    assert done['action'] == 'retry'
    # budget used up: no request at all, in either round
    assert d('reactive_help', remaining=0)['action'] == 'retry'
    assert d('reactive_help', remaining=0, made=('review',), review={'passed': False, 'feedback': 'x'})['action'] == 'retry'


def test_reactive_without_a_proxy_keeps_the_hint_only_behaviour():
    r = d('reactive_help', available=False)
    assert (r['action'], r['request_type']) == ('request', 'hint')


def test_retry_k_waits_for_k_unconfirmed_versions_then_behaves_like_reactive():
    assert d('retry_k_then_ask', {'k': 2}, failures=1)['action'] == 'retry'
    r = d('retry_k_then_ask', {'k': 2}, failures=2)
    assert (r['action'], r['request_type']) == ('request', 'review')
    r = d('retry_k_then_ask', {'k': 2}, failures=2, made=('review',), review={'passed': False, 'feedback': 'x'})
    assert (r['action'], r['request_type']) == ('request', 'hint')


def test_random_escalation_matches_request_count_and_review_mix_and_is_reproducible():
    params = {'target_request_count': 3, 'total_failure_slots': 5, 'seed': 7, 'target_review_count': 2}
    def run_once():
        kinds = []
        for slot in range(5):
            r = d('budget_matched_random_escalation', params, failures=slot + 1)
            kinds.append(r['request_type'] if r['action'] == 'request' else None)
        return kinds
    kinds = run_once()
    assert kinds == run_once()
    assert sum(k is not None for k in kinds) == 3 and kinds.count('review') == 2 and kinds.count('hint') == 1
    # a selected slot sends exactly one request: after the review no hint is added
    slot = kinds.index('review')
    again = d('budget_matched_random_escalation', params, failures=slot + 1, made=('review',), review={'passed': False, 'feedback': 'x'})
    assert again['action'] == 'retry'


def test_random_escalation_legacy_parameters_still_mean_all_hints():
    legacy = {'target_request_count': 1, 'total_failure_slots': 2, 'seed': 101}
    kinds = [d('budget_matched_random_escalation', legacy, failures=i + 1) for i in range(2)]
    assert [k['request_type'] for k in kinds if k['action'] == 'request'] == ['hint']
    with pytest.raises(ValueError):policy_parameters('budget_matched_random_escalation', {**legacy, 'target_review_count': 2})
    with pytest.raises(ValueError):policy_parameters('budget_matched_random_escalation', {**legacy, 'bogus': 1})


def test_hint_only_can_never_request_a_review():
    for failures in range(1, 5):
        for made in ((), ('review',)):
            r = decide('trace_aware_hint_only', {}, 2 + failures, FAIL, failures, help_remaining=2, review_available=True, requests_made=made,
                       review={'passed': False, 'feedback': 'x'} if made else None)
            assert r['request_type'] != 'review'


def test_full_planner_can_choose_review_hint_or_continue():
    seen = set()
    for failures in range(1, 5):
        r = decide('full_trace_aware_attention_planner', {}, 2 + failures, FAIL, failures, help_remaining=2, review_available=True)
        seen.add(r['request_type'] if r['action'] == 'request' else r['action'])
        r2 = decide('full_trace_aware_attention_planner', {}, 2 + failures, FAIL, failures, help_remaining=2, review_available=True,
                    requests_made=('review',), review={'passed': False, 'feedback': 'x'})
        seen.add(r2['request_type'] if r2['action'] == 'request' else r2['action'])
        r3 = decide('full_trace_aware_attention_planner', {}, 2 + failures, FAIL, failures, help_remaining=2, review_available=True,
                    requests_made=('review',), review={'passed': True, 'feedback': 'ok'})
        seen.add(r3['request_type'] if r3['action'] == 'request' else r3['action'])
    assert {'review', 'hint', 'continue'} <= seen


def test_review_feedback_text_never_enters_the_policy_context_only_the_outcome():
    r = d('reactive_help', made=('review',), review={'passed': False, 'feedback': 'frame #1: the handle was missed'})
    assert 'handle' not in json.dumps(r) and r['context']['review_outcome'] == 'failed'
    assert d('reactive_help')['context']['review_outcome'] is None


def test_oracle_fields_cannot_reach_the_policy_context():
    for bad in ('native_success', 'reward', '_check_success', 'private state', 'evaluator verdict'):
        with pytest.raises(ValueError):d('reactive_help', made=('review',), review={'passed': False, 'feedback': bad})
    with pytest.raises(ValueError):d('reactive_help', review={'passed': False, 'feedback': 'x'})  # outcome without a request


def test_request_type_enum_keeps_approval_and_interrupt():
    assert {t.value for t in RequestType} == {'review', 'hint', 'approval', 'interrupt'}


# ---------------------------------------------------------------- ledger arithmetic

def test_attention_cost_recomputes_under_other_weights():
    used = {'help_credits': 3, 'review_human_seconds': 120, 'hint_human_seconds': 60}
    assert attention_cost(used, request_weight=1, second_weight=0) == 3
    assert attention_cost(used, request_weight=0, second_weight=1) == 180
    assert attention_cost(used) == pytest.approx(3 + 180 / 60)
    assert attention_cost({}) == 0


def test_old_budgets_without_the_new_resources_stay_valid(tmp_path):
    old = {k: 3 for k in RESOURCES if k not in OPTIONAL}
    assert complete_resources(old) and fill_optional(old)['review_human_seconds'] == 0
    assert not complete_resources({**old, 'surprise': 1})
    s = DevV2Store(tmp_path / 'ledger.sqlite')
    s.ensure_goal('legacy-goal-1', old, 300)
    s.ensure_goal('legacy-goal-1', fill_optional(old), 300)  # same limits spelled with the new fields: not a conflict
    assert s.goal('legacy-goal-1')['remaining']['review_human_seconds'] == 0


# ---------------------------------------------------------------- HumanProxy unit tests

class ProxyClient(Client):
    """Dev calls get the normal fake answers. Proxy calls (review/hint, vision or the text-only fallback) are scripted."""
    def __init__(self, review=REVIEW_FAIL, hint='Move the gripper 2 cm lower before closing (frame #1).', fail_vision=0, vision_down=False):
        super().__init__()
        self.review_text, self.hint_text, self.fail_vision, self.vision_down = review, hint, fail_vision, vision_down
        self.proxy_inputs = []
        self.timeout_seconds = 120
    def is_proxy(self, kw):return str(kw['messages'][0]['content']).startswith('You are a robotics')
    def chat(self, **kw):
        if not self.is_proxy(kw):return super().chat(**kw)
        self.proxy_inputs.append(kw)
        text_only = kw['model'] == 'parcc/GLM'
        if not text_only:
            if self.vision_down:raise TimeoutError('vision timed out')
            if self.fail_vision > 0:
                self.fail_vision -= 1
                raise RuntimeError('HTTP 503')
        system = kw['messages'][0]['content']
        text = self.hint_text if system.startswith('You are a robotics supervisor') else self.review_text
        return ParccResponse(kw['model'], text, None, .01, 1, USAGE)
    def kinds(self):
        return ['hint' if kw['messages'][0]['content'].startswith('You are a robotics supervisor') else 'review' for kw in self.proxy_inputs]


def images_of(kw):
    content = kw['messages'][1]['content']
    return [p for p in content if isinstance(p, dict) and p['type'] == 'image_url'] if isinstance(content, list) else []


def evidence(tmp_path, frames=12, views=True, hypothesis=None):
    (tmp_path).mkdir(parents=True, exist_ok=True)
    root = evidence_dir(tmp_path / 'execution', frames)
    if views and frames:add_views(root, frames)
    e = ev.build_eval_evidence(root, {'task_id': 'open_cabinet', 'task_prompt': 'Open the cabinet'})
    if hypothesis:e['agent_hypothesis'] = hypothesis
    return e


def test_config_is_explicit_hashed_and_validated():
    c = hp.normalize({'enabled': True})
    assert c['model'] == 'parcc/Qwen' and c['prompt_versions'] == {'review': hp.REVIEW_PROMPT_VERSION, 'hint': hp.HINT_PROMPT_VERSION}
    assert set(c['prompt_sha256']) == {'review', 'hint'} and c['reading_seconds'] == {'review': 30, 'hint': 30}
    assert hp.normalize(c) == c
    for bad in ({'model': 'parcc/GLM'}, {'bogus': 1}, {'max_attempts': 5}, {'max_attempts': 3, 'attempt_timeout_seconds': 55},
                {'prompt_sha256': {'review': 'x', 'hint': 'y'}}, {'text_only_fallback': 'always'}, {'reading_seconds': {'review': 30}}):
        with pytest.raises(ValueError):hp.normalize(bad)
    assert hp.from_config({'eval_agent': {'enabled': True, 'model': 'parcc/Qwen'}})['model'] == 'parcc/Qwen'  # legacy alias
    assert hp.from_config({'eval_agent': {'enabled': False, 'model': 'parcc/Qwen'}}) is None
    assert hp.from_config({}) is None
    assert hp.logical_seconds('review', 12.2, c) == 13 + 30


def test_review_and_hint_get_images_from_every_view_and_the_frames_are_recorded(tmp_path):
    e = evidence(tmp_path, hypothesis='The handle is higher than I assumed.')
    client = ProxyClient()
    cfg = hp.normalize({})
    review = hp.ask('review', e, cfg, client)
    hint = hp.ask('hint', e, cfg, client)
    assert client.kinds() == ['review', 'hint']
    for record, kw in zip((review, hint), client.proxy_inputs):
        assert kw['model'] == 'parcc/Qwen' and len(images_of(kw)) == len(record['frames']) > 0
        assert record['mode'] == 'vision' and {'overview', 'side', 'top', 'left_wrist', 'right_wrist'} <= set(record['views'])
        assert all(f['sha256'] and f['view'] for f in record['frames'])
        assert images_of(kw)[0]['image_url']['url'].startswith('data:image/jpeg;base64,')
    assert review['passed'] is False and hint['passed'] is None and hint['feedback'].startswith('Move the gripper')
    hint_wire = json.dumps(client.proxy_inputs[1]['messages'][1]['content'][0])
    assert 'handle is higher' in hint_wire and 'public_feedback' in hint_wire  # hypothesis and public trace reach the hint
    assert client.proxy_inputs[0]['messages'][0]['content'] != client.proxy_inputs[1]['messages'][0]['content']  # two prompts


def test_information_boundary_no_oracle_fields(tmp_path):
    e = evidence(tmp_path, hypothesis='native_success looked true')  # a hypothesis naming an oracle is dropped by the loop; here it must be rejected
    root = tmp_path / 'execution'
    for name in ('native_result.json', 'safety.json', 'result.json', 'config.json'):
        (root / name).write_text('native_success _check_success SECRET_SCENE')
    e2 = ev.build_eval_evidence(root, {'task_id': 'open_cabinet', 'task_prompt': 'Open the cabinet'})
    client = ProxyClient()
    hp.ask('hint', e2, hp.normalize({}), client)
    wire = ev.encoded(client.proxy_inputs[0]['messages'])
    assert not ev.FORBIDDEN.search(wire) and 'SECRET_SCENE' not in wire
    with pytest.raises(AssertionError):hp.messages_for('hint', e)  # contaminated evidence cannot be turned into a request


def test_vision_is_retried_then_the_call_is_refused_not_degraded(tmp_path):
    e = evidence(tmp_path)
    cfg = hp.normalize({})
    ok = ProxyClient(fail_vision=1)
    r = hp.ask('review', e, cfg, ok)
    assert r['attempts'] == 2 and r['mode'] == 'vision' and ok.kinds() == ['review', 'review']
    down = ProxyClient(vision_down=True)
    with pytest.raises(hp.HumanProxyUnavailable):hp.ask('review', e, cfg, down)
    assert len(down.proxy_inputs) == cfg['max_attempts'] and all(kw['model'] == 'parcc/Qwen' for kw in down.proxy_inputs)  # no GLM call
    with pytest.raises(hp.HumanProxyUnavailable):hp.ask('hint', evidence(tmp_path / 'n', frames=0), cfg, ProxyClient())  # no frames at all
    garbled = ProxyClient(review='no envelope at all')
    with pytest.raises(hp.HumanProxyUnavailable):hp.ask('review', e, cfg, garbled)  # an unparseable answer is a failed attempt


def test_text_only_fallback_exists_only_when_explicitly_allowed_and_is_labelled(tmp_path):
    e = evidence(tmp_path)
    client = ProxyClient(vision_down=True, review='EVAL_RESULT: {"passed": true, "feedback": "The door is open in frame #2."}')
    r = hp.ask('review', e, hp.normalize({}), client, allow_text_only=True)
    assert r['mode'] == 'text_only' and r['model'] == 'parcc/GLM' and r['passed'] is None
    assert r['feedback'].lower().startswith('text-only') and r['fallback_reason']
    assert not images_of(client.proxy_inputs[-1])


def test_cache_replays_identical_answers_and_keys_on_image_hashes(tmp_path):
    e = evidence(tmp_path)
    cfg = hp.normalize({})
    cache = tmp_path / 'cache'
    c1, c2 = ProxyClient(), ProxyClient(review=REVIEW_PASS)  # c2 would answer differently if it were called
    first = hp.ask('review', e, cfg, c1, cache_dir=cache)
    second = hp.ask('review', e, cfg, c2, cache_dir=cache)
    assert not first['cache_hit'] and second['cache_hit'] and not c2.proxy_inputs
    assert (second['passed'], second['feedback']) == (first['passed'], first['feedback']) and second['cache_key'] == first['cache_key']
    assert second['frames'] == first['frames']  # frames sent are recorded even on a replay
    # review and hint never share an entry; a different image changes the key
    assert hp.cache_key('hint', e, cfg) != hp.cache_key('review', e, cfg)
    changed = json.loads(json.dumps(e))
    changed['frames'][0]['sha256'] = 'f' * 64
    assert hp.cache_key('review', changed, cfg) != hp.cache_key('review', e, cfg)
    other_prompt = {**cfg, 'prompt_versions': {**cfg['prompt_versions'], 'review': 'human-proxy-review-v2'}}
    assert hp.cache_key('review', e, other_prompt) != hp.cache_key('review', e, cfg)
    # a text-only answer is never cached
    t = hp.ask('review', evidence(tmp_path / 'b', frames=3, views=False), cfg, ProxyClient(vision_down=True), cache_dir=tmp_path / 'c2', allow_text_only=True)
    assert t['mode'] == 'text_only' and not list((tmp_path / 'c2').glob('*.json'))


# ---------------------------------------------------------------- the loop (fake runner writes frames)

class FrameRunner(Runner):
    def execute(self, r):
        result = super().execute(r)
        root = Path(result['artifact_dir'])
        frames = root / 'recording_frames'
        frames.mkdir()
        for i in range(4):Image.new('RGB', (320, 180), (i * 20, 0, 0)).save(frames / f'{i:03}.jpg')
        add_views(root, 4)
        return result


class NoFrameRunner(Runner):
    pass


def setup_run(tmp_path, policy='reactive_help', help_budget=2, mode='experiment', proxy=PROXY, extra=None):
    s = DevV2Store(tmp_path / 'ledger.sqlite')
    selection = dict(request_key='hp-loop-test', tasks=['open_cabinet'], seeds=[102], policies=[policy], help_budget=help_budget, mode=mode)
    if proxy is not None:selection['human_proxy'] = proxy
    selection.update(extra or {})
    e = create(s, tmp_path / 'artifacts', selection)
    budget = tmp_path / 'budget.json'
    budget.write_text(json.dumps({'goal': GOAL, 'limits': {'dev_calls': 28, 'advisor_calls': 14, 'provider_tokens': 500000, 'executions': 28, 'wall_seconds': 7200}}))
    return s, e, budget


def go(s, e, b, client, runner=FrameRunner, **kw):
    return run(s, e['experiment_id'], max_cells=kw.pop('max_cells', 1), budget_file=b, client=client, runner_factory=runner, **kw)


def dev_packets(client):
    return [json.loads(kw['messages'][1]['content']) for kw in client.inputs if not client.is_proxy(kw) and kw['max_tokens'] == 8192]


def test_autonomous_never_requests_and_dev_sees_only_the_public_failure(tmp_path):
    s, e, b = setup_run(tmp_path, 'autonomous')
    client = ProxyClient()
    c = go(s, e, b, client)['cells'][0]
    assert c['state'] == 'completed' and not client.proxy_inputs
    assert c['used']['eval_calls'] == 0 and c['used']['help_count'] == 0 and c['used']['review_human_seconds'] == 0
    assert all('human_proxy_review' not in p['public_feedback'] for p in dev_packets(client))
    assert not any((Path(v['source']['uri']).parent / 'evaluator-review.json').exists() for v in c['versions'])


def test_reactive_review_then_hint_charges_one_shared_budget_and_two_logical_times(tmp_path):
    s, e, b = setup_run(tmp_path, 'reactive_help', help_budget=2)
    client = ProxyClient()
    c = go(s, e, b, client)['cells'][0]
    assert c['state'] == 'completed' and client.kinds() == ['review', 'hint']
    u = c['used']
    assert u['eval_calls'] == 1 and u['advisor_calls'] == 1 and u['help_count'] == 2 and u['help_credits'] == 2
    assert u['review_human_seconds'] == 31 and u['hint_human_seconds'] == 31  # ceil(recording 0.01 s) + 30 s reading, each
    assert attention_cost(u, request_weight=1, second_weight=0) == 2
    # the review conclusion reaches the next Dev version only because it was requested
    packets = dev_packets(client)
    assert 'human_proxy_review' not in packets[0]['public_feedback']
    assert packets[1]['public_feedback']['human_proxy_review'] == {'passed': False, 'feedback': 'frame #1 and trace event #0: the arm never reached the handle.'}
    # hint request: images from every view, vision model, shared decision record
    hint_call = client.proxy_inputs[1]
    assert len(images_of(hint_call)) >= 5 and hint_call['model'] == 'parcc/Qwen'
    v1 = Path(c['versions'][0]['source']['uri']).parent
    v2 = Path(c['versions'][1]['source']['uri']).parent
    round1 = json.loads((v2 / 'decision-round1.json').read_text())
    final = json.loads((v2 / 'decision.json').read_text())
    assert (round1['request_type'], final['request_type'], final['preceded_by']) == ('review', 'hint', 'review')
    review = json.loads((v1 / 'evaluator-review.json').read_text())
    assert review['requested'] and review['request_type'] == 'review' and review['mode'] == 'vision' and review['frames']
    help_record = json.loads((v2 / 'help.json').read_text())
    assert help_record['provider'] == 'human_proxy' and help_record['content'].startswith('Move the gripper')
    assert json.loads((v2 / 'human-proxy-hint.json').read_text())['views']
    assert s.export_ledger()['recompute_matches']
    from benchmarks.attention_harness.dev_report import report
    assert not report(s, e['experiment_id'])['cells'][0]['resource_errors']


def test_a_passing_review_ends_the_requests_and_is_shown_to_dev(tmp_path):
    s, e, b = setup_run(tmp_path, 'reactive_help', help_budget=2)
    client = ProxyClient(review=REVIEW_PASS)
    c = go(s, e, b, client)['cells'][0]
    assert client.kinds() == ['review'] and c['used']['help_count'] == 1 and c['used']['advisor_calls'] == 0
    assert dev_packets(client)[1]['public_feedback']['human_proxy_review']['passed'] is True
    assert 'HumanProxy review of your previous attempt' in client.inputs[-1]['messages'][0]['content']


def test_review_and_hint_share_the_budget_a_single_unit_buys_only_the_review(tmp_path):
    s, e, b = setup_run(tmp_path, 'reactive_help', help_budget=1)
    client = ProxyClient()
    c = go(s, e, b, client)['cells'][0]
    assert client.kinds() == ['review'] and c['used']['help_count'] == 1 and c['used']['advisor_calls'] == 0
    assert c['state'] == 'completed'


def test_hint_only_policy_asks_for_hints_and_never_gets_a_review(tmp_path):
    s, e, b = setup_run(tmp_path, 'trace_aware_hint_only', help_budget=2, extra=dict(policy_parameters={}))
    client = ProxyClient()
    c = go(s, e, b, client)['cells'][0]
    assert 'review' not in client.kinds()
    assert all('human_proxy_review' not in p['public_feedback'] for p in dev_packets(client))
    assert c['used']['eval_calls'] == 0


def test_cache_replay_across_cells_charges_attention_but_not_model_time(tmp_path):
    s, e, b = setup_run(tmp_path, 'reactive_help', help_budget=1, extra=dict(policies=['reactive_help', 'retry_k_then_ask'], policy_parameters={'retry_k_then_ask': {'k': 1}}))
    client = ProxyClient()
    final = go(s, e, b, client, max_cells=2)
    assert client.kinds() == ['review']  # the second cell replays the first answer
    first, second = final['cells'][0], final['cells'][1]
    j = [x for x in second['jobs'] if x['kind'] == 'eval'][0]
    assert j['receipt']['cache_hit'] is True and j['actual']['input_tokens'] == 0
    assert second['used']['help_count'] == 1 and second['used']['review_human_seconds'] == 31  # attention is charged regardless
    assert s.export_ledger()['recompute_matches']


def test_vision_failure_is_retried_once_inside_the_request(tmp_path):
    s, e, b = setup_run(tmp_path, 'reactive_help', help_budget=1)
    client = ProxyClient(fail_vision=1)
    c = go(s, e, b, client)['cells'][0]
    assert c['state'] == 'completed' and client.kinds() == ['review', 'review']
    assert c['used']['help_count'] == 1  # one request unit regardless of internal retries


def test_unavailable_vision_marks_the_cell_invalid_and_no_text_answer_is_used(tmp_path):
    s, e, b = setup_run(tmp_path, 'reactive_help', help_budget=2)
    client = ProxyClient(vision_down=True)
    final = go(s, e, b, client)
    c = final['cells'][0]
    assert c['state'] == 'condition_invalid' and 'human_proxy_vision_unavailable' in c['reason']
    assert len(c['versions']) == 1  # no second Dev version was generated from a degraded answer
    assert all(kw['model'] == 'parcc/Qwen' for kw in client.proxy_inputs)
    review = json.loads((Path(c['versions'][0]['source']['uri']).parent / 'evaluator-review.json').read_text())
    assert review['status'] == 'invalid_vision_unavailable'
    assert c['used']['eval_calls'] == 1 and s.export_ledger()['recompute_matches']  # the attempt is charged, never refunded


def test_missing_frames_invalidate_an_experiment_cell(tmp_path):
    s, e, b = setup_run(tmp_path, 'reactive_help')
    c = go(s, e, b, ProxyClient(), runner=NoFrameRunner)['cells'][0]
    assert c['state'] == 'condition_invalid'


def test_interactive_mode_may_degrade_to_a_labelled_text_only_answer(tmp_path):
    s, e, b = setup_run(tmp_path, 'reactive_help', help_budget=1, mode='interactive')
    client = ProxyClient(vision_down=True, review='EVAL_RESULT: {"passed": true, "feedback": "Looks done in frame #2."}')
    c = go(s, e, b, client)['cells'][0]
    assert c['state'] == 'completed'
    r = json.loads((Path(c['versions'][0]['source']['uri']).parent / 'evaluator-review.json').read_text())
    assert r['mode'] == 'text_only' and r['passed'] is None
    assert 'TEXT-ONLY review of your previous attempt' in client.inputs[-1]['messages'][0]['content']


def test_legacy_eval_agent_configuration_is_an_alias_that_no_longer_reviews_for_free(tmp_path):
    s, e, b = setup_run(tmp_path, 'autonomous', proxy=None, extra=dict(eval_agent={'enabled': True, 'model': 'parcc/Qwen'}))
    client = ProxyClient()
    c = go(s, e, b, client)['cells'][0]
    m = verify_manifest(e)
    assert m['configs']['open_cabinet:102']['eval_agent']['enabled'] is True and 'human_proxy' not in m['configs']['open_cabinet:102']
    assert not client.proxy_inputs and c['used']['eval_calls'] == 0  # an autonomous policy gets no automatic review any more
    s2, e2, b2 = setup_run(tmp_path / 'r', 'reactive_help', help_budget=1, proxy=None, extra=dict(eval_agent={'enabled': True, 'model': 'parcc/Qwen'}))
    client2 = ProxyClient()
    go(s2, e2, b2, client2)
    assert client2.kinds() == ['review']  # the alias enables the HumanProxy with default settings


def test_both_field_names_cannot_be_given_together(tmp_path):
    with pytest.raises(ValueError):setup_run(tmp_path, proxy=PROXY, extra=dict(eval_agent={'enabled': True, 'model': 'parcc/Qwen'}))


def test_human_proxy_settings_are_part_of_the_configuration_hash(tmp_path):
    s, e, _ = setup_run(tmp_path / 'a', 'autonomous')
    s2, e2, _ = setup_run(tmp_path / 'b', 'autonomous', proxy={'enabled': True, 'model': 'parcc/Qwen', 'reading_seconds': {'review': 45, 'hint': 30}})
    cfg1 = verify_manifest(e)['configs']['open_cabinet:102']
    cfg2 = verify_manifest(e2)['configs']['open_cabinet:102']
    assert cfg1['human_proxy']['reading_seconds']['review'] == 30 and cfg2['human_proxy']['reading_seconds']['review'] == 45
    assert e['manifest']['sha256'] != e2['manifest']['sha256']
    # an unreviewed edit of the stored settings is refused by the configuration validator
    from openarm365_dev_v2.dev_config import validate_dev_config
    bad = json.loads(json.dumps(cfg1))
    bad['human_proxy']['reading_seconds']['review'] = 0
    bad['human_proxy']['model'] = 'parcc/GLM'
    with pytest.raises(ValueError):validate_dev_config(bad)


def test_v3_budget_reserves_vision_requests_and_leaves_old_budgets_unchanged():
    from benchmarks.attention_harness.dev_configuration import derive_cell_budget, normalize_configuration, configured_budget
    base = dict(help_budget=2)
    plain = derive_cell_budget(normalize_configuration(base))
    with_proxy = derive_cell_budget(normalize_configuration({**base, 'human_proxy': {'enabled': True}}))
    assert plain['eval_calls'] == 0 and plain['review_human_seconds'] == 0
    assert with_proxy['eval_calls'] == 2 and with_proxy['review_human_seconds'] > 0 and with_proxy['provider_tokens'] > plain['provider_tokens']
    assert {k: v for k, v in with_proxy.items() if k in ('dev_calls', 'executions', 'help_count', 'model_seconds')} == {k: v for k, v in plain.items() if k in ('dev_calls', 'executions', 'help_count', 'model_seconds')}
    assert configured_budget(normalize_configuration({**base, 'human_proxy': {'enabled': True}}))['eval_calls'] == 2
    # a pre-existing complete cell budget without the new resources is still accepted
    legacy = {k: v for k, v in plain.items() if k not in OPTIONAL}
    assert configured_budget(normalize_configuration({**base, 'cell_budget': legacy}))['review_human_seconds'] == 0
