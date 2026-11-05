"""Run-local Advisor chat client; credentials never enter child environments."""
import json
import time
import urllib.error
import urllib.request

from . import dev_agent_provider
from .dev_agent_secret import scrub
from .parcc_client import ParccError, ParccResponse


class ByokChatClient:
    def __init__(self, provider, *, timeout_seconds=90):
        self.provider=provider
        self.timeout_seconds=timeout_seconds

    def chat(self, *, model, messages, max_tokens, temperature=0.0, reasoning_effort=None):
        spec,key=self.provider.spec,self.provider.secret
        body=dict(model=model,messages=messages,temperature=temperature,stream=False)
        body[spec.token_param]=max_tokens
        effort=dev_agent_provider.reasoning_effort(spec,model,reasoning_effort)
        if spec.reasoning_param and effort:body[spec.reasoning_param]=effort
        body.update(json.loads(json.dumps(spec.extra_body)))
        started=time.monotonic();deadline=started+self.timeout_seconds;failures=[]
        for attempt in range(1,4):  # Initial request plus at most two retries, regardless of model name.
            try:
                remaining=deadline-time.monotonic()
                if remaining<=0:raise TimeoutError('provider_timeout')
                request=urllib.request.Request(spec.chat_url,data=json.dumps(body).encode(),method='POST',
                    headers={'Authorization':'Bearer '+key.reveal(),'Content-Type':'application/json'})
                with dev_agent_provider.open_request(request,timeout=remaining) as response:
                    raw=response.read()
                    request_id=response.headers.get('x-request-id') or response.headers.get('x-litellm-call-id')
                # Successful responses can echo credentials too; sanitize before returning any artifact.
                clean,_=scrub(raw.decode(),[key])
                value=json.loads(clean)
                if 'error' in value:raise ValueError(json.dumps(value['error']))
                choice=value['choices'][0];message=choice['message'];content=message.get('content')
                if not isinstance(content,str):raise ValueError('provider_missing_content')
                usage=value.get('usage') or {}
                prompt=usage.get('prompt_tokens',0);completion=usage.get('completion_tokens',0)
                usage=dict(usage,prompt_tokens=prompt,completion_tokens=completion,total_tokens=usage.get('total_tokens',prompt+completion))
                request_id=scrub(request_id,[key])[0] if request_id else None
                return ParccResponse(value.get('model') or model,content,message.get('reasoning_content'),
                    round(time.monotonic()-started,3),attempt,usage,request_id=request_id,
                    finish_reason=choice.get('finish_reason'),failed_attempts=tuple(failures))
            except Exception as exc:
                status=getattr(exc,'code',None)
                retryable=(isinstance(exc,urllib.error.HTTPError) and (status==429 or 500<=status<600)
                    or isinstance(exc,TimeoutError)
                    or isinstance(exc,urllib.error.URLError) and isinstance(exc.reason,TimeoutError))
                raw=exc.read(8192).decode(errors='replace') if isinstance(exc,urllib.error.HTTPError) else str(exc)
                detail,redacted=scrub(raw,[key])
                failures.append(dict(attempt=attempt,status=status,error=detail,redacted=redacted))
                if not retryable or attempt==3 or time.monotonic()>=deadline:raise ParccError(detail) from None
                time.sleep(.1*attempt)
