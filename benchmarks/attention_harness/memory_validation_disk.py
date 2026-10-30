"""Agent validation admission, measured estimates and bounded progress sampling."""
import subprocess
from pathlib import Path

GIB = 1024**3
RESERVE_BYTES = 50 * GIB
DEFAULT_ARM_BYTES = 3 * GIB


def estimate(runner, state):
    config = getattr(getattr(runner, 'provision', None), 'config', {})
    value = config.get('agent_arm_estimated_bytes', DEFAULT_ARM_BYTES)
    if type(value) is not int or value <= 0:
        raise ValueError('agent_arm_estimated_bytes must be a positive integer')
    return max(value, state.get('agent_arm_measured_max_bytes', 0))


def gate(state, deps, current, *, batch=False):
    from . import dev_disk
    count = 10 if batch else 1
    required = count * estimate(deps.runner, state) + RESERVE_BYTES
    root = getattr(deps.runner, 'experiments_root', deps.root)
    free = int(dev_disk.status(root)['free_gb'] * 1e9)
    row = dict(free_bytes=free, required_bytes=required, estimated_bytes=required-RESERVE_BYTES,
               reserve_bytes=RESERVE_BYTES, stage='batch' if batch else 'arm')
    state['disk_gate'] = row
    if free < required:
        row['paused'] = True
        return (f'Automatic checks paused by disk: {free/GIB:.1f} GiB free; '
                f'{required/GIB:.1f} GiB required for {row["stage"]} including 50 GiB reserve.')
    row['paused'] = False
    return None


def arm_bytes(runner, current):
    directory = runner.arm_directory(current)
    # du counts shared hardlinks once and never follows symlinks out of the arm.
    return int(subprocess.check_output(['du', '-sb', '--', str(Path(directory))], text=True).split()[0])


def progress(state, deps, current, *, finished=False):
    if current.get('dev_mode') != 'agent' or not current.get('arm_id'):
        return
    now = deps.clock()
    if not finished and now-current.get('disk_sample_at', 0) < 5:
        return
    size = arm_bytes(deps.runner, current)
    current.update(disk_sample_at=now, arm_disk_bytes=size)
    if finished:
        from .memory_validation_replacements import attempt_id
        current.setdefault('arm_measurements', {})[attempt_id(current)] = size
        state['agent_arm_measured_max_bytes'] = max(size, state.get('agent_arm_measured_max_bytes', 0))
    current['disk_used_bytes'] = sum(current.get('arm_measurements', {}).values())
    if not finished:
        from .memory_validation_replacements import attempt_id
        if attempt_id(current) not in current.get('arm_measurements', {}):
            current['disk_used_bytes'] += size
