"""Quota observations can decrease after a plan or limit change."""
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from benchmarks.attention_harness.codex_quota import read_quota
from benchmarks.attention_harness.dev_agent_quota import QuotaMonitor, read_weekly_quota


NOW = 1793390000
RESET = 1793775379


def event(used, observed, reset=RESET):
    return dict(timestamp=datetime.fromtimestamp(observed, timezone.utc).isoformat(),
                payload=dict(rate_limits=dict(plan_type='prolite', secondary=dict(
                    used_percent=used, window_minutes=10080, resets_at=reset))))


def session(root, name, used, observed, reset=RESET):
    path = root/'.codex/sessions'/f'rollout-{name}.jsonl'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(event(used, observed, reset))+'\n')


def reading(used, observed, reset=RESET):
    return dict(used_percent=used, observed_at=observed, resets_at=reset, window_minutes=10080)


def monitor(root, reader, clock=lambda: NOW):
    private = root/'private'
    private.mkdir(exist_ok=True)
    return QuotaMonitor(private, SimpleNamespace(quota=lambda sample: None),
                        stop_percent=60, reader=reader, clock=clock)


@pytest.mark.parametrize('reader', [read_weekly_quota, read_quota])
def test_incident_older_63_newer_42_43(tmp_path, reader):
    session(tmp_path, 'old', 63, NOW-86400)
    session(tmp_path, 'recent', 42, NOW-120)
    session(tmp_path, 'newest', 43, NOW)
    assert reader(tmp_path, now=NOW)['used_percent'] == 43


def test_monitor_ignores_old_high_reading_and_stop_is_sticky(tmp_path):
    now = [NOW]
    session(tmp_path, 'old', 63, NOW-86400)
    session(tmp_path, 'recent', 42, NOW-120)
    session(tmp_path, 'newest', 43, NOW)
    quota = monitor(tmp_path, lambda: read_weekly_quota(tmp_path, now=now[0]), lambda: now[0])
    quota.event(event(63, NOW-86400))
    sample = quota.sample('before-first-turn')
    assert sample['used_percent'] == 43 and not sample['stopped'] and not sample['stale']
    now[0] += 60
    session(tmp_path, 'crossed', 61, now[0])
    with pytest.raises(ValueError, match='quota_stop'):
        quota.check('crossed')
    now[0] += 601
    quota.event(event(43, now[0]))
    session(tmp_path, 'lower', 43, now[0])
    with pytest.raises(ValueError, match='quota_stop'):
        quota.check('already-stopped')
    assert json.loads((quota.private/'quota.jsonl').read_text().splitlines()[-1])['used_percent'] == 43


@pytest.mark.parametrize('reader', [read_weekly_quota, read_quota])
@pytest.mark.parametrize('gap, expected', [(120, 44), (600, 44), (601, 43)])
def test_session_lag_is_bounded_even_for_stale_readings(tmp_path, reader, gap, expected):
    # Both readings are stale: this must use relative observation time, not freshness.
    session(tmp_path, 'earlier', 44, NOW-3600-gap)
    session(tmp_path, 'latest', 43, NOW-3600)
    assert reader(tmp_path, now=NOW)['used_percent'] == expected


@pytest.mark.parametrize('reader', [read_weekly_quota, read_quota])
def test_new_reset_window_wins_with_lower_usage(tmp_path, reader):
    session(tmp_path, 'old-window', 63, NOW)
    session(tmp_path, 'new-window', 9, NOW-120, RESET+604800)
    value = reader(tmp_path, now=NOW)
    assert value['used_percent'] == 9 and value['resets_at'] == RESET+604800


@pytest.mark.parametrize('event_is_newer', [False, True])
@pytest.mark.parametrize('gap, expected', [(120, 44), (600, 44), (601, 43)])
def test_monitor_reader_and_event_use_observation_lag(tmp_path, event_is_newer, gap, expected):
    older, newer = reading(44, NOW-3600-gap), reading(43, NOW-3600)
    from_reader, from_event = (older, newer) if event_is_newer else (newer, older)
    quota = monitor(tmp_path, lambda: from_reader)
    quota.event(event(from_event['used_percent'], from_event['observed_at']))
    value = quota.sample('stale')
    assert value['used_percent'] == expected and value['observed_at'] == NOW-3600
    assert value['stale'] and not value['stopped']


@pytest.mark.parametrize('event_is_newer_window', [False, True])
def test_monitor_new_reset_window_wins(tmp_path, event_is_newer_window):
    old, new = reading(63, NOW), reading(9, NOW-120, RESET+604800)
    from_reader, from_event = (old, new) if event_is_newer_window else (new, old)
    quota = monitor(tmp_path, lambda: from_reader)
    quota.event(event(from_event['used_percent'], from_event['observed_at'], from_event['resets_at']))
    value = quota.sample('reset')
    assert value['used_percent'] == 9 and value['resets_at'] == RESET+604800 and not value['stopped']


def test_event_stream_replaces_high_reading_and_persists_latest(tmp_path):
    quota = monitor(tmp_path, lambda: None)
    quota.event(event(63, NOW-86400))
    quota.event(event(43, NOW-120, RESET-2))  # Same window despite reset jitter.
    assert quota.latest == reading(43, NOW-120, RESET-2)
    quota.event(event(63, NOW-86400))  # Late delivery must not revive an old high.
    assert quota.latest['used_percent'] == 43
    restarted = monitor(tmp_path, lambda: None)
    assert restarted.latest == quota.latest
    assert restarted.sample('restart')['used_percent'] == 43
    quota.event(event(61, NOW))
    with pytest.raises(ValueError, match='quota_stop'):
        quota.check('crossed')


def test_event_new_window_precedes_observation_time(tmp_path):
    quota = monitor(tmp_path, lambda: None)
    quota.event(event(63, NOW))
    quota.event(event(9, NOW-120, RESET+604800))
    quota.event(event(65, NOW, RESET))
    assert quota.latest == reading(9, NOW-120, RESET+604800)


def test_reader_reset_jitter_does_not_revive_old_high(tmp_path):
    session(tmp_path, 'old', 63, NOW-86400, RESET+2)
    session(tmp_path, 'current', 43, NOW)
    value = read_weekly_quota(tmp_path, now=NOW)
    assert value == reading(43, NOW, RESET+2)
