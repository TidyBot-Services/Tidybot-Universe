"""Offline T6 audit of a settled probe. Never connect to the live service.

Copies the experiment/ledger and authority database into a fresh /tmp directory.
Only native execution, scene reset and the Codex provider are substituted.
Synthetic GPU samples are explicitly test data, never recovered measurements.
No production evidence is repaired by this script.
"""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from benchmarks.attention_harness.workspace_paths import get


def read(path):
    return json.loads(Path(path).read_bytes())


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def snapshot(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(Path(source).as_uri() + '?mode=ro', uri=True) as src, sqlite3.connect(destination) as dst:
        src.backup(dst)


class CopyEvidence:
    """Relocate executor references and reseal the copied reference DAG only.

    Native identity lock bytes remain unchanged; references into the copied
    template move with it. SQLite backup handles live WALs. Changed reference
    hashes are propagated, so the copy is explicitly new, test-only evidence.
    """
    def __init__(self, original, destination):
        self.original, self.destination = Path(original), Path(destination)
        self.done = set()
        self.hashes = {}

    def path(self, value):
        if not isinstance(value, str) or not value.startswith(str(self.original) + '/'):
            return value
        return str(self.destination / Path(value).relative_to(self.original))

    def value(self, value):
        if isinstance(value, list):
            return [self.value(v) for v in value]
        if isinstance(value, dict):
            for field in ('uri', 'path'):
                old = value.get(field)
                if 'sha256' in value and isinstance(old, str) and self.path(old) != old:
                    self.file(Path(old))
            result = {k: self.value(v) for k, v in value.items()}
            return result
        return self.hashes.get(value, self.path(value)) if isinstance(value, str) else value

    def file(self, source):
        target = Path(self.path(str(source)))
        if source == target or source in self.done:
            return target
        self.done.add(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.suffix in ('.sqlite', '.sqlite3'):
            snapshot(source, target)
            with sqlite3.connect(target) as db:
                tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
                for table in tables:
                    if 'payload' not in [r[1] for r in db.execute(f'PRAGMA table_info("{table}")')]:
                        continue
                    for rowid, raw in db.execute(f'SELECT rowid,payload FROM "{table}"').fetchall():
                        db.execute(f'UPDATE "{table}" SET payload=? WHERE rowid=?',
                                   (json.dumps(self.value(json.loads(raw))), rowid))
        elif source.suffix == '.json':
            raw = source.read_bytes()
            value = read(source)
            result = self.value(value)
            target.write_bytes(raw if result == value else json.dumps(result, sort_keys=True, indent=2).encode())
            if source.name == 'config.json':
                from benchmarks.attention_harness.dev_planner import digest as config_digest
                self.hashes[config_digest(value)] = config_digest(result)
        else:
            shutil.copyfile(source, target)
        self.hashes[digest(source)] = digest(target)
        return target

    def experiment(self, directory):
        # Full independent copies; no hardlinks to writable historical evidence.
        for source in sorted(directory.rglob('*')):
            if source.is_file() and 'validation-import' not in source.relative_to(directory).parts:
                self.file(source)


class Provider:
    response_observer = None
    def __init__(self):
        self.calls = []

    def chat(self, **request):
        self.calls.append(request)
        source = json.loads(request['messages'][1]['content'])['previous_complete_source']
        content = json.dumps(dict(source=source, reason='Offline boundary replay; unchanged baseline.'))
        usage = dict(prompt_tokens=100, completion_tokens=100, total_tokens=200,
                     codex_usage=dict(input_tokens=100, output_tokens=100))
        body = dict(object='chat.completion', model=request['model'], choices=[dict(message=dict(role='assistant', content=content), finish_reason='stop')], usage=usage)
        self.response_observer(200, {}, json.dumps(body).encode())
        artifact = dict(model=request['model'], content=content, usage=usage, finish_reason='stop')
        return SimpleNamespace(content=content, usage=usage, artifact=lambda: artifact)


def protect(root, allow_model_hashes=False):
    """Deny secrets, external writes and accidental native/provider subprocesses."""
    def audit(event, args):
        if event == 'subprocess.Popen':
            # Identity verification uses read-only git queries. No other child.
            argv = args[1]
            if not isinstance(argv, (list, tuple)) or Path(argv[0]).name != 'git' or not any(x in argv for x in ('rev-parse', 'status', 'diff')):
                raise PermissionError('Offline replay refuses subprocess: ' + str(argv[:2]))
        if event != 'open' or not isinstance(args[0], (str, bytes, os.PathLike)):
            return
        p = Path(os.path.abspath(os.fsdecode(args[0])))
        if p.name == 'keys.env':
            raise PermissionError('Offline replay refuses credentials')
        if not allow_model_hashes and any(s in str(p) for s in ('/openarm365/model/', '/openarm_mobile/model/')):
            raise PermissionError('Model reads prohibited: ' + str(p))
        mode, flags = args[1:3]
        writing = (isinstance(mode, str) and any(c in mode for c in 'wax+')) or (isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC))
        if writing and not p.is_relative_to(root) and not p.is_relative_to(Path('/tmp')):
            raise PermissionError('Offline replay refuses external write: ' + str(p))
    sys.addaudithook(audit)


def run(args, root):
    from benchmarks.attention_harness.dev_v2_loop import immutable, checked, job_for
    from benchmarks.attention_harness.dev_ledger import DevV2Store
    from benchmarks.attention_harness.memory_family import FamilyClient
    from benchmarks.attention_harness.memory_family_provision import FamilyProvisioner
    from benchmarks.attention_harness.memory_validation_runtime import LedgerRunner
    from attention_memory_service.memory_service import MemoryService

    state = read(args.state / 'state.json')
    current = state['family_checks'][args.candidate]['worker']['current']
    assert current['stage'] == 'probe' and current['target_seed'] == 114
    original = Path(current['cells'][current['arm_id']]['ledger']).parent
    experiments = root / 'experiments'
    copier = CopyEvidence(original.parent, experiments)
    copier.experiment(original)
    current = copier.value(current)
    store = DevV2Store(current['cells'][current['arm_id']]['ledger'])
    key = current['arm_id']
    job = job_for(store.cell(key), 1, 'execute')
    assert job['state'] == 'settled' and 'gpu_measurement' not in job['receipt']
    try:
        FamilyProvisioner.gpu_seconds(store, key)
    except Exception as exc:
        missing = str(exc)
        assert 'measurement is unavailable' in missing
    else:
        raise AssertionError('Historical missing GPU measurement accepted')
    bundle = read(checked(job['receipt']['bundle']))
    episode = Path(bundle['artifact_dir'])
    scene = read(episode / 'scene-condition.json')['state']
    observation = dict(robot=scene['robot'], perception=dict(values=scene['objects']))
    # The old run cannot supply samples it never recorded. This is only a test
    # input at the same external hardware boundary used for subsequent arms.
    sample = immutable(Path(store.cell(key)['artifact_dir']) / 'v1/gpu-measurement.json',
                       dict(source='harness_gpu_meter', cell=key, version=1,
                            bundle_sha256=job['receipt']['bundle']['sha256'], attempt_id=bundle['attempt_id'],
                            gpu_seconds=1.25, wall_seconds=2., samples=3, test_only=True))
    with store.tx() as db:
        row = store.get(db, 'jobs', job['job_id'])
        row['receipt']['gpu_measurement'] = sample
        store.put(db, 'jobs', job['job_id'], row)
    snapshot(args.service_db, root / 'authority.sqlite')
    validator = root / 'validator'; validator.mkdir()
    # Reuse only source registration roots, without reading credentials/config.
    with sqlite3.connect(root / 'authority.sqlite') as db:
        catalogs = [json.loads(r[0]) for r in db.execute('SELECT payload FROM dev_v2_catalogs')]
    source_roots = sorted({c['root'] for c in catalogs if c['role'] == 'source'})
    source_roots = [p for p in source_roots if not any(p != q and Path(p).is_relative_to(q) for q in source_roots)]
    contract_root = str(Path(current['target']['contract']['uri']).parent)
    service = MemoryService(root / 'authority.sqlite', artifact_root=root / 'authority-artifacts',
        dev_v2_registration_roots=dict(executor=[str(experiments)], validator=[str(validator)],
                                       source=source_roots, contract=[contract_root]))
    spec = read(args.state / 'family-check-spec.json')
    assert spec['candidate_id'] == args.candidate
    client = FamilyClient(service, spec)
    provision = FamilyProvisioner(dict(experiments_root=str(experiments), state_root=str(root / 'worker'),
                                       contract_registration_roots=[contract_root]), client)
    provider = Provider()
    runner = LedgerRunner(client, provision, experiments_root=experiments, validator_root=validator,
                          state_root=root / 'worker', gpu_meter=provision.gpu_seconds,
                          replay=lambda *a: (copy.deepcopy(observation), copy.deepcopy(scene)), provider=provider)
    report = dict(test_only=True, root=str(root), historical_missing_gpu=missing,
                  substitutions=['native execution', 'GPU sampler (synthetic samples)', 'scene reset (recorded state, not fresh replay)', 'Codex provider'], steps=[])
    def step(name, action):
        print(name, flush=True)
        try:
            value = action()
        except Exception as exc:
            report['steps'].append(dict(step=name, status='blocked', error=f'{type(exc).__name__}: {exc}'))
            raise
        report['steps'].append(dict(step=name, status='passed'))
        return value
    try:
        # Validate the copied native execution independently of catalog
        # registration, which additionally reads the protected model inventory.
        # This is an extra diagnostic, never a cached substitute for finish.
        step('real validate_arm on copied settled probe (recorded reset only)',
             lambda: validate_copied_probe(copier, original, current, bundle, runner, root))
        result = step('settled real probe: finish / prepare / validate_arm / service probe', lambda: runner.finish(current))
        report['probe_role'] = result['probe']['role']
        assert report['probe_role'] == 'neutral'
        batch = step('real paired provisioning / service plan', lambda: runner.begin_cross(current['note'], current['operation'] + '-t6-pairs', current['target'], pairs=3))
        run_pairs(runner, batch, bundle, episode, provider, step)
        report['family_status'] = step('family verdict / conditions', lambda: service.family.status(args.candidate))
        from benchmarks.attention_harness.memory_monitor import automatic_checks
        gateway = SimpleNamespace(family_list=service.family.public, memory_events=service.memory_events)
        report['monitor_family'] = step('monitor family read', lambda: automatic_checks(root / 'worker', gateway)['family'])
        assert report['family_status']['scenes'][0]['verdict'] == 'neutral_no_harm'
        report['provider_calls'] = len(provider.calls)
    finally:
        runner.pool.shutdown()
        (root / 'report.json').write_text(json.dumps(report, indent=2, ensure_ascii=False))
    return report


def validate_copied_probe(copier, original, current, bundle, runner, root):
    from benchmarks.attention_harness.memory_independent_validator import validate_arm
    from benchmarks.attention_harness.dev_v2_loop import job_for, checked
    from benchmarks.attention_harness.dev_planner import digest as config_digest
    store, key, cell = runner._cell(current)
    exported = original / 'validation-import' / key
    # Rebase the existing successful T5 export in a separate diagnostic folder;
    # real finish must still call real prepare_recoverable from a clean location.
    copied_descriptor = copier.file(exported / 'descriptor.json')
    descriptor = read(copied_descriptor)
    # Permission binds canonical config JSON, unlike the file-byte config ref.
    descriptor['permission_scope']['config_sha256'] = config_digest(read(checked(descriptor['config'])))
    local = Path(cell['artifact_dir']).parent
    files = {str(p.relative_to(local)): digest(p) for p in local.rglob('*') if p.is_file()}
    execution = job_for(cell, 1, 'execute')['receipt']['bundle']
    result = validate_arm(experiment_dir=local, experiments_root=runner.experiments_root,
        cell=key, version=1, execution_import_id=key, bundle_ref=execution,
        config_ref=descriptor['config'], source_ref=cell['versions'][0]['source'], grant=None,
        permission_scope=descriptor['permission_scope'], executor_catalog=dict(root=str(local), role='executor', files=files, context=current['context']),
        contract=read(checked(descriptor['config']))['scene_contract'], validator_root=root / 'diagnostic-validator',
        replay=runner.replay, failure_signature=current['failure_signature'], budget=current['budget'],
        developer_model=current['dev_model']['model'], gpu_seconds=runner.provision.gpu_seconds(store,key),
        ledger_ref=descriptor['ledger'], probe_input=descriptor['validation_input'], origin_source_sha256=current['origin']['sha256'],
        import_purpose=descriptor.get('import_purpose'), source_drift=descriptor.get('source_drift'), current_lock=descriptor.get('current_lock'))
    assert result['status'] == 'passed', result
    # Remove only our diagnostic copy of the old export. Finish builds a fresh
    # snapshot containing the explicitly synthetic GPU receipt.
    shutil.rmtree(copied_descriptor.parent)
    return result


def replay_artifact(name, value, rebind, consumption):
    value = rebind(copy.deepcopy(value))
    if name == 'trace':
        value['memory_validation'] = consumption
    if name == 'sandbox_receipt':
        # The adapter produces no camera frames or video. Do not retain the
        # probe's nested recording reference after disabling bundle recording.
        value['camera_recording'] = dict(enabled=False, test_only=True)
    return value


def run_pairs(runner, batch, old_bundle, old_episode, provider, step):
    """Exercise real execute/envelope consumption with recorded native outputs."""
    from benchmarks.attention_harness import dev_v2_loop as loop
    from benchmarks.attention_harness.dev_memory_validation import policy_input
    from benchmarks.attention_harness import gpu_meter
    from openarm365_dev_v2.runner import OpenArm365DevRunnerV2

    def native(self, request):
        episode = request.artifact_root / 'offline-native'
        episode.mkdir(parents=True)
        config = read(request.config_path)
        # Keep the genuine configuration checks and public envelope consumer.
        from openarm365_dev_v2.dev_config import validate_dev_config
        epoch = read(config['dev_lock']['path'])['source_root']
        validate_dev_config(config, expected_source_root=epoch)
        self.budgets(request, config)
        public, consumption = policy_input(self.memory_validation, request)
        assert public['exposure'] in ('none', 'candidate_validation')
        replacements = {old_bundle[k]: getattr(request, k) for k in
                        ('run_id', 'attempt_id', 'config_sha256', 'policy_sha256')}
        def rebind(value):
            if isinstance(value, dict):return {k: rebind(v) for k, v in value.items()}
            if isinstance(value, list):return [rebind(v) for v in value]
            if isinstance(value, str):
                if value.startswith(str(old_episode)):return str(episode) + value[len(str(old_episode)):]
                return replacements.get(value, value)
            return value
        result = rebind(copy.deepcopy(old_bundle))
        for name, ref in old_bundle['artifacts'].items():
            value = replay_artifact(name, read(loop.checked(ref)), rebind, consumption)
            result['artifacts'][name] = loop.immutable(episode / Path(ref['uri']).name, value)
        for name in ('initial_public_observation.json', 'final_public_observation.json', 'scene-condition.json'):
            if (old_episode / name).exists():loop.immutable(episode / name, rebind(read(old_episode / name)))
        # No camera/video bytes are claimed for the simulated arm execution.
        result['recording'] = dict(enabled=False)
        result['artifact_dir'] = str(episode)
        loop.immutable(episode / 'result.json', result)
        return result

    with patch.object(OpenArm365DevRunnerV2, 'execute', native), \
         patch.object(gpu_meter.GpuMeter, 'start', lambda self: self), \
         patch.object(gpu_meter.GpuMeter, 'stop', lambda self: dict(gpu_seconds=1.25, wall_seconds=2., samples=3, test_only=True)):
        for pair in batch['plan']['pairs']:
            results = {}
            for arm in ('control', 'treatment'):
                current = dict(batch, arm=arm, arm_id=pair[arm + '_cell'])
                def execute_arm():
                    runner.start(current, current['arm_id'])
                    from benchmarks.attention_harness.memory_validation_replacements import attempt_id
                    runner.futures[attempt_id(current)].result()
                    assert runner.poll(current) == 'completed'
                    return runner.finish(current)
                results[arm] = step(f'pair {pair["pair_id"]} {arm}: generate / grant / execute / finish / trial import', execute_arm)
                assert results[arm]['status'] == 'passed', results[arm]
                request = provider.calls[-1]
                label = runner.client.get_memory(batch['memory_id']).treatment_label
                assert request['messages'][0]['content'].endswith(label) is (arm == 'treatment')
            step(f'pair {pair["pair_id"]}: records / evaluate', lambda: runner.client.cross_seed_write('pairs',
                memory_id=batch['memory_id'], target_seed=batch['target_seed'],
                control_import_id=results['control']['trial_import_id'], treatment_import_id=results['treatment']['trial_import_id']))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    base = get('memory.m3_root')
    p.add_argument('--state', type=Path, default=base / 'validation-state')
    p.add_argument('--service-db', type=Path, default=base / 'store.sqlite3')
    p.add_argument('--candidate', default='family-aabd247c42d9073faacff69dcecccf7d')
    p.add_argument('--allow-model-hashes', action='store_true', help='explicitly permit existing identity verification to read model files; never modify them')
    args = p.parse_args()
    root = Path(tempfile.mkdtemp(prefix='t6-live-path-')).resolve()
    print('Offline audit root:', root, flush=True)
    protect(root, args.allow_model_hashes)
    run(args, root)


if __name__ == '__main__':
    main()
