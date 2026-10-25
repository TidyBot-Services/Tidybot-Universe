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
from .memory_layers import (OTHER_UNVALIDATED_CAP as NOT_TRUSTED_HERE_CAP, LEVELS,
                            FAMILY_CAP, layer_of, order)

SCOPE = ('benchmark', 'suite', 'task_id', 'robot', 'controller', 'perception_mode',
         'scene_id', 'object_set_id', 'camera_config_id', 'task_variant_id',
         'sdk_sha256', 'namespace')


def cell_memory(cell):
    """Memory settings for a cell: the shared authority, or legacy planner Memory."""
    if cell.get('memory_authority'):
        return cell['memory_authority']
    return (cell.get('planner_features') or {}).get('memory')


def layered(cell):
    """Only cells bound through the shared authority use the layered rules;
    frozen planner-Memory manifests keep the exact trusted-only behaviour."""
    return bool(cell.get('memory_authority'))


def memory_scope(config, mode):
    """Classify non-experiment cells without weakening the existing context checks.

    Experiments retain their existing scene gate and context failure behaviour.
    """
    if mode == 'experiment':
        return {'level': 'scene'}
    from openarm365_dev_v2.scene_contract import registered_contract_ref, load_contract, seed_entry
    ref = config.get('scene_contract')
    if not ref:
        reason = 'scene_contract_missing'
    elif ref != registered_contract_ref():
        reason = 'scene_contract_not_registered'
    else:
        try:
            seed_entry(load_contract(ref), config['task_id'], config['seed'])
        except (KeyError, ValueError):
            reason = 'no_scene_contract'
        else:
            return {'level': 'scene'}
    return {'level': 'reference_only', 'reason': reason, 'seed': config.get('seed')}


def sdk_interface_sha256(identity):
    """Hash of the SDK contract plus the SDK text given to Dev."""
    return hashlib.sha256(encoded({'contract': identity['contract'], 'prompt': identity['prompt']}).encode()).hexdigest()


def context(config, namespace, *, contract_catalog=None):
    required=('task_id','robot','controller','perception_mode','scene_id','object_set_id',
              'camera_names','camera_width','camera_height','randomize_cameras',
              'official_env','layout_id','style_id','base_mode','task_prompt','seed',
              'scene_contract','dev_lock')
    missing=[key for key in required if key not in config]
    if missing:raise ValueError('Memory context requires config fields: '+', '.join(missing))
    value = {key: config[key] for key in SCOPE if key in config and key not in {'sdk_sha256', 'namespace'}}
    camera={k:config[k] for k in ('camera_names','camera_width','camera_height','randomize_cameras')}
    identity=sdk_identity(config)
    value.update(benchmark='AttentionBench', suite='robocasa365', namespace=namespace,
                 camera_config_id='robocasa365:'+hashlib.sha256(encoded(camera).encode()).hexdigest(),
                 task_variant_id=config['official_env']+':layout'+str(config['layout_id'])+':style'+str(config['style_id'])+':'+config['base_mode'],
                 sdk_sha256=hashlib.sha256(encoded(identity).encode()).hexdigest())
    if set(value) != set(SCOPE) or any(not isinstance(v, str) or not v for v in value.values()):
        raise ValueError('complete task/robot/SDK/scene Memory scope required')
    if type(config.get('seed')) is not int:
        raise ValueError('Memory context requires integer config seed')
    cameras=config.get('camera_names')
    if not isinstance(cameras, (list,tuple)) or not cameras or any(not isinstance(n,str) or not n.strip() for n in cameras) or len(set(cameras))!=len(cameras):
        raise ValueError('Memory context requires ordered, nonempty camera_names')
    if not isinstance(config.get('task_prompt'),str) or not config['task_prompt'].strip():
        raise ValueError('Memory context requires task_prompt')
    from openarm365_dev_v2.scene_contract import registered_contract_ref
    contract=registered_contract_ref()
    if contract_catalog is not None:
        # Offline importer only: an explicit, exact per-seed catalog. Ordinary
        # retrieval keeps the global registration rule unchanged.
        from .memory_source_catalogs import covers
        from openarm365_dev_v2.scene_contract import load_contract,seed_entry
        if contract_catalog.get('role')!='contract' or not covers(contract_catalog,config['scene_contract']):raise ValueError('exact target contract catalog required')
        seed_entry(load_contract(config['scene_contract']),config['task_id'],config['seed'])
        contract=config['scene_contract']
    if config.get('scene_contract')!=contract:
        raise ValueError('Memory context requires the current registered scene_contract in cell config')
    value.update(seed=config['seed'], camera_names=list(cameras), task_prompt=config['task_prompt'],
                 scene_contract_ref=contract)
    # Matching identity; sdk_sha256 (implementation) stays for audit.
    value['sdk_interface_sha256'] = sdk_interface_sha256(identity)
    if 'execution_settings' in config:value['execution_profile']=config['execution_settings']['profile']
    public_screen(value)
    return value


def sdk_identity(config):
    from .dev_tasks import dev_sdk, dev_sdk_prompt
    ref=config['dev_lock']
    if sha(ref['path'])!=ref['sha256']:raise DevConflict('Memory SDK lock drift')
    lock=json.loads(Path(ref['path']).read_bytes());root=Path(lock['source_root'])
    sdk_files={str(Path(name).relative_to(root)):expected for name,expected in lock['files'].items()
               if Path(name).is_relative_to(root) and any(part in Path(name).parts for part in ('openarm365','openarm365_dev','openarm365_dev_v2','curobo_v2'))
               and '/tests/' not in name}
    if not sdk_files:raise ValueError('Memory requires frozen SDK implementation identity')
    if any(sha(root/name)!=expected for name,expected in sdk_files.items()):raise DevConflict('Memory runtime SDK differs from frozen identity')
    return dict(contract=dev_sdk(config),prompt=dev_sdk_prompt(config),implementation=sdk_files,planner_lock=lock['planner_lock']['sha256'],robot_model_identity=lock['model_sources'])


def client_for(settings):
    from .memory_service_client import MemoryServiceClient
    key = os.getenv('ATTENTION_MEMORY_API_KEY')
    if not key:
        raise PermissionError('ATTENTION_MEMORY_API_KEY unavailable; no Memory exposure')
    return MemoryServiceClient(settings['base_url'], api_key=key, timeout=5)


class Note:
    """One retrieved note with its applicability level for this Dev context."""
    def __init__(self, memory_id, version, guidance, status, applicability, *, created_at=0.0,
                 match_level='same_scene', trusted_here=None, source_seed=None, source_task_id=None):
        self.memory_id, self.version, self.guidance = memory_id, version, guidance
        self.status, self.applicability, self.created_at = status, applicability or {}, created_at or 0.0
        self.match_level = match_level
        self.trusted_here = (status == 'trusted' and match_level == 'same_scene') if trusted_here is None else trusted_here
        self.unvalidated = status == 'provisional'
        self.source_seed = source_seed
        self.source_task_id = source_task_id or self.applicability.get("task_id")
        self.layer = layer_of(self)


TASK_SCOPE = ('benchmark', 'suite', 'task_id', 'robot', 'controller', 'perception_mode', 'namespace')
SCENE_SCOPE = ('scene_id', 'object_set_id', 'camera_config_id', 'task_variant_id')


class DevMemory:
    def __init__(self, gateway, settings, config, *, layered=False, mode='experiment', family=False):
        self.gateway, self.settings, self.layered = gateway, settings, layered
        self.family = layered and family is True
        self.scope = memory_scope(config, mode if layered else 'experiment')
        # An absent/unregistered contract cannot form an authorized context.
        # Keep an explicit empty packet; never substitute a different contract.
        self.context = None
        if self.scope.get('reason') in {'scene_contract_missing', 'scene_contract_not_registered'}:
            return
        self.context = context(config, settings['namespace'])
        if not layered:
            # Frozen planner-Memory manifests keep the exact pre-split context.
            self.context.pop('sdk_interface_sha256', None)
        if gateway.store_id() != settings['store_id']:
            raise PermissionError('Memory Service authority identity mismatch')

    def eligible(self):
        if self.context is None:
            return []
        if self.layered:
            return [n for n in self.notes() if n.trusted_here]
        return sorted([m for m in self.gateway.retrieve(self.context, now=time.time())
                if getattr(m.status, 'value', m.status) == 'trusted'
                and type(m.version) is int and m.version > 0
                and m.applicability.get('execution_profile') == self.context.get('execution_profile')
                and all(m.applicability.get(k) == v for k, v in self.context.items() if k in SCOPE)],
                key=lambda m: (m.memory_id, m.version))

    def notes(self):
        """Every note readable here (uncapped): trusted here first, then others newest first."""
        if self.context is None:
            return []
        reference_only = self.scope['level'] == 'reference_only'
        now = time.time()
        levels = [level for level in LEVELS if (not reference_only or level != 'same_scene')
                  and (self.family or level != 'same_family')]
        if callable(getattr(self.gateway, 'retrieve_entries', None)):
            rows = [Note(r['memory_id'], r['version'], r['guidance'], r['status'], r.get('applicability'),
                         created_at=r.get('created_at'), match_level=r.get('match_level', 'same_scene'),
                         trusted_here=r.get('trusted_here'), source_seed=r.get('source_seed'), source_task_id=r.get('source_task_id'))
                    for r in self.gateway.retrieve_entries(self.context, now=now,
                        match_levels=levels)
                    if (not reference_only or r.get('match_level') in levels and r.get('trusted_here') is False)
                    and (r.get('match_level') != 'same_family' or
                         r.get('status') in {'provisional','trusted'} and not r.get('seeded_for_tests')
                         and (r.get('expires_at') is None or r['expires_at']>now)
                         and not any(r.get(k) for k in ('disabled','expired','withdrawn')))]
        else:
            if reference_only:
                return []  # Old gateways cannot explicitly attest same-task reference trust.
            rows = [Note(m.memory_id, m.version, m.guidance, getattr(m.status, 'value', m.status), m.applicability,
                         created_at=getattr(m, 'created_at', 0.0), match_level=getattr(m, 'match_level', 'same_scene'),
                         trusted_here=getattr(m, 'trusted_here', None),
                         source_seed=(m.applicability or {}).get('seed'))
                    for m in self.gateway.retrieve(self.context, now=now)]
        result = []
        for n in rows:
            if reference_only and (n.match_level not in levels or n.trusted_here is not False):
                continue
            family_scope = n.match_level == 'same_family'
            if family_scope:
                try:
                    from attention_memory_service.families import family_of
                    source_family = family_of(n.applicability.get('task_id'))
                    if (not self.family or source_family is None
                            or source_family != family_of(self.context.get('task_id'))
                            or n.applicability.get('task_id') == self.context.get('task_id')):
                        continue
                except ImportError:
                    continue
            if (n.status not in {'trusted', 'provisional'} or type(n.version) is not int or n.version < 1
                    or n.match_level not in levels or type(n.trusted_here) is not bool
                    or n.trusted_here and (n.status != 'trusted' or n.match_level not in {'same_scene','same_task'})
                    or n.match_level == 'same_scene' and n.trusted_here is not (n.status == 'trusted')
                    or n.applicability.get('execution_profile') != self.context.get('execution_profile')
                    or any(n.applicability.get(k) != self.context.get(k) for k in TASK_SCOPE if not (family_scope and k == 'task_id'))
                    or n.match_level == 'same_scene' and any(n.applicability.get(k) != self.context.get(k) for k in SCENE_SCOPE)):
                continue
            result.append(n)
        here = sorted((n for n in result if n.trusted_here), key=lambda n: (n.memory_id, n.version))
        rest = sorted((n for n in result if not n.trusted_here), key=lambda n: (-n.created_at, n.memory_id))
        return here + rest

    def selected(self):
        if self.context is None:
            return []
        if not self.layered:
            return self.eligible()
        notes = self.notes()
        return order(notes, family=self.family)

    def packet(self, *, development_id, source):
        entries = []
        # Retrieve records carry authority and guidance; the separate public
        # catalogue carries display text. Match it by exact id and version.
        lister = getattr(self.gateway, 'list_dev_v2_memories', None) if self.context is not None else None
        catalogue = ((lister() if self.layered else lister(status='trusted')) if callable(lister) else [])
        display = {(row['memory_id'], row['version']): row for row in catalogue
                   if row.get('status') in ({'trusted', 'provisional'} if self.layered else {'trusted'})}
        for m in self.selected():
            grant = self.gateway.authorize_dev_use(memory_id=m.memory_id, memory_version=m.version, development_id=development_id,
                context=self.context, source_policy_sha256=hashlib.sha256(source.encode()).hexdigest(), now=time.time())
            if (type(grant.get('memory_version')) is not int or grant.get('memory_id') != m.memory_id or grant.get('memory_version') != m.version
                    or grant.get('context') != self.context or grant.get('development_id') != development_id
                    or grant.get('source_policy_sha256') != hashlib.sha256(source.encode()).hexdigest()
                    or grant.get('guidance') != m.guidance or not grant.get('grant_id')):
                raise PermissionError('Memory exact version/exposure binding mismatch')
            if grant.get('seeded_for_tests') is True and grant.get('excluded_from_results') is not True:
                raise PermissionError('test-seeded Memory grant lacks exclusion flag')
            if getattr(m,'match_level',None)=='same_family' and grant.get('seeded_for_tests') is True:
                raise PermissionError('test-seeded Memory cannot be a family reference')
            # Deliberately exclude validation/evaluation internals from online input.
            entry = dict(memory_id=m.memory_id, version=m.version, content=grant['guidance'],
                         grant_id=grant['grant_id'], source_trace_id=grant['source_trace_id'],
                         evidence_refs=grant['evidence_refs'], authority=self.settings['store_id'],
                         context=self.context, development_id=development_id,
                         source_policy_sha256=grant['source_policy_sha256'],
                         seeded_for_tests=grant.get('seeded_for_tests') is True,
                         excluded_from_results=grant.get('excluded_from_results') is True,
                         status=getattr(m.status,'value',m.status))
            if self.layered:
                if (grant.get('match_level', m.match_level) != m.match_level
                        or grant.get('trusted_here', m.trusted_here) is not m.trusted_here
                        or not m.trusted_here and grant.get('excluded_from_results') is not True
                        or m.unvalidated and (grant.get('unvalidated') is not True or grant.get('trust_scope') != 'provisional')):
                    raise PermissionError('Memory grant trust level/exclusion mismatch')
                entry.update(layer=layer_of(m), source_task_id=m.source_task_id, created_at=m.created_at, match_level=m.match_level, trusted_here=m.trusted_here, unvalidated=m.unvalidated,
                             trust_scope=grant.get('trust_scope'), source_seed=grant.get('source_seed', m.source_seed),
                             excluded_from_results=grant.get('excluded_from_results') is True or not m.trusted_here)
            # Catalogue copy is descriptive only; the grant still binds guidance,
            # source, context and version exactly as before.
            for field in ('title', 'summary'):
                value = display.get((m.memory_id, m.version), {}).get(field)
                if isinstance(value, str) and value:
                    entry[field] = value
            public_screen(entry)
            entries.append(entry)
        packet = {**EMPTY_MEMORY, 'entries': entries, 'explicit_registry_authorization': True,
                  'seeded_for_tests': any(e['seeded_for_tests'] for e in entries),
                  'excluded_from_results': any(e['excluded_from_results'] for e in entries)}
        if self.layered:
            packet.update(layered=True, family_reference=self.family, family_cap=FAMILY_CAP, not_trusted_here_cap=NOT_TRUSTED_HERE_CAP,
                          unvalidated=any(e['unvalidated'] for e in entries),
                          not_trusted_here=any(not e['trusted_here'] for e in entries))
        if self.scope['level'] == 'reference_only':
            packet.update(scope='reference_only', scope_reason=self.scope['reason'], excluded_from_results=True)
        return packet

    def recheck(self, packet):
        if packet.get("family_reference", False) is not self.family:
            raise PermissionError("Memory packet family reference switch changed")
        if (packet.get('scope') == 'reference_only') != (self.scope['level'] == 'reference_only'):
            raise PermissionError('Memory packet scene scope changed')
        if self.scope['level'] == 'reference_only' and (
                packet.get('scope_reason') != self.scope['reason'] or packet.get('excluded_from_results') is not True):
            raise PermissionError('Memory packet scene exclusion changed')
        if bool(packet.get('layered')) != self.layered:
            raise PermissionError('Memory packet trust rules changed')
        eligible = {m.memory_id: m for m in (self.notes() if self.layered else self.eligible())}
        for entry in packet['entries']:
            if entry.get('authority')!=self.settings['store_id'] or entry.get('context')!=self.context:raise PermissionError('Memory packet authority/context changed')
            m = eligible.get(entry['memory_id'])
            if m is None or m.version != entry['version'] or m.guidance != entry['content']:
                raise PermissionError('Memory disabled/expired/rolled back/version changed before exposure')
            if self.layered and (m.match_level != entry.get('match_level') or m.trusted_here is not entry.get('trusted_here')):
                raise PermissionError('Memory trust level changed before exposure')
            grant = self.gateway.get_dev_use(entry['grant_id'])
            if any(grant.get(k) != v for k, v in dict(memory_id=entry['memory_id'], memory_version=entry['version'],
                    context=self.context, guidance=entry['content'], development_id=entry['development_id'],
                    source_policy_sha256=entry['source_policy_sha256']).items()):
                raise PermissionError('Memory exposure grant no longer matches')
            if (entry.get('seeded_for_tests') is True)!=(grant.get('seeded_for_tests') is True) or (entry.get('excluded_from_results') is True)!=(grant.get('excluded_from_results') is True or self.layered and entry.get('trusted_here') is False):
                raise PermissionError('Memory exclusion flags changed')


def _load_memory(store, cell, version, previous, config, settings, directory, layered=False):
    if not settings:
        return EMPTY_MEMORY, None
    mode = store.cell(cell).get('mode', 'experiment') if layered else 'experiment'
    scope = memory_scope(config, mode)
    with store.tx() as db:
        exists = db.execute("SELECT 1 FROM v2_events WHERE kind='planner_memory_scope' "
                            "AND json_extract(payload, '$.cell')=? AND json_extract(payload, '$.version')=?",
                            (cell, version)).fetchone()
        if not exists:
            store.event(db, 'planner_memory_scope', dict(cell=cell, version=version,
                        level=scope['level'], reason=scope.get('reason'), seed=config.get('seed')))
    gateway = None if scope.get('reason') in {'scene_contract_missing', 'scene_contract_not_registered'} else client_for(settings)
    service = DevMemory(gateway, settings, config, layered=layered, mode=mode, family=layered and store.cell(cell).get('memory_family') is True)
    path = Path(directory) / 'memory-input.json'
    if path.exists():
        packet = json.loads(path.read_bytes())
        if any(e.get('development_id')!=f'{cell}:v{version}' or e.get('source_policy_sha256')!=hashlib.sha256(previous.encode()).hexdigest() for e in packet['entries']):
            raise PermissionError('Memory grant belongs to a different generation/source version')
        service.recheck(packet)
    else:
        packet = service.packet(development_id=f'{cell}:v{version}', source=previous)
        ref = immutable(path, packet)
        trust = ('layer', 'source_task_id', 'match_level', 'trusted_here', 'unvalidated', 'trust_scope', 'source_seed') if layered else ()
        with store.tx() as db:
            store.event(db, 'planner_memory_retrieved', dict(cell=cell, version=version,
                        authority=settings['store_id'], namespace=settings['namespace'], packet=ref,
                        seeded_for_tests=packet['seeded_for_tests'], excluded_from_results=packet['excluded_from_results'],
                        **({'not_trusted_here': packet['not_trusted_here'], 'unvalidated': packet['unvalidated']} if layered else {}),
                        versions=[dict(memory_id=e['memory_id'], version=e['version'], grant_id=e['grant_id'],
                                       seeded_for_tests=e['seeded_for_tests'], excluded_from_results=e['excluded_from_results'],
                                       **{k: e.get(k) for k in trust}) for e in packet['entries']]))
            untrusted = [e for e in packet['entries'] if layered and e.get('trusted_here') is False]
            if untrusted:
                # Run flag: this run read a note not trusted for its own scene;
                # summaries exclude it and the UI tags it "Used unvalidated memory".
                store.event(db, 'planner_memory_unvalidated_used', dict(cell=cell, version=version,
                            memory_ids=[e['memory_id'] for e in untrusted],
                            match_levels=[e['match_level'] for e in untrusted],
                            layers=[layer_of(e) for e in untrusted],
                            excluded_from_results=True))
    return packet, service


def load_memory(store, cell, version, previous, config, settings, directory, layered=False):
    if not settings:return EMPTY_MEMORY, None
    store.start_cell_clock(cell)
    started=time.time();status='failed';error=None
    with store.tx() as db:
        store.event(db,'planner_memory_query_started',dict(cell=cell,version=version,started_at=started,owner=__import__('benchmarks.attention_harness.dev_loop',fromlist=['process_identity']).process_identity()))
    try:
        result=_load_memory(store,cell,version,previous,config,settings,directory,layered)
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
    if (Path(directory)/'memory-validation-input.json').exists():return None
    from .memory_run_notes import delivered
    run_notes=delivered(directory) if store.cell(cell).get('run_notes') is True else []
    if packet.get('scope') == 'reference_only' and not run_notes:
        with store.tx() as db:
            store.event(db, 'planner_memory_result_skipped', dict(cell=cell, version=version,
                        reason='reference_only_no_scene_contract',
                        grants=[e['grant_id'] for e in packet.get('entries', [])]))
        return None
    if not packet.get('entries') and not run_notes:return
    from .dev_v2_memory_import import queue
    provisional=[e for e in packet.get('entries',[]) if (e.get('trust_scope')=='provisional'
                 or e.get('status')=='trusted' and e.get('trusted_here') is not False)
                 and not e.get('seeded_for_tests')
                 and packet.get('scope') != 'reference_only']
    if provisional:
        # Observational use records only; the importer sends the raw public
        # feedback and the Service computes the outcome itself.
        queue(store,cell,version,directory,'provisional_outcome',dict(
            grants=[dict(grant_id=e['grant_id'],memory_id=e['memory_id'],development_id=e['development_id']) for e in provisional],
            public_feedback=public_feedback_ref))
    if run_notes:
        with store.tx() as db:
            store.event(db,'planner_memory_result_skipped',dict(cell=cell,version=version,
                reason='run_note_excluded_from_results',note_ids=[e['note_id'] for e in run_notes],
                grants=[e['grant_id'] for e in packet.get('entries',[])]))
        return None
    if packet.get('excluded_from_results') is True or any(e.get('excluded_from_results') is True or e.get('seeded_for_tests') is True or e.get('trusted_here') is False for e in packet['entries']):
        layered_skip=any(e.get('trusted_here') is False for e in packet['entries'])
        with store.tx() as db:
            store.event(db,'planner_memory_result_skipped',dict(cell=cell,version=version,
                reason='not_trusted_here_excluded_from_results' if layered_skip else 'test_seeded_excluded_from_results',
                grants=[e['grant_id'] for e in packet['entries']]))
        return None
    # This is an offline execution import queue, never a self-reported verdict.
    return queue(store,cell,version,directory,'result',dict(
        grants=[e['grant_id'] for e in packet['entries']],result_source=source_ref,
        public_feedback=public_feedback_ref))


def stage_candidate(store, cell, version, help_ref, source_ref, feedback_ref, directory):
    for ref in (help_ref,source_ref,feedback_ref):
        if sha(ref['uri'])!=ref['sha256']:raise DevConflict('candidate evidence digest drift')
    help_value=json.loads(Path(help_ref['uri']).read_bytes())
    if help_value.get('rejected'):return None
    if help_value.get('provider')=='human_proxy':
        # A visual AdvisorProxy answer may be displayed as an offline proposal.
        # The Service importer still independently decides whether it can be a
        # candidate; this queue carries no authority or promotion decision.
        proxy_ref=help_value.get('advisor_proxy')
        if (help_value.get('mode')!='vision' or not isinstance(proxy_ref,dict)
                or set(proxy_ref)!={'uri','sha256'}):return None
        if sha(proxy_ref['uri'])!=proxy_ref['sha256']:raise DevConflict('visual Advisor evidence digest drift')
        answer=json.loads(Path(proxy_ref['uri']).read_bytes())
        if answer.get('status')!='completed' or answer.get('mode')!='vision' or answer.get('request_type')!='hint' or answer.get('feedback')!=help_value.get('content'):
            return None
    elif help_value.get('provider')!='advisor':return None
    from .dev_v2_memory_import import queue
    return queue(store,cell,version,directory,'source',dict(
        guidance_source=help_ref,repair_source=source_ref,public_trace_source=feedback_ref))
