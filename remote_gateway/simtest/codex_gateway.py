"""The gateway for this test only: Dev, Eval and Advisor are all the lab's Codex, under a stop line.

PARCC could not be reached from this host on 2026-11-07, so the test does not use it, and it does not test that path.

The public gateway refuses Codex on purpose, and that does not change: nothing else starts this file, and neither its
defaults nor its deploy templates know about it. The page is unchanged; a person picks the lab models as usual.

  python -m remote_gateway.simtest.codex_gateway --config gateway.json [--model gpt-6-astra] [--stop-percent 77]
"""
import argparse
import threading

from remote_gateway import policy
from remote_gateway.config import load
from remote_gateway.server import create_server

MODEL = 'gpt-6-astra'
STOP_PERCENT = 77  # The user's stop line; the public gateway's constant is 90.


def with_codex_roles(rebuild, *, model=MODEL, stop_percent=STOP_PERCENT):
    def rebuilt(*args, **kwargs):
        result = rebuild(*args, **kwargs)
        roles = {role: dict(credential='lab', provider='openai', model=model) for role in ('dev', 'eval', 'advisor')}
        return dict(result, agent_provider=dict(roles=roles), dev_model=model, eval_model=model, advisor_model=model, quota_stop_percent=stop_percent)
    return rebuilt


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--config', required=True)
    parser.add_argument('--model', default=MODEL)
    parser.add_argument('--stop-percent', type=float, default=STOP_PERCENT)
    args = parser.parse_args()
    policy.rebuild = with_codex_roles(policy.rebuild, model=args.model, stop_percent=args.stop_percent)
    config = load(args.config)
    config.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    server = create_server(config)
    threading.Thread(target=server.gateway.loop, args=(threading.Event(),), daemon=True).start()
    print(f'test gateway (all roles on Codex) on http://{config.listen_host}:{config.listen_port}/', flush=True)
    server.serve_forever()


if __name__ == '__main__':
    main()
