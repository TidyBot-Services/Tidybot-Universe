"""Ephemeral local provider; all credential observations remain in memory."""
import json
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import threading
import time


class FakeProvider:
    def __init__(self, expected, *, mode=None, tools=True):
        self.expected=expected;self.mode=mode;self.tools=tools;self.authorizations=[];self.requests=[]
        provider=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):self.respond()
            def do_POST(self):self.respond()
            def respond(self):
                try:provider.handle(self)
                except (BrokenPipeError,ConnectionResetError):pass
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.server.daemon_threads=True
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
    @property
    def base_url(self):return 'http://127.0.0.1:%d/v1'%self.server.server_port
    def close(self):self.server.shutdown();self.server.server_close();self.thread.join()
    def __enter__(self):return self
    def __exit__(self,*args):self.close()
    def handle(self,handler):
        auth=handler.headers.get('Authorization','');self.authorizations.append(auth)
        body=json.loads(handler.rfile.read(int(handler.headers.get('Content-Length',0)))) if handler.command=='POST' else {}
        self.requests.append((handler.path,body))
        expected=self.expected
        if isinstance(expected,dict):expected=expected.get((body.get('model'),handler.path),expected.get(handler.path,''))
        def send(status,value,content='application/json'):
            raw=value if isinstance(value,bytes) else json.dumps(value).encode()
            handler.send_response(status);handler.send_header('Content-Type',content);handler.send_header('Content-Length',str(len(raw)))
            handler.end_headers();handler.wfile.write(raw)
        if self.mode=='slow':time.sleep(.1)
        if self.mode in ('echo_key_401','mask_key_401') or auth!='Bearer '+expected:
            message=('Incorrect API key provided: '+expected if self.mode=='echo_key_401' else
                     'Incorrect API key provided: sk-xxxx****abcd' if self.mode=='mask_key_401' else 'Unauthorized')
            send(401,dict(error=dict(message=message)));return
        if self.mode in ('echo_key_429','echo_key_500'):
            send(int(self.mode[-3:]),dict(error=dict(message=expected)));return
        if self.mode=='http_500':send(500,dict(error=dict(message='provider unavailable')));return
        if self.mode=='bad_json':send(200,b'{invalid');return
        if handler.path=='/v1/models':send(200,dict(data=[dict(id='fake-model')]));return
        if handler.path!='/v1/chat/completions':send(404,dict(error='not found'));return
        function=(body.get('tools') or [dict(function=dict(name='ping'))])[0]['function']['name']
        call=dict(index=0,id='call_test',type='function',function=dict(name=function,arguments='{}'))
        delta=dict(tool_calls=[call]) if self.tools and body.get('tools') else dict(content='ok')
        if body.get('response_format'):
            delta=dict(content=json.dumps(dict(passed=False,reason='Frame inspected.',evidence=[dict(claim='Frame inspected',source='frames/overview-000000-sim-0.000000s.png')],advice='Try again.')))
        usage=dict(prompt_tokens=3,completion_tokens=2,total_tokens=5)
        if not body.get('stream'):
            send(200,dict(model=body['model'],choices=[dict(message=dict(role='assistant',**delta),finish_reason='stop')],usage=usage));return
        chunk=dict(model=body['model'],choices=[dict(delta=delta,finish_reason='tool_calls' if 'tool_calls' in delta else 'stop')],usage=usage)
        raw=b'data: '+json.dumps(chunk).encode()+b'\n\n'
        if self.mode!='drop_stream':raw+=b'data: [DONE]\n\n'
        send(200,raw,'text/event-stream')
