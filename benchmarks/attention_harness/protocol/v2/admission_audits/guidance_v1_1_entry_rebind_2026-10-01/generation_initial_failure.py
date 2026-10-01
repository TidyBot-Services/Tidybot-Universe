"""Generate the new development identity freeze; never execute launch commands."""
import copy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

from benchmarks.attention_harness.formal_entry import inspect_formal_entry

U = Path('/home/truares/桌面/Tidybot-Universe-attention-native')
ROOT = Path(__file__).resolve().parent
OLD = U / 'benchmarks/attention_harness/protocol/v2/review_packages/seven_primary_dev_2026-09-29'
GUIDANCE = U / 'benchmarks/attention_harness/protocol/v2/review_packages/guidance_adoption_v1_1_dev_2026-09-29'
PACKAGE = U / 'benchmarks/attention_harness/protocol/v2/review_packages/seven_primary_guidance_v1_1_dev_2026-10-01'
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
read = lambda p: json.loads(Path(p).read_text())
ref = lambda p: {'path': str(p.resolve()), 'sha256': sha(p)}


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + '\n')


def cloned(path):
    return PACKAGE / Path(path).relative_to(OLD)


def generate():
    if PACKAGE.exists():
        raise ValueError('new freeze directory already exists; never overwrite')
    baseline = read(ROOT / 'protected_baseline.json')
    assert all(sha(p) == h for p, h in baseline['protected_files'].items())
    prior = read(OLD / 'matrix_plan.json')
    approval = read(GUIDANCE / 'operator_approval.json')
    assert approval['decision'] == 'approved'
    shutil.copytree(OLD, PACKAGE)
    historical = PACKAGE / 'historical_identity'
    historical.mkdir()
    for name in ['matrix_plan.json', 'sha256_manifest.json', 'software_versions.json',
                 'runtime_source_lock.json', 'execution_contract.json']:
        shutil.copyfile(OLD / name, historical / name)
    policies = {}
    for suite, task in prior['grid']['tasks']:
        directory = PACKAGE / f'base/{suite}_{task}'
        original = OLD / f'base/{suite}_{task}'
        shutil.copyfile(original / 'policy.py', historical / f'{suite}_old_policy.py')
        shutil.copyfile(original / 'generation_receipt.json', historical / f'{suite}_old_generation_receipt.json')
        source = GUIDANCE / f'frozen/final_policies/{suite}_{task}.py'
        assert sha(source) == approval['identity']['policies'][suite]['sha256']
        code = directory / 'policy.py'
        shutil.copyfile(source, code)
        # A truthful derived-source receipt satisfies the unchanged CLI receipt guard.
        # No provider generation or historical engineering result is attributed to new bytes.
        receipt = directory / 'generation_receipt.json'
        write(receipt, {'schema_version': 'attentionbench.approved-guidance-derived-source.v1',
            'source': str(code.resolve()), 'sha256': sha(code),
            'hypothesis': 'Exact approved public-SDK guidance consumer; identity rebinding only, no new generation or execution.',
            'generation_kind': 'approved_guidance_control_revision', 'new_model_generation_calls': 0,
            'counts_for_chain_or_profile': False,
            'lineage': {'old_policy': ref(original / 'policy.py'),
                        'old_generation_receipt': ref(original / 'generation_receipt.json'),
                        'approved_guidance_policy': ref(source),
                        'approved_guidance_REVIEW': ref(GUIDANCE / 'frozen/REVIEW.md'),
                        'approved_guidance_operator_approval': ref(GUIDANCE / 'operator_approval.json')}})
        policies[suite] = {'path': str(code), 'sha256': sha(code), 'generation_receipt': ref(receipt),
            'original_policy_review': ref(directory / 'original_policy_review.json'),
            'original_package_decision': prior['slots'][0]['policy']['original_package_decision'],
            'current_guidance_review': ref(GUIDANCE / 'frozen/REVIEW.md'),
            'current_guidance_approval': ref(GUIDANCE / 'operator_approval.json'),
            'review_scope_note': 'Original policy review is historical; current exact bytes are approved by the guidance v1.1 review/approval.'}
        policies[suite]['original_package_decision'] = next(r for r in prior['slots'] if r['suite']==suite)['policy']['original_package_decision']
    old_runtime = read(OLD / 'runtime_source_lock.json')
    runtime = copy.deepcopy(old_runtime['file_sha256'])
    assert all(sha(U / rel) == h for rel, h in runtime.items())
    extra = 'benchmarks/attention_harness/robocasa_native/controlled_negative.py'
    runtime[extra] = sha(U / extra)
    write(PACKAGE / 'runtime_source_lock.json', {
        'runtime_base_commit': baseline['base_commit'], 'source_identity_mode': 'original398_bytes_plus_current_Safety_request_helper',
        'file_sha256': runtime, 'old398_unchanged': True,
        'request_helper_evidence': ref(U.parent / 'attentionbench-guidance-safety-resign-20260930/validation_plan.json'),
        'runtime_mutations_this_round': 0})
    versions = read(OLD / 'software_versions.json')
    versions['universe'] = {'path': str(U), 'runtime_base_commit': baseline['base_commit'],
        'runtime_source_lock': ref(PACKAGE / 'runtime_source_lock.json'),
        'scope': 'Exact byte lock governs this current source; dirty documentation and prior pending deliveries are not a clean-HEAD claim.'}
    write(PACKAGE / 'software_versions.json', versions)
    execution = read(OLD / 'execution_contract.json')
    execution['service_versions'] = ref(PACKAGE / 'software_versions.json')
    execution['launch_preflight'] = 'Verify this exact manifest and runtime source byte lock, clean pinned Service versions and separate explicit execution authorization. Admission and input approval do not authorize execution.'
    execution['mode'] = 'identity_generation_and_review_only'
    write(PACKAGE / 'execution_contract.json', execution)
    rows, differences = [], []
    for old_row in prior['slots']:
        row = copy.deepcopy(old_row)
        suite, task, condition = row['suite'], row['task_id'], row['condition']
        directory = PACKAGE / f"entries/{suite}_{task}_seed{row['seed']}/{condition}"
        config = cloned(old_row['config']['path'])
        memory = cloned(old_row['memory_contract']['path'])
        demo = cloned(old_row['demo']['path']) if old_row['demo'] else None
        pc = cloned(old_row['policy_config']['path']) if old_row['policy_config'] else None
        old_lock = read(old_row['m1_entry_lock']['path'])
        code = Path(policies[suite]['path'])
        lock, _ = inspect_formal_entry(suite=suite, task_id=task, seed=row['seed'], policy_id=condition,
            code=code, approved_policy_sha256=sha(code), config=config, approved_config_sha256=sha(config),
            max_attempts=old_lock['max_attempts'], assistance_credits=old_lock['assistance_credits'],
            token_limit=old_lock['token_limit'], assistance_mode=old_lock['assistance_mode'],
            human_deadline_seconds=old_lock['human_deadline_seconds'], overall_deadline_seconds=old_lock['overall_deadline_seconds'],
            demo_prior=demo, approved_demo_sha256=sha(demo) if demo else None,
            policy_config=pc, approved_policy_config_sha256=sha(pc) if pc else None,
            memory_contract=memory, approved_memory_contract_sha256=sha(memory))
        changed = [k for k in set(lock) | set(old_lock) if lock.get(k) != old_lock.get(k)]
        assert set(changed) == {'approved_policy_sha256', 'sha256'}, changed
        write(directory / 'm1_entry_lock.json', lock)
        launch = read(old_row['launch_arguments']['path'])
        args = launch['fixed_arguments']
        replacements = {'--code': str(code), '--approved-policy-sha256': sha(code),
            '--config': str(config), '--memory-contract': str(memory), '--expected-entry-sha256': lock['sha256'],
            '--dev-generation-artifact': policies[suite]['generation_receipt']['path']}
        if demo: replacements['--demo-prior'] = str(demo)
        if pc: replacements['--policy-config'] = str(pc)
        for flag, value in replacements.items(): args[args.index(flag)+1] = value
        assert launch['execution_authorized'] is False
        write(directory / 'launch_arguments.json', launch)
        row.update(policy=policies[suite], config=ref(config), memory_contract=ref(memory),
                   m1_entry_lock=ref(directory / 'm1_entry_lock.json'), m1_entry_identity_sha256=lock['sha256'],
                   demo=ref(demo) if demo else None, policy_config=ref(pc) if pc else None,
                   launch_arguments=ref(directory / 'launch_arguments.json'),
                   accounting_rules_sha256=sha(PACKAGE / 'accounting_rules.json'),
                   execution_contract_sha256=sha(PACKAGE / 'execution_contract.json'))
        rows.append(row)
        differences.append({'suite': suite, 'task_id': task, 'seed': row['seed'], 'condition': condition,
            'old_lock': old_row['m1_entry_lock'], 'new_lock': row['m1_entry_lock'],
            'old_entry_identity_sha256': old_lock['sha256'], 'new_entry_identity_sha256': lock['sha256'],
            'changed_canonical_fields': sorted(changed), 'configuration_and_condition_input_bytes_unchanged': True})
    plan = copy.deepcopy(prior)
    for name in ['specification', 'base_protocol', 'resolution']:
        plan[name] = ref(cloned(prior[name]['path']))
    plan.update(schema_version='attentionbench.seven-primary-guidance-v1.1-identity-freeze.v1',
                runtime_commit=baseline['base_commit'], slots=rows,
                identity_only_rebind={'old_matrix': ref(OLD / 'matrix_plan.json'),
                    'approved_guidance_REVIEW': ref(GUIDANCE / 'frozen/REVIEW.md'),
                    'approved_guidance_approval': ref(GUIDANCE / 'operator_approval.json'),
                    'policy_control_changes_this_round': 0, 'robot_executions': 0})
    write(PACKAGE / 'matrix_plan.json', plan)
    write(PACKAGE / 'identity_rebind_index.json', {'entries': differences, 'entries_generated': len(rows),
        'configurations': 50, 'suites': 2, 'seeds_per_task': 25, 'conditions_per_seed': 7,
        'execution_authorized': False, 'effects_run': 0, 'generator': ref(Path(__file__))})
    shutil.copyfile(Path(__file__), PACKAGE / 'review_tools/generate_identity_package.py')
    print(json.dumps({'package': str(PACKAGE), 'entries': len(rows), 'matrix_SHA': sha(PACKAGE / 'matrix_plan.json'), 'executed': 0}))


if __name__ == '__main__':
    generate()
