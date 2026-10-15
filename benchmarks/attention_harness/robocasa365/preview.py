"""Publish verified public observations; no Service credentials, reads or actions."""
import time
import numpy as np
from ..live_preview import save_preview, finish_preview, mark_preview_offline
from ..public_station import publish_public_station
from ..ui_media import rgb_jpeg


class RoboCasa365Preview:
    def __init__(self, run_dir, run_id, attempt_id, origin, camera_name):
        self.directory, self.run_id, self.attempt_id = run_dir, run_id, attempt_id
        self.origin, self.camera_name = origin, camera_name
        self.last_sequence = -1
        self.episode_id = None
        self.receipt = dict(source='verified_executor_public_observation', camera_name=camera_name,
                            frame_count=0, error=None, ended=False)

    def publish(self, observation):
        try:
            sequence = observation['sequence']
            if type(sequence) is not int or sequence < 0:
                raise ValueError('invalid camera observation sequence')
            if self.episode_id is not None and observation['episode_id'] != self.episode_id:
                raise ValueError('camera observation episode changed')
            if sequence == self.last_sequence:
                return  # The same physical observation must retain its original age.
            if sequence < self.last_sequence:
                raise ValueError('camera observation sequence regressed')
            captured = time.time()
            raw = np.asarray(observation['images'][self.camera_name + '_image'])
            if raw.ndim != 3 or raw.shape[2] != 3 or raw.dtype.kind not in 'ui' or np.any(raw < 0) or np.any(raw > 255):
                raise ValueError('invalid public camera RGB')
            # The approved MuJoCo camera observations use bottom-up image rows.
            rgb = np.asarray(raw[::-1], dtype=np.uint8)
            frame = rgb_jpeg(rgb)
            count = self.receipt['frame_count'] + 1
            save_preview(self.directory, run_id=self.run_id, attempt_id=self.attempt_id,
                frame=frame, sequence=count, captured_at=captured, width=rgb.shape[1], height=rgb.shape[0])
            if count == 1:
                publish_public_station(run_dir=self.directory, run_id=self.run_id, attempt_id=self.attempt_id,
                    suite='robocasa365', origin=self.origin, camera_name=self.camera_name,
                    camera_transport='robocasa365_preview')
            self.episode_id, self.last_sequence = observation['episode_id'], sequence
            self.receipt.update(frame_count=count, error=None, source_width=rgb.shape[1], source_height=rgb.shape[0])
        except Exception as error:
            self.receipt['error'] = type(error).__name__ + ': ' + str(error)
            mark_preview_offline(self.directory, self.run_id, self.attempt_id, self.receipt['error'])

    def finish(self):
        try:
            finish_preview(self.directory, self.run_id, self.attempt_id)
        except Exception as error:
            self.receipt['error'] = type(error).__name__ + ': ' + str(error)
        self.receipt['ended'] = True
