"""Offline Dev arms: exact origin and actual request exposure, no online retrieval."""
import hashlib,json,re
from pathlib import Path
from .core.dev_store import DevConflict
from .dev_v2_loop import checked,immutable,model_messages,provider_call
from .dev_loop import sha
from openarm365_dev_v2.source_policy import source_limit


def exposure_receipt(directory,cell,version,request,origin,*,memory=None):
    """Read the bytes that were actually sent, before writing the immutable version."""
    if type(version) is not int:raise DevConflict('integer version required')
    body=json.loads(checked(request).read_bytes());messages=body['messages']
    if len(messages)!=2 or [m['role'] for m in messages]!=['system','user']:raise DevConflict('exact Dev messages required')
    packet=json.loads(messages[1]['content'])
    allowed={'locked_task','previous_complete_source','public_feedback','attention_control','remaining_budget','source_size_limit_bytes'}
    if set(packet)-allowed or packet['previous_complete_source']!=checked(origin).read_text():raise DevConflict('Dev origin/request changed')
    entries=[];notes=[]
    if memory is not None:
        if type(memory['version']) is not int:raise DevConflict('integer memory version required')
        notes=[{'content':memory['guidance']}]
        entries=[dict(memory_id=memory['memory_id'],memory_version=memory['version'],guidance_sha256=hashlib.sha256(memory['guidance'].encode()).hexdigest())]
    if packet['attention_control']!={'memory':notes}:raise DevConflict('Dev prompt Memory exposure differs')
    return immutable(Path(directory)/'dev-memory-exposure.json',dict(schema_version='attentionbench.dev-memory-exposure.v1',cell=cell,version=version,model_input=request,entries=entries))


def parse_generation(text, config=None, manifest=None):
    """Classify only model-authored output; provider/evidence errors stay outside."""
    from openarm365_dev.policy_validation import validate_generated_policy
    from .dev_help import public_screen
    text=text.strip()
    if text.startswith('```'):text=re.sub(r'^```(?:json)?\s*|\s*```$','',text)
    try:
        value=json.loads(text);source=value['source'];reason=value['reason']
        if not isinstance(source,str) or not isinstance(reason,str):raise ValueError()
    except (ValueError,TypeError,KeyError):return None,None,'unparsable_output'
    try:size=len(source.encode())
    except UnicodeError:return None,None,'unparsable_output'
    if size>source_limit(config or {},manifest):return None,None,'source_size_limit_exceeded'
    try:public_screen(source);public_screen(reason)
    except ValueError:return None,None,'public_source_validation_failed'
    try:validate_generated_policy(source,config=config or {},manifest=manifest)
    except Exception:return source,reason,'source_validation_failed'
    return source,reason,None


def generate(store,cell,config,origin,feedback,owner,*,memory=None,client=None,version=1):
    c=store.cell(cell)
    if c.get('run_notes') or c.get('memory_family') or c.get('memory_authority') or (c.get('planner_features') or {}).get('memory'):raise DevConflict('validation cell must have clean Memory')
    with store.tx() as db:experiment=store.get(db,'experiments',c['experiment_id'])
    if experiment['selection'].get('model_source')!='codex':raise DevConflict('validation requires Codex')
    directory=Path(c['artifact_dir'])/f'v{version}';directory.mkdir(exist_ok=True)
    if (directory/'memory-input.json').exists() or list(directory.glob('memory-run*')):raise DevConflict('ordinary Memory artifacts in validation arm')
    # Copy the frozen origin into this executor catalog, never reference another run.
    parent=immutable(directory/'parent-source.py',checked(origin).read_text())
    if parent['sha256']!=origin['sha256']:raise DevConflict('origin bytes changed')
    # Every attempt receives the same per-arm allowance, never accumulated headroom.
    base=c['budget'] if version==1 else json.loads(checked(experiment['manifest']).read_bytes())['cell_budget']
    remaining=dict(base)
    manifest=json.loads(checked(experiment['manifest']).read_bytes())
    messages=model_messages(config,checked(parent).read_text(),feedback,{'memory':[{'content':memory['guidance']}] if memory else []},remaining,source_size_limit_bytes=source_limit(config,manifest),validation_memory=True)
    if memory and memory.get('label'):messages[0]['content']+='\n'+memory['label']
    text,request,response,usage=provider_call(store,cell,version,'model',messages,owner,directory,client)
    source,reason,error=parse_generation(text,config,manifest)
    exposure=exposure_receipt(directory,cell,version,request,parent,memory=memory)
    record=dict(version=version,source=immutable(directory/'policy.py',source if source is not None else checked(parent).read_text()),parent_source=parent,dev_memory_exposure=exposure,
        model_input=request,model_output=response,usage=usage,reason=reason or 'Invalid Dev output; no execution',valid=error is None,validation_error=error,
        config_sha256=sha(Path(c['artifact_dir'])/'config.json'),manifest=experiment['manifest'],formal_eligible=False)
    if error:record['status']='invalid_generation'
    immutable(directory/'version.json',record);store.version(cell,record)
    return record
