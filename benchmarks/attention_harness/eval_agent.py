"""System Eval agent: vision verdict for every executed Dev version."""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path

from . import v2_evaluator as vision
from .dev_help import public_screen
from .dev_feedback_evidence import visible_review

MODEL = 'parcc/Qwen'
# User decision, 2026-10-18: this fixed Qwen Eval is free. If the model
# changes, the ordinary limited Eval accounting applies again.
from .free_models import eval_is_unmetered
EVAL_MODEL_IS_FREE = eval_is_unmetered({'enabled':True,'model':MODEL})
def is_unmetered(config):
    return eval_is_unmetered(config)
PROMPT_VERSION = 'eval-agent-vision-v3'
OLD_PROMPT_VERSION = 'eval-agent-vision-v2'
CACHE_VERSION = 'eval-agent-image-cache-v1'
MAX_TOKENS = vision.VISION_TOKENS
FRAME_TOKEN_BOUND = 4096
SYSTEM=vision.SYSTEM.replace(
    'You are a robotics recording reviewer. Review this attempt only.',
    'You are the system Eval agent. Judge this executed code version from its public recording and trace only.').replace(
    'Your review is an opinion for the Dev agent; it never decides success.',
    'Your passed verdict ends this Dev cell; judge only from supplied public evidence.')
SYSTEM_V2 = SYSTEM
SYSTEM_V3 = SYSTEM.split('Recommend concrete repairs')[0] + '''Return only a JSON object with exactly passed (boolean), reason (nonempty factual text), and optional reference_suggestion (string). In reason describe what happened, at which step, and what was observed, citing public frames, trace events or stderr. Never put repair advice in reason: no should, suggest, recommend, fix, try, need to, 应该, 建议, 尝试, 修复, 改为, 调整, or imperatives. Put optional repair advice in reference_suggestion. If evidence is missing, say so. Use false unless frames and numerical observations clearly show the public task goal achieved. Keep both texts concise.'''
REPAIR_WORDS = re.compile(r'(?i)\b(?:should|suggest|recommend|fix|try|need to|must|change|adjust|use instead|please)\b|^\s*(?:use|move|add|remove|replace|consider)\b|应该|建议|尝试|修复|改为|调整|需要|请(?:把|将|用|改)')


def split_reason(reason, suggestion=''):
    """Conservatively move whole advice-bearing sentences away from Dev-visible facts."""
    facts, advice = [], []
    # ASCII sentence punctuation needs following whitespace; a decimal point,
    # version separator or ellipsis with no trailing space stays in its text.
    # Chinese sentence punctuation may separate sentences without whitespace.
    for sentence in re.split(r'(?<=[.!?])\s+|(?<=[。！？])\s*', reason):
        if not sentence.strip(): continue
        (advice if REPAIR_WORDS.search(sentence) else facts).append(sentence.strip())
    if not advice:return reason.strip(),suggestion.strip(),False
    moved = ' '.join(advice)
    return ' '.join(facts), ' '.join(part for part in (suggestion.strip(), moved) if part), bool(advice)


def parse_v3(text):
    try:
        # Qwen may wrap the object in a code fence or explain it on either side.
        # Decode the first object itself; its field contract remains strict.
        start=text.find('{')
        if start<0:raise ValueError('missing Eval object')
        value,_=json.JSONDecoder().raw_decode(text,start)
        if not isinstance(value,dict) or set(value)-{'passed','reason','reference_suggestion'} or not {'passed','reason'}<=set(value):raise ValueError('missing Eval fields')
        if type(value['passed']) is not bool or not isinstance(value['reason'],str) or not value['reason'].strip() or not isinstance(value.get('reference_suggestion',''),str):raise ValueError('invalid Eval fields')
        reason,suggestion,moved=split_reason(value['reason'][:1500],value.get('reference_suggestion','')[:1500])
        if not reason:raise ValueError('no factual Eval reason remains')
        return {'passed':value['passed'],'reason':reason,'reference_suggestion':suggestion,'reason_advice_moved':moved}
    except (ValueError,TypeError) as exc:
        raise ValueError('invalid Eval v3 JSON') from exc


def _failed_answer(response, usage):
    details=response.usage.get('completion_tokens_details')
    diagnostic_usage=usage.copy()
    if isinstance(details,dict) and type(details.get('reasoning_tokens')) is int and details['reasoning_tokens']>=0:
        diagnostic_usage['reasoning_tokens']=details['reasoning_tokens']
    return response.content[:4000],getattr(response,'finish_reason',None),diagnostic_usage


class EvalUnavailable(Exception):
    def __init__(self, reason, *, raw_answer_excerpt=None, finish_reason=None, usage=None):
        super().__init__(reason)
        self.raw_answer_excerpt=raw_answer_excerpt
        self.finish_reason=finish_reason
        self.usage=usage or {}


def default_config(enabled=True, model_source='parcc'):
    from .codex_client import MODEL as CODEX_MODEL
    if model_source not in {'parcc','codex'}:raise ValueError('unknown model source')
    result={'enabled': enabled, 'model': CODEX_MODEL if model_source=='codex' else MODEL, 'prompt_version': PROMPT_VERSION,
            'prompt_sha256': hashlib.sha256(SYSTEM_V3.encode()).hexdigest(),
            'max_attempts': 2, 'attempt_timeout_seconds': 55, 'cache_version': CACHE_VERSION,
            'cache_namespace':'eval','session_namespace':'eval-vision'}
    if model_source=='codex':result.update(model_source='codex',reasoning_effort='high')
    return result


def normalize(value, model_source='parcc', *, agent=False):
    if not isinstance(value, dict):raise ValueError('Eval config must be an object')
    base=default_config(value.get('enabled', True), model_source)
    if agent and model_source=='codex':
        # Agent runs record their Eval model here (10-29); the one-call Eval stays fixed.
        from .dev_agent_config import MODELS
        if value.get('model') in MODELS:base['model']=value['model']
    if model_source=='codex' and value.get('prompt_version')==OLD_PROMPT_VERSION:raise ValueError('legacy Eval cannot use Codex')
    if value.get('prompt_version')==OLD_PROMPT_VERSION:
        base['prompt_version']=OLD_PROMPT_VERSION
        base['prompt_sha256']=hashlib.sha256(SYSTEM_V2.encode()).hexdigest()
        base.pop('cache_namespace');base.pop('session_namespace')
    if set(value)-set(base):raise ValueError('unknown Eval configuration')
    out={**base, **value}
    expected_sha=hashlib.sha256((SYSTEM_V2 if out['prompt_version']==OLD_PROMPT_VERSION else SYSTEM_V3).encode()).hexdigest()
    if type(out['enabled']) is not bool or out['model']!=base['model'] or out['prompt_version'] not in (PROMPT_VERSION,OLD_PROMPT_VERSION) or out['prompt_sha256']!=expected_sha or out['cache_version']!=CACHE_VERSION or any(out[k]!=base[k] for k in ('cache_namespace','session_namespace','model_source','reasoning_effort') if k in base):
        raise ValueError('fixed vision Eval configuration required')
    if type(out['max_attempts']) is not int or not 1<=out['max_attempts']<=3 or type(out['attempt_timeout_seconds']) is not int or out['attempt_timeout_seconds']<1 or out['max_attempts']*out['attempt_timeout_seconds']>120:
        raise ValueError('Eval retries exceed reserve')
    return out


def from_config(config):
    value=config.get('eval_agent')
    if isinstance(value,dict) and value.get('enabled'):
        source=config.get('model_source','parcc')
        return normalize(value if source=='codex' or set(value)!={'enabled','model'} else {'enabled':value['enabled']},source)
    return None


def ask(evidence, config, client, *, cache_dir=None):
    vision.assert_public(evidence);public_screen({k:v for k,v in evidence.items() if k!='frames'})
    frames=evidence.get('frames',[])
    if not frames:raise EvalUnavailable('no_frames')
    key=hashlib.sha256(vision.encoded({'config':config,'images':[(f['view'],f['sha256']) for f in frames],
        'text':{k:v for k,v in evidence.items() if k!='frames'}}).encode()).hexdigest()
    path=Path(cache_dir)/(config.get('cache_namespace','eval')+'-'+key+'.json') if cache_dir else None
    if path and path.is_file():
        record=json.loads(path.read_text())
        if record.get('cache_key')==key:return {**record,'cache_hit':True,'usage':{},'latency_seconds':0}
    begun=time.monotonic();last='unavailable';usage={};failed_answer=None
    for attempt in range(1,config['max_attempts']+1):
        try:
            messages=vision.evaluator_messages(evidence);messages[0]={'role':'system','content':SYSTEM_V2 if config['prompt_version']==OLD_PROMPT_VERSION else SYSTEM_V3}
            if evidence.get('success_condition',{}).get('schema')=='attentionbench.success-condition.fixture.v1':
                from .dev_fixture_success import EVAL_RULE
                messages[0]['content']+=EVAL_RULE
            response=vision._call(client,config['attempt_timeout_seconds'],model=config['model'],messages=messages,max_tokens=MAX_TOKENS,temperature=0,**({'reasoning_effort':config['reasoning_effort']} if config.get('model_source')=='codex' else {}))
            usage={k:response.usage[k] for k in ('total_tokens','prompt_tokens','completion_tokens') if type(response.usage.get(k)) is int and response.usage[k]>=0}
            if config.get('model_source')=='codex':usage=dict(response.usage)
            if config['prompt_version']==OLD_PROMPT_VERSION:
                parsed=vision.parse_eval_result(response.content)
                if parsed['status']!='completed':last='unparseable_answer';continue
                content={'feedback':parsed['feedback']}
            else:
                try:parsed=parse_v3(response.content)
                except ValueError:
                    last='unparseable_answer';failed_answer=_failed_answer(response,usage);continue
                content={k:parsed[k] for k in ('reason','reference_suggestion','reason_advice_moved')}
            public_screen(content);vision.assert_public(content)
            record={'schema_version':'attentionbench.eval-agent.v2' if config['prompt_version']==OLD_PROMPT_VERSION else 'attentionbench.eval-agent.v3','status':'completed','passed':parsed['passed'],**content,
                'model':config['model'],'mode':'vision','prompt_version':config['prompt_version'],'frames':[{k:f[k] for k in ('filename','sha256','view') if k in f} for f in frames],
                'views':sorted({f.get('view','external') for f in frames}),'cache_key':key,'cache_hit':False,'attempts':attempt,'usage':usage,'latency_seconds':time.monotonic()-begun}
            if getattr(response,'failed_attempts',()):record.update(provider_attempts=response.attempts,provider_failed_attempts=list(response.failed_attempts))
            if config.get('model_source')=='codex':record.update(model_source='codex',quota_before=getattr(client,'quota_before',None),quota_after=getattr(client,'quota_after',None))
            if path:
                path.parent.mkdir(parents=True,exist_ok=True);temp=path.with_suffix('.tmp');temp.write_text(json.dumps(record,ensure_ascii=False));temp.replace(path)
            return record
        except Exception as exc:
            last=vision._safe_detail(exc)
    if failed_answer is not None:
        raise EvalUnavailable('vision unavailable after retries: '+last,
                              raw_answer_excerpt=failed_answer[0],finish_reason=failed_answer[1],usage=failed_answer[2])
    raise EvalUnavailable('vision unavailable after retries: '+last)
