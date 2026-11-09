"""OpenStandMixerHead is set aside (user, 2026-11-10): not offered, refused for new runs, old runs still validate."""
import pytest

from benchmarks.attention_harness import dev_fixture_tasks as ft


def test_mixer_not_offered_and_other_13_are():
    ids = [o['task_id'] for o in ft.options()]
    assert 'open_stand_mixer_head' not in ids
    assert len(ids) == 13 and len(set(ids)) == 13


def test_new_mixer_run_refused_with_reason():
    sel = dict(rules_profile=ft.PROFILE, mode='interactive', tasks=['open_stand_mixer_head'], seeds=[102])
    with pytest.raises(ValueError, match='set aside 2026-11-10'):
        ft.prepare_selection(sel)
    ok = dict(sel, tasks=['turn_on_microwave'])
    assert ft.prepare_selection(ok)['rules_profile'] == ft.PROFILE


def test_old_mixer_records_still_validate():
    assert ft.validate('open_stand_mixer_head', 102).task_id == 'open_stand_mixer_head'
