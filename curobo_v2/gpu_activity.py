"""Default-disabled interface test observation of public-call/GPU timestamps.

No solver object is changed. No kernel names or algorithm state are inspected.
The explicit signed test policy records SIGTERM at the public API boundary and
then exits immediately; it is never enabled in the native physical executor.
"""
from contextlib import contextmanager
import ctypes
import json,sys
from pathlib import Path

_directory=None
_probe=None

def configure(directory):
    global _directory
    if _directory is not None:raise RuntimeError('GPU observation policy was already configured')
    _directory=Path(directory)

@contextmanager
def public_call():
    global _probe
    if _directory is None:
        yield
        return
    import torch
    # Complete all precheck/argument work before the public call marker. The
    # activity records accepted by the test must start after this marker.
    torch.cuda.synchronize()
    if _probe is None:
        _probe=ctypes.CDLL(str(Path(__file__).with_name('gpu_activity_probe.so')))
        _probe.interface_gpu_probe_initialize.argtypes=[ctypes.c_char_p,ctypes.c_char_p]
        status=_probe.interface_gpu_probe_initialize(bytes(_directory/'gpu-activity.jsonl'),bytes(_directory/'gpu-cancel.bin'))
        if status:raise RuntimeError(f'CUPTI observation unavailable (code {status}); cancellation scope remains unverified')
        (_directory/'native-maps.txt').write_text(Path('/proc/self/maps').read_text())
        modules={name:str(Path(module.__file__).resolve())for name,module in list(sys.modules.items())
                 if(name=='curobo'or name.startswith('curobo.'))and getattr(module,'__file__',None)}
        (_directory/'loaded_modules.json').write_text(json.dumps(modules,indent=2)+'\n')
    status=_probe.interface_gpu_probe_begin()
    if status:raise RuntimeError(f'CUPTI public-call timestamp unavailable ({status})')
    try:yield
    finally:_probe.interface_gpu_probe_end()
