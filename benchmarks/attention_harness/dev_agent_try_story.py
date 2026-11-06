"""An Agent run told in plain words: every try the robot made, what stopped it, and a short review for people."""
import json
import re
from pathlib import Path

from .dev_agent_workspace import read_beneath

LIMIT = 4_000_000
# First match wins. A message nothing here knows stays readable as it was, under "other".
ERRORS = (
    (r'bilateral grasp contact lost', 'hold', 'The object slipped out of the gripper while moving'),
    (r'Close did not confirm bilateral', 'hold', 'The gripper closed but did not hold the object'),
    (r'lateral descent rejected: official_ik', 'reach', 'The arm could not reach sideways into the grasp'),
    (r'descent rejected: official_ik', 'reach', 'The arm could not reach down to the grasp point'),
    (r'Pregrasp approach failed', 'reach', 'The arm could not get to the starting point of the grasp'),
    (r'action_delta_limit', 'reach', 'The requested move was too large for one step'),
    (r'ik_joint_margin', 'reach', "That pose was too close to the arm's limits"),
    (r'joint tracking exceeds', 'reach', 'The arm could not hold its target closely enough'),
)
_cache = {}


def plain_error(message):
    """(category, sentence) for one failure."""
    for pattern, kind, text in ERRORS:
        if re.search(pattern, message or ''):return kind, text
    return 'other', re.sub(r'^\w*(Error|Rejected):\s*', '', (message or 'The try failed').strip())[:160]


def _json(root, name):
    try:return json.loads(read_beneath(root, name, LIMIT, shared=True))
    except (OSError, ValueError):return None


def _failure(public, eid):
    trace = _json(public, f'executions/{eid}/trace.json') or []
    bad = [row for row in trace if isinstance(row, dict) and row.get('status') in ('failed', 'rejected', 'blocked')]
    error = bad[-1].get('error') if bad else None
    message = error.get('message') if isinstance(error, dict) else error
    if message:return str(message)
    try:lines = read_beneath(public, f'executions/{eid}/stderr.txt', LIMIT, shared=True).decode(errors='replace').strip().splitlines()
    except (OSError, ValueError):return ''
    return lines[-1] if lines else ''


def _one(private, public, eid, attempt):
    run = _json(public, f'executions/{eid}/execution.json') or {}
    result = _json(public, f'executions/{eid}/result.json') or {}
    done = result.get('status') == 'completed'
    kind, why = ('done', 'Ran to the end') if done else plain_error(_failure(public, eid))
    row = dict(execution_id=eid, number=int(eid[1:]), submitted=run.get('op') == 'submit', ok=done, kind=kind, why=why)
    if row['submitted']:
        vid = 'v%04d' % attempt
        review = _json(public, f'versions/{vid}/eval.json') or {}
        plain = _json(private, f'eval/{vid}/plain.json') or {}
        row.update(attempt=attempt, passed=review.get('passed') if type(review.get('passed')) is bool else None,
                   wrong=plain.get('wrong'), next=plain.get('next'))
    help_row = _json(public, f'executions/{eid}/help.json')
    if help_row and help_row.get('delivered') is True:
        row['help'] = dict(content=help_row.get('content'), provider=help_row.get('provider', 'advisor'))
    return row


def tries(private):
    """Every try in order. A settled try never changes, so each is read once."""
    private = Path(private);public = private/'public';rows = [];attempt = 0
    for folder in sorted((public/'executions').glob('e*')):
        eid = folder.name
        if not re.fullmatch(r'e\d+', eid) or not (folder/'result.json').exists():continue
        op = (_json(public, f'executions/{eid}/execution.json') or {}).get('op')
        attempt += op == 'submit'
        vid = 'v%04d' % attempt
        key = (str(folder), attempt if op == 'submit' else 0, (folder/'help.json').exists(),
               op == 'submit' and (private/'eval'/vid/'plain.json').exists(), op == 'submit' and (public/'versions'/vid/'eval.json').exists())
        if key not in _cache:
            if len(_cache) > 4000:_cache.clear()
            _cache[key] = _one(private, public, eid, attempt)
        rows.append(_cache[key])
    return rows


SYSTEM = ('You rewrite a robot task review for someone who has never seen this project. Answer with exactly two lines. '
          'Line 1 starts with "Wrong: " and says what went wrong. Line 2 starts with "Next: " and says what the robot should try next. '
          'At most 25 words each, everyday words. No numbers, coordinates, units, file names, code or line references. '
          'Talk only about what the robot did, never about logs or reports.')
UNREADABLE = re.compile(r'\d|\.json|\.py\b|\.txt\b|`|trace|stdout|stderr')


def parse_review(text):
    """The two lines, or None if the answer is not what a person should be shown."""
    found = re.fullmatch(r'\s*Wrong:\s*(.+?)\s*\n+\s*Next:\s*(.+?)\s*', text or '', re.S)
    if not found:return None
    wrong, after = found.groups()
    if UNREADABLE.search(wrong+after) or len(wrong) > 300 or len(after) > 300:return None
    return dict(wrong=wrong, next=after)


def review_prompt(review):
    """The whole instruction for one turn: this rewrite is not a task, and the workspace has nothing to do with it."""
    return f"{SYSTEM} Do not run any command or read any file.\n\nReview: {review.get('reason', '')}\nAdvice: {review.get('advice', '')}"
