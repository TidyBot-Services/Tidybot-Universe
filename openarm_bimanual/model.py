"""Compose the pinned Tidyverse planar chassis with unchanged OpenArm arms.

The base is the native Tidyverse PhysX X/Y/Yaw articulation abstraction;
wheel traction dynamics are not part of that source model. Motion is produced
by force-limited joints, with mass/inertia and chassis collision geometry.
"""
from pathlib import Path
import copy
import hashlib
import json
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'openarm_mobile/model/source/tidyverse.urdf'
ARM = ROOT / 'openarm_phase0_recheck/model/compat_v6/robot.urdf'
OUTPUT = Path(__file__).parent / 'model/mobile_openarm.urdf'


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def build_model():
    """Use the frozen original model bytes; never regenerate an execution model."""
    expected = "c82a2500c5471449374b4dd8d6037d58209174ffee211bb196a1c691cea45eff"
    if digest(OUTPUT) != expected:
        raise ValueError("Frozen original bimanual URDF changed")
    return {"path": str(OUTPUT), "sha256": expected}
