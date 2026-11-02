"""Local lobby HTTP adapter. The host supplies its existing control-token decision."""
import json
from http.cookies import SimpleCookie, CookieError
from pathlib import Path
import re
from urllib.parse import urlsplit

from .demo_engine import DemoEngine, EXPERIMENT
from .lobby import LobbyError


def prepare(lobby, bridge):
    return DemoEngine(lobby, bridge) if isinstance(lobby,dict) else lobby


def viewer_of(handler, engine):
    cookie = SimpleCookie()
    try:cookie.load(handler.headers.get('Cookie',''))
    except CookieError:return None
    value = cookie.get('ab_lobby')
    return engine.lobby.identify(value.value if value else None,create=False)[0]


def _send(handler, value, status=200, cookie=None):
    headers = {'Cache-Control':'no-store'}
    if cookie:headers['Set-Cookie'] = f'ab_lobby={cookie}; Path=/; HttpOnly; SameSite=Lax'
    handler._send(json.dumps(value,ensure_ascii=False).encode(), 'application/json; charset=utf-8', status, headers)


def _body(handler, fields):
    try:size = int(handler.headers.get('Content-Length','0'))
    except ValueError:raise LobbyError('invalid_body',400) from None
    if handler.headers.get('Content-Type','').split(';')[0] != 'application/json' or not 0<size<=32768:
        raise LobbyError('invalid_body',400)
    try:body = json.loads(handler.rfile.read(size))
    except (ValueError,UnicodeError):raise LobbyError('invalid_body',400) from None
    if not isinstance(body,dict) or set(body)-set(fields):raise LobbyError('invalid_body',400)
    # Even malicious input must never persist an incoming control token or identity cookie.
    forbidden = [handler.headers.get('X-Attention-Token','')]
    cookie = SimpleCookie()
    try:cookie.load(handler.headers.get('Cookie',''))
    except CookieError:pass
    if cookie.get('ab_lobby'):forbidden.append(cookie['ab_lobby'].value)
    if any(secret and secret in json.dumps(body,ensure_ascii=False) for secret in forbidden):
        raise LobbyError('invalid_body',400)
    return body


def _file(handler, result):
    value, content = result[:2]
    status = result[2] if len(result)>2 else 200
    headers = dict(result[3]) if len(result)>3 else {}
    headers['Cache-Control'] = 'no-store'
    if isinstance(value,Path):
        size = value.stat().st_size
        start, end = 0, size-1
        if handler.headers.get('Range'):
            match = re.fullmatch(r'bytes=(\d+)-(\d*)',handler.headers['Range'])
            if not match or int(match[1])>=size:
                handler._send(b'',content,416,{'Cache-Control':'no-store','Content-Range':f'bytes */{size}'})
                return
            start = int(match[1]);end = min(int(match[2]) if match[2] else end,end)
            if end<start:raise LobbyError('invalid_range',400)
            status = 206;headers['Content-Range'] = f'bytes {start}-{end}/{size}'
        headers['Accept-Ranges'] = 'bytes'
        with value.open('rb') as stream:
            stream.seek(start)
            raw = stream.read(end-start+1)
    elif content.startswith('application/json'):raw = json.dumps(value,ensure_ascii=False).encode()
    else:raw = value.encode() if isinstance(value,str) else value
    handler._send(raw,content,status,headers)


def handle(handler, lobby, *, authorized, viewer):
    """Return False for unrelated routes; never include request data in errors."""
    parts = urlsplit(handler.path)
    path, method = parts.path, handler.command
    match = EXPERIMENT.fullmatch(path)
    simulated = lobby.lobby.find(match[1]) if match else None
    if path == '/api/runs' or path.startswith('/api/lobby') or simulated:
        # BaseHTTPRequestHandler logs the peer address in send_response(). Lobby
        # routes never pass request metadata to that logger.
        handler.log_request = lambda *args: None
    if not (path == '/api/lobby' or path.startswith('/api/lobby/') or simulated):return False
    try:
        if method != 'GET' and not authorized:raise LobbyError('control_token_required',403)
        if parts.query or '..' in path or '//' in path:raise LobbyError('invalid_path',400)
        if simulated:
            lobby.tick()
            run = lobby.lobby.find(match[1])
            if method == 'GET':_file(handler,lobby.get(run,match[2] or '',viewer))
            elif method == 'POST' and match[2] == '/cancel':
                _body(handler,())
                _send(handler,lobby.lobby.cancel(run['run_id'],viewer))
            else:raise LobbyError('simulated_run')
            return True
        if path == '/api/lobby' and method == 'GET':
            cookie = None
            if viewer is None:viewer,cookie = lobby.lobby.identify()
            _send(handler,lobby.lobby.snapshot(viewer),cookie=cookie)
            return True
        if method == 'POST':
            if path == '/api/lobby/clock':
                body = _body(handler,('speed',))
                _send(handler,lobby.lobby.set_speed(body.get('speed')))
                return True
            if viewer is None:raise LobbyError('not_owner',403)
            if path == '/api/lobby/runs':
                body = _body(handler,('task_key','strategy','dev_source','share_replies'))
                if not isinstance(body.get('task_key'),str):raise LobbyError('invalid_body',400)
                _send(handler,lobby.lobby.enqueue(viewer,**body),201)
                return True
            action = re.fullmatch(r'/api/lobby/runs/(sim-[a-f0-9]{8})/(cancel|reply|share)',path)
            if action:
                run_id, operation = action.groups()
                if operation == 'cancel':
                    _body(handler,())
                    result = lobby.lobby.cancel(run_id,viewer)
                elif operation == 'share':
                    body = _body(handler,('share',))
                    result = lobby.lobby.share(run_id,viewer,body.get('share'))
                else:
                    body = _body(handler,('outcome','category','content'))
                    if 'outcome' not in body:raise LobbyError('invalid_body',400)
                    result = lobby.lobby.reply(run_id,viewer,**body)
                _send(handler,result)
                return True
        if method == 'GET':
            action = re.fullmatch(r'/api/lobby/runs/(sim-[a-f0-9]{8})/replies',path)
            if action:
                _send(handler,lobby.lobby.replies(action[1],viewer))
                return True
        raise LobbyError('not_found',404)
    except LobbyError as error:
        _send(handler,dict(error=dict(code=error.code,message=error.code.replace('_',' '))),error.status)
    except (BrokenPipeError,ConnectionResetError):
        pass
    except Exception:
        _send(handler,dict(error=dict(code='source_unavailable',message='Recorded source unavailable')),503)
    return True
