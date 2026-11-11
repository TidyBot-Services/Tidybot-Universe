"""Read-only test port for the run page with the 3D view (test only, not the harness).

Usage: python3 -I free_view_preview.py <ui dir> <port> <dev-v2 root> <sim python> <cache dir>

- /ui/* comes from <ui dir> (the worktree), everything else is fetched from 8769 with GET.
- POST, PUT, PATCH and DELETE are refused, so nothing here can start, stop or change a run.
- One extra GET route stands in for the route the harness would get on a merge:
    /api/dev-v2/experiments/<experiment>/cells/<cell>/agent/free-view/<eNNNNNN>/<scene.json|geom.bin|poses.bin>
  The first request for an attempt exports its 3D bundle into <cache dir> (a few seconds), later ones read the cache.
"""
import gzip, http.server, json, re, subprocess, sys, threading, urllib.request, urllib.error
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

UI = Path(sys.argv[1]).resolve()
PORT = int(sys.argv[2])
ROOT = Path(sys.argv[3]).resolve()
SIM_PYTHON = sys.argv[4]
CACHE = Path(sys.argv[5]).resolve()
EXPORT = Path(__file__).resolve().parent / 'free_view_export.py'
UP = 'http://127.0.0.1:8769'
TYPES = {'.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8',
         '.png': 'image/png', '.jpg': 'image/jpeg', '.webp': 'image/webp', '.svg': 'image/svg+xml', '.json': 'application/json',
         '.bin': 'application/octet-stream'}
ROOT_TAG = re.compile(r'<div id="attentionbench-clear"[^>]*>')
FREE = re.compile(r'/api/dev-v2/experiments/(devexp-[a-f0-9]{24})/cells/([a-f0-9]{64})/agent/free-view/(e\d{6})/(scene\.json|geom\.bin|poses\.bin)')
LOCK = threading.Lock()


def bundle(experiment, cell, execution):
    """Return (directory, None) or (None, reason)."""
    out = CACHE / experiment / cell / execution
    if (out / 'scene.json').is_file():
        return out, None
    if (out / 'error.txt').is_file():
        return None, (out / 'error.txt').read_text()
    base = ROOT / experiment / 'artifacts' / experiment / 'cells' / cell / 'agent' / 'private' / 'executions' / execution
    sources = [d for d in base.glob('*') if (d / 'native-physics.jsonl').is_file() and (d / 'scene.xml').is_file()] if base.is_dir() else []
    if not sources:
        return None, 'This attempt has no per-step physics log or scene file on this machine, so it cannot be shown in 3D.'
    with LOCK:
        if (out / 'scene.json').is_file():
            return out, None
        tmp = out.with_name(out.name + '.tmp')
        tmp.mkdir(parents=True, exist_ok=True)
        try:
            run = subprocess.run([SIM_PYTHON, str(EXPORT), str(sources[0]), str(tmp)], capture_output=True, text=True, timeout=180,
                                 env={'MUJOCO_GL': 'egl', 'PATH': '/usr/bin:/bin'})
            problem = None if run.returncode == 0 and (tmp / 'scene.json').is_file() else (run.stderr.strip().splitlines() or ['export failed'])[-1]
        except subprocess.TimeoutExpired:
            problem = 'export took longer than 180 s'
        if problem:
            out.mkdir(parents=True, exist_ok=True)
            (out / 'error.txt').write_text('3D export failed: ' + problem)
            for f in tmp.glob('*'):
                f.unlink()
            tmp.rmdir()
            return None, '3D export failed: ' + problem
        if out.exists():
            out.rmdir()
        tmp.rename(out)
    return out, None


class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, ctype, extra=()):
        if len(body) > 4096 and 'gzip' in self.headers.get('Accept-Encoding', '') and ctype.startswith(('application/json', 'application/octet-stream', 'text/')):
            body = gzip.compress(body, 5)
            extra = [*extra, ('Content-Encoding', 'gzip')]
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        for k, v in extra:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def deny(self):
        self.send(403, b'{"error":"read-only preview: only GET is allowed"}', 'application/json')

    do_POST = do_PUT = do_PATCH = do_DELETE = deny

    def do_GET(self):
        url = urlsplit(self.path)
        free = FREE.fullmatch(url.path)
        if free:
            folder, reason = bundle(*free.groups()[:3])
            if not folder:
                return self.send(404, json.dumps({'error': reason}).encode(), 'application/json')
            f = folder / free.group(4)
            return self.send(200, f.read_bytes(), TYPES[f.suffix])
        if url.path in ('/ui', '/ui/', '/ui/index.html'):
            html = (UI / 'index.html').read_text()
            try:
                upstream = urllib.request.urlopen(UP + '/ui/', timeout=10).read().decode()
                tag = ROOT_TAG.search(upstream).group(0)
            except Exception:
                tag = ROOT_TAG.search(html).group(0)
            tag = tag.replace(' data-remote-tab="1"', '')
            if parse_qs(url.query).get('remote') == ['1']:
                tag = tag.replace('<div id="attentionbench-clear"', '<div id="attentionbench-clear" data-remote="1"')
            html = ROOT_TAG.sub(lambda _: tag, html, count=1)
            html = html.replace('<title>AttentionBench · Operations</title>', '<title>3D TEST · AttentionBench</title>')
            return self.send(200, html.encode(), TYPES['.html'])
        if url.path.startswith('/ui/'):
            f = (UI / url.path[4:]).resolve()
            if UI in f.parents and f.is_file():
                return self.send(200, f.read_bytes(), TYPES.get(f.suffix, 'application/octet-stream'))
        req = urllib.request.Request(UP + self.path, headers={k: v for k, v in self.headers.items() if k.lower() in ('range', 'accept', 'cookie')})
        try:
            r = urllib.request.urlopen(req, timeout=60)
        except urllib.error.HTTPError as e:
            r = e
        body = r.read()
        extra = [(k, v) for k, v in r.headers.items() if k.lower() in ('content-range', 'accept-ranges', 'etag', 'last-modified')]
        self.send_response(r.status if hasattr(r, 'status') else r.code)
        self.send_header('Content-Type', r.headers.get('Content-Type', 'application/octet-stream'))
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        for k, v in extra:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)


http.server.ThreadingHTTPServer(('127.0.0.1', PORT), H).serve_forever()
