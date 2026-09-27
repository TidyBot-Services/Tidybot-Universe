"""Copy the original source and failure artifacts into this movable archive."""
import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent
graph = Path('/home/truares/桌面/attentionbench-week2-graph-live-20260927/robocasa')
run = graph / 'attention-robocasa-counter_to_sink-seed101-59f3e6e1b326'
memory = graph / 'attempts/memory/candidate-attempt-attention-robo-e45ed3c1d0ccbe2bb48e9e7928bb5a468315b528ff9c6b0937874d4e6075b5eb'
old_depth = Path('/home/truares/桌面/attentionbench-week2-auto-smoke-20260927/clean-checkout/attention-robosuite-cube_lift-seed101-1cde894ef24b/attempts/cube_lift-seed101-formal-1790481804027982384')
old_success = Path('/home/truares/桌面/attentionbench-v2-robocasa-mobile-dev/counter_to_sink-seed101-20260924T164622.879733Z')
target = ROOT / 'original_sources'
assert not target.exists()
target.mkdir()
shutil.copytree(memory, target / 'candidate_memory')
shutil.copytree(Path(json.loads((run / 'attention_run.json').read_text())['attempts'][0]['artifact_dir']),
                target / 'robocasa_source_attempt')
for name in ('attention_run.json', 'eval_diagnosis.json'):
    shutil.copy2(run / name, target / name)
shutil.copytree(old_depth, target / 'original_depth_500')
for name in ('result.json', 'policy.py', 'trace.jsonl'):
    shutil.copy2(old_success / name, target / ('historical_success_' + name))
manifest = {}
for path in sorted(target.rglob('*')):
    if path.is_file():
        manifest[path.relative_to(ROOT).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
(ROOT / 'original_source_hashes.json').write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')
print(json.dumps({'source_files': len(manifest)}))
