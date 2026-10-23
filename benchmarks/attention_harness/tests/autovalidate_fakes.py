"""Controlled synthetic original receipts. Never imported by production workers."""
import hashlib,json,sqlite3,time
from pathlib import Path
from types import SimpleNamespace
from attention_memory_service.memory_service import MemoryService
from attention_memory_service.memory_service_api import create_app
from attention_memory_service.dev_v2 import digest,encoded,public_trace_projection,stop_position,SCOPE
from openarm365_dev_v2.scene_contract import fingerprint,validate
from benchmarks.attention_harness.memory_validation_generation import exposure_receipt
from benchmarks.attention_harness.memory_validation_worker import freeze_plan,atomic
from benchmarks.attention_harness.memory_independent_validator import validate_arm

# Self-contained synthetic fixtures. No imports from another repository's tests.
import inspect
from fastapi import HTTPException
from starlette.requests import Request
from starlette.routing import Match
API='online-key-123456789'
IMPORTER='importer-key-123456789'
OPERATOR='operator-key-123456789'
MODEL={'provider':'codex','model':'controlled_test_dev'}
PROVISIONAL_SIGNATURE={'public_error_class':'RuntimeError: PlanningRejected','operation':'left_arm.move_delta'}
SIGNATURE={'public_error_class':'RuntimeError: TimeoutError','operation':'left_arm.move_delta'}

def require_m8(*features):
    import pytest
    from attention_memory_service.dev_v2 import DevV2Evidence
    for feature in features:
        if feature in ('auto_validation','scene_contract'):
            pytest.importorskip('attention_memory_service.'+feature,reason='needs the M-8 Memory service')
        else:
            supported=hasattr(DevV2Evidence,{'arms':'_validation_generation','source':'_source_set_catalog'}[feature]) if feature in ('arms','source') else hasattr(MemoryService,'record_dev_v2_failed_check')
            if not supported:pytest.skip('needs the M-8 Memory service: '+feature)


def sync_request(app, path, *, method='GET', payload=None, key=None, importer_key=None, operator_key=None):
    from urllib.parse import unquote
    path = unquote(path)
    headers = {'x-memory-service-key': key, 'x-memory-importer-key': importer_key, 'x-memory-operator-key': operator_key}
    scope = {'type': 'http', 'method': method, 'path': path, 'root_path': '', 'headers': []}
    for route in app.routes:
        matched, child = route.matches(scope)
        if matched is not Match.FULL:
            continue
        scope.update(child)

        def invoke(dependant):
            kwargs = {}
            for field in dependant.header_params:
                kwargs[field.name] = headers.get(field.alias.lower())
            for field in dependant.path_params:
                kwargs[field.name] = scope['path_params'][field.alias]
            for field in dependant.body_params:
                annotation = inspect.signature(dependant.call).parameters[field.name].annotation
                annotation = inspect.get_annotations(dependant.call, eval_str=True)[field.name]
                kwargs[field.name] = annotation.model_validate(payload) if hasattr(annotation, 'model_validate') else payload
            if dependant.request_param_name:
                kwargs[dependant.request_param_name] = Request(scope)
            for dependency in dependant.dependencies:
                result = invoke(dependency)
                if dependency.name:
                    kwargs[dependency.name] = result
            return dependant.call(**kwargs)
        try:
            return 200, invoke(route.dependant)
        except HTTPException as exc:
            return exc.status_code, {'detail': exc.detail}
    return 404, {'detail': 'not found'}

def sha(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()

def feedback(error, events):
    return json.dumps({'schema_version': 'attentionbench.dev-public-feedback.v1',
                       'execution_status': 'failed', 'public_error': error,
                       'action_trace': [{'source': s, 'operation': o, 'status': st} for s, o, st in events]})

ORIGIN=feedback('RuntimeError: PlanningRejected: cuRoboV2 planning rejected: goal',[('sensors','get_observation','completed'),('left_arm','move_delta','failed')])
CHANGED=feedback('RuntimeError: TimeoutError: timed out',[('sensors','get_observation','completed'),('right_arm','move_delta','failed')])


def context(**changes):
    value = {key: f'fake-{key}' for key in SCOPE}
    value.update(sdk_sha256='a' * 64, sdk_interface_sha256='9' * 64, perception_mode='sim_gt', seed=102,
                 camera_names=['left', 'right'], task_prompt='Move fake cup',
                 scene_contract_ref={'uri': 'fixture://scene', 'sha256': 'b' * 64})
    value.update(changes)
    return value

def payload(memory_id='p-1', help_text=CHANGED, reason='signature_changed', **changes):
    digests = dict(origin_public_feedback=sha(ORIGIN), origin_source='1' * 64, help='2' * 64,
                   help_model_input='3' * 64, help_source='4' * 64, help_public_feedback=sha(help_text))
    origin = dict(experiment_id='devexp-fake', cell='cell-1', policy_id='reactive_help',
                  origin_version=1, help_version=2, provider='advisor', effective_reason=reason,
                  eval_reason_class='planner_rejected', digests=digests)
    value = dict(actor='importer', memory_id=memory_id, context=context(), guidance='Lift before moving left.',
                 failure_signature=dict(PROVISIONAL_SIGNATURE), origin=origin, origin_public_feedback_text=ORIGIN,
                 help_public_feedback_text=help_text, title='Avoid PlanningRejected on left_arm.move_delta')
    value.update(changes)
    return value

def catalog(root, role='executor'):
    root.mkdir(parents=True, exist_ok=True)
    file = root / 'evidence.json'
    file.write_text('{}')
    return dict(role=role, root=str(root), context=context(), files={file.name: digest(file.read_bytes())})

def registration(tmp_path):
    require_m8("auto_validation")
    executor, validator = tmp_path / 'executor', tmp_path / 'validator'
    executor.mkdir(); validator.mkdir()
    roots = dict(executor=[str(executor)], validator=[str(validator)])
    app = create_app(tmp_path / 'store.sqlite', api_key=API, operator_key=OPERATOR,
                     importer_key=IMPORTER, dev_v2_registration_roots=roots)
    return app, roots

def outcome(service, mid, index, text=CHANGED, ctx=None, version=None):
    memory = service.store.get_memory(mid)
    grant = service.authorize_dev_use(memory_id=mid, memory_version=version or memory.version,
        development_id=f'{mid}-use{index}:v1', context=ctx or context(),
        source_policy_sha256='c' * 64, now=time.time())
    args = dict(grant_id=grant['grant_id'], development_id=grant['development_id'],
                public_feedback_text=text, public_feedback_sha256=sha(text))
    return service.record_provisional_outcome(**args), args

class Fixture:
    def __init__(self, tmp, interface=False):
        self.interface = interface
        self.root = tmp / 'executor'; self.root.mkdir()
        self.validator = tmp / 'validator'; self.validator.mkdir()
        self.catalogs = {}
        sdk_file = self.root / 'openarm365_dev_v2/sdk.py'
        sdk_file.parent.mkdir(); sdk_file.write_text('# controlled_test SDK\n')
        files = {str(sdk_file): digest(sdk_file.read_bytes())}
        planner = self.write(self.root / 'planner.json', {'evidence_kind': 'controlled_test'})
        model = self.write(self.root / 'model.json', {'evidence_kind': 'controlled_test'})
        files.update({planner['uri']: planner['sha256'], model['uri']: model['sha256']})
        self.sdk = {'contract': 'controlled_test', 'prompt': 'controlled_test', 'implementation': {'openarm365_dev_v2/sdk.py': files[str(sdk_file)]}, 'planner_lock': planner['sha256'], 'robot_model_identity': {model['uri']: model['sha256']}}
        self.sdk_sha = digest(json.dumps(self.sdk, sort_keys=True, ensure_ascii=False, allow_nan=False).encode())
        lock = {'files': files, 'source_root': str(self.root), 'planner_lock': {'path': planner['uri'], 'sha256': planner['sha256']}, 'model_sources': {model['uri']: model['sha256']}, 'schema': 'attentionbench.dev-identity-lock.v2', 'formal_eligible': False, 'source_sets': {str(sdk_file.parent): [str(sdk_file)]}}
        self.lock = self.write(self.root / 'lock.json', lock)
        self.records = {}

    @staticmethod
    def write(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((encoded(value) + '\n').encode() if not isinstance(value, str) else value.encode())
        return {'uri': str(path), 'sha256': digest(path.read_bytes())}

    def make(self, name, seed=101, variant=0, success=False, code='def policy(sdk):\n    pass\n', exposure='none', memory_id=None, failure=False, completed_calls=None, visual=False, scene=None):
        directory = self.root / name; directory.mkdir()
        config = {'task_id': 'pick', 'seed': seed, 'robot': 'OpenArm365', 'controller': 'test_controller', 'perception_mode': 'sim_gt', 'scene_id': f'scene-{variant}', 'object_set_id': f'objects-{variant}', 'camera_names': [f'camera-{variant}'], 'task_prompt': f'pick cup variant {variant}', 'camera_width': 64, 'camera_height': 64, 'randomize_cameras': False, 'official_env': 'PickPlaceCounterToSink', 'layout_id': 11 + variant, 'style_id': 34, 'base_mode': 'fixed', 'evidence_kind': 'controlled_test', 'sdk_call_limit': 80, 'horizon': 2400, 'attempt_wall_seconds': 480}
        config.update(getattr(self,'config_overrides',{}))
        if visual:
            config['advisor_proxy'] = {'enabled': True, 'mode': 'vision', 'model': 'controlled_test_visual_advisor'}
        if scene is not None:
            config['scene_contract'] = scene['contract']
        camera = {k: config[k] for k in ('camera_names', 'camera_width', 'camera_height', 'randomize_cameras')}
        ctx = {k: config[k] for k in ('task_id', 'robot', 'controller', 'perception_mode', 'scene_id', 'object_set_id', 'camera_names', 'task_prompt')}
        ctx.update(benchmark='AttentionBench', suite='robocasa365', namespace='controlled_test_only', sdk_sha256=self.sdk_sha, camera_config_id='robocasa365:' + digest(json.dumps(camera, sort_keys=True, ensure_ascii=False).encode()), task_variant_id=f"PickPlaceCounterToSink:layout{11+variant}:style34:fixed")
        if self.interface:
            from attention_memory_service.applicability import interface_digest
            ctx['sdk_interface_sha256'] = interface_digest(self.sdk)
        budget = getattr(self,'budget_override',{'assistance_credits': 1})
        config_ref = self.write(directory / 'config.json', config)
        source = self.write(directory / 'policy.py', code)
        ledger = directory / 'ledger.sqlite'
        manifest = {'schema': 'attentionbench.dev-experiment.v3', 'experiment_id': name, 'ledger_path': str(ledger), 'identity_lock': {'path': self.lock['uri'], 'sha256': self.lock['sha256']}, 'configs': {f'pick:{seed}': config}, 'cells': [{'key': name, 'task_id': 'pick', 'seed': seed, 'policy_id': 'full_trace_aware_attention_planner'}], 'evidence_kind': 'controlled_test'}
        manifest.update(getattr(self,'manifest_overrides',{}))
        mref = self.write(directory / 'manifest.json', manifest)
        record = {'version': 1, 'formal_eligible': False, 'valid': True, 'source': source, 'manifest': mref, 'config_sha256': config_ref['sha256']}
        vref = self.write(directory / 'version.json', record)
        binding = {'run_id': 'run:' + name, 'attempt_id': 'attempt:' + name + ':0', 'suite': 'robocasa365', 'task_id': 'pick', 'seed': seed, 'policy_sha256': source['sha256'], 'config_sha256': config_ref['sha256']}
        trace_value = {**binding, 'status': 'completed', 'evidence_kind': 'controlled_test'}
        if failure or completed_calls is not None:
            trace_value['sdk_events'] = [{'operation': 'get_observation', 'source': 'sensors', 'status': 'completed'} for _ in range(1 if completed_calls is None else completed_calls)]
            if failure:
                trace_value['error'] = 'RuntimeError: TimeoutError'
                trace_value['sdk_events'].append({'operation': 'move_delta', 'source': 'left_arm', 'status': 'failed'})
        trace = self.write(directory / 'trace.json', trace_value)
        native_value = {'run_id': binding['run_id'], 'attempt_id': binding['attempt_id'], 'status': 'completed', 'evaluated': True, 'native_success': success, 'evidence_kind': 'controlled_test'}
        native = self.write(directory / 'native.json', native_value)
        receipt = self.write(directory / 'sandbox.json', {**{k: binding[k] for k in ('run_id', 'attempt_id')}, 'sdk_call_budget': {'dispatched': 1}, 'control_steps': 1, 'elapsed_seconds': 1.0, 'evidence_kind': 'controlled_test'})
        safety = self.write(directory / 'safety.json', {**{k: binding[k] for k in ('run_id', 'attempt_id')}, 'evidence_kind': 'controlled_test'})
        bundle = {**binding, 'schema_version': 'attentionbench.formal-runner-result.v1', 'channel': 'dev_v2', 'formal_eligible': False, 'dev_version': 1, 'policy_id': 'full_trace_aware_attention_planner', 'config_lock': config_ref, 'status': 'completed', 'native_success': success, 'native_evaluator': native_value, 'infrastructure_failure': False, 'outcome_unknown': False, 'sandbox': {'process_isolated': True, 'sdk_rpc_only': True, 'deadline_enforced': True, 'action_cancellation_verified': True}, 'scene_condition': {'matched': True}, 'artifacts': {'trace': trace, 'native_result': native, 'sandbox_receipt': receipt, 'safety': safety}, 'evidence_kind': 'controlled_test'}
        if visual:
            bundle['artifact_dir'] = str(directory)
        if scene is not None:
            bundle['scene_condition'] = scene['validation']
        bref = self.write(directory / 'bundle.json', bundle)
        feedback = self.write(directory / 'feedback.json', {**public_trace_projection(trace_value), 'evidence_kind': 'controlled_test'})
        validation_input = self.write(directory / 'validation-input.json', {'memory_ids': [] if exposure == 'none' else [memory_id], 'exposure': exposure, 'context': ctx, 'policy_sha256': source['sha256'], 'config_sha256': config_ref['sha256']})
        cell = {'experiment_id': name, 'policy_id': bundle['policy_id'], 'budget': budget}
        job = {'job_id': name + '-execute', 'cell': name, 'version': 1, 'kind': 'execute', 'claimed_at': 1.0, 'settled_at': 2.0, 'actual': {'executions': 1, 'sdk_calls': 1, 'control_steps': 1, 'execution_seconds': 1.0}, 'receipt': {'bundle': bref, 'trace': trace, 'native_result': native, 'execution_status': 'completed', 'public_feedback': feedback}}
        with sqlite3.connect(ledger) as db:
            db.executescript('CREATE TABLE v2_cells(id TEXT,exp TEXT,payload TEXT); CREATE TABLE v2_experiments(id TEXT,payload TEXT); CREATE TABLE v2_versions(cell TEXT,version INTEGER,payload TEXT); CREATE TABLE v2_jobs(id TEXT,cell TEXT,version INTEGER,kind TEXT,state TEXT,payload TEXT); CREATE TABLE v2_planner_requests(id TEXT,cell TEXT,payload TEXT);')
            db.execute('INSERT INTO v2_cells VALUES (?,?,?)', (name, name, encoded(cell)))
            db.execute('INSERT INTO v2_experiments VALUES (?,?)', (name, encoded({'manifest': mref})))
            db.execute('INSERT INTO v2_versions VALUES (?,?,?)', (name, 1, encoded(record)))
            db.execute('INSERT INTO v2_jobs VALUES (?,?,?,?,?,?)', (job['job_id'], name, 1, 'execute', 'settled', encoded(job)))
        descriptor = {'schema_version': 'attentionbench.dev-v2-import.v1', 'cell': name, 'version': 1, 'context': ctx, 'manifest': mref, 'config': config_ref, 'version_record': vref, 'sdk_identity': self.sdk, 'ledger': {'uri': str(ledger), 'sha256': digest(ledger.read_bytes())}, 'execute_job_id': job['job_id'], 'validation_input': validation_input, 'permission_scope': {'run_id': binding['run_id'], 'cell': name, 'code_version': 1, 'code_sha256': source['sha256'], 'config_sha256': digest(json.dumps(config, sort_keys=True, ensure_ascii=False).encode()), 'action': 'sensor_only', 'action_version': 1, 'scope': {'mode': 'controlled_test_no_robot'}}}
        if scene is not None:
            descriptor['scene_state'] = self.write(directory / 'scene-condition.json',
                                                   {'state': scene['state'], 'validation': scene['validation']})
        self.write(directory / 'execution.json', descriptor)
        vd = self.validator / name
        monitor = self.write(vd / 'monitor.json', {'schema_version': 'attentionbench.safety-monitor.v1', 'source': 'independent_safety_monitor', 'attempt_id': binding['attempt_id'], 'unsafe_attempts': 0, 'events': [{'kind': 'controlled_test_sample'}], 'violations': [], 'evidence_kind': 'controlled_test'})
        obs = [self.write(vd / f'obs{i}.json', {'robot': {'position': [0, 0, 0]}, 'perception': {'values': {'cup': [1, 2, 3]}}, 'evidence_kind': 'controlled_test'}) for i in (1, 2)]
        comparison = {'budget': budget, 'developer_model': 'controlled_test_model', 'execution_config': {k: v for k, v in config.items() if k != 'seed'}}
        comp = self.write(vd / 'comparison.json', comparison)
        costs = {'credits': 0, 'tokens': 0, 'seconds': 1.0, 'gpu_seconds': 0.0}
        verdict = {'schema_version': 'attentionbench.dev-v2-independent-validation.v1', 'purpose': 'offline_memory_validation', 'execution_import_id': name, 'bindings': {k: binding[k] for k in ('run_id', 'attempt_id', 'policy_sha256', 'config_sha256', 'task_id', 'seed')}, 'bundle': bref, 'native_result': native, 'trace': trace, 'native_success': success, 'checks': {'native_evaluation': True, 'safety': True, 'scene_reproduction': True, 'permissions': True, 'source_identity': True}, 'scene_replay_observations': obs, 'scene_replay_bindings': {'task_id': 'pick', 'seed': seed, 'config_sha256': config_ref['sha256']}, 'safety': monitor, 'permission_scope': descriptor['permission_scope'], 'costs': costs, 'validation_config': comparison, 'evidence_kind': 'controlled_test'}
        if scene is not None:
            verdict['scene_replay_states'] = [self.write(vd / f'state{i}.json', state)
                                               for i, state in enumerate(scene['replays'])]
        self.write(vd / 'verdict.json', verdict)
        trial = {'schema_version': 'attentionbench.dev-v2-trial.v1', 'execution_import_id': name, 'validation_descriptor': name + '/verdict.json', 'exposure': exposure, 'memory_id': memory_id, 'costs': costs, 'developer_model': 'controlled_test_model', 'budget': budget, 'validation_config': comp, 'evidence_kind': 'controlled_test'}
        self.write(vd / 'trial.json', trial)
        value = dict(name=name, context=ctx, config=config, descriptor=descriptor, record=record, bundle=bundle, verdict=verdict, trial=trial, cell=cell)
        self.records[name] = value
        return value

    def add_advice(self, failed, failure_signature=None):
        name = failed['name']; directory = self.root / name; d = failed['descriptor']; ledger = Path(d['ledger']['uri'])
        code = 'def policy(sdk):\n    # controlled_test repair\n    pass\n'
        source = self.write(directory / 'repair.py', code)
        previous = Path(failed['record']['source']['uri']).read_text()
        feedback = json.loads((directory / 'feedback.json').read_text())
        usage = {'total_tokens': 7, 'prompt_tokens': 4, 'completion_tokens': 3}
        hint = 'Align the gripper before closing it. controlled_test'
        ar_packet = {'previous_complete_source': previous, 'public_feedback': feedback}
        mr_packet = {**ar_packet, 'attention_control': {'help': {'provider': 'advisor', 'content': hint}}}
        jobs = []
        for kind, packet, output in [('advisor', ar_packet, hint), ('model', mr_packet, json.dumps({'source': code, 'reason': 'controlled_test repair'}))]:
            req = self.write(directory / f'{kind}-input.json', {'messages': [{'role': 'user', 'content': encoded(packet)}]})
            resp = self.write(directory / f'{kind}-output.json', {'content': output, 'usage': usage})
            wire = self.write(directory / f'{kind}-wire.json', {'http_status': 200, 'request_id': 'controlled_test_' + kind, 'body': {'usage': usage, 'choices': [{'message': {'content': output}}]}})
            receipt = {'request': req, 'response': resp, 'provider_response': wire, 'usage': usage}
            job = {'job_id': name + '-' + kind, 'cell': name, 'version': 2, 'kind': kind, 'claimed_at': 3.0 if kind == 'advisor' else 5.0, 'settled_at': 4.0 if kind == 'advisor' else 6.0, 'actual': {'provider_tokens': 7, 'input_tokens': 4, 'output_tokens': 3, **({'advisor_calls': 1, 'help_count': 1, 'help_credits': 1} if kind == 'advisor' else {'dev_calls': 1})}, 'receipt': receipt}
            jobs.append(job)
        ac, mc = [j['receipt'] for j in jobs]
        help_ref = self.write(directory / 'help.json', {'provider': 'advisor', 'content': hint, 'request': ac['request'], 'response': ac['response'], 'usage': usage})
        record = {'version': 2, 'formal_eligible': False, 'valid': True, 'source': source, 'manifest': d['manifest'], 'config_sha256': d['config']['sha256'], 'help': help_ref, 'model_input': mc['request'], 'model_output': mc['response']}
        vref = self.write(directory / 'version2.json', record)
        row = {'created_at': 3.1, 'responded_at': 4.1, 'deadline_epoch': 5.0, 'binding': {'cell': name, 'version': 2, 'input_sha256': digest(json.dumps([{'role': 'user', 'content': encoded(ar_packet)}], sort_keys=True, ensure_ascii=False).encode()), 'source_sha256': failed['record']['source']['sha256'], 'policy_id': failed['cell']['policy_id'], 'config_sha256': d['config']['sha256']}, 'cell': name, 'version': 2, 'request_type': 'advisor', 'state': 'answered', 'accepted': True, 'adopted': True, 'response': ac['response'], 'adoption': {'model_input': mc['request'], 'result_source': source}}
        with sqlite3.connect(ledger) as db:
            db.execute('INSERT INTO v2_versions VALUES (?,?,?)', (name, 2, encoded(record)))
            db.execute('INSERT INTO v2_planner_requests VALUES (?,?,?)', ('help-' + name, name, encoded(row)))
            for j in jobs:
                db.execute('INSERT INTO v2_jobs VALUES (?,?,?,?,?,?)', (j['job_id'], name, 2, j['kind'], 'settled', encoded(j)))
        d['ledger']['sha256'] = digest(ledger.read_bytes())
        self.write(directory / 'execution.json', d)
        source_d = {**d, 'version': 2, 'version_record': vref, 'failed_execution_import_id': name, 'validator_id': 'validator-' + name, 'validation_descriptor': name + '/verdict.json', 'advisor_job_id': jobs[0]['job_id'], 'model_job_id': jobs[1]['job_id'], 'request_id': 'help-' + name}
        if failure_signature is not None:
            source_d['failure_signature'] = failure_signature
        self.write(directory / 'source.json', source_d)
        return source_d

    def seal(self):
        for name, r in self.records.items():
            for role, root, cid in [('executor', self.root, name), ('validator', self.validator, 'validator-' + name)]:
                self.catalogs[cid] = {'root': str(root), 'role': role, 'context': r['context'], 'ledger_origin': r['descriptor']['ledger']['uri'], 'files': {str(p.relative_to(root)): digest(p.read_bytes()) for p in root.rglob('*') if p.is_file()}}
        return self.catalogs

    def add_result_model(self, result, grant, authority):
        """Controlled generation receipt for a previously issued real Service grant."""
        import time
        name = result['name']; directory = self.root / name; d = result['descriptor']
        previous = 'controlled_test previous code\n'
        code = Path(result['record']['source']['uri']).read_text()
        entry = {'memory_id': grant['memory_id'], 'version': grant['memory_version'], 'content': grant['guidance'], 'grant_id': grant['grant_id'], 'context': grant['context'], 'development_id': grant['development_id'], 'source_policy_sha256': grant['source_policy_sha256'], 'authority': authority}
        packet = {'previous_complete_source': previous, 'attention_control': {'memory': [entry]}}
        usage = {'total_tokens': 7, 'prompt_tokens': 4, 'completion_tokens': 3}
        req = self.write(directory / 'model-input.json', {'messages': [{'role': 'system', 'content': 'Do not access private attributes.'}, {'role': 'user', 'content': encoded(packet)}]})
        content = json.dumps({'source': code, 'reason': 'controlled_test'})
        resp = self.write(directory / 'model-output.json', {'content': content, 'usage': usage})
        wire = self.write(directory / 'model-wire.json', {'http_status': 200, 'request_id': 'controlled_test_model', 'body': {'usage': usage, 'choices': [{'message': {'content': content}}]}})
        receipt = {'request': req, 'response': resp, 'provider_response': wire, 'usage': usage}
        now = time.time()
        job = {'job_id': name + '-model', 'cell': name, 'version': 1, 'kind': 'model', 'claimed_at': now, 'settled_at': now + .01, 'actual': {'dev_calls': 1, 'provider_tokens': 7, 'input_tokens': 4, 'output_tokens': 3}, 'receipt': receipt}
        record = {**result['record'], 'model_input': req, 'model_output': resp}
        self.write(directory / 'version.json', record)
        d['version_record'] = {'uri': str(directory / 'version.json'), 'sha256': digest((directory / 'version.json').read_bytes())}
        with sqlite3.connect(d['ledger']['uri']) as db:
            db.execute('UPDATE v2_versions SET payload=? WHERE version=1', (encoded(record),))
            db.execute('INSERT INTO v2_jobs VALUES (?,?,?,?,?,?)', (job['job_id'], name, 1, 'model', 'settled', encoded(job)))
            execute = json.loads(db.execute("SELECT payload FROM v2_jobs WHERE kind='execute'").fetchone()[0])
            execute.update(claimed_at=now + .02, settled_at=now + .03)
            db.execute("UPDATE v2_jobs SET payload=? WHERE kind='execute'", (encoded(execute),))
        d['ledger']['sha256'] = digest(Path(d['ledger']['uri']).read_bytes())
        d['model_job_id'] = job['job_id']
        self.write(directory / 'execution.json', d)

    def bind_validation(self, record, grant, *, consume=True):
        """Synthetic pre-dispatch test event; never a real execution receipt."""
        import time
        d = record['descriptor']; name = record['name']
        ref = d['validation_input']
        value = json.loads(Path(ref['uri']).read_bytes())
        value['validation_grant_id'] = grant['grant_id']
        d['validation_input'] = self.write(Path(ref['uri']), value)
        # Synthetic native trace consumption proof, built only for test fixtures.
        trace_ref = record['bundle']['artifacts']['trace']
        trace = json.loads(Path(trace_ref['uri']).read_bytes())
        binding = grant['binding']
        if consume:
            trace['memory_validation'] = {'validation_grant_id': grant['grant_id'], 'input_sha256': d['validation_input']['sha256'], 'memory_ids': value['memory_ids'], 'exposure': value['exposure'], 'memory_version': binding['memory_version'] if value['memory_ids'] else None, 'guidance_sha256': digest(b'Align the gripper before closing it. controlled_test') if value['memory_ids'] else None, 'consumed_before_policy': True}
        record['bundle']['artifacts']['trace'] = self.write(Path(trace_ref['uri']), trace)
        bundle_ref = self.write(self.root / name / 'bundle.json', record['bundle'])
        record['verdict']['bundle'] = bundle_ref
        record['verdict']['trace'] = record['bundle']['artifacts']['trace']
        self.write(self.validator / name / 'verdict.json', record['verdict'])
        now = time.time()
        event = {'cell': name, 'version': 1, 'validation_input': d['validation_input'], 'validation_grant_id': grant['grant_id'], 'source': record['record']['source'], 'config': d['config']}
        with sqlite3.connect(d['ledger']['uri']) as db:
            db.execute('CREATE TABLE v2_events(seq INTEGER PRIMARY KEY,at REAL,kind TEXT,payload TEXT)')
            db.execute('INSERT INTO v2_events(at,kind,payload) VALUES (?,?,?)', (now, 'memory_validation_dispatch', encoded(event)))
            job = json.loads(db.execute("SELECT payload FROM v2_jobs WHERE kind='execute'").fetchone()[0])
            job.update(claimed_at=now + .01, settled_at=now + .02)
            job['receipt'].update(bundle=bundle_ref, trace=record['bundle']['artifacts']['trace'])
            db.execute("UPDATE v2_jobs SET payload=? WHERE kind='execute'", (encoded(job),))
        d['ledger']['sha256'] = digest(Path(d['ledger']['uri']).read_bytes())
        self.write(self.root / name / 'execution.json', d)
        record['trial']['validation_grant_id'] = grant['grant_id']
        self.write(self.validator / name / 'trial.json', record['trial'])

def grant_args(r, plan):
    return dict(memory_id=plan['memory_id'], cell=r['name'], version=1, seed=101, context=r['context'],
                policy_sha256=r['record']['source']['sha256'], config_sha256=r['descriptor']['config']['sha256'],
                exposure=r['trial']['exposure'], origin_source_sha256=plan['origin_source_sha256'])

def generated_prepare(f, service, plan, name, *, fault=None, unsafe=False, checks=None):
    r = f.records[name]
    args = grant_args(r, plan)
    if fault == 'granted_sha': args['policy_sha256'] = '0' * 64
    grant = service.authorize_dev_v2_validation(**args)
    f.bind_validation(r, grant)
    d, record = r['descriptor'], r['record']
    directory = f.root / name
    memory = service.store.get_memory(plan['memory_id'])
    treatment = r['trial']['exposure']=='candidate_validation'
    parent = f.records['failed']['record']['source']
    if fault == 'wrong_parent':
        parent = f.write(directory / 'wrong-parent.py', 'def policy(sdk):\n    return\n')
    notes = [{'content': memory.guidance}] if treatment else []
    if fault in {'control_note', 'second_note'}:
        notes.append({'content': 'other provisional or run note'})
    if fault == 'other_note':
        notes = [{'content': 'other guidance'}]
    if fault == 'old_guidance':
        notes = [{'content': 'guidance from an older version'}]
    packet = {'previous_complete_source': Path(parent['uri']).read_text(), 'attention_control': {'memory': notes}}
    if fault == 'extra_section':
        packet['run_notes'] = [{'content': 'run hint'}]
    if fault == 'help_note':
        packet['attention_control']['help'] = {'content': 'task reference'}
    model = {**plan.get('dev_model',MODEL), **({'model': 'another_dev'} if fault == 'model' else {})}
    if fault == 'provider':
        model['provider'] = 'another_provider'
    request = f.write(directory / 'model-input.json', dict(model_source=model['provider'], model=model['model'],
        messages=[{'role': 'system', 'content': 'Write a public SDK policy.'},
                  {'role': 'user', 'content': encoded(packet)}]))
    code = Path(record['source']['uri']).read_text()
    if fault == 'generated_sha': code += '# different output\n'
    content = encoded({'source': code, 'reason': 'controlled_test'})
    usage = {'total_tokens': 7, 'prompt_tokens': 4, 'completion_tokens': 3}
    response = f.write(directory / 'model-output.json', dict(model=model['model'], model_source=model['provider'],
                                                           content=content, usage=usage))
    wire = f.write(directory / 'model-wire.json', dict(http_status=200, request_id='controlled_test',
        body=dict(model=model['model'], usage=usage, choices=[{'message': {'content': content}}])))
    entries = [dict(memory_id=memory.memory_id, memory_version=memory.version,
                    guidance_sha256=digest(memory.guidance.encode()))] if treatment else []
    if fault == 'old_version': entries[0]['memory_version'] -= 1
    if fault == 'wrong_receipt_hash': entries[0]['guidance_sha256'] = '0' * 64
    exposure = dict(schema_version='attentionbench.dev-memory-exposure.v1', cell=name, version=1,
                    model_input=request, entries=entries)
    exposure_ref = f.write((f.validator / name if fault == 'outside' else directory) / 'dev-memory-exposure.json', exposure)
    record.update(parent_source=parent, model_input=request, model_output=response, dev_memory_exposure=exposure_ref)
    if fault == 'missing': record.pop('dev_memory_exposure')
    d['version_record'] = f.write(directory / 'version.json', record)
    job = dict(job_id=name + '-model', cell=name, version=1, kind='model', claimed_at=1.0, settled_at=2.0,
               actual=dict(dev_calls=1, provider_tokens=7, input_tokens=4, output_tokens=3),
               receipt=dict(request=request, response=response, provider_response=wire, usage=usage))
    with sqlite3.connect(d['ledger']['uri']) as db:
        db.execute('UPDATE v2_versions SET payload=? WHERE version=1', (encoded(record),))
        db.execute('INSERT INTO v2_jobs VALUES (?,?,?,?,?,?)', (job['job_id'], name, 1, 'model', 'settled', encoded(job)))
    d['ledger']['sha256'] = digest(Path(d['ledger']['uri']).read_bytes())
    d['model_job_id'] = job['job_id']
    f.write(directory / 'execution.json', d)
    trace = json.loads(Path(r['bundle']['artifacts']['trace']['uri']).read_text())
    r['verdict'].update(failure_signature=SIGNATURE,
        signature_present=public_trace_projection(trace)['public_error'].startswith(SIGNATURE['public_error_class']),
        stop_position=stop_position(trace))
    r['verdict']['costs']['tokens'] = r['trial']['costs']['tokens'] = 7
    r['verdict']['checks'].update(checks or {})
    if unsafe:
        monitor = Path(r['verdict']['safety']['uri'])
        value = json.loads(monitor.read_text())
        value.update(unsafe_attempts=1, violations=[{'kind': 'controlled_test_sample'}])
        r['verdict']['safety'] = f.write(monitor, value)
    f.write(f.validator / name / 'verdict.json', r['verdict'])
    f.write(f.validator / name / 'trial.json', r['trial'])
    catalogs = f.seal()
    for cid in (name, 'validator-' + name):
        service.dev_v2.register(cid, catalogs[cid])
    service.import_dev_v2_execution(import_id=name, catalog_id=name, descriptor=name + '/execution.json')
    if fault == 'tamper':
        Path(exposure_ref['uri']).write_text('{}')
    return r, grant

STATE={'robot':{'position':[0,0,0]},'objects':{'cup':[1,2,3]}}
OBS={'robot':STATE['robot'],'perception':{'values':STATE['objects']}}


class SceneFixture(Fixture):
    def __init__(self,root):
        super().__init__(root,interface=True)
        self.contract=self.write(root/'contracts/scene.json',dict(schema='attentionbench.public-scene-contract.v2',tolerance=1e-6,tasks={'pick':{'seeds':{'101':{'normalized_sha256':fingerprint(STATE,True)}}}}))

    def make(self,*args,**kwargs):
        condition=validate(self.contract,'pick',STATE,101)
        r=super().make(*args,**kwargs,scene=dict(contract=self.contract,state=STATE,validation=condition,replays=[dict(state=STATE,validation=condition)]*2))
        # Synthetic native monitor, with explicit monitor attestation and a raw step.
        safety=dict(run_id=r['bundle']['run_id'],attempt_id=r['bundle']['attempt_id'],source='native_per_physics_step_monitor',monitor_available=True,unsafe_attempts=0,events=[],violations=[])
        self.write(Path(r['bundle']['artifacts']['safety']['uri']),safety)
        r['bundle']['artifacts']['safety']['sha256']=digest(Path(r['bundle']['artifacts']['safety']['uri']).read_bytes())
        bref=self.write(self.root/r['name']/'bundle.json',r['bundle']);r['verdict']['bundle']=bref
        d=r['descriptor']
        d['permission_scope']['scope']=dict(operations=['sensors'],task_id=r['config']['task_id'],robot=r['config']['robot'],seed=r['config']['seed'],sdk_calls=r['config']['sdk_call_limit'],control_steps=r['config']['horizon'],wall_seconds=r['config']['attempt_wall_seconds'])
        with sqlite3.connect(d['ledger']['uri']) as db:
            raw=db.execute("SELECT payload FROM v2_jobs WHERE kind='execute'").fetchone()[0];job=json.loads(raw);job['receipt']['bundle']=bref
            db.execute("UPDATE v2_jobs SET payload=? WHERE kind='execute'",(encoded(job),))
        d['ledger']['sha256']=digest(Path(d['ledger']['uri']).read_bytes());self.write(self.root/r['name']/'execution.json',d)
        self.write(self.validator/r['name']/'verdict.json',r['verdict'])
        return r


class FakeRunner:
    def __init__(self,root,*,treatments=(False,)*5,failed_check=None):
        require_m8("auto_validation","scene_contract","arms","failed")
        root.mkdir(parents=True,exist_ok=True);self.root=root;self.f=SceneFixture(root);self.treatments=treatments
        self.failed_check=failed_check
        self.failed=self.f.make('failed',failure=True);self.f.add_advice(self.failed,SIGNATURE)
        roots={'executor':[str(self.f.root)],'validator':[str(self.f.validator)],'contract':[str(root/'contracts')]}
        self.app=create_app(root/'service.sqlite',api_key=API,importer_key=IMPORTER,dev_v2_registration_roots=roots)
        self.service=MemoryService(root/'service.sqlite');self.phase='idle';self.starts=[];self.pauses=0;self.resumes=0;self.finished={}
        contract=dict(role='contract',root=str(root/'contracts'),context=self.failed['context'],files={'scene.json':self.f.contract['sha256']})
        self.register('contract',contract)
        catalogs=self.f.seal()
        self.register('failed',catalogs['failed']);self.register('validator-failed',catalogs['validator-failed'])
        ctx={**self.failed['context'],'seed':101,'scene_contract_ref':self.f.contract}
        self.mid='p-'+'a'*24
        self.service.create_provisional_note(**payload(self.mid,context=ctx,guidance='Align the gripper before closing it. controlled_test'))
        outcome(self.service,self.mid,0,ctx=ctx)

    def register(self,cid,catalog):
        status,result=sync_request(self.app,'/dev-v2/catalogs',method='POST',key=API,importer_key=IMPORTER,payload=dict(catalog_id=cid,catalog=catalog))
        if status!=200:raise ValueError(str(result))

    def begin(self,note,operation):
        s=self.service;f=self.f
        s.import_dev_v2_execution(import_id='failed',catalog_id='failed',descriptor='failed/execution.json')
        s.import_dev_v2_source(import_id='source',catalog_id='failed',descriptor='failed/source.json')
        s.create_dev_v2_candidate(memory_id='m1',source_import_id='source',created_at=1.0,from_provisional=note['memory_id'])
        for i in range(5):
            for a in ('c','t'):
                name=f'{a}{i}'
                if name not in f.records:f.make(name,failure=True if a=='c' else self.treatments[i],completed_calls=1,exposure='none' if a=='c' else 'candidate_validation',memory_id=None if a=='c' else 'm1',code=f'def policy(sdk):\n    # {name}\n    pass\n')
        r=f.records['c0'];cfg={**r['config'],'sha256':r['descriptor']['config']['sha256']}
        plan=freeze_plan('m1',self.failed['record']['source'],cfg,r['context'],SIGNATURE,[dict(pair_id=str(i),control_cell=f'c{i}',treatment_cell=f't{i}') for i in range(5)],policy_id=r['bundle']['policy_id'],budget={'assistance_credits':1},dev_model={'provider':'codex','model':'controlled_test_dev'})
        s.record_plan('m1',plan)
        return dict(memory_id='m1',plan=plan,experiment_id='validation-fake')

    def start(self,current,arm_id):
        if arm_id not in self.starts:self.starts.append(arm_id)
        self.phase='running'

    def poll(self,current):return self.phase
    def pause(self,current):self.pauses+=1;self.phase='paused'
    def resume(self,current):self.resumes+=1;self.phase='running'

    def finish(self,current):
        name=current['arm_id']
        if name in self.finished:return self.finished[name]
        # The service fixture builds synthetic originals, not a real execution.
        # Defer registration/import until our producer's receipt and validator exist.
        proxy=SimpleNamespace(authorize_dev_v2_validation=self.service.authorize_dev_v2_validation,store=self.service.store,
            dev_v2=SimpleNamespace(register=lambda *a:None),import_dev_v2_execution=lambda **kw:None)
        r,grant=generated_prepare(self.f,proxy,current['plan'],name)
        fail=self.failed_check and self.failed_check[0]==name
        if fail and self.failed_check[1]=='safety':
            ref=r['bundle']['artifacts']['safety'];raw=json.loads(Path(ref['uri']).read_bytes());raw.update(unsafe_attempts=1,violations=[{'kind':'controlled_test_violation'}])
            r['bundle']['artifacts']['safety']=self.f.write(Path(ref['uri']),raw)
            bref=self.f.write(self.f.root/name/'bundle.json',r['bundle']);r['verdict']['bundle']=bref
            with sqlite3.connect(r['descriptor']['ledger']['uri']) as db:
                job=json.loads(db.execute("SELECT payload FROM v2_jobs WHERE kind='execute'").fetchone()[0]);job['receipt']['bundle']=bref
                db.execute("UPDATE v2_jobs SET payload=? WHERE kind='execute'",(encoded(job),))
        memory=self.service.store.get_memory(current['memory_id']);record=r['record'];directory=self.f.root/name
        # Remove the fixture receipt: exercise the actual main-tree receipt writer.
        Path(record['dev_memory_exposure']['uri']).unlink()
        record['dev_memory_exposure']=exposure_receipt(directory,name,1,record['model_input'],record['parent_source'],memory=dict(memory_id=memory.memory_id,version=memory.version,guidance=memory.guidance) if r['trial']['exposure']=='candidate_validation' else None)
        r['descriptor']['version_record']=self.f.write(directory/'version.json',record)
        with sqlite3.connect(r['descriptor']['ledger']['uri']) as db:db.execute('UPDATE v2_versions SET payload=? WHERE version=1',(encoded(record),))
        r['descriptor']['ledger']['sha256']=digest(Path(r['descriptor']['ledger']['uri']).read_bytes());self.f.write(directory/'execution.json',r['descriptor'])
        catalog=self.f.seal()[name]
        result=validate_arm(experiment_dir=self.f.root,experiments_root=self.f.root,cell=name,version=1,execution_import_id=name,
            bundle_ref=r['verdict']['bundle'],config_ref=r['descriptor']['config'],source_ref=record['source'],grant={**grant,'permission_scope':r['descriptor']['permission_scope']},permission_scope=r['descriptor']['permission_scope'],executor_catalog=catalog,
            contract=self.f.contract,validator_root=self.f.validator,replay=lambda *a:(OBS,dict(STATE,objects={'cup':[2,2,3]}) if fail and self.failed_check[1]=='scene' else STATE),failure_signature=SIGNATURE,budget=r['cell']['budget'],developer_model='controlled_test_model',gpu_seconds=0,ledger_ref=r['descriptor']['ledger'])
        assert result['status']==('failed' if fail else 'passed'),result
        self.register(name,catalog);self.register('independent-'+name,result['catalog'])
        self.service.import_dev_v2_execution(import_id=name,catalog_id=name,descriptor=name+'/execution.json')
        if result['status']=='failed':
            self.finished[name]=dict(status='failed',validation=result,failed_check=dict(memory_id=current['memory_id'],validation_grant_id=grant['grant_id'],execution_import_id=name,catalog_id='independent-'+name,descriptor='verdict.json'))
            return self.finished[name]
        self.service.import_dev_v2_trial(import_id='trial-'+name,catalog_id='independent-'+name,descriptor='trial.json')
        self.finished[name]=dict(trial_import_id='trial-'+name,validation=result)
        return self.finished[name]

    def deps(self,root):
        root.mkdir(exist_ok=True)
        return SimpleNamespace(root=root,save=lambda state:atomic(root/'state.json',state),clock=time.time,quota=lambda:10,
                               activity=lambda current:None,client=self.service,runner=self)

    def demote(self):
        ctx={**self.failed['context'],'seed':101,'scene_contract_ref':self.f.contract}
        same=json.dumps({'schema_version':'attentionbench.dev-public-feedback.v1','execution_status':'failed','public_error':SIGNATURE['public_error_class'],'action_trace':[{'source':'left_arm','operation':'move_delta','status':'failed'}]})
        for i in range(5):outcome(self.service,getattr(self,'candidate_id','m1'),i,same if i>=2 else CHANGED,ctx=ctx)
        assert self.service.get_memory(getattr(self,'candidate_id','m1')).status.value=='provisional'


from contextlib import contextmanager,ExitStack
from unittest.mock import patch


@contextmanager
def synthetic_creation(f):
    """Only replace native registry/lock validation for this controlled fake SDK.

    Production creation, resource accounting, manifests and source resolution
    still run. There is no native SDK, model source or simulator in this fixture.
    """
    from benchmarks.attention_harness import dev_experiment as experiments
    from openarm365_dev_v2 import dev_config,scene_contract
    with ExitStack() as stack:
        for module,name,value in [(experiments,'validate_selection',lambda *a:None),
                (experiments,'get_task',lambda t:SimpleNamespace(initial_policy=f.records['failed']['record']['source']['uri'])),
                (experiments,'verify_lock',lambda *a,**kw:None),(dev_config,'verify_lock',lambda *a,**kw:None),
                (experiments,'validate_dev_config',lambda *a,**kw:None),(dev_config,'validate_dev_config',lambda *a,**kw:None),
                (scene_contract,'registered_contract_ref',lambda:f.contract)]:stack.enter_context(patch.object(module,name,value))
        stack.enter_context(patch.object(experiments.DemoProvider,'result',lambda *a:None))
        yield


class ProvisionedFakeRunner(FakeRunner):
    """Default adapter with synthetic origin/execution/replay and a real service."""
    def __init__(self,root,state_root):
        require_m8('auto_validation','scene_contract','arms','failed')
        from benchmarks.attention_harness.memory_validation_provision import Provisioner
        root.mkdir(parents=True);self.root=root;self.f=SceneFixture(root)
        # Disjoint, synthetic SDK root mirrors the real executor/source layout.
        source_root=root/'sources';source_root.mkdir()
        lock=json.loads(Path(self.f.lock['uri']).read_bytes());renamed={}
        for old in lock['files']:
            target=source_root/Path(old).relative_to(self.f.root);target.parent.mkdir(parents=True,exist_ok=True)
            Path(old).rename(target);renamed[old]=str(target)
        lock['files']={renamed[k]:v for k,v in lock['files'].items()}
        lock['source_root']=str(source_root);lock['source_sets']={str(source_root/'openarm365_dev_v2'):[str(source_root/'openarm365_dev_v2/sdk.py')]}
        lock['planner_lock']['path']=renamed[lock['planner_lock']['path']]
        lock['model_sources']={renamed[k]:v for k,v in lock['model_sources'].items()}
        self.f.sdk['robot_model_identity']=dict(lock['model_sources'])
        self.f.sdk_sha=digest(json.dumps(self.f.sdk,sort_keys=True,ensure_ascii=False,allow_nan=False).encode())
        self.f.lock=self.f.write(Path(self.f.lock['uri']),lock)
        self.f.config_overrides=dict(budget_tier='dev')
        self.f.manifest_overrides=dict(demos={'pick':{}})
        self.failed=self.f.make('failed',failure=True);self.f.add_advice(self.failed,SIGNATURE)
        roots={'executor':[str(self.f.root)],'validator':[str(self.f.validator)],'contract':[str(root/'contracts')],'source':[str(source_root)]}
        self.app=create_app(root/'service.sqlite',api_key=API,importer_key=IMPORTER,dev_v2_registration_roots=roots)
        self.service=MemoryService(root/'service.sqlite');self.phase='idle';self.starts=[];self.pauses=0;self.resumes=0;self.finished={};self.failed_check=None
        catalogs=self.f.seal();refs={cid:self.f.write(root/'inventory'/f'{cid}.json',c) for cid,c in catalogs.items()}
        self.provision=Provisioner(dict(experiments_root=str(self.f.root),state_root=str(state_root),evidence_catalogs=refs),self.service)
        self.mid='p-'+'b'*24
        p=payload(self.mid,context={**self.failed['context'],'seed':101,'scene_contract_ref':self.f.contract},guidance='Align the gripper before closing it. controlled_test',failure_signature=SIGNATURE)
        public=(self.f.root/'failed/feedback.json').read_text()
        p['origin_public_feedback_text']=public
        p['origin'].update(experiment_id='failed',cell='failed',policy_id=self.failed['cell']['policy_id'])
        source_version=json.loads((self.f.root/'failed/version2.json').read_bytes())
        p['origin']['digests'].update(origin_public_feedback=sha(public),origin_source=self.failed['record']['source']['sha256'],help=source_version['help']['sha256'],help_model_input=source_version['model_input']['sha256'],help_source=source_version['source']['sha256'])
        self.service.create_provisional_note(**p);outcome(self.service,self.mid,0,ctx=p['context'])

    def begin(self,note,operation):
        from benchmarks.attention_harness.memory_validation_runtime import LedgerRunner
        proxy=SimpleNamespace(**{name:getattr(self.service,name) for name in ('provenance','get_memory','import_dev_v2_execution','import_dev_v2_source','create_dev_v2_candidate','record_plan')},register_dev_v2_catalog=lambda **kw:self.register(kw['catalog_id'],kw['catalog']))
        runner=LedgerRunner(proxy,self.provision,experiments_root=self.f.root,validator_root=self.f.validator,state_root=self.provision.state_root)
        try:
            with synthetic_creation(self.f):batch=runner.begin(note,operation)
        finally:runner.pool.shutdown()
        self.candidate_id=batch['memory_id'];self.batch=batch;self.f.budget_override=batch['budget']
        # The executor is explicitly synthetic; each receipt is produced by the
        # same local fake used by the independent-validator acceptance tests.
        for pair in batch['pairs']:
            for arm in ('control','treatment'):
                name=pair[arm+'_cell']
                if name not in self.f.records:self.f.make(name,failure=arm=='control',completed_calls=1,exposure='none' if arm=='control' else 'candidate_validation',memory_id=None if arm=='control' else self.candidate_id,code=f'def policy(sdk):\n    # {name}\n    pass\n')
        return batch
