"""Shared Memory prompt order, caps and labels (plan 0d fourth revision)."""
LAYERS = ('run', 'scene', 'task', 'skill', 'general', 'family')
LEVEL_LAYER = {'same_scene': 'scene', 'same_task': 'task', 'same_family': 'family', 'same_skill': 'skill', 'general': 'general', 'run_note': 'run'}
LEVELS = tuple(level for level in LEVEL_LAYER if level != 'run_note')
RUN_NOTE_CAP = 3
TRUSTED_PROMPT_CAP = 3
OTHER_UNVALIDATED_CAP = 3
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
    rest = [e for e in entries if layer_of(e) not in {'run','family'}]
    families = [e for e in entries if layer_of(e) == 'family'] if family else []
    families.sort(key=lambda e: (_get(e,'trust_scope') != 'family_scoped', -(_get(e,'created_at',0) or 0)))
    rest += families[:FAMILY_CAP]
    rank = {'same_task':0, 'same_skill':1, 'general':2}
    def priority(e):
        level = _get(e, 'level') or _get(e, 'match_level') or {'scene':'same_task','task':'same_task'}.get(layer_of(e))
        if level == 'same_scene':level = 'same_task'
        return (rank.get(level, 3), not _get(e, 'source_verified', False),
                -(_get(e, 'created_at', 0) or 0), _get(e, 'memory_id', ''))
    # A run note is local L1 guidance. The shared cap includes local notes.
    return sorted(runs + rest, key=lambda e: (0, not _get(e,'source_verified',False), -(_get(e,'created_at',0) or _get(e,'help_version',0) or 0), '')
                  if layer_of(e) == 'run' else priority(e))[:3]



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
    if layer in {'skill','general'}:
        return ('Same skill' if layer == 'skill' else 'General') + ' · Unvalidated: '
    if _get(entry,'trust_scope')=='family_scoped':return 'Trusted for the family (model rewrite): '
    return f"Reference (task {_get(entry, 'source_task_id')}): "
