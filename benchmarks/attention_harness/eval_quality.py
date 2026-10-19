"""Offline Eval-versus-native accounting. No provider or experiment imports."""
from collections import defaultdict


def summarize_eval_quality(cells):
    """Cells contain policy_id, key, reason and executed version observations.

    A version has passed (bool or None), mode, and native_success (bool or
    None). Only a vision boolean paired with a native boolean enters the matrix.
    """
    matrix = {name: 0 for name in ('true_positive', 'false_positive', 'true_negative', 'false_negative')}
    counts = {name: 0 for name in ('unavailable', 'text_only', 'unevaluated', 'unknown_verdict')}
    policies = defaultdict(lambda: {'false_passes': 0, 'comparable_verdicts': 0, 'native_failures_with_eval': 0,
                                    'eval_passes_with_native': 0,
                                    'text_only': 0, 'unavailable': 0, 'unevaluated': 0})
    false_stops = []
    for cell in cells:
        if cell.get('memory_seeded_excluded'):
            continue
        policy = cell['policy_id']
        policies[policy]
        versions = cell.get('versions', [])
        for version in versions:
            mode = version.get('mode', 'unavailable')
            passed = version.get('passed')
            native = version.get('native_success')
            if mode == 'text_only':
                counts['text_only'] += 1
                policies[policy]['text_only'] += 1
            elif mode != 'vision':
                counts['unavailable'] += 1
                policies[policy]['unavailable'] += 1
            if type(native) is not bool:
                counts['unevaluated'] += 1
                policies[policy]['unevaluated'] += 1
            if mode != 'vision' or type(passed) is not bool:
                counts['unknown_verdict'] += 1
                continue
            if type(native) is not bool:
                continue
            policies[policy]['comparable_verdicts'] += 1
            if not native:
                policies[policy]['native_failures_with_eval'] += 1
            if passed:
                policies[policy]['eval_passes_with_native'] += 1
            name = ('true_positive' if native else 'false_positive') if passed else ('false_negative' if native else 'true_negative')
            matrix[name] += 1
            if name == 'false_positive':
                policies[policy]['false_passes'] += 1
        if cell.get('reason') == 'eval_passed' and versions:
            last = versions[-1]
            if last.get('mode') == 'vision' and last.get('passed') is True and last.get('native_success') is False:
                false_stops.append({'cell': cell['key'], 'policy_id': policy, 'version': last['version']})
    for policy, row in policies.items():
        row['false_pass_rate'] = row['false_passes'] / row['native_failures_with_eval'] if row['native_failures_with_eval'] else None
        row['false_pass_fraction_all'] = row['false_passes'] / row['comparable_verdicts'] if row['comparable_verdicts'] else None
        row['false_pass_fraction_of_eval_passes'] = row['false_passes'] / row['eval_passes_with_native'] if row['eval_passes_with_native'] else None
    return {'confusion_matrix': matrix, 'false_passes': matrix['false_positive'],
            'false_failures': matrix['false_negative'], **counts,
            'by_policy': dict(sorted(policies.items())), 'false_stops': false_stops,
            'false_stop_count': len(false_stops)}
