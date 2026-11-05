#!/usr/bin/env python3
"""Check one model route the way a run would, role by role. Runs under the UI environment."""
from __future__ import annotations

import argparse
import sys

from benchmarks.attention_harness.dev_agent_provider import probe, resolve_provider
from benchmarks.attention_harness.dev_agent_secret import Secret
from scripts.attentionbench import routes


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('model', choices=tuple(name for name, route in routes.ROUTES.items() if route.provider))
    p.add_argument('--dev-model')
    p.add_argument('--eval-model')
    p.add_argument('--advisor-model')
    p.add_argument('--base-url')
    p.add_argument('--api-key-env')
    args = p.parse_args()
    route = routes.ROUTES[args.model]
    key = routes.find_key(route, args.api_key_env)
    if not key:
        print(f'route {route.name} has no key: {route.hint}')
        return 1
    spec = resolve_provider(route.provider, args.base_url, allow_custom=True)
    models = routes.role_models(route, args.dev_model, args.eval_model, args.advisor_model)
    failed = False
    for role in routes.ROLES:
        result = probe(spec, Secret(key), models[role], role=role)
        blind = role != 'dev' and [name for name, value in result.checks.items() if value == 'fail'] == ['vision']
        failed |= not result.ok and not blind
        state = 'ok' if result.ok else 'text only: this role will not see images' if blind else 'failed'
        print(f'{role} {models[role]}: {state}; {result.checks}')
        if not result.ok and not blind:
            print('; '.join(error['message'] for error in result.errors))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
