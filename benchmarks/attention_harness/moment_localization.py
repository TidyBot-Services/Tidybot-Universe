"""Public trace completion time to recorded sample, matching live.js replayTarget."""
from __future__ import annotations

import math
from bisect import bisect_right

VIEWS = ('external', 'side', 'top', 'left_wrist', 'right_wrist')
ROLES = ('before', 'after')
DEFAULT_MAX_SELECTED_FRAMES = 4


def locate_event(event, replay_timing):
    """Return the preceding sampled frame (the UI's floor rule), or a reason.

    A missing mapping is deliberately unavailable here. The UI can jump
    approximately to a timestamp; evidence selection requires an exact sample.
    """
    stamp = event.get('timestamp') if isinstance(event, dict) else None
    if not isinstance(event, dict) or event.get('timestamp_basis') != 'relative' or isinstance(stamp, bool) or not isinstance(stamp, (int, float)) or not math.isfinite(stamp) or stamp < 0:
        return {'status': 'unavailable', 'reason': 'no_relative_timestamp'}
    if not isinstance(replay_timing, dict):
        return {'status': 'unavailable', 'reason': 'recording_time_mapping_missing'}
    traces = replay_timing.get('frame_trace_seconds')
    videos = replay_timing.get('frame_video_seconds')
    def monotonic(values, nonnegative=False):
        return isinstance(values, list) and bool(values) and all(
            not isinstance(t, bool) and isinstance(t, (int, float)) and math.isfinite(t)
            and (not nonnegative or t >= 0) and (i == 0 or t > values[i-1])
            for i, t in enumerate(values))
    if replay_timing.get('basis') != 'relative' or not monotonic(traces) or not monotonic(videos, True) or len(traces) != len(videos):
        return {'status': 'unavailable', 'reason': 'recording_time_mapping_invalid'}
    if stamp < traces[0] or stamp > traces[-1]:
        return {'status': 'unavailable', 'reason': 'event_outside_sample_range'}
    index = bisect_right(traces, stamp) - 1
    return {'status': 'located', 'frame_index': index, 'trace_seconds': traces[index], 'video_seconds': videos[index]}


def validate_selected_moments(moments, localization):
    if not isinstance(moments, list) or not isinstance(localization, dict) or localization.get('status') not in ('targeted', 'generic_fallback'):
        raise ValueError('invalid selected moments envelope')
    if localization['status'] == 'generic_fallback' and not localization.get('reason'):
        raise ValueError('generic fallback needs a reason')
    if localization['status']=='generic_fallback' and moments or localization['status']=='targeted' and not moments:
        raise ValueError('moments and localization status disagree')
    if len({m.get('moment_id') for m in moments if isinstance(m,dict)})!=len(moments):
        raise ValueError('duplicate moment ID')
    for moment in moments:
        if not isinstance(moment, dict) or set(moment) != {'moment_id', 'event_id', 'view', 'frame_index', 'video_seconds', 'trace_seconds', 'role', 'note'}:
            raise ValueError('invalid selected moment')
        if moment['view'] not in VIEWS or moment['role'] not in ROLES or type(moment['frame_index']) is not int or moment['frame_index'] < 0:
            raise ValueError('invalid selected moment location')
        if any(not isinstance(moment[k], str) for k in ('moment_id', 'event_id', 'note')):
            raise ValueError('invalid selected moment text')
        if any(isinstance(moment[k], bool) or not isinstance(moment[k], (int, float)) or not math.isfinite(moment[k]) or moment[k] < 0 for k in ('video_seconds', 'trace_seconds')):
            raise ValueError('invalid selected moment time')
    return True


def select_moments(events, event_ids, replay_timing, *, available_views=VIEWS, max_frames=DEFAULT_MAX_SELECTED_FRAMES, view_index_map=None):
    if type(max_frames) is not int or max_frames < 1:
        raise ValueError('positive selected frame limit required')
    chosen = [];localized=[]
    by_id = {str(e.get('event_id')): i for i, e in enumerate(events) if isinstance(e, dict)}
    views = [v for v in ('left_wrist', 'right_wrist', 'external', 'side', 'top') if v in available_views]
    if not views:
        return [], {'status': 'generic_fallback', 'reason': 'no_available_view'}
    for event_id in event_ids:
        if event_id not in by_id:
            return [], {'status': 'generic_fallback', 'reason': 'selected_event_missing:' + str(event_id)}
        i = by_id[event_id]
        after = locate_event(events[i], replay_timing)
        if after['status'] != 'located':
            return [], {'status': 'generic_fallback', 'reason': str(event_id) + ':' + after['reason']}
        before = locate_event(events[i-1], replay_timing) if i > 0 else None
        if before is not None and before['status'] != 'located':
            return [], {'status': 'generic_fallback', 'reason': str(event_id) + ':previous_' + before['reason']}
        note = 'call completed'
        if before and before['frame_index'] == after['frame_index']:
            j = after['frame_index'] - 1
            if j >= 0:
                before = {'frame_index': j, 'trace_seconds': replay_timing['frame_trace_seconds'][j], 'video_seconds': replay_timing['frame_video_seconds'][j]}
                note = 'before moved to previous sample because both completions share one sample'
            else:
                before = None
                note = 'before unavailable: both completions share first sample'
        localized.append((str(event_id),[(role,position,description) for role,position,description in
            (('before',before,note),('after',after,'call completed' if before else note)) if position is not None]))
    if sum(len(items) for _,items in localized)>max_frames:
        return [], {'status':'generic_fallback','reason':'selected_events_exceed_frame_limit'}
    for view in views:
        for event_id,items in localized:
            for role, position, description in items:
                if len(chosen) >= max_frames:
                    break
                mapped=(view_index_map or {}).get(view,{}).get(position['frame_index'],position)
                chosen.append({'moment_id': f'm{len(chosen)+1}', 'event_id': str(event_id), 'view': view,
                               'frame_index': mapped['frame_index'], 'video_seconds': mapped['video_seconds'],
                               'trace_seconds': mapped['trace_seconds'], 'role': role, 'note': description})
    localization = {'status': 'targeted', 'reason': None} if chosen else {'status': 'generic_fallback', 'reason': 'no_selected_events'}
    validate_selected_moments(chosen, localization)
    return chosen, localization
