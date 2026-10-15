"""Native display-only view, matching RoboCasa's human camera resolution.

Called on the simulator's execution thread after reset and each existing step.
Rendering neither advances physics nor changes the frozen policy observations.
"""
import numpy as np

WIDTH, HEIGHT = 1920, 1080
SOURCE = 'official_simulator_display_rgb_after_each_control_step'


def capture_human_view(recorder, env, episode_id, sequence):
    if sequence == recorder.last_sequence and episode_id == recorder.episode_id:
        return
    try:
        raw = env.sim.render(width=WIDTH, height=HEIGHT,
                             camera_name=recorder.context['camera_name'], depth=False)
        if np.asarray(raw).shape != (HEIGHT, WIDTH, 3) or np.asarray(raw).dtype != np.uint8:
            raise ValueError('human view requires native 1920x1080 uint8 RGB; no resize fallback')
        recorder.capture({recorder.context['camera_name']+'_image': raw},
                         episode_id, sequence, source=SOURCE)
    except Exception as exc:
        recorder.record_error(exc)
