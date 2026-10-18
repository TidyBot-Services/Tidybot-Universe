"""System Eval agent: vision verdict for every executed Dev version."""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from . import v2_evaluator as vision
from .dev_help import public_screen

MODEL = 'parcc/Qwen'
# User decision, 2026-10-18: this fixed Qwen Eval is free. If the model
# changes, the ordinary limited Eval accounting applies again.
from .free_models import eval_is_unmetered
EVAL_MODEL_IS_FREE = eval_is_unmetered({'enabled':True,'model':MODEL})
def is_unmetered(config):
    return eval_is_unmetered(config)
PROMPT_VERSION = 'eval-agent-vision-v2'
CACHE_VERSION = 'eval-agent-image-cache-v1'
MAX_TOKENS = vision.VISION_TOKENS
FRAME_TOKEN_BOUND = 4096
SYSTEM=vision.SYSTEM.replace(
    'You are a robotics recording reviewer. Review this attempt only.',
    'You are the system Eval agent. Judge this executed code version from its public recording and trace only.').replace(
    'Your review is an opinion for the Dev agent; it never decides success.',
    'Your passed verdict ends this Dev cell; judge only from supplied public evidence.')


class EvalUnavailable(Exception):
    pass


def default_config(enabled=True):
    return {'enabled': enabled, 'model': MODEL, 'prompt_version': PROMPT_VERSION,
            'prompt_sha256': hashlib.sha256(SYSTEM.encode()).hexdigest(),
            'max_attempts': 2, 'attempt_timeout_seconds': 55, 'cache_version': CACHE_VERSION}


def normalize(value):
    if not isinstance(value, dict):raise ValueError('Eval config must be an object')
    base=default_config(value.get('enabled', True))
    if set(value)-set(base):raise ValueError('unknown Eval configuration')
    out={**base, **value}
    if type(out['enabled']) is not bool or out['model']!=MODEL or out['prompt_version']!=PROMPT_VERSION or out['prompt_sha256']!=base['prompt_sha256'] or out['cache_version']!=CACHE_VERSION:
        raise ValueError('fixed vision Eval configuration required')
    if type(out['max_attempts']) is not int or not 1<=out['max_attempts']<=3 or type(out['attempt_timeout_seconds']) is not int or out['attempt_timeout_seconds']<1 or out['max_attempts']*out['attempt_timeout_seconds']>120:
        raise ValueError('Eval retries exceed reserve')
    return out


def from_config(config):
    value=config.get('eval_agent')
    if isinstance(value,dict) and value.get('enabled'):
        return normalize(value if set(value)!={'enabled','model'} else {'enabled':value['enabled']})
    return None


def ask(evidence, config, client, *, cache_dir=None):
    vision.assert_public(evidence);public_screen({k:v for k,v in evidence.items() if k!='frames'})
    frames=evidence.get('frames',[])
    if not frames:raise EvalUnavailable('no_frames')
    key=hashlib.sha256(vision.encoded({'config':config,'images':[(f['view'],f['sha256']) for f in frames],
        'text':{k:v for k,v in evidence.items() if k!='frames'}}).encode()).hexdigest()
    path=Path(cache_dir)/('eval-'+key+'.json') if cache_dir else None
    if path and path.is_file():
        record=json.loads(path.read_text())
        if record.get('cache_key')==key:return {**record,'cache_hit':True,'usage':{},'latency_seconds':0}
    begun=time.monotonic();last='unavailable';usage={}
    for attempt in range(1,config['max_attempts']+1):
        try:
            messages=vision.evaluator_messages(evidence);messages[0]={'role':'system','content':SYSTEM}
            response=vision._call(client,config['attempt_timeout_seconds'],model=config['model'],messages=messages,max_tokens=MAX_TOKENS,temperature=0)
            usage={k:response.usage[k] for k in ('total_tokens','prompt_tokens','completion_tokens') if type(response.usage.get(k)) is int and response.usage[k]>=0}
            parsed=vision.parse_eval_result(response.content)
            if parsed['status']!='completed':last='unparseable_answer';continue
            public_screen(parsed['feedback']);vision.assert_public(parsed)
            record={'schema_version':'attentionbench.eval-agent.v2','status':'completed','passed':parsed['passed'],'feedback':parsed['feedback'],
                'model':config['model'],'mode':'vision','prompt_version':config['prompt_version'],'frames':[{k:f[k] for k in ('filename','sha256','view') if k in f} for f in frames],
                'views':sorted({f.get('view','external') for f in frames}),'cache_key':key,'cache_hit':False,'attempts':attempt,'usage':usage,'latency_seconds':time.monotonic()-begun}
            if path:
                path.parent.mkdir(parents=True,exist_ok=True);temp=path.with_suffix('.tmp');temp.write_text(json.dumps(record,ensure_ascii=False));temp.replace(path)
            return record
        except Exception as exc:
            last=vision._safe_detail(exc)
    raise EvalUnavailable('vision unavailable after retries: '+last)
