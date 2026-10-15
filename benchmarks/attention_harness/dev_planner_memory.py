"""Exact authority/version-scoped Dev guidance from the independent Service.

No direct store access, fixture records, promotion decisions or private results
enter the online planner. Source/result imports run in a separate offline role.
"""
import hashlib
import json
import os
import time
from pathlib import Path

from .core.dev_store import DevConflict
from .dev_help import EMPTY_MEMORY, public_screen
from .dev_ledger import encoded
from .dev_loop import immutable, sha

SCOPE = ('benchmark', 'suite', 'task_id', 'robot', 'controller', 'perception_mode',
         'scene_id', 'object_set_id', 'camera_config_id', 'task_variant_id',
         'sdk_sha256', 'namespace')


def context(config, namespace):
    value = {key: config[key] for key in SCOPE if key in config and key not in {'sdk_sha256', 'namespace'}}
    camera={k:config[k] for k in ('camera_names','camera_width','camera_height','randomize_cameras')}
    identity=sdk_identity(config)
    value.update(benchmark='AttentionBench', suite='robocasa365', namespace=namespace,
                 camera_config_id='robocasa365:'+hashlib.sha256(encoded(camera).encode()).hexdigest(),
                 task_variant_id=config['official_env']+':layout'+str(config['layout_id'])+':style'+str(config['style_id'])+':'+config['base_mode'],
                 sdk_sha256=hashlib.sha256(encoded(identity).encode()).hexdigest())
    if set(value) != set(SCOPE) or any(not isinstance(v, str) or not v for v in value.values()):
        raise ValueError('complete task/robot/SDK/scene Memory scope required')
    value.update(camera_names=config['camera_names'], task_prompt=config['task_prompt'])
    public_screen(value)
    return value


def sdk_identity(config):
    from .dev_tasks import SDK, SDK_PROMPT
    ref=config['dev_lock']
    if sha(ref['path'])!=ref['sha256']:raise DevConflict('Memory SDK lock drift')
    lock=json.loads(Path(ref['path']).read_bytes());root=Path(lock['source_root'])
    sdk_files={str(Path(name).relative_to(root)):expected for name,expected in lock['files'].items()
               if Path(name).is_relative_to(root) and any(part in Path(name).parts for part in ('openarm365','openarm365_dev','openarm365_dev_v2','curobo_v2'))
               and '/tests/' not in name}
    if not sdk_files:raise ValueError('Memory requires frozen SDK implementation identity')
    if any(sha(root/name)!=expected for name,expected in sdk_files.items()):raise DevConflict('Memory runtime SDK differs from frozen identity')
    return dict(contract=SDK,prompt=SDK_PROMPT,implementation=sdk_files,planner_lock=lock['planner_lock']['sha256'],robot_model_identity=lock['model_sources'])


def client_for(settings):
    from .memory_service_client import MemoryServiceClient
    key = os.getenv('ATTENTION_MEMORY_API_KEY')
    if not key:
        raise PermissionError('ATTENTION_MEMORY_API_KEY unavailable; no Memory exposure')
    return MemoryServiceClient(settings['base_url'], api_key=key, timeout=5)


class DevMemory:
    def __init__(self, gateway, settings, config):
        self.gateway, self.settings = gateway, settings
        self.context = context(config, settings['namespace'])
        if gateway.store_id() != settings['store_id']:
            raise PermissionError('Memory Service authority identity mismatch')

    def eligible(self):
        return sorted([m for m in self.gateway.retrieve(self.context, now=time.time())
                if getattr(m.status, 'value', m.status) == 'trusted'
                and type(m.version) is int and m.version > 0
                and all(m.applicability.get(k) == v for k, v in self.context.items() if k in SCOPE)],
                key=lambda m: (m.memory_id, m.version))

    def packet(self, *, development_id, source):
        entries = []
        for m in self.eligible():
            grant = self.gateway.authorize_dev_use(memory_id=m.memory_id, memory_version=m.version, development_id=development_id,
                context=self.context, source_policy_sha256=hashlib.sha256(source.encode()).hexdigest(), now=time.time())
            if (type(grant.get('memory_version')) is not int or grant.get('memory_id') != m.memory_id or grant.get('memory_version') != m.version
                    or grant.get('context') != self.context or grant.get('development_id') != development_id
                    or grant.get('source_policy_sha256') != hashlib.sha256(source.encode()).hexdigest()
                    or grant.get('guidance') != m.guidance or not grant.get('grant_id')):
                raise PermissionError('Memory exact version/exposure binding mismatch')
            # Deliberately exclude validation/evaluation internals from online input.
            entry = dict(memory_id=m.memory_id, version=m.version, content=grant['guidance'],
                         grant_id=grant['grant_id'], source_trace_id=grant['source_trace_id'],
                         evidence_refs=grant['evidence_refs'], authority=self.settings['store_id'],
                         context=self.context, development_id=development_id,
                         source_policy_sha256=grant['source_policy_sha256'])
            public_screen(entry)
            entries.append(entry)
        return {**EMPTY_MEMORY, 'entries': entries, 'explicit_registry_authorization': True}

    def recheck(self, packet):
        eligible = {m.memory_id: m for m in self.eligible()}
        for entry in packet['entries']:
            if entry.get('authority')!=self.settings['store_id'] or entry.get('context')!=self.context:raise PermissionError('Memory packet authority/context changed')
            m = eligible.get(entry['memory_id'])
            if m is None or m.version != entry['version'] or m.guidance != entry['content']:
                raise PermissionError('Memory disabled/expired/rolled back/version changed before exposure')
            grant = self.gateway.get_dev_use(entry['grant_id'])
            if any(grant.get(k) != v for k, v in dict(memory_id=entry['memory_id'], memory_version=entry['version'],
                    context=self.context, guidance=entry['content'], development_id=entry['development_id'],
                    source_policy_sha256=entry['source_policy_sha256']).items()):
                raise PermissionError('Memory exposure grant no longer matches')


def _load_memory(store, cell, version, previous, config, settings, directory):
    if not settings:
        return EMPTY_MEMORY, None
    service = DevMemory(client_for(settings), settings, config)
    path = Path(directory) / 'memory-input.json'
    if path.exists():
        packet = json.loads(path.read_bytes())
        if any(e.get('development_id')!=f'{cell}:v{version}' or e.get('source_policy_sha256')!=hashlib.sha256(previous.encode()).hexdigest() for e in packet['entries']):
            raise PermissionError('Memory grant belongs to a different generation/source version')
        service.recheck(packet)
    else:
        packet = service.packet(development_id=f'{cell}:v{version}', source=previous)
        ref = immutable(path, packet)
        with store.tx() as db:
            store.event(db, 'planner_memory_retrieved', dict(cell=cell, version=version,
                        authority=settings['store_id'], namespace=settings['namespace'], packet=ref,
                        versions=[dict(memory_id=e['memory_id'], version=e['version'], grant_id=e['grant_id']) for e in packet['entries']]))
    return packet, service


def load_memory(store, cell, version, previous, config, settings, directory):
    if not settings:return EMPTY_MEMORY, None
    store.start_cell_clock(cell)
    started=time.time();status='failed';error=None
    with store.tx() as db:
        store.event(db,'planner_memory_query_started',dict(cell=cell,version=version,started_at=started,owner=__import__('benchmarks.attention_harness.dev_loop',fromlist=['process_identity']).process_identity()))
    try:
        result=_load_memory(store,cell,version,previous,config,settings,directory)
        status='completed';return result
    except BaseException as exc:
        error=type(exc).__name__;raise
    finally:
        ended=time.time()
        with store.tx() as db:
            store.event(db,'planner_memory_query_finished',dict(cell=cell,version=version,
                started_at=started,ended_at=ended,actual_seconds=max(0,ended-started),status=status,error=error,
                accounting='same persistent goal wall; no provider/native usage'))


def journal_result(store, cell, version, packet, source_ref, public_feedback_ref, directory):
    if not packet.get('entries'):return
    # This is an offline execution import queue, never a self-reported verdict.
    from .dev_v2_memory_import import queue
    return queue(store,cell,version,directory,'result',dict(
        grants=[e['grant_id'] for e in packet['entries']],result_source=source_ref,
        public_feedback=public_feedback_ref))


def stage_candidate(store, cell, version, help_ref, source_ref, feedback_ref, directory):
    help_value=json.loads(Path(help_ref['uri']).read_bytes())
    if help_value.get('rejected') or help_value.get('provider')!='advisor':return None
    for ref in (help_ref,source_ref,feedback_ref):
        if sha(ref['uri'])!=ref['sha256']:raise DevConflict('candidate evidence digest drift')
    from .dev_v2_memory_import import queue
    return queue(store,cell,version,directory,'source',dict(
        guidance_source=help_ref,repair_source=source_ref,public_trace_source=feedback_ref))
