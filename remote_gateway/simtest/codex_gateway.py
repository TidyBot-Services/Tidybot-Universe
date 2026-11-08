"""The gateway for this test only: next to the lab's PARCC it offers the lab's Codex (gpt-6.1-sol, reasoning high), under a stop line.

Each role (Dev, Eval, Advisor) runs on what the person chose in New run: PARCC stays PARCC, "Lab Codex · gpt-6.1-sol"
runs on gpt-6.1-sol with reasoning high. Nothing is replaced behind the form. Every run stops at 77% of the weekly Codex
quota; the harness also stops a run that has used 5 points of it (RUN_CAP_POINTS) whenever a role is on the lab's Codex.

The public gateway refuses Codex on purpose, and that does not change: nothing else starts this file, and neither its
defaults nor its deploy templates know about it.

  python -m remote_gateway.simtest.codex_gateway --config gateway.json [--model gpt-6.1-sol] [--stop-percent 77]
"""
import argparse
import threading

from remote_gateway import policy
from remote_gateway.config import load
from remote_gateway.server import create_server

MODEL = 'gpt-6.1-sol'
EFFORT = 'high'
STOP_PERCENT = 77  # The user's stop line; the public gateway's constant is 90.
RUN_CAP_POINTS = 5  # benchmarks.attention_harness.dev_agent_quota.RUN_CAP_POINTS, shown on the page.
_PLACEHOLDER = dict(credential='lab', provider='parcc', model='parcc/GLM')


def lab_codex(role):
    return isinstance(role, dict) and role.get('credential') == 'lab' and role.get('provider') == 'openai'


def with_lab_codex_roles(roles_check, *, model=MODEL):
    """The public role check, plus one more lab source: the lab's Codex on exactly `model`."""
    def checked(value, credentials):
        roles = value.get('roles') if isinstance(value, dict) else None
        if not isinstance(roles, dict):return roles_check(value, credentials)
        codex = [role for role, desc in roles.items() if lab_codex(desc)]
        for role in codex:
            if roles[role].get('model') != model:raise policy.Refused('lab_model_not_offered', f'Lab Codex here is {model} only.', 400)
        result = roles_check(dict(value, roles={role: _PLACEHOLDER if role in codex else desc for role, desc in roles.items()}), credentials)
        return {**result, **{role: dict(credential='lab', provider='openai', model=model) for role in codex}}
    return checked


def with_test_roles(rebuild, *, effort=EFFORT, stop_percent=STOP_PERCENT):
    """The run as the person chose it; only the stop line and Dev's effort are this test's."""
    def rebuilt(*args, **kwargs):
        return dict(rebuild(*args, **kwargs), dev_reasoning_effort=effort, quota_stop_percent=stop_percent)
    return rebuilt


def with_test_options(filter_options, *, model=MODEL, effort=EFFORT, stop_percent=STOP_PERCENT):
    """Tells the page this host offers the lab's Codex, so New run lists it next to PARCC."""
    def filtered(*args, **kwargs):
        value = filter_options(*args, **kwargs)
        hosted = dict(value.get('hosted') or {}, lab_codex=dict(model=model, reasoning_effort=effort, quota_stop_percent=stop_percent, run_cap_points=RUN_CAP_POINTS))
        return dict(value, hosted=hosted)
    return filtered


def install(*, model=MODEL, stop_percent=STOP_PERCENT):
    policy._roles = with_lab_codex_roles(policy._roles, model=model)
    policy.rebuild = with_test_roles(policy.rebuild, stop_percent=stop_percent)
    policy.filter_options = with_test_options(policy.filter_options, model=model, stop_percent=stop_percent)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--config', required=True)
    parser.add_argument('--model', default=MODEL)
    parser.add_argument('--stop-percent', type=float, default=STOP_PERCENT)
    # Kept so an old start command still works: the roles now come from the form, so this changes nothing.
    parser.add_argument('--reviewers', choices=('parcc', 'codex'), help='ignored: each role runs on what the person chose')
    args = parser.parse_args()
    install(model=args.model, stop_percent=args.stop_percent)
    config = load(args.config)
    config.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    server = create_server(config)
    threading.Thread(target=server.gateway.loop, args=(threading.Event(),), daemon=True).start()
    ignored = f' (--reviewers {args.reviewers} ignored)' if args.reviewers else ''
    print(f'test gateway (each role on the person\'s choice: PARCC or Lab Codex {args.model} high){ignored} on http://{config.listen_host}:{config.listen_port}/', flush=True)
    server.serve_forever()


if __name__ == '__main__':
    main()
