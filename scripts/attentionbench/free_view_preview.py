"""Read-only test port for the run page with the 3D view (test only, not the harness).

Usage: python3 -I free_view_preview.py <ui dir> <port> <dev-v2 root> <sim python> <cache dir> [<live execution dir>]

- /ui/* comes from <ui dir> (the worktree), everything else is fetched from 8769 with GET.
- POST, PUT, PATCH and DELETE are refused, so nothing here can start, stop or change a run.
- One GET route is answered here instead of by 8769:
    /api/dev-v2/experiments/<experiment>/cells/<cell>/agent/files/free-view/<eNNNNNN>/<scene.json|geom.bin.gz|poses.bin.gz>
  It is the harness's own route (benchmarks/attention_harness/free_view.py) with one difference: the built files
  go to <cache dir> instead of into the run's folder, so this port never writes to the run store.
- Live 3D: .../agent/files/free-view/live/<scene.json|geom.bin.gz|status.json|poses-<n>.bin> is the harness's live
  route too.  The attempt it follows is <live execution dir> when given (e.g. a finished attempt being written out
  again step by step, to try live without a run), else the newest unfinished execution of that cell.
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
LIVE_SOURCE = Path(sys.argv[6]).resolve() if len(sys.argv) > 6 else None
FREE_LIVE = re.compile(r'/api/dev-v2/experiments/(devexp-[a-f0-9]{24})/cells/([a-f0-9]{64})/agent/files/(free-view/live/[a-z0-9.-]+)')
FREE = re.compile(r'/api/dev-v2/experiments/(devexp-[a-f0-9]{24})/cells/([a-f0-9]{64})/agent/files/free-view/(e\d{6})/(scene\.json|geom\.bin\.gz|poses\.bin\.gz)')
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from benchmarks.attention_harness import free_view
free_view.LIVE_ROOT = CACHE / 'live'
free_view._sim_python = lambda: SIM_PYTHON


def live_cell(experiment, cell):
    """The cell as the harness would describe it while an attempt runs (this port has no run store)."""
    root = ROOT / experiment / 'artifacts' / experiment / 'cells' / cell
    episode = LIVE_SOURCE
    if episode is None:
        running = [d for d in root.glob('agent/private/executions/e*/openarm365-dev-*') if d.is_dir() and not (d / 'native_result.json').exists()]
        episode = max(running, key=lambda d: d.stat().st_mtime, default=None)
    return dict(dev_mode='agent', key=cell, state='running' if episode else 'completed',
                artifact_dir=str(episode.parents[1] if LIVE_SOURCE else root), active_episode=str(episode) if episode else None)


def export(episode, out):
    """Same build as the harness, but with the sim Python given on the command line."""
    try:
        run = subprocess.run([SIM_PYTHON, str(EXPORT), str(episode), str(out)], capture_output=True, text=True, timeout=180, env={'MUJOCO_GL': 'egl', 'PATH': '/usr/bin:/bin'})
    except subprocess.TimeoutExpired:
        return 'the build took longer than 180 s'
    return None if run.returncode == 0 and (out / 'scene.json').is_file() else (run.stderr.strip().splitlines() or ['the build failed'])[-1]


def bundle(experiment, cell, execution):
    """Return (directory, None) or (None, reason). The built files go to <cache dir>, never into the run's own folder."""
    base = ROOT / experiment / 'artifacts' / experiment / 'cells' / cell / 'agent' / 'private' / 'executions' / execution
    sources = [d for d in base.glob('openarm365-dev-*') if d.is_dir()] if base.is_dir() else []
    if len(sources) != 1:
        return None, 'This attempt has no recording on this machine, so it cannot be shown in 3D.'
    try:
        return free_view.build(sources[0], CACHE / experiment / cell / execution, export), None
    except FileNotFoundError as error:
        return None, str(error)


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

    def plain(self, body, ctype):
        self.send_response(200)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def deny(self):
        self.send(403, b'{"error":"read-only preview: only GET is allowed"}', 'application/json')

    do_POST = do_PUT = do_PATCH = do_DELETE = deny

    def do_GET(self):
        url = urlsplit(self.path)
        live = FREE_LIVE.fullmatch(url.path)
        if live:
            try:
                value, kind = free_view.live_file(live_cell(live[1], live[2]), live[3])
            except FileNotFoundError as error:
                return self.send(404, json.dumps({'error': str(error)}).encode(), 'application/json')
            return self.plain(value.read_bytes() if isinstance(value, Path) else value, kind)
        free = FREE.fullmatch(url.path)
        if free:
            folder, reason = bundle(*free.groups()[:3])
            if not folder:
                return self.send(404, json.dumps({'error': reason}).encode(), 'application/json')
            f = folder / free.group(4)
            return self.plain(f.read_bytes(), 'application/json' if f.suffix == '.json' else 'application/octet-stream')
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
