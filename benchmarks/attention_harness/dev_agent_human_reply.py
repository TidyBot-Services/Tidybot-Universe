"""A person's answer to one Agent help request. The worker waits in the run's private folder; the UI server writes there.

The first outcome written wins, so a reply that lands as the window closes either reaches Dev or is refused, never both."""
import json
import os
import re
import tempfile
import time
from pathlib import Path

from .core.dev_store import DevConflict
from .dev_help import public_screen

MIN_SECONDS, MAX_SECONDS, MAX_REPLY = 30, 600, 2000
EXECUTION = re.compile(r'e\d{6}')
WAIT, OUTCOME = 'human-wait.json', 'human-outcome.json'


def _folder(private, execution_id):
    if not isinstance(execution_id, str) or not EXECUTION.fullmatch(execution_id):raise ValueError('invalid execution id')
    return Path(private)/'help'/execution_id


def _read(path):
    try:return json.loads(path.read_bytes())
    except (OSError, ValueError):return None


def _settle(folder, outcome):
    """Write the outcome if none exists yet. Returns the one that stands."""
    with tempfile.NamedTemporaryFile('w', dir=folder, delete=False, encoding='utf-8') as stream:
        json.dump(outcome, stream);name = stream.name
    try:
        os.link(name, folder/OUTCOME)
        return outcome
    except FileExistsError:
        return _read(folder/OUTCOME)
    finally:
        os.unlink(name)


def answer(private, execution_id, *, question, seconds, poll=.5, clock=time.time, sleep=time.sleep):
    """Wait up to seconds for a person. Returns their words, or None when the Advisor should answer."""
    folder = _folder(private, execution_id);folder.mkdir(parents=True, exist_ok=True)
    deadline = clock()+seconds
    (folder/WAIT).write_text(json.dumps(dict(execution_id=execution_id, question=question, deadline_at=deadline, seconds=seconds)), encoding='utf-8')
    while clock() < deadline and not (folder/OUTCOME).exists():sleep(poll)
    outcome = _settle(folder, dict(kind='timed_out', at=clock()))
    return outcome.get('content') if outcome.get('kind') == 'reply' else None


def pending(private, cell=None):
    """The open wait the page should offer a reply to, if any."""
    root = Path(private)/'help'
    for path in sorted(root.glob('e*/'+WAIT), reverse=True):
        if not (path.parent/OUTCOME).exists():
            value = _read(path)
            if value and value['deadline_at'] > time.time():return dict(value, cell=cell)
    return None


def submit(private, execution_id, *, content=None, skip=False):
    """One reply or skip from the page, on the run's open wait."""
    folder = _folder(private, execution_id)
    if skip is not True and (not isinstance(content, str) or not content.strip() or len(content) > MAX_REPLY):
        raise ValueError(f'A reply is 1 to {MAX_REPLY} characters.')
    wait = _read(folder/WAIT)
    if not wait or wait['deadline_at'] <= time.time():raise DevConflict('This request is no longer waiting for a reply.')
    outcome = dict(kind='skip', at=time.time()) if skip is True else dict(kind='reply', at=time.time(), content=content.strip())
    if skip is not True:
        try:public_screen(content)
        except ValueError:raise ValueError('This reply cannot be sent: it quotes evaluation text.') from None
    if _settle(folder, outcome) != outcome:raise DevConflict('This request is no longer waiting for a reply.')
    return dict(status='received', kind=outcome['kind'])
