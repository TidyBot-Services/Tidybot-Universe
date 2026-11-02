"""Replay mode of the Run page: the page for recorded runs, with no control token and no write access.

The page itself is the ordinary Run page (ui/index.html + live.js) opened as data-mode="replay". Replies given
there are not run controls; they are recorded by the separate collector service, reached through the fixed
paths below (the collector listens on loopback only).
"""
import json
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

PAGE = '/ui/replay'
CONFIG = '/api/replay/config'
# server path -> (method, collector path)
COLLECTOR = {'/api/replay/reply': ('POST', '/api/replay-reply'),
             '/api/replay/delete': ('POST', '/api/replay-reply/delete'),
             '/api/replay/viewed': ('POST', '/api/replay-reply/viewed'),
             '/api/replay/count': ('GET', '/api/replay-count')}
MAX_BODY = 8192
LABEL = re.compile(r'^[a-z0-9-]{1,24}$')


def load_config(value):
    """Validate a replay configuration: {runs: {experiment_id: label}, collector_url, dataset_version, requires_code}."""
    if not isinstance(value, dict) or set(value) - {'runs', 'collector_url', 'dataset_version', 'requires_code'}:
        raise ValueError('invalid replay configuration')
    runs = value.get('runs')
    if not isinstance(runs, dict) or not runs:
        raise ValueError('replay configuration needs at least one run')
    for experiment, label in runs.items():
        if not re.fullmatch(r'devexp-[a-f0-9]{24}', experiment) or not isinstance(label, str) or not LABEL.match(label):
            raise ValueError('invalid replay run entry')
    url = urlsplit(value.get('collector_url', ''))
    if url.scheme != 'http' or url.hostname not in ('127.0.0.1', 'localhost', '::1') or url.path not in ('', '/') or url.query:
        raise ValueError('the collector must be a loopback http origin')
    return dict(runs=dict(runs), collector=f'http://{url.hostname}:{url.port}', dataset_version=str(value.get('dataset_version') or ''),
                requires_code=bool(value.get('requires_code')))


def page(index_html):
    """The Run page for replay: the live page without a control token, marked data-replay."""
    return index_html.replace('data-mode="demo"', 'data-mode="live"', 1).replace('data-mode="live"', 'data-mode="live" data-replay="1"', 1)


def public_config(config):
    return dict(mode='replay', runs=config['runs'], dataset_version=config['dataset_version'], requires_code=config['requires_code'])


def handle(handler, config):
    """Serve one /api/replay/* request; returns True when the path was one of ours."""
    parts = urlsplit(handler.path)
    if parts.path == CONFIG and handler.command == 'GET':
        handler.reply(public_config(config))
        return True
    route = COLLECTOR.get(parts.path)
    if route is None:
        return False
    method, target = route
    if handler.command != method:
        handler.reply(dict(error='method_not_allowed'), 405)
        return True
    body = None
    if method == 'POST':
        size = int(handler.headers.get('Content-Length', 0))
        if not 0 < size <= MAX_BODY or handler.headers.get('Content-Type', '').split(';')[0] != 'application/json':
            handler.reply(dict(error='bad_request'), 400)
            return True
        body = handler.rfile.read(size)
        try:
            json.loads(body)
        except ValueError:
            handler.reply(dict(error='bad_request'), 400)
            return True
    request = Request(config['collector'] + target + (('?' + parts.query) if parts.query and method == 'GET' else ''), data=body, method=method,
                      headers={'Content-Type': 'application/json'} if body is not None else {})
    # The collector sees the visitor's address only as a salted hash; pass it on as the one forwarded header.
    request.add_header('X-Forwarded-For', handler.client_address[0])
    try:
        with urlopen(request, timeout=10) as response:
            payload, status = response.read(), response.status
    except HTTPError as error:
        payload, status = error.read(), error.code
    except (URLError, OSError):
        handler.reply(dict(error='collector_unavailable'), 503)
        return True
    try:
        handler.reply(json.loads(payload), status)
    except ValueError:
        handler.reply(dict(error='collector_unavailable'), 502)
    return True
