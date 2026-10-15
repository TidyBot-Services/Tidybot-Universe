"""High-resolution public observations in the real isolated policy worker.

No Service connection, Attention run, policy action, or reply is created.
"""
import shutil
import time
from types import SimpleNamespace

import numpy as np
import pytest

from benchmarks.attention_harness.robosuite_memory.formal_sandbox import execute_formal_policy
from benchmarks.attention_harness.robosuite_memory.formal_runner import _policy_memory_limit_mb


@pytest.mark.parametrize("width,height,expected",[(64,64,512),(768,768,512),(1024,1024,512),(1920,1080,2048),(2048,2048,2048)])
def test_worker_memory_selected_only_from_approved_camera_size(width,height,expected):
    assert _policy_memory_limit_mb({"camera_width":width,"camera_height":height}) == expected


@pytest.mark.parametrize("invalid",[True,0,511,1024,2049,4096,"2048"])
def test_worker_rejects_unapproved_memory_limits_before_spawn(tmp_path,invalid):
    with pytest.raises(ValueError,match="memory"):
        execute_formal_policy(code="from robot_sdk import sensors\n",sdk=None,context={},
                              deadline=time.monotonic()+5,stderr_path=tmp_path/"worker.stderr",
                              memory_limit_mb=invalid)
    assert not (tmp_path/"worker.stderr").exists()


@pytest.mark.skipif(shutil.which("bwrap") is None,reason="bubblewrap unavailable")
@pytest.mark.parametrize("width,height",[(1920,1080),(2048,2048)])
def test_highres_rgb_depth_roundtrip_inside_bounded_worker(tmp_path,width,height):
    class Sensors:
        def get_observation(self):
            return {"agentview_image":np.full((height,width,3),127,dtype=np.uint8),
                    "agentview_depth":np.full((height,width,1),0.98123,dtype=np.float32),
                    "robot0_eef_pos":np.array([0.1,0.2,0.83])}
    # Ordinary nested indexing/float semantics remain intact; no downsampling,
    # dropping camera keys, or altered source values to avoid MemoryError.
    code=("from robot_sdk import sensors\n"
          "obs=sensors.get_observation()\n"
          f"if len(obs['agentview_image'])!={height} or len(obs['agentview_image'][0])!={width}: raise RuntimeError('RGB dimensions changed')\n"
          "if obs['agentview_image'][-1][-1]!=[127,127,127]: raise RuntimeError('RGB values changed')\n"
          "if abs(obs['agentview_depth'][-1][-1][0]-0.98123)>0.000001: raise RuntimeError('depth changed')\n"
          "if obs['robot0_eef_pos']!=[0.1,0.2,0.83]: raise RuntimeError('pose changed')\n")
    result=execute_formal_policy(code=code,sdk=SimpleNamespace(sensors=Sensors()),context={},
                                deadline=time.monotonic()+45,stderr_path=tmp_path/"worker.stderr",
                                memory_limit_mb=_policy_memory_limit_mb({"camera_width":width,"camera_height":height}))
    assert result.status=="completed",result.error
    assert result.process_exit_code==0
    assert result.call_count==1
    assert result.memory_limit_mb==2048
