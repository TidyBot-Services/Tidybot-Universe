"""Byte-level fixtures for the author's carried deployment-02 through -04 layer."""
import hashlib
import json
from pathlib import Path

from scripts.attentionbench import build_d05
from scripts.attentionbench import build_planner_chain as planner


# SHA256 of the author's deployment-05 and dev-local files (both equal) after
# replacing each project root with @PROJECT_ROOT@. Tests need no author path.
AUTHOR_NORMALIZED_SHA256 = {
    'config/openarm_fixed.yml': '22077c47d4fe1807225894cf09b88b591e90fd01403b054faa0cd5409344ed91',
    'config/openarm-model/robot.urdf': 'da691990f279252d22bc13835f8ce76e3204fadba482d0e792eeffa7b7055b61',
    'curobo_v2/service/curobo_service/assets/franka_tidyverse.yml': 'e65bba74dfc6758e3f0126c1f174ee1f6f47f5e1308907dd4b4a40775a99584d',
    'curobo_v2/service/curobo_service/assets/ik_native_limits.yml': '4c090fece990f04fea7d34fa5e6317ca267fb40c1bc238493f65af26c2024273',
}


def test_carried_files_match_full_author_bytes(tmp_path):
    project = tmp_path / 'runtime/project'
    build_d05.apply_layer(project)
    for name, expected in AUTHOR_NORMALIZED_SHA256.items():
        raw = (project / name).read_bytes().replace(str(project).encode(), b'@PROJECT_ROOT@')
        assert hashlib.sha256(raw).hexdigest() == expected, name
    config = (project / 'config/openarm_fixed.yml').read_text()
    assert '  native_chassis:\n' in config
    assert '      openarm_tidyverse_chassis:\n' in config
    assert '      - openarm_tidyverse_chassis\n' in config
    assert '  pose_search: measured_reference_high_penalty_v1\n' in config
    assert '  use_cuda_graph: false\n' in config


def test_d05_project_inventory_matches_author():
    expected = json.loads(Path(__file__).resolve().parents[1].joinpath('d05-author-project-files.json').read_text())
    r12 = set(json.loads(planner.PROJECT_FILES.read_text())) - set(json.loads(planner.PROJECT_OMISSIONS.read_text()))
    d05 = json.loads(build_d05.SOURCE_LIST.read_text())
    assembled = r12 | set(d05['SOURCE_FILES']) | set(d05['NEW_FILES'])
    assembled |= set(build_d05.PINNED_LAYER_SOURCES) | set(build_d05.LAYER_FILES) | {build_d05.PROBE}
    assert sorted(assembled) == expected
    assert not any('.git/' in name or '.egg-info/' in name or '__pycache__/' in name for name in expected)


def test_probe_build_uses_pinned_cupti(tmp_path, monkeypatch):
    prefix = tmp_path / 'env'
    include = prefix / 'targets/x86_64-linux/include'
    lib = prefix / 'targets/x86_64-linux/lib'
    include.mkdir(parents=True)
    lib.mkdir(parents=True)
    (include / 'cupti.h').write_text('')
    (lib / 'libcupti.so').write_text('')
    commands = []
    monkeypatch.setattr(build_d05.subprocess, 'run', lambda argv, **kw: commands.append((argv, kw)))
    build_d05.build_probe(tmp_path / 'project', prefix)
    argv, kwargs = commands[0]
    assert argv[:5] == ['g++', '-std=c++17', '-O2', '-fPIC', '-shared']
    assert '-lcupti' in argv and kwargs['check'] is True
    assert argv[-1].endswith('/gpu_activity_probe.so')
