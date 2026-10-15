"""World-frame object orientation for the independently bound dual-arm SDK."""
from math import isfinite
from types import SimpleNamespace
from tidybot_sdk.perception import ModeBoundPerceptionBackend


class BimanualPerceptionBackend(ModeBoundPerceptionBackend):
    def find_objects(self, target_names=None, camera_names=None):
        # Validate the same single capture through the unchanged legacy contract.
        rows = self._provider.find_objects(target_names=target_names, camera_names=camera_names)
        validator = ModeBoundPerceptionBackend(self._action_backend, mode=self.perception_mode,
            target=self.execution_target, provider=SimpleNamespace(find_objects=lambda **_: rows))
        output = validator.find_objects(target_names=target_names, camera_names=camera_names)
        for row, validated in zip(rows, output):
            if 'quaternion_wxyz' not in row:
                continue
            quaternion = row['quaternion_wxyz']
            if (not isinstance(quaternion, (list, tuple)) or len(quaternion) != 4
                    or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not isfinite(v) for v in quaternion)
                    or abs(sum(v*v for v in quaternion)-1.) > 1e-5):
                raise ValueError('perception orientation must be a finite unit quaternion in the SDK control frame')
            validated['quaternion_wxyz'] = [float(v) for v in quaternion]
        return output
