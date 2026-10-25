"""Shared Memory prompt order, caps and labels (plan 0d fourth revision)."""
LAYERS = ('run', 'scene', 'task', 'family')
LEVEL_LAYER = {'same_scene': 'scene', 'same_task': 'task', 'same_family': 'family', 'run_note': 'run'}
LEVELS = tuple(level for level in LEVEL_LAYER if level != 'run_note')
RUN_NOTE_CAP = 3
TRUSTED_PROMPT_CAP = 8
OTHER_UNVALIDATED_CAP = 2
FAMILY_CAP = 1
FAMILY_DEFAULT = True  # New development selections only; old manifests remain opt-in.


def _get(entry, key, default=None):
    return entry.get(key, default) if isinstance(entry, dict) else getattr(entry, key, default)


def layer_of(entry):
    return _get(entry, 'layer') or LEVEL_LAYER.get(_get(entry, 'match_level'), 'scene')


def _newest(entries):
    # Stable sorting preserves the given order for equal/absent timestamps.
    return sorted(entries, key=lambda e: _get(e, 'created_at', 0) or 0, reverse=True)


def order(service_entries, run_notes=(), *, family=False):
    entries = list(service_entries) + list(run_notes)
    runs = [e for e in entries if layer_of(e) == 'run']
    runs.sort(key=lambda e: _get(e, 'help_version', 0), reverse=True)
    trusted = [e for e in entries if (layer_of(e) == 'scene' and _get(e, 'trusted_here') is not False)
               or (layer_of(e) == 'task' and _get(e, 'trusted_here') is True)]
    others = _newest([e for e in entries if layer_of(e) == 'scene' and _get(e, 'trusted_here') is False])
    others += _newest([e for e in entries if layer_of(e) == 'task' and _get(e, 'trusted_here') is not True])
    families = _newest([e for e in entries if layer_of(e) == 'family']) if family else []
    return trusted + runs[:RUN_NOTE_CAP] + others[:OTHER_UNVALIDATED_CAP] + families[:FAMILY_CAP]


def label(entry):
    layer = layer_of(entry)
    if layer == 'run':
        return f"This run (v{_get(entry, 'help_version')}) · Unvalidated: "
    if layer == 'scene':
        return '' if _get(entry, 'trusted_here') is not False else 'Unvalidated: '
    prefix = 'Unvalidated reference' if _get(entry, 'unvalidated') else 'Reference'
    if layer == 'task':
        if _get(entry, 'trusted_here') is True:return 'Trusted for this task: '
        seed = _get(entry, 'source_seed')
        return prefix + (f' (scene {seed}): ' if seed is not None else ': ')
    return f"Reference (task {_get(entry, 'source_task_id')}): "
