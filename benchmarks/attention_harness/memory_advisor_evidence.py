"""Read-only preflight for Advisor sources without planner request records."""
import json
import math
import re

from .core.dev_store import DevConflict
from .dev_v2_loop import checked
from .memory_provider_evidence import verify_provider, frozen_provider, verify_codex_record
from .dev_feedback_evidence import verify_feedback


ASKING_POLICIES = {'reactive_help', 'retry_k_then_ask',
                   'budget_matched_random_escalation', 'trace_aware_hint_only',
                   'full_trace_aware_attention_planner'}


def require(value, reason):
    if not value:
        raise DevConflict(reason)


def read(ref):
    return json.loads(checked(ref).read_bytes())


def verify_advisor_job(store, cell, version, manifest, config):
    """Mirror the service's job evidence checks before replay or source export.

    The service independently repeats these checks against sealed catalogs.
    No historical ledger is initialized, recovered, or modified here.
    """
    selection = manifest.get('selection')
    require(isinstance(selection, dict) and not any(
        value.get('planner_features') for value in (manifest, selection, config, cell)),
        'Original adopted help request is missing or ambiguous: frozen non-planner selection required')
    require(cell['policy_id'] in ASKING_POLICIES, 'Advisor source requires an asking policy')
    key = cell['key']; number = version['version']
    require(config == manifest['configs'][f"{config['task_id']}:{config['seed']}"]
            and any(c['key'] == key and c['policy_id'] == cell['policy_id']
                    and c['task_id'] == config['task_id'] and c['seed'] == config['seed']
                    for c in manifest['cells']), 'Original frozen configuration or policy differs')
    with store.tx() as db:
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='v2_planner_requests'").fetchone():
            requests = [json.loads(p) for p, in db.execute(
                'SELECT payload FROM v2_planner_requests WHERE cell=?', (key,))]
            require(not any(r.get('version') == number and r.get('request_type') == 'advisor'
                            for r in requests), 'Original adopted help request is missing or ambiguous')

        def job(kind, n):
            rows = db.execute('SELECT id,cell,version,kind,state,payload FROM v2_jobs '
                              'WHERE cell=? AND version=? AND kind=?', (key, n, kind)).fetchall()
            require(len(rows) == 1 and rows[0][4] == 'settled', 'Original job must be exact and settled')
            jid, c, v, k, _, raw = rows[0]; result = json.loads(raw)
            require((result['job_id'], result['cell'], result['version'], result['kind']) == (jid, c, v, k),
                    'Original job payload/columns disagree')
            require(all(type(result.get(k)) in (int, float) and math.isfinite(result[k])
                        for k in ('claimed_at', 'settled_at'))
                    and result['claimed_at'] <= result['settled_at'], 'Original job timing invalid')
            require(isinstance(result.get('actual'), dict) and all(
                type(x) in (int, float) and math.isfinite(x) and x >= 0
                for x in result['actual'].values()), 'Original job actual costs invalid')
            return result

        advisor = job('advisor', number); model = job('model', number)
        job('execute', number - 1)
    require(all(advisor['actual'].get(k) == 1 for k in ('advisor_calls', 'help_count', 'help_credits'))
            and model['actual'].get('dev_calls') == 1, 'Committed assistance/model cost required')
    require(advisor['settled_at'] <= model['claimed_at'], 'Advisor answer settled after model claim')

    ar, ao, ac = provider(advisor, manifest, config)
    mr, mo, mc = provider(model, manifest, config)
    help_value = read(version['help'])
    require(help_value.get('provider') == 'advisor' and 'rejected' not in help_value
            and isinstance(ao['content'], str) and ao['content'].strip()
            and help_value.get('content') == ao['content']
            and all(help_value.get(k) == ac[k] for k in ('request', 'response', 'usage')),
            'Answered original Advisor guidance required')
    require(version['model_input'] == mc['request'] and version['model_output'] == mc['response'],
            'Model input/output lineage mismatch')
    packet = json.loads(mr['messages'][-1]['content'])
    advisor_packet = json.loads(ar['messages'][-1]['content'])
    projection = verify_generation_feedback(cell, version, manifest, [('dev', packet), ('advisor', advisor_packet)])
    help_input = packet.get('attention_control', {}).get('help', {})
    require(help_input.get('provider') == 'advisor' and help_input.get('content') == ao['content'],
            'Guidance not actually present in generation input')
    generated = mo['content'].strip()
    if generated.startswith('```'):
        generated = re.sub(r'^```(?:json)?\s*|\s*```$', '', generated)
    # Adoption is the guidance in the next generation input plus the adopted
    # version's source matching that generation's model output byte for byte.
    require(json.loads(generated)['source'].encode() == checked(version['source']).read_bytes(),
            'Adopted source differs from actual model output')
    return dict(identity_evidence(ac, mc), feedback_projection=projection)


def verify_generation_feedback(cell, version, manifest, packets):
    number = version['version'] - 1
    previous = [v for v in cell['versions'] if v['version'] == number]
    failed = [j for j in cell['jobs'] if j['version'] == number and j['kind'] == 'execute']
    require(len(previous) == len(failed) == 1 and failed[0]['state'] == 'settled',
            'Original failed version/execution missing or ambiguous')
    source = checked(previous[0]['source']).read_bytes().decode()
    feedback = read(failed[0]['receipt']['public_feedback'])

    def eval_job():
        jobs = [j for j in cell['jobs'] if j['version'] == number and j['kind'] == 'eval']
        require(len(jobs) == 1, 'sealed Eval job missing or ambiguous')
        return jobs[0]

    modes = []
    for audience, packet in packets:
        require(packet['previous_complete_source'] == source,
                'Online help source/public failed trace mismatch')
        modes.append(verify_feedback(packet['public_feedback'], feedback, audience=audience, version=previous[0],
                     cell=cell, manifest=manifest, read=read, eval_job=eval_job, require=require))
    return 'with_eval_review' if 'with_eval_review' in modes else 'exact'


def provider(job, manifest, config):
    receipt = job['receipt']; request = read(receipt['request'])
    output = read(receipt['response']); wire = read(receipt['provider_response'])
    identity = verify_provider(request, output, wire, receipt, job['actual'],
                               manifest, config, job['kind'], require)
    return request, output, dict(receipt, provider_identity=identity)


def identity_evidence(advisor, model):
    identities = dict(advisor=advisor['provider_identity'], model=model['provider_identity'])
    return (dict(provider_identity='codex_exec', provider_identities=identities)
            if 'codex_exec' in identities.values() else {})


def verify_source_providers(cell, version, manifest, config):
    """Also check provider identities for planner and visual source preflight."""
    jobs = {}
    for kind in ('model', 'advisor'):
        matches = [j for j in cell['jobs'] if j['version'] == version['version'] and j['kind'] == kind]
        require(len(matches) == 1 and matches[0]['state'] == 'settled', 'Original job must be exact and settled')
        jobs[kind] = matches[0]
    request, _, model = provider(jobs['model'], manifest, config)
    packets = [('dev', json.loads(request['messages'][-1]['content']))]
    help_value = read(version['help'])
    if help_value.get('provider') == 'advisor':
        request, _, advisor = provider(jobs['advisor'], manifest, config)
        packets.append(('advisor', json.loads(request['messages'][-1]['content'])))
    else:
        source, locked = frozen_provider(manifest, config, 'advisor', require)
        advisor = dict(provider_identity='parcc_http')
        if source == 'codex':
            hint = read(jobs['advisor']['receipt']['advisor_answer'])
            verify_codex_record(hint, locked, require)
            advisor['provider_identity'] = 'codex_exec'
    projection = verify_generation_feedback(cell, version, manifest, packets)
    return dict(identity_evidence(advisor, model), feedback_projection=projection)
