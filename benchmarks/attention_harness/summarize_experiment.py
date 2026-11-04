"""Read-only Dev v2 experiment summary. Run with --help for the CLI."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections import defaultdict
from pathlib import Path

from .execution_profile import config_metadata,manifest_metadata
from .eval_quality import summarize_eval_quality


def _artifact(ref):
    path = Path(ref['uri'])
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != ref['sha256']:
        raise ValueError(f'artifact digest mismatch: {path}')
    return json.loads(data)


def read_experiment(store_path, experiment_id=None):
    """Read persisted v2 tables and their immutable artifact references without writes."""
    path = Path(store_path).resolve(strict=True)
    with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as db:
        experiments = [json.loads(p) for p, in db.execute('SELECT payload FROM v2_experiments')]
        if experiment_id is None:
            if len(experiments) != 1:
                raise ValueError('store has multiple experiments; specify --experiment-id')
            experiment = experiments[0]
        else:
            experiment = next((e for e in experiments if e['experiment_id'] == experiment_id), None)
            if experiment is None:
                raise ValueError('experiment not found in store')
        manifest = _artifact(experiment['manifest'])
        if manifest.get('schema') not in {'attentionbench.dev-experiment.v2', 'attentionbench.dev-experiment.v3'}:
            raise ValueError('Dev v2/v3 experiment manifest required')
        if manifest.get('experiment_id') != experiment['experiment_id']:
            raise ValueError('experiment/manifest identity mismatch')
        cells = []
        seeded_cells=set()
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='v2_events'").fetchone():
            seeded_cells={detail['cell'] for (payload,) in db.execute(
                "SELECT payload FROM v2_events WHERE kind='planner_memory_retrieved'")
                if (detail:=json.loads(payload)).get('excluded_from_results') is True}
        found = set()
        for key, payload in db.execute('SELECT id,payload FROM v2_cells WHERE exp=? ORDER BY rowid', (experiment['experiment_id'],)):
            found.add(key)
            cell = json.loads(payload)
            jobs = [{**json.loads(p), 'state': state} for state, p in db.execute('SELECT state,payload FROM v2_jobs WHERE cell=? ORDER BY rowid', (key,))]
            versions = [json.loads(p) for p, in db.execute('SELECT payload FROM v2_versions WHERE cell=? ORDER BY version', (key,))]
            observations = []
            errors = []
            for version in versions:
                number = version['version']
                execution = next((j for j in jobs if j['kind'] == 'execute' and j['version'] == number and j['state'] == 'settled'), None)
                if execution is None:
                    continue  # generated or unresolved is not an executed version
                receipt = execution.get('receipt') or {}
                host_sha = receipt.get('host_fingerprint_sha256')
                host_drift = receipt.get('host_drift')
                obs = {'version': number, 'native_success': None, 'passed': None, 'mode': 'unavailable',
                       'settled_at': execution.get('settled_at')}
                try:
                    bundle = _artifact(receipt['bundle'])
                    if bundle.get('status') == 'condition_invalid':
                        obs['condition_invalid'] = True
                    else:
                        native_ref = receipt['native_result']
                        if native_ref != bundle['artifacts']['native_result']:
                            raise ValueError('native receipt/bundle reference differs')
                        native = _artifact(native_ref)
                        if native.get('run_id') != bundle.get('run_id') or native.get('attempt_id') != bundle.get('attempt_id'):
                            raise ValueError('native execution identity differs')
                        if native.get('evaluated') is True and type(native.get('native_success')) is bool:
                            obs['native_success'] = native['native_success']
                except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
                    errors.append(f'v{number} native: {exc}')
                evaluation = next((j for j in jobs if j['kind'] == 'eval' and j['version'] == number and j['state'] == 'settled'), None)
                if evaluation:
                    ref = (evaluation.get('receipt') or {}).get('eval_review') or (evaluation.get('receipt') or {}).get('review')
                    if ref:
                        try:
                            verdict = _artifact(ref)
                            obs['mode'] = verdict.get('mode', 'unavailable')
                            if verdict.get('status') == 'completed' and type(verdict.get('passed')) is bool:
                                obs['passed'] = verdict['passed']
                        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
                            errors.append(f'v{number} Eval: {exc}')
                observations.append(obs)
                if host_sha:
                    obs['host_fingerprint_sha256'] = host_sha
                if host_drift:
                    obs['host_drift'] = host_drift
            version_numbers = {v['version'] for v in versions}
            for orphan in jobs:
                if orphan['kind'] == 'execute' and orphan['state'] == 'settled' and orphan['version'] not in version_numbers:
                    errors.append(f"v{orphan['version']} settled execution has no version record")
                    observations.append({'version': orphan['version'], 'native_success': None, 'passed': None,
                                         'mode': 'unavailable', 'settled_at': orphan.get('settled_at')})
            observations.sort(key=lambda row: row['version'])
            requests = [j for j in jobs if j['kind'] == 'advisor']
            generic_fallbacks = 0
            for request in requests:
                if request['state'] != 'settled':
                    continue
                answer_ref = (request.get('receipt') or {}).get('advisor_answer')
                if answer_ref:
                    try:
                        answer = _artifact(answer_ref)
                        localization = answer.get('localization') or {}
                        generic_fallbacks += (localization.get('status') == 'generic_fallback' and
                                              localization.get('reason') != 'policy_uses_full_trace')
                    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
                        errors.append(f'advisor answer: {exc}')
            planner_requests = []
            if db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='v2_planner_requests'").fetchone():
                planner_requests = [json.loads(p) for p, in db.execute('SELECT payload FROM v2_planner_requests WHERE cell=?', (key,))]
            unanswered_advisor = [r for r in planner_requests if r.get('request_type') == 'advisor' and
                                  r.get('state') == 'timed_out' and not any(j['state'] == 'settled' and
                                  j['version'] == r.get('version') for j in requests)]
            memory_seeded=key in seeded_cells or any(v.get('memory_provenance',{}).get('excluded_from_results') is True for v in versions)
            cells.append({'key': key, 'task_id': cell['task_id'], 'seed': cell['seed'], 'policy_id': cell['policy_id'],
                          'memory_seeded_excluded':memory_seeded,
                          'repeat': cell.get('repeat', 0),
                          'state': cell['state'], 'reason': cell.get('reason'), 'created_at': cell.get('created_at'),
                          'versions': observations, 'generated_versions': len(versions),
                          **({'host_fingerprint_sha256': next(v['host_fingerprint_sha256'] for v in reversed(observations) if v.get('host_fingerprint_sha256'))}
                             if any(v.get('host_fingerprint_sha256') for v in observations) else {}),
                          'attention_budget_level': cell.get('attention_budget_level', manifest['selection'].get('help_budget')),
                          'request_units': sum(j.get('actual', {}).get('advisor_calls', 0) for j in requests if j['state'] == 'settled') + len(unanswered_advisor),
                          'logical_human_seconds': sum(j.get('actual', {}).get('hint_human_seconds', 0) for j in requests if j['state'] == 'settled'),
                          'attention_seconds_unknown_requests': len(unanswered_advisor),
                          'timeouts': sum(r.get('state') == 'timed_out' for r in planner_requests),
                          'execution_timeouts': sum(j['kind'] == 'execute' and j['state'] == 'settled' and
                                                    (j.get('receipt') or {}).get('execution_status') in {'timeout', 'timed_out'} for j in jobs),
                          'fallbacks': generic_fallbacks + sum(bool(r.get('fallback')) or r.get('state') == 'fallback' for r in planner_requests),
                          'fallback_detail': {'generic_request': generic_fallbacks,
                                              'model_answer': sum(bool(r.get('fallback')) or r.get('state') == 'fallback' for r in planner_requests)},
                          'eval_compute': {k: sum(j.get('actual', {}).get(k, 0) for j in jobs if j['kind'] == 'eval' and j['state'] == 'settled')
                                           for k in ('eval_calls', 'provider_tokens', 'input_tokens', 'output_tokens', 'model_seconds')},
                          'read_errors': errors})
        for planned in manifest.get('cells', []):
            if planned['key'] not in found:
                cells.append({'key': planned['key'], 'task_id': planned['task_id'], 'seed': planned['seed'],
                              'policy_id': planned['policy_id'], 'repeat': planned.get('repeat', 0),
                              'state': 'not_started', 'reason': 'missing persisted cell',
                              'created_at': None, 'versions': [], 'generated_versions': 0,
                              'attention_budget_level': manifest['selection'].get('help_budget'),
                              'request_units': 0, 'logical_human_seconds': 0, 'timeouts': 0, 'fallbacks': 0,
                              'attention_seconds_unknown_requests': 0,
                              'execution_timeouts': 0,
                              'eval_compute': {'eval_calls': 0, 'provider_tokens': 0, 'input_tokens': 0, 'output_tokens': 0, 'model_seconds': 0},
                              'read_errors': ['missing persisted cell']})
    return {'experiment_id': experiment['experiment_id'], 'manifest': manifest, 'cells': cells}


def _rate(successes, denominator):
    return successes / denominator if denominator else None


def summarize(experiment):
    """Partition before computing any rates, including Eval quality and frontier."""
    experiments=experiment if isinstance(experiment,list) else [experiment]
    grouped=defaultdict(list)
    for exp in experiments:
        manifest=exp.get('manifest',{})
        for cell in exp['cells']:
            config=manifest.get('configs',{}).get(f"{cell['task_id']}:{cell['seed']}")
            metadata=config_metadata(config) if config is not None else {
                k:cell.get(k,manifest.get(k,v)) for k,v in config_metadata({}).items()}
            if config is None:
                for key in ('dev_sdk_sha256','dev_sdk_description'):
                    if key in cell or key in manifest:metadata[key]=cell.get(key,manifest.get(key))
            if config is None and cell.get('deployment_kind',manifest.get('deployment_kind'))=='dev-local':metadata.update(deployment_kind='dev-local',formal_eligible=False)
            # Pre-0g and 0g legacy conditions remain separate as well.
            generation=('0g' if config is not None and all(k in config for k in
                ('planner_mode','ik_candidates','ik_max_joint_jump')) else 'pre-0g')
            grouped[(metadata['execution_profile'],generation,metadata.get('deployment_kind','registered'),metadata.get('dev_sdk_sha256','unsealed'),metadata.get('dev_sdk_description','base stationary'))].append((exp,dict(cell,**metadata)))
    if not grouped:
        value=_summarize_one(experiments[0]);value.update(manifest_metadata(experiments[0].get('manifest',{})))
        return value
    values=[]
    for (profile,generation,kind,sdk_sha,sdk_description),members in sorted(grouped.items()):
        first=members[0][0]
        value=_summarize_one(dict(first,cells=[c for _,c in members]))
        shas=sorted({c['planner_lock_sha256'] for _,c in members})
        value.update(execution_profile=profile,grasp_generation=generation,
                     planner_lock_sha256=shas[0] if len(shas)==1 else shas)
        hosts = sorted({sha for _, c in members for sha in
                        [c.get('host_fingerprint_sha256'),
                         *(v.get('host_fingerprint_sha256') for v in c.get('versions', []))] if sha})
        if hosts:
            value['host_fingerprint_sha256'] = hosts
            if len(hosts) > 1:
                value['host_differs'] = True
        if sdk_sha!='unsealed':
            value.update(dev_sdk_sha256=sdk_sha,dev_sdk_description=sdk_description)
        if kind=='dev-local':value.update(deployment_kind=kind,formal_eligible=False)
        value['experiment_ids']=sorted({exp['experiment_id'] for exp,_ in members})
        values.append(value)
    if len(values)==1:return values[0]
    return dict(schema='attentionbench.experiment-summary.profiles.v1',
        note=('Different execution profiles, SDK descriptions or grasp generations are not comparable; tables and rates are reported separately.'
              if any('dev_sdk_sha256' in value for value in values) else
              'Different execution profiles or grasp generations are not comparable; tables and rates are reported separately.'),
        profiles=values)


def _summarize_one(experiment):
    """Aggregate observations; unknown native outcomes never enter a failure denominator."""
    cells = experiment['cells']
    quality = summarize_eval_quality([c for c in cells if not c.get('memory_seeded_excluded')])
    policies = defaultdict(list)
    frontier = defaultdict(list)
    invalid = defaultdict(lambda: {'scene_contract_mismatch': 0, 'vision_unavailable': 0, 'other': 0})
    seeded=defaultdict(int)
    for cell in cells:
        if cell.get('memory_seeded_excluded'):
            seeded[cell['policy_id']]+=1
            continue
        reason = cell.get('reason') or ''
        if cell['state'] == 'condition_invalid':
            cause = ('scene_contract_mismatch' if 'scene_contract' in reason else
                     'vision_unavailable' if 'vision' in reason else 'other')
            invalid[cell['policy_id']][cause] += 1
            continue
        observations = cell['versions']
        known = [v for v in observations if type(v['native_success']) is bool]
        # A cell's failure is known only if every executed version has native evaluation
        # and the run has terminated. Any observed success remains a known success.
        success = any(v['native_success'] is True for v in known)
        complete = cell['state'] in {'completed', 'failed'}
        all_versions_observed = len(observations) == cell.get('generated_versions', len(observations))
        outcome = True if success else False if complete and observations and all_versions_observed and len(known) == len(observations) else None
        final = observations[-1]['native_success'] if complete and observations and all_versions_observed else None
        first = next((v for v in observations if v['native_success'] is True), None)
        result = {**cell, 'native_success': outcome, 'final_version_success': final,
                  'first_success_version': first['version'] if first else None,
                  'time_to_first_success_seconds': max(0, first['settled_at'] - cell['created_at']) if first and first.get('settled_at') is not None and cell.get('created_at') is not None else None}
        policies[cell['policy_id']].append(result)
        frontier[(cell['policy_id'], cell.get('attention_budget_level'))].append(result)
    def aggregate(rows):
        observed = [c for c in rows if type(c['native_success']) is bool]
        final = [c for c in rows if type(c['final_version_success']) is bool]
        return {'cells': len(rows), 'evaluated_cells': len(observed), 'unknown_cells': len(rows)-len(observed),
                'native_successes': sum(c['native_success'] is True for c in observed),
                'native_success_rate': _rate(sum(c['native_success'] is True for c in observed), len(observed)),
                'final_version_success_rate': _rate(sum(c['final_version_success'] is True for c in final), len(final)),
                'final_version_evaluated_cells': len(final),
                'first_success_versions': [c['first_success_version'] for c in rows if c['first_success_version'] is not None],
                'time_to_first_success_seconds': [c['time_to_first_success_seconds'] for c in rows if c['time_to_first_success_seconds'] is not None],
                'request_units': sum(c['request_units'] for c in rows),
                'logical_human_seconds': sum(c['logical_human_seconds'] for c in rows),
                'attention_seconds_unknown_requests': sum(c.get('attention_seconds_unknown_requests', 0) for c in rows),
                'timeouts': sum(c['timeouts'] for c in rows),
                'execution_timeouts': sum(c.get('execution_timeouts', 0) for c in rows),
                'fallbacks': sum(c['fallbacks'] for c in rows),
                'fallback_detail': {kind: sum(c.get('fallback_detail', {}).get(kind, 0) for c in rows)
                                    for kind in ('generic_request', 'model_answer')},
                'eval_compute': {k: sum(c['eval_compute'].get(k, 0) for c in rows) for k in ('eval_calls', 'provider_tokens', 'input_tokens', 'output_tokens', 'model_seconds')}}
    by_policy = {p: {**aggregate(rows), 'invalid_cells': invalid[p], 'memory_seeded_excluded':seeded[p]} for p, rows in sorted(policies.items())}
    for p in set(invalid)|set(seeded):
        by_policy.setdefault(p, {**aggregate([]), 'invalid_cells': invalid[p], 'memory_seeded_excluded':seeded[p]})
    points = []
    budget_rows = defaultdict(dict)
    for (policy, budget), rows in frontier.items():
        budget_rows[budget][policy] = rows
    pair_key = lambda c: (c['task_id'], c['seed'], c.get('repeat', 0))
    for budget, policy_rows in sorted(budget_rows.items(), key=lambda item: str(item[0])):
        matched = set.intersection(*(set(pair_key(c) for c in rows if type(c['native_success']) is bool)
                                     for rows in policy_rows.values()))
        for policy, rows in sorted(policy_rows.items()):
            paired = [c for c in rows if pair_key(c) in matched and type(c['native_success']) is bool]
            stats = aggregate(paired)
            points.append({'policy_id': policy, 'attention_budget_level': budget, **stats,
                           'paired_task_scene_repeats': len(matched), 'unpaired_or_unknown_cells': len(rows) - len(paired),
                           'mean_request_units': _rate(stats['request_units'], len(paired)),
                           'mean_logical_human_seconds': (None if stats['attention_seconds_unknown_requests'] else
                                                          _rate(stats['logical_human_seconds'], len(paired)))})
    by_budget = defaultdict(list)
    for point in points:
        by_budget[str(point['attention_budget_level'])].append(point)
    frontier_by_budget = {}
    for budget, candidates in sorted(by_budget.items()):
        frontier_by_budget[budget] = [p['policy_id'] for p in candidates if p['native_success_rate'] is not None and
            p['attention_seconds_unknown_requests'] == 0 and not any(
            q is not p and q['native_success_rate'] is not None and q['attention_seconds_unknown_requests'] == 0 and
            q['native_success_rate'] >= p['native_success_rate'] and
            q['mean_request_units'] <= p['mean_request_units'] and
            q['mean_logical_human_seconds'] <= p['mean_logical_human_seconds'] and
            (q['native_success_rate'] > p['native_success_rate'] or
             q['mean_request_units'] < p['mean_request_units'] or
             q['mean_logical_human_seconds'] < p['mean_logical_human_seconds'])
            for q in candidates)]
    marginal = {}
    for policy, rows in sorted(policies.items()):
        buckets = defaultdict(list)
        for row in rows:
            buckets[row['request_units']].append(row)
        series = [{'request_units': n, 'native_success_rate': aggregate(group)['native_success_rate'],
                   'evaluated_cells': aggregate(group)['evaluated_cells']} for n, group in sorted(buckets.items())]
        for previous, current in zip(series, series[1:]):
            current['delta_success_per_request'] = ((current['native_success_rate'] - previous['native_success_rate']) /
                                                    (current['request_units'] - previous['request_units'])
                                                    if current['native_success_rate'] is not None and previous['native_success_rate'] is not None else None)
        marginal[policy] = series
    manifest = experiment.get('manifest', {})
    return {'schema': 'attentionbench.experiment-summary.v1', 'experiment_id': experiment['experiment_id'],
            'mode': manifest.get('selection', {}).get('mode'), 'budget_tier': manifest.get('budget_tier', 'legacy'),
            'formal_eligible': manifest.get('formal_eligible'),
            'memory_seeded_excluded':sum(seeded.values()),
            'by_policy': by_policy,
            'cell_results': [{k: c[k] for k in ('key', 'task_id', 'seed', 'policy_id', 'native_success',
                             'final_version_success', 'first_success_version', 'time_to_first_success_seconds',
                             'request_units', 'logical_human_seconds', 'attention_seconds_unknown_requests')
                             if k in c} for rows in policies.values() for c in rows],
            'frontier_plot_data': points, 'nondominated_policies_by_budget': frontier_by_budget,
            'marginal_utility_by_request': marginal,
            'eval_quality': quality, 'read_errors': {c['key']: c['read_errors'] for c in cells if c['read_errors']},
            'denominator_rule': 'Only known native outcomes enter success rates; invalid, unknown and memory-seeded cells are excluded.'}


def markdown(value):
    if 'profiles' in value:
        return value['note']+'\n\n'+'\n'.join(markdown(group) for group in value['profiles'])
    rate = lambda x: 'unknown' if x is None else f'{x:.3f}'
    lines = [f"# Experiment {value['experiment_id']}", '',
             *([f"host differs: {', '.join(value['host_fingerprint_sha256'])}", ''] if value.get('host_differs') else []),
             *([f"SDK description: {value['dev_sdk_description']}; SHA-256: {value['dev_sdk_sha256']}."] if 'dev_sdk_sha256' in value else []),
             f"Execution profile: {value.get('execution_profile','legacy')}; grasp generation: {value.get('grasp_generation','pre-0g')}; planner lock SHA: {value.get('planner_lock_sha256')}.", '',
             f"Mode: {value.get('mode')}; budget tier: {value.get('budget_tier')}; formal eligible: {value.get('formal_eligible')}.",
             f"Memory-seeded (excluded): {value.get('memory_seeded_excluded', 0)}.", '',
             '| Policy | Native success | Final success | First success versions | Time to first success (s) | Requests | Logical human seconds | Unknown attention seconds | Invalid scene / vision / other | Request / execution timeout | Fallback |',
             '|---|---:|---:|---|---|---:|---:|---:|---:|---:|---:|']
    for policy, row in value['by_policy'].items():
        inv = row['invalid_cells']
        lines.append(f"| {policy} | {row['native_successes']}/{row['evaluated_cells']} ({rate(row['native_success_rate'])}) | {rate(row['final_version_success_rate'])} | {row['first_success_versions']} | {[round(x, 1) for x in row['time_to_first_success_seconds']]} | {row['request_units']} | {row['logical_human_seconds']:.1f} | {row['attention_seconds_unknown_requests']} | {inv['scene_contract_mismatch']} / {inv['vision_unavailable']} / {inv['other']} | {row['timeouts']} / {row['execution_timeouts']} | {row['fallbacks']} |")
    q = value['eval_quality']
    false_stop_cells = ', '.join(f"{r['cell']} (v{r['version']})" for r in q['false_stops']) or 'none'
    lines += ['', '## Eval quality', '', f"Confusion matrix: {json.dumps(q['confusion_matrix'], sort_keys=True)}",
              f"False passes: {q['false_passes']}; false failures: {q['false_failures']}; false stops: {q['false_stop_count']}.",
              f"Text only: {q['text_only']}; unavailable: {q['unavailable']}; unevaluated: {q['unevaluated']}.",
              f"False-stop cells: {false_stop_cells}.",
              '', '### False passes by policy', '',
              '| Policy | False passes | Native failures | False pass rate | Fraction of all verdicts | Fraction of Eval passes | Text only | Unavailable | Unevaluated |',
              '|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    for policy, row in q['by_policy'].items():
        lines.append(f"| {policy} | {row['false_passes']} | {row['native_failures_with_eval']} | {rate(row['false_pass_rate'])} | {rate(row['false_pass_fraction_all'])} | {rate(row['false_pass_fraction_of_eval_passes'])} | {row['text_only']} | {row['unavailable']} | {row['unevaluated']} |")
    lines += ['', '## Frontier plot data', '',
              '| Budget | Policy | Native success rate | Mean requests | Mean logical seconds |',
              '|---|---|---:|---:|---:|']
    for point in value['frontier_plot_data']:
        lines.append(f"| {point['attention_budget_level']} | {point['policy_id']} | {rate(point['native_success_rate'])} | {rate(point['mean_request_units'])} | {rate(point['mean_logical_human_seconds'])} |")
    lines += ['', '## Marginal utility per request', '',
              '| Policy | Actual requests | Evaluated cells | Native success rate | Delta per request |',
              '|---|---:|---:|---:|---:|']
    for policy, series in value['marginal_utility_by_request'].items():
        for bucket in series:
            lines.append(f"| {policy} | {bucket['request_units']} | {bucket['evaluated_cells']} | {rate(bucket['native_success_rate'])} | {rate(bucket.get('delta_success_per_request'))} |")
    lines.append('')
    return '\n'.join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--store', type=Path, action='append', required=True, help='Dev v2 SQLite ledger')
    parser.add_argument('--experiment-id')
    parser.add_argument('--output-prefix', type=Path, required=True)
    args = parser.parse_args(argv)
    value = summarize([read_experiment(path, args.experiment_id) for path in args.store])
    if "note" in value:print(value["note"])
    args.output_prefix.parent.mkdir(parents=True, exist_ok=True)
    args.output_prefix.with_suffix('.json').write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + '\n')
    args.output_prefix.with_suffix('.md').write_text(markdown(value))


if __name__ == '__main__':
    main()
