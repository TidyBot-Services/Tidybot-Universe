"""Deterministic, post-version Memory citation audit; never calls a model or Service.

Only authored messages/reasoning and Python COMMENT tokens qualify. Tool output,
prompts, string literals and approximate behavioural similarity are not citations.
The optional behavioural_matcher hook is gated on machine-readable note fields;
its evidence is separate and never changes `referenced` or the UI's used count.
"""
import io
import json
from pathlib import Path
import re
import tokenize

from .dev_agent_workspace import atomic_write, encode, read_beneath, stat_beneath

LIMIT = 32 * 1024 * 1024
WORDS = re.compile(r"\w+(?:['’]\w+)*", re.UNICODE)


def _words(text):
    return [(m.group().casefold(), m.start(), m.end()) for m in WORDS.finditer(text)]


def _match(note, text):
    for field in ('memory_id', 'title'):
        value = note.get(field)
        if value and value != '[withheld]':
            boundary = r'[\w-]' if field == 'memory_id' else r'\w'
            match = re.search(r'(?<!' + boundary + ')' + re.escape(value) + r'(?!' + boundary + ')', text, re.I)
            if match:
                return match.span()
    guidance = _words(note.get('content', note.get('guidance', '')))
    windows = {tuple(w[0] for w in guidance[i:i+8]) for i in range(len(guidance)-7)}
    words = _words(text)
    for i in range(len(words)-7):
        if tuple(w[0] for w in words[i:i+8]) in windows:
            return words[i][1], words[i+7][2]
    return None


def _evidence(source, span):
    start, end = span
    text = source['text']
    match_line = source.get('line', 0) + text[:start].count('\n')
    # Centre the excerpt on the actual match, even in a long reasoning item.
    start = max(0, start - min(50, max(0, 200 - (end-start))))
    result = {k: source[k] for k in ('file', 'line', 'event_index') if k in source}
    if 'line' in result:
        result['line'] = match_line
    result['excerpt'] = text[start:start+200]
    return result


def analyse(notes, sources, *, behavioural_matcher=None):
    """Pure function over delivered notes and scoped, authored text records.

    A future matcher receives (machine_fields, sources) and returns evidence
    pointers of the same shape. Human-readable applies_when strings do not enable
    it. No current note schema or approximate action matching is assumed.
    """
    result = []
    for note in notes:
        row = {k: note[k] for k in ('memory_id', 'title', 'level')}
        evidence = []
        declined = []
        for source in sources:
            identity = note.get('memory_id', '')
            decisions = list(re.finditer(r'(?<![\w-])(?P<no>Not )?using note\s+'
                             + re.escape(identity) + r'(?![\w-])\s*:', source['text'], re.I)) if identity else []
            if decisions:
                for decision in decisions:
                    target = declined if decision.group('no') else evidence
                    target.append(_evidence(source, decision.span()))
            else:
                span = _match(note, source['text'])
                if span is not None:
                    evidence.append(_evidence(source, span))
        row.update(delivered=True, referenced='explicit' if evidence else 'none', evidence=evidence)
        if declined:row.update(considered_not_used=True, considered_not_used_evidence=declined)
        fields = {k: note[k] for k in ('applies_when', 'parameters')
                  if isinstance(note.get(k), (dict, list)) and note[k]}
        if fields and behavioural_matcher is not None:
            hints = behavioural_matcher(fields, sources)
            row['behavioural_hint'] = [
                {**{k: e[k] for k in ('file', 'line', 'event_index') if k in e},
                 'excerpt': e['excerpt'][:200]} for e in hints]
        result.append(row)
    value = dict(notes=result)
    if any(n.get('considered_not_used') for n in result):
        value.update(used=sum(bool(n['evidence']) for n in result),
                     considered_not_used=sum(bool(n.get('considered_not_used')) for n in result))
    return value


def event_cursor(private):
    """Byte positions captured by the controller at delivery/version completion."""
    private = Path(private)
    return {str(p.relative_to(private)): stat_beneath(private, str(p.relative_to(private)), shared=False)
            for p in sorted((private/'turns').glob('t*/events.jsonl'))}


def record_delivery(private, number, entries, view):
    """Retain failures as well as successful delivery provenance for the UI."""
    try:
        audit = dict(notes=delivered_notes(entries, view))
    except Exception as exc:
        audit = dict(notes=[], errors=[dict(file='Memory delivery', error=type(exc).__name__+': '+str(exc))])
    try:
        cursor = event_cursor(private)
    except Exception as exc:
        audit.setdefault('errors', []).append(dict(file='Memory delivery cursor', error=type(exc).__name__+': '+str(exc)))
        cursor = dict(error=str(exc))
    directory = Path(private)/'memory'/f'v{number}'
    atomic_write(directory, 'memory-delivered.json', encode(audit))
    atomic_write(directory, 'memory-use-start.json', encode(cursor))


def validate_report(value):
    """Reject malformed persisted audits visibly instead of breaking the page."""
    if not isinstance(value, dict) or not isinstance(value.get('notes'), list):
        raise ValueError('invalid memory_use.json notes')
    for note in value['notes']:
        if (not isinstance(note, dict) or
                any(not isinstance(note.get(k), str) for k in ('memory_id', 'title', 'level')) or
                note.get('delivered') is not True or note.get('referenced') not in ('explicit', 'none')):
            raise ValueError('invalid memory_use.json note')
        for field in ('evidence', 'behavioural_hint', 'considered_not_used_evidence'):
            evidence = note.get(field, [])
            if not isinstance(evidence, list):
                raise ValueError('invalid memory_use.json evidence')
            for row in evidence:
                if (not isinstance(row, dict) or not isinstance(row.get('file'), str) or
                        not isinstance(row.get('excerpt'), str) or len(row['excerpt']) > 200 or
                        not any(type(row.get(k)) is int for k in ('line', 'event_index'))):
                    raise ValueError('invalid memory_use.json evidence pointer')
        if ('considered_not_used' in note and (type(note['considered_not_used']) is not bool
                or note['considered_not_used'] != bool(note.get('considered_not_used_evidence')))):
            raise ValueError('memory_use.json considered/evidence disagreement')
        if (note['referenced'] == 'explicit') != bool(note.get('evidence')):
            raise ValueError('memory_use.json reference/evidence disagreement')
    if not isinstance(value.get('errors', []), list) or any(
            not isinstance(e, dict) or not isinstance(e.get('error'), str) for e in value.get('errors', [])):
        raise ValueError('invalid memory_use.json errors')
    return value


def delivered_notes(entries, visible):
    """Bind actual guidance-only delivery to IDs, including truncation/screening.

    Use the same per-entry projection as Memory.open. Omitted notes never count
    as delivered; withheld text cannot match the original secret guidance.
    """
    from .dev_agent_eval import feedback
    from .dev_v2_loop import prompt_memory_view
    # Preserve compact_memory's order, including duplicate guidance/title pairs.
    remaining = (list(entries) if any('trusted_here' in e for e in entries) else
                 sorted(entries, key=lambda e: (e.get('memory_id', ''), e.get('version', 0))))
    notes = []
    for shown in visible['memory']:
        for index, entry in enumerate(remaining):
            projected = feedback(prompt_memory_view(dict(memory=[entry])))['memory']
            if 'level' not in shown and 'memory_id' not in shown:
                # Reconstruct the pre-T2 bounded projection for immutable historical deliveries.
                # Compare exact screened bytes; do not infer identity from similar guidance.
                from .memory_layers import label
                legacy = {k:entry[k] for k in ('title','summary') if isinstance(entry.get(k),str) and entry[k]}
                prefix = label(entry)
                if prefix:legacy['title'] = prefix + (legacy.get('title') or 'note')
                legacy['content'] = entry['content'][:1200]
                projected = feedback(dict(memory=[legacy]))['memory']
            if projected == [shown]:
                identity = entry.get('memory_id') or entry.get('note_id')
                if not identity:
                    raise ValueError('delivered note has no memory_id/note_id')
                notes.append(dict(memory_id=identity, title=feedback(entry.get('title')) or shown.get('title') or identity,
                    level=entry.get('level') or entry.get('reuse_level') or entry.get('layer') or 'unknown',
                    content=shown['content'], **{k: entry[k] for k in ('applies_when', 'parameters') if k in entry}))
                remaining.pop(index)
                break
        else:
            raise ValueError('delivered Memory view does not match its input packet')
    return notes


def _json(root, name, default=None):
    try:
        return json.loads(read_beneath(root, name, LIMIT))
    except FileNotFoundError:
        return default


def _comments(name, code):
    # Joining adjacent comment lines allows a wrapped eight-word quotation.
    tokens = [t for t in tokenize.generate_tokens(io.StringIO(code).readline) if t.type == tokenize.COMMENT]
    sources = []
    for token in tokens:
        line = token.start[0]
        if sources and line == sources[-1]['last_line'] + 1:
            sources[-1]['text'] += '\n' + token.string
            sources[-1]['last_line'] = line
        else:
            sources.append(dict(file=name, line=line, last_line=line, text=token.string))
    return sources


def _events(private, start, end):
    for cursor in (start, end):
        if not isinstance(cursor, dict):
            raise ValueError('invalid event cursor')
        if cursor.get('error'):
            raise ValueError('event cursor failed: ' + str(cursor['error']))
        if any(not re.fullmatch(r'turns/t\d+/events.jsonl', name) for name in cursor):
            raise ValueError('invalid event cursor path')
    if not start.keys() <= end.keys():
        raise ValueError('event stream missing from version end cursor')
    sources = []
    for name, stop in sorted(end.items()):
        begin = start.get(name, 0)
        raw = read_beneath(private, name, LIMIT)
        if type(begin) is not int or type(stop) is not int or not 0 <= begin <= stop <= len(raw):
            raise ValueError('invalid event cursor offset: ' + name)
        if (begin and raw[begin-1:begin] != b'\n') or (stop and raw[stop-1:stop] != b'\n'):
            raise ValueError('event cursor splits a JSONL event: ' + name)
        offset = raw[:begin].count(b'\n')
        for index, line in enumerate(raw[begin:stop].splitlines(), offset):
            event = json.loads(line)
            item = event.get('item', {})
            if event.get('type') == 'item.completed' and item.get('type') in ('agent_message', 'reasoning'):
                text = item.get('text')
                if not isinstance(text, str):
                    raise ValueError('authored event has no text: ' + name)
                sources.append(dict(file='agent/private/'+name, event_index=index, text=text))
    return sources


def _legacy_window(private, number):
    """Old runs: bind submit command completion to its exact version receipt.

    Never infer the Nth submission from command text alone (rejected submissions
    exist). Stop at that receipt; for v>1 start after the previous receipt.
    An ambiguous/missing boundary is an error, leaving code-comment evidence.
    """
    starts, ends, receipts = {}, {}, {}
    for path in sorted((private/'turns').glob('t*/events.jsonl')):
        name = str(path.relative_to(private)); offset = 0
        for line in read_beneath(private, name, LIMIT).splitlines(keepends=True):
            event = json.loads(line); item = event.get('item', {})
            identity = (name, item.get('id'))
            if event.get('type') == 'item.started':
                starts[identity] = offset
            if event.get('type') == 'item.completed' and item.get('type') == 'command_execution':
                try:
                    receipt = json.loads(item.get('aggregated_output', ''))
                except ValueError:
                    receipt = None
                if isinstance(receipt, dict) and receipt.get('op') == 'submit' and receipt.get('version_id'):
                    n = int(receipt['version_id'][1:])
                    if n in receipts or identity not in starts:
                        raise ValueError('ambiguous legacy submit boundary')
                    receipts[n] = (name, starts[identity], offset+len(line))
            offset += len(line)
        ends[name] = offset
    if number not in receipts or (number > 1 and number-1 not in receipts):
        raise ValueError('Memory citation event boundary unavailable')
    name, _, stop = receipts[number]
    end = {k: min(v, stop) if k == name else v for k, v in ends.items() if k <= name}
    start = {}
    if number > 1:
        previous, _, begin = receipts[number-1]
        start = {k: begin if k == previous else v for k, v in ends.items() if k <= previous}
    return start, end


def analyse_version(root, number):
    """Read-only adapter over one run's small artifacts, with visible errors.

    Paths in evidence are relative to the cell artifact directory. Old packets
    retain their recorded layer (e.g. scene), never invent a new L1/L2/L3 level.
    """
    root = Path(root); private = root/'agent/private'; prefix = f'v{number}'
    errors, notes, sources = [], [], []
    def attempt(label, fn, default=None):
        try:
            return fn()
        except Exception as exc:
            errors.append(dict(file=label, error=type(exc).__name__+': '+str(exc)))
            return default
    def delivery():
        saved = _json(private, f'memory/v{number}/memory-delivered.json')
        if saved is not None:
            errors.extend(saved.get('errors', []))
            return saved['notes']
        packet = _json(root, prefix+'/memory-input.json', dict(entries=[]))
        run_notes = _json(root, prefix+'/memory-run-notes-input.json', dict(entries=[]))
        entries = packet['entries'] + run_notes['entries']
        visible = _json(private, f'public/memory/v{number}.json')
        if visible is None:
            if entries:
                raise ValueError('delivered Memory view unavailable')
            return []
        return delivered_notes(entries, visible)
    notes = attempt(prefix+'/memory-input.json', delivery, [])
    if notes:
        def messages():
            end = _json(private, f'memory/v{number}/memory-use-end.json')
            if end is not None:
                start = _json(private, f'memory/v{number}/memory-use-start.json')
                if start is None:
                    raise ValueError('Memory delivery event boundary unavailable')
            else:
                start, end = _legacy_window(private, number)
            return _events(private, start, end)
        sources += attempt('agent/private/turns', messages, [])
        def comments():
            code = read_beneath(root, prefix+'/policy.py', LIMIT).decode('utf-8')
            return _comments(prefix+'/policy.py', code)
        sources += attempt(prefix+'/policy.py', comments, [])
    result = attempt(prefix+'/memory_use.json', lambda: analyse(notes, sources), dict(notes=[]))
    if errors:
        result['errors'] = errors
    return result


def record_version(root, number):
    """Persist the derived audit; write failures propagate to the export error UI."""
    result = analyse_version(root, number)
    atomic_write(Path(root)/f'v{number}', 'memory_use.json', encode(result))
    return result
