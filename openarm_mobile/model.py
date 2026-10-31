"""Compose the pinned Tidyverse planar chassis with unchanged OpenArm arms.

The base is the native Tidyverse PhysX X/Y/Yaw articulation abstraction;
wheel traction dynamics are not part of that source model. Motion is produced
by force-limited joints, with mass/inertia and chassis collision geometry.
"""
from pathlib import Path
import os
import copy
import hashlib
import json
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
BASE = Path(__file__).parent / 'model/source/tidyverse.urdf'
ARM = ROOT / 'openarm_phase0_recheck/model/compat_v6/robot.urdf'
OUTPUT = Path(__file__).parent / 'model/mobile_openarm.urdf'
def output_path(path):
    return Path(os.path.relpath(path, OUTPUT.parent)).as_posix()


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def build_model():
    arm = ET.parse(ARM).getroot()
    base = ET.parse(BASE).getroot()
    root = ET.Element('robot', name='openarm_tidyverse_mobile')
    root.append(copy.deepcopy(arm.find("link[@name='world']")))
    for tag, names in [('link', ['base_x_link','base_y_link','base_link']),
                       ('joint', ['base_x_joint','base_y_joint','base_yaw_joint'])]:
        for name in names:
            node = copy.deepcopy(base.find(f"{tag}[@name='{name}']"))
            if name == 'base_x_joint':
                node.find('parent').set('link', 'world')
            for mesh in node.findall('.//mesh'):
                mesh.set('filename', output_path((BASE.parent / mesh.get('filename')).resolve()))
            root.append(node)
    for node in arm:
        if node.tag == 'link' and node.get('name') == 'world':
            continue
        node = copy.deepcopy(node)
        if node.get('name') == 'openarm_body_world_joint':
            node.find('parent').set('link', 'base_link')
            node.find('origin').set('xyz', '0 0 0.6')
        for mesh in node.findall('.//mesh'):
            mesh.set('filename', output_path((ARM.parent / mesh.get('filename')).resolve()))
        root.append(node)
    data = ET.tostring(root, encoding='utf-8', xml_declaration=True)
    if not OUTPUT.exists() or OUTPUT.read_bytes() != data:
        OUTPUT.write_bytes(data)
    # Reuse exactly the accepted per-pair exceptions. No global collision filter.
    source_srdf = ARM.with_suffix('.srdf')
    if source_srdf.exists():
        target = OUTPUT.with_suffix('.srdf')
        if not target.exists(): target.write_bytes(source_srdf.read_bytes())
    identity = {'path': OUTPUT.relative_to(ROOT).as_posix(), 'sha256': digest(OUTPUT),
        'source_sha256': digest(BASE), 'arm_sha256': digest(ARM),
        'base_source': 'https://github.com/TidyBot-Services/maniskill-tidyverse',
        'base_source_revision': 'fbbac3a0e105001f46a0a63b61ebda51ac771980',
        'base_visual_sha256': digest(BASE.parent / 'tidyverse_base/base_vis.stl'),
        'installation': {'parent':'base_link', 'child':'openarm_body_link0', 'xyz_m':[0,0,.6], 'rpy_rad':[0,0,0]},
        'control': 'PhysX force-limited planar X/Y/Yaw joint position drives',
        'base_frame': 'X forward, Y left, Z up; joint X/Y in initial mount frame',
        'mass_kg':50.,'inertia_kg_m2':[1.,1.,.5],
        'collision_box_m':[.733,.523,.606], 'collision_center_m':[-.198,0,.309],
        'wheel_traction_model': False}
    manifest=OUTPUT.parent/'identity.json'
    manifest.write_text(json.dumps(identity,indent=2)+'\n')
    return identity


if __name__ == '__main__':
    build_model()
