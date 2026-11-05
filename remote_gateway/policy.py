"""What the gateway lets through. Everything here is default-deny and free of I/O, so it can be read and tested alone."""
import hashlib
import re
from urllib.parse import unquote

EXPERIMENT = re.compile(r'/api/dev-v2/experiments/(devexp-[a-f0-9]{24})(?:/(.+))?')
READABLE = re.compile(r'report|export\.json|cells/[a-f0-9]{64}/(?:view|agent/view|live|live/frame|agent/files/.+)')
COMMANDS = {'run', 'cancel', 'pause'}
CREDENTIAL = re.compile(r'/api/provider/credentials(?:/([a-z0-9-]{1,32})(/test|/models)?)?')
SEGMENT = re.compile(r'[A-Za-z0-9_][A-Za-z0-9._-]*')
QUERY = re.compile(r'[A-Za-z0-9_.=&-]{0,200}')
UI_ASSETS = {'shell.css': 'text/css; charset=utf-8', 'shell.js': 'text/javascript; charset=utf-8', 'notices.js': 'text/javascript; charset=utf-8',
             'human_reply.js': 'text/javascript; charset=utf-8', 'lobby.js': 'text/javascript; charset=utf-8', 'live.js': 'text/javascript; charset=utf-8'}
# Fields the harness keeps for itself: where things live on its disk, and who runs it.
PRIVATE_FIELDS = {'manifest', 'owner', 'runtime_root', 'runtime', 'planner_deployment', 'identity_lock', 'codex_identity', 'source_root', 'authorization', 'worker', 'pid',
                  'host', 'memory_authority', 'memory_family', 'quota', 'budget_file'}
# These can also be the page's own relative names for evidence (agent-view/...); only an absolute path is the harness's.
PATH_FIELDS = {'uri', 'artifact_dir', 'ledger_path', 'log_path'}
HOST_PATH = re.compile(r'(?<![\w:/.])/(?:home|root|tmp|var|mnt|opt|srv|usr|etc|Users)/[^\s"\'<>)\]:,;]*')
IDLE_STATES = {'planned', 'not_started'}
ENDED_STATES = {'completed', 'failed', 'cancelled', 'condition_invalid', 'needs_review', 'stopped', 'budget_exhausted'}


class Refused(Exception):
    def __init__(self, code, message, status=409):
        super().__init__(message);self.code, self.message, self.status = code, message, status


def safe_target(path):
    """No traversal, in any encoding, before a path is matched against anything."""
    once = unquote(path);twice = unquote(once)
    return once == twice and '\\' not in once and '//' not in once and '..' not in once and not any(ord(c) < 32 or ord(c) == 127 for c in once)


def readable(suffix):
    """Whether a part of an owned experiment may be read: its report, its views, and its files by plain names."""
    if suffix is None:return True
    if not READABLE.fullmatch(suffix):return False
    return all(SEGMENT.fullmatch(part) for part in suffix.split('/'))


def _absolute(value):
    return not isinstance(value, str) or (value.startswith('/') and not value.startswith('/api/'))


def scrub(value):
    """Drop what is the harness's own business, in any answer that reaches a browser."""
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items() if k not in PRIVATE_FIELDS and not (k in PATH_FIELDS and _absolute(v))}
    if isinstance(value, list):return [scrub(v) for v in value]
    if isinstance(value, str):return HOST_PATH.sub('[host path]', value)
    return value


def filter_runs(listing, owned):
    rows = [row for row in listing.get('dev_sessions', []) if row.get('experiment_id') in owned]
    return dict(runs=[], launches=[], dev_sessions=scrub(rows))


def active_runs(listing, owned):
    def state(row):return row.get('experiment_state') or row.get('state')
    return sum(1 for row in listing.get('dev_sessions', []) if row.get('experiment_id') in owned and state(row) not in IDLE_STATES | ENDED_STATES)


def filter_options(options, limits):
    v2 = options.get('dev_v2') or {}
    keep = ('enabled', 'budget_tier', 'tasks', 'fixture_tasks', 'agent_policies', 'registered_scene_seeds', 'default_cell_budget', 'default_max_iterations', 'profiles')
    return dict(dev_v2={**scrub({k: v2[k] for k in keep if k in v2}), 'goal_key': 'set-by-host'}, hosted=dict(limits=limits.public()))


def filter_presets(presets):
    return [p for p in presets if isinstance(p, dict) and p.get('id') != 'openai-compatible']


def _integer(value, low, high, name):
    if type(value) is not int or not low <= value <= high:raise Refused('invalid_' + name, f'{name.replace("_", " ").capitalize()} must be between {low} and {high}.', 400)
    return value


def _roles(value, credentials):
    """All three roles, each on the lab's PARCC or on a key this person saved. Nothing can reach the lab's Codex."""
    roles = value.get('roles') if isinstance(value, dict) else None
    if not isinstance(roles, dict) or set(roles) != {'dev', 'eval', 'advisor'}:
        raise Refused('roles_required', 'Choose a source for Dev, Eval and Advisor.', 400)
    known = {row['credential_id']: row for row in credentials}
    result = {}
    for role, desc in roles.items():
        if not isinstance(desc, dict) or not isinstance(desc.get('model'), str) or not 0 < len(desc['model'].strip()) <= 128:
            raise Refused('invalid_role', 'Each agent needs a model.', 400)
        name = desc.get('credential')
        if name == 'lab':
            if desc.get('provider') != 'parcc':raise Refused('lab_source_not_offered', 'Only the lab PARCC models are offered here.', 400)
            result[role] = dict(credential='lab', provider='parcc', model=desc['model'])
        elif name in known and desc.get('provider') == known[name]['provider'] and known[name]['provider'] != 'openai-compatible':
            result[role] = dict(credential=name, provider=known[name]['provider'], model=desc['model'].strip())
        else:raise Refused('key_not_found', 'That key is not saved, or has expired.', 400)
    return result


def rebuild(body, *, user, options, limits, credentials):
    """The selection the harness receives: built from named fields, never copied, so nothing else a browser sends gets through."""
    selection = body.get('selection') if isinstance(body, dict) else None
    if not isinstance(selection, dict):raise Refused('invalid_body', 'A selection is required.', 400)
    v2 = options.get('dev_v2') or {}
    tasks, seeds, policies = selection.get('tasks'), selection.get('seeds'), selection.get('policies')
    if not all(isinstance(v, list) and len(v) == 1 for v in (tasks, seeds, policies)):raise Refused('single_run_required', 'Choose one task, seed and policy.', 400)
    rules = selection.get('rules_profile')
    profile = next((p for p in v2.get('profiles', []) if p.get('task_id') == tasks[0] and p.get('rules_profile') == rules and p.get('enabled')), None)
    if profile is None:raise Refused('task_not_offered', 'That task is not offered.', 400)
    if policies[0] not in [p.get('policy_id') for p in (v2.get('agent_policies') or {}).get('policies', [])]:raise Refused('policy_not_offered', 'That policy is not offered.', 400)
    if selection.get('mode') not in ('experiment', 'interactive'):raise Refused('invalid_mode', 'Choose a session type.', 400)
    wanted = selection.get('agent_limits') if isinstance(selection.get('agent_limits'), dict) else {}
    client_key = selection.get('request_key')
    if not isinstance(client_key, str) or not 8 <= len(client_key) <= 100:raise Refused('invalid_body', 'A request key is required.', 400)
    roles = _roles(selection.get('agent_provider'), credentials)
    tag = hashlib.sha256(f'{user}\n{client_key}'.encode()).hexdigest()[:32]
    result = dict(request_key=f'gw-{user}-{tag}', goal_key=f'gw-{user}-{tag}', tasks=[tasks[0]], seeds=[_integer(seeds[0], 1, 99999, 'seed')], policies=[policies[0]],
                  mode=selection['mode'], help_budget=_integer(selection.get('help_budget'), 1, limits.help_requests, 'help_requests'),
                  dev_mode='agent', model_source='codex', budget_tier='dev', quota_stop_percent=90, wall_limit=21600,
                  dev_model=roles['dev']['model'], eval_model=roles['eval']['model'], advisor_model=roles['advisor']['model'],
                  agent_provider=dict(roles=roles),
                  agent_limits=dict(versions=_integer(wanted.get('versions', limits.versions), 1, limits.versions, 'versions'),
                                    wall_seconds=_integer(wanted.get('wall_seconds', limits.wall_seconds), 3600, limits.wall_seconds, 'wall_seconds')))
    if rules:result['rules_profile'] = rules
    suffix = selection.get('name_suffix')
    if isinstance(suffix, str) and suffix.strip():result['name_suffix'] = suffix.strip()[:80]
    return result


def audit_route(path):
    """A path with its identifiers taken out, for the log."""
    path = re.sub(r'devexp-[a-f0-9]{24}', '{experiment}', path)
    path = re.sub(r'cells/[a-f0-9]{64}(/agent/files)?(/.*)?', lambda m: 'cells/{cell}' + ('/agent/files/{file}' if m[1] else m[2] or ''), path)
    return re.sub(r'(/api/provider/credentials)/[^/]+', r'\1/{key}', path)
