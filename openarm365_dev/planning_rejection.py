"""Public facts from the current authenticated rejection; never repair advice."""
import json
import math
from pathlib import Path

from curobo_v2.client import PlanningRejected


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _name(value):
    # Keep the fact inside the existing sandbox/worker message limits.
    return isinstance(value, str) and 0 < len(value) <= 120 and value.isprintable()


def rejection_fact(response):
    reason = response.get('reason')
    if reason == 'ik_joint_margin_below_execution_limit':
        candidates = response.get('candidate_joint_margins')
        limit = response.get('joint_margin_limit_rad')
        if not isinstance(candidates, list) or not candidates or not _number(limit):
            return ''
        evaluated = []
        for candidate in candidates:
            if candidate is None:
                continue
            if not isinstance(candidate, dict):
                return ''
            margin = candidate.get('minimum_joint_margin_rad')
            joints = candidate.get('joint_margins_rad')
            if (not _number(margin) or not isinstance(joints, dict) or not joints
                    or not all(_name(n) and _number(v) for n, v in joints.items())):
                return ''
            joint = min(joints, key=joints.get)
            if joints[joint] != margin:
                return ''
            evaluated.append((margin, joint))
        if not evaluated:
            return ''
        # Best means the largest minimum margin, not the closest-to-limit candidate.
        margin, joint = max(evaluated, key=lambda item: item[0])
        count = len(candidates)
        scope = (f'all {count} IK candidates are below the required margin'
                 if len(evaluated) == count and margin < limit else
                 f'{len(evaluated)} of {count} IK candidates have recorded margins')
        return (f'best evaluated IK candidate has closest joint {joint} at '
                f'{margin:.9g} rad from its limit (required {limit:.9g} rad); {scope}')
    if reason != 'start_state_collision_or_constraint_violation':
        return ''
    validation = response.get('validation')
    if not isinstance(validation, dict):
        return ''
    exact = validation.get('exact_geometry')
    exact = exact if isinstance(exact, dict) else {}
    checks = []
    if exact.get('collision_free') is False:
        checks.append('exact geometry')
    if validation.get('proxy_collision_free') is False:
        checks.append('sphere proxy')
    if validation.get('joint_limits') is False:
        checks.append('joint limits')
    if not checks:
        return ''
    context = []
    # The proxy result is aggregate: attachment presence does not prove which
    # sphere collided. Do not turn a carried-object inventory into contact names.
    if validation.get('proxy_collision_free') is False and validation.get('attachment'):
        context.append('carried object present')
    if exact.get('collision_free') is True:
        context.append('exact geometry clear')
    names = []
    for check in (exact, validation):
        contacts = check.get('contacts', [])
        if not isinstance(contacts, list):
            continue
        for contact in contacts:
            bodies = contact.get('bodies', []) if isinstance(contact, dict) else []
            if isinstance(bodies, list):
                for name in bodies:
                    if _name(name) and name not in names and len(names) < 2:
                        names.append(name)
    if names:
        context.append('colliding bodies: ' + ', '.join(names))
    return ('checks failing at the start state: ' + ', '.join(checks)
            + (' (' + '; '.join(context) + ')' if context else ''))


def call_with_rejection_facts(client, path, body, settings):
    if (getattr(settings, 'profile', None) not in ('r3-20261025', 'r4-20261027')
            or getattr(client, 'evidence', None) is None):
        return client.call(path, body)
    # V2Client writes its response in finally before raising. Capture the offset
    # before this call so an old rejection can never supply facts for a new one.
    evidence = Path(client.evidence) / 'v2_client.jsonl'
    try:
        offset = evidence.stat().st_size
    except FileNotFoundError:
        offset = 0
    except OSError:
        return client.call(path, body)
    try:
        return client.call(path, body)
    except PlanningRejected as exc:
        try:
            with evidence.open('rb') as stream:
                stream.seek(offset)
                rows = stream.read().splitlines()
            row = json.loads(rows[-1]) if len(rows) == 1 else {}
            response = row.get('response')
            if (isinstance(response, dict) and row.get('path') == path
                    and row.get('request') == body
                    and row.get('error') == f'PlanningRejected: {exc}'
                    and str(exc) == 'cuRoboV2 planning rejected: ' + str(response.get('reason'))):
                fact = rejection_fact(response)
                if fact:
                    exc.args = (str(exc) + '; ' + fact,)
        except (OSError, ValueError, TypeError, AttributeError):
            # Missing/malformed optional evidence must not mask the rejection.
            pass
        raise
