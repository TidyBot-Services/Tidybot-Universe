#!/usr/bin/env python3
"""Encode and trim a short recording through the code every execution uses."""
from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

import numpy as np

from benchmarks.attention_harness import replay_trim
from benchmarks.attention_harness.robocasa365.recording import StepRecorder, finalize_recording

CAMERA = 'robot0_agentview_left'
# Uneven gaps with one pause long enough to trim, like a real execution.
GAPS = (0, .02, .3, .05, 1.4, .03, .02)


def main() -> int:
    context = dict(run_id='run:media-check', attempt_id='attempt:media-check:0', camera_name=CAMERA,
                   started_monotonic=time.monotonic())
    with tempfile.TemporaryDirectory(dir=sys.argv[1]) as temp:
        recorder = StepRecorder(temp, context)
        for index, gap in enumerate(GAPS):
            time.sleep(gap)
            recorder.capture({f'{CAMERA}_image': np.full((36, 64, 3), 20 + 30 * index, dtype=np.uint8)}, 'media-check', index)
        receipt = finalize_recording(temp, context, 'completed')
        if not receipt['video_saved']:
            print(receipt['error'])
            return 1
        plan = replay_trim.build(temp)
    print(f"{receipt['frame_count']} frames encoded, {len(plan['frames'])} kept after trimming")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
