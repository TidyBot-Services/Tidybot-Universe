"""Fake Codex: real keyless child; exercise its bridge with text, frames and schema."""
import base64
import json
import os
from pathlib import Path
import re
import sys
import urllib.request


def request_body(argv, root=Path('.')):
    model=argv[argv.index('-m')+1]
    body=dict(model=model,input=[dict(role='user',content='Inspect the public record')],reasoning=dict(effort='high'))
    if '--output-schema' in argv:
        schema=json.loads(Path(argv[argv.index('--output-schema')+1]).read_text())
        frames=sorted((root/'frames').glob('*.png'))
        body['input'] += [dict(type='function_call',name='view_image',call_id='frame',arguments='{}'),
            dict(type='function_call_output',call_id='frame',output=[dict(type='input_image',image_url='data:image/png;base64,'+base64.b64encode(frames[0].read_bytes()).decode())])]
        body['text']=dict(format=dict(type='json_schema',name='eval',schema=schema,strict=True))
    return body


def events(raw):
    output=[]
    output.append(dict(type='thread.started',thread_id='fake-codex'))
    for line in raw.splitlines():
        if not line.startswith(b'data: '):continue
        value=json.loads(line[6:])
        if value.get('type')=='response.completed':
            for item in value['response']['output']:
                if item['type']=='message':
                    output.append(dict(type='item.completed',item=dict(type='agent_message',text=''.join(p['text'] for p in item['content']))))
            usage=value['response']['usage']
            output.append(dict(type='turn.completed',usage=dict(input_tokens=usage['input_tokens'],output_tokens=usage['output_tokens'])))
    return b''.join((json.dumps(row)+'\n').encode() for row in output)


def main():
    argv=sys.argv[1:]
    Path('child-env.json').write_text(json.dumps(dict(os.environ)))
    options=next(v for v in argv if v.startswith('model_providers.parcc='))
    url=re.search(r'base_url="([^"]+)"',options)[1]
    token_env=re.search(r'env_key="([^"]+)"',options)[1]
    req=urllib.request.Request(url+'/responses',data=json.dumps(request_body(argv)).encode(),
        headers={'Authorization':'Bearer '+os.environ[token_env],'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=10) as response:raw=response.read()
    sys.stdout.buffer.write(events(raw))


if __name__=='__main__':main()
