"""Configuration supplied only by the verified owner process."""
from pathlib import Path
from curobo_v2.identity import require_service_context
_lock, _robot_config = require_service_context()
class Config:
    DEVICE = "cuda:0"
    ROBOT_CFG = _robot_config
    ASSETS_DIR = Path(_lock["assets_root"])
    USE_CUDA_GRAPH = True
    DEFAULT_ENV = "default"
    MAX_ENVS = 8
