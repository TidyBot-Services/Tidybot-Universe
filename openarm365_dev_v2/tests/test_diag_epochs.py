"""Read-only cross-epoch diagnostic identity; ordinary execution stays strict."""
import json,subprocess,os,shutil
from benchmarks.attention_harness.legacy_paths import workspace_root
from pathlib import Path
import pytest
from openarm365_dev_v2.dev_config import SIM_PYTHON,ROOT,verify_lock,validate_dev_config
from benchmarks.attention_harness.dev_experiment import verify_manifest
from openarm365_dev_v2.tests.test_worker_inputs import inputs

@pytest.fixture(scope='module')
def original_epoch(tmp_path_factory):
 directory=tmp_path_factory.mktemp('original-epoch');main=directory/'source';main.mkdir()
 # A distinct source root is required even when these tests run in the main tree.
 # Copy code and the already-frozen physical dependencies, no environment/run.
 for folder in ['benchmarks','openarm365_dev_v2','openarm365_dev','openarm365','curobo_v2']:
  for source in (ROOT/folder).rglob('*'):
   if not source.is_file() or source.is_symlink() or '__pycache__' in source.parts or source.suffix not in {'.py','.json','.js','.html'}:continue
   destination=main/source.relative_to(ROOT);destination.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,destination)
 for source in ROOT.rglob('*.py'):
  if source.is_symlink() or '__pycache__' in source.parts:continue
  destination=main/source.relative_to(ROOT)
  if not destination.exists():destination.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,destination)
 from openarm365_dev_v2.dev_config import model_sources
 for source in model_sources():
  source=Path(source)
  if source.is_relative_to(ROOT):
   destination=main/source.relative_to(ROOT);destination.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,destination)

 # The original diagnostic epoch predates r3 and retains its legacy lock.
 code='from openarm365_dev_v2.dev_config import make_lock,make_dev_config;import json,sys;from pathlib import Path;p=Path(sys.argv[1]);ref=make_lock(p/"lock.json");(p/"config.json").write_text(json.dumps(make_dev_config("open_cabinet",101,ref,budget_tier="legacy")))'
 subprocess.run([str(SIM_PYTHON),'-B','-c',code,str(directory)],cwd=main,env=dict(os.environ,PYTHONPATH=str(main),ATTENTIONBENCH_WORKSPACE=str(workspace_root())),check=True,capture_output=True)
 return main,json.loads((directory/'config.json').read_text())

def test_original_diagnostic_lock_remains_verified(original_epoch):
 main,config=original_epoch
 with pytest.raises(ValueError,match='source root mismatch'):verify_lock(config['dev_lock'])
 assert verify_lock(config['dev_lock'],expected_source_root=main)['source_root']==str(main)
 assert validate_dev_config(config,expected_source_root=main)==config
 with pytest.raises(ValueError,match='source root mismatch'):validate_dev_config(config)


def test_probe_epoch_conditions_rejected_before_claim(tmp_path):
 from benchmarks.attention_harness.dev_scene_diag import probe
 s,e,cfg=inputs(tmp_path);bad=dict(cfg);bad['seed']=102
 with pytest.raises(ValueError):probe(s,e['experiment_id'],configs={'open_cabinet:101':bad})
 assert not s.cell(e['cells'][0]['key'])['jobs']
