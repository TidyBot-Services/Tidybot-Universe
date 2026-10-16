"""Formal suite runner for OpenArm365 on the authenticated cuRobo V2 owner.

Reuses the Dev v2 runner for everything physical (owner start, reset, SDK, native evaluation,
recording, cleanup, receipts). What it changes is the formal contract: it accepts an approved
entry lock and carries it into trace and result, takes budgets from the frozen formal
configuration, and hands the public Attention input (hint, demo, Memory guidance) to the policy.
"""
import json

from .formal_config import validate_formal_config
from .runner import OpenArm365DevRunnerV2, write


class OpenArm365FormalSuiteRunner(OpenArm365DevRunnerV2):
    runner_boundary = 'formal'
    allow_entry_lock = True

    def load_config(self, request):
        request.validate()
        if request.config_path is None:
            raise ValueError('OpenArm365 formal runner requires a frozen config')
        c = validate_formal_config(json.loads(request.config_path.read_bytes()))
        if (request.overall_deadline_seconds > c['whole_case_wall_seconds']
                or request.effective_attempt_deadline_seconds > c['attempt_wall_seconds']):
            raise ValueError('OpenArm365 deadline exceeds freeze; explicit attempt deadline required')
        return c

    def budgets(self, request, c):
        limit = min(c['sdk_call_limit'], request.sdk_call_limit or c['sdk_call_limit'])
        seconds = request.effective_attempt_deadline_seconds
        if not 0 < seconds <= c['attempt_wall_seconds']:
            raise ValueError('OpenArm365 attempt deadline exceeds the frozen attempt wall')
        return seconds, limit

    def policy_context(self, request, c, ready, validation_public, episode):
        from benchmarks.attention_harness.policy_input import public_attention_input
        from benchmarks.attention_harness.robocasa365.formal_runner import public_demo_steps
        attention = public_attention_input(request.attention_input)
        write(episode / 'policy_context.json', {
            'attention_input': attention, 'perception_mode': 'sim_gt',
            'source': 'official RoboCasa365 public SDK only', 'robot': c['robot'],
            'controller': c['controller'], 'task_id': c['task_id']})
        context = super().policy_context(request, c, ready, validation_public, episode)
        context.update(attention_input=attention, demo_steps=public_demo_steps(attention))
        return context
