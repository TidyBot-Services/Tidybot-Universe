"""Read-only verifier for byte-identical delivery and retained failure evidence."""
import hashlib
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
read = lambda p: json.loads(Path(p).read_text())
manifest = read(ROOT / 'sha256_manifest.json')
issues = []
for row in manifest['files']:
    path = ROOT / row['relative_path']
    if not path.is_file() or sha(path) != row['sha256']:
        issues.append({'check': 'delivery SHA', 'path': str(path)})
for row in manifest['implementation_sources']:
    path = Path(row['path'])
    if not path.is_file() or sha(path) != row['sha256']:
        issues.append({'check': 'implementation SHA', 'path': str(path)})
baseline = read(ROOT / 'protected_baseline.json')
for path, digest in baseline['protected_files'].items():
    if not Path(path).is_file() or sha(path) != digest:
        issues.append({'check': 'preserved old evidence/source SHA', 'path': path})
overall = read(ROOT / 'independent/overall_admission_audit_v3.json')
prior = read(overall['prior_overall_audit']['path'])
for name, gate in overall['canonical_gates'].items():
    if name != 'safety_fault_injection' and gate != prior['canonical_gates'][name]:
        issues.append({'check': 'old PASS gate payload changed', 'gate': name})
if overall['formal_eligible'] != (all(g['pass'] for g in overall['canonical_gates'].values()) and not overall['protected_evidence_integrity']['issues']):
    issues.append({'check': 'eligibility differs from evidence conjunction'})
negative = read(ROOT / 'independent/negative_control_post_run_audit_v3.json')
for group in negative['service_reaping']:
    try:
        os.killpg(group['receipt']['process_group'], 0)
        issues.append({'check': 'owned Service process group still present', 'service': group['service']})
    except ProcessLookupError:
        pass
report = {'passed': not issues, 'delivery_files': len(manifest['files']),
          'protected_old_files': len(baseline['protected_files']),
          'overall_sha256': sha(ROOT / 'independent/overall_admission_audit_v3.json'),
          'negative_sha256': sha(ROOT / 'independent/negative_control_post_run_audit_v3.json'),
          'formal_eligible': overall['formal_eligible'], 'issues': issues}
print(json.dumps(report, ensure_ascii=False, sort_keys=True))
raise SystemExit(0 if not issues else 1)
