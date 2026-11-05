"""The HTTP side: login, the page, and the few routes a browser may use. Anything not listed here answers 404."""
from base64 import b64encode
from hashlib import sha256
from http import HTTPStatus
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import secrets
import sys
import threading
import time
from urllib.parse import parse_qs, urlsplit

from . import policy
from .pages import LOGIN_PAGE
from .store import Store
from .upstream import Upstream, UpstreamError

COOKIE = 'ab_session'
JSON_LIMIT = 8192
PASSED_HEADERS = ('Content-Type', 'Content-Length', 'Content-Range', 'Accept-Ranges', 'ETag', 'Last-Modified')
BASE_HEADERS = {'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer', 'X-Frame-Options': 'DENY'}
UNOWNED = policy.Refused('not_found', 'Not found.', 404)
SCRIPT = re.compile(r'<script>(.*?)</script>', re.S)


class RateLimits:
    """Per person, a token bucket; per address, a count of failed logins in the current hour."""
    def __init__(self, limits, clock):
        self.limits, self.clock, self.lock = limits, clock, threading.Lock()
        self.buckets, self.failures = {}, {}

    def take(self, user):
        with self.lock:
            now = self.clock();tokens, last = self.buckets.get(user, (self.limits.request_burst, now))
            tokens = min(self.limits.request_burst, tokens + (now - last) * self.limits.requests_per_second)
            if tokens < 1:
                self.buckets[user] = (tokens, now)
                return False
            self.buckets[user] = (tokens - 1, now)
            return True

    def login_allowed(self, address):
        with self.lock:
            hour = int(self.clock() // 3600)
            count = self.failures.get((address, hour), 0)
            self.failures = {key: value for key, value in self.failures.items() if key[1] == hour}
            return count < self.limits.logins_per_hour

    def login_failed(self, address):
        with self.lock:
            key = (address, int(self.clock() // 3600))
            self.failures[key] = self.failures.get(key, 0) + 1


class Audit:
    """What happened, never what was said: no bodies, cookies, keys or addresses."""
    def __init__(self, path, clock):
        self.path, self.clock, self.lock = path, clock, threading.Lock()
        path.touch(mode=0o600, exist_ok=True)

    def write(self, **row):
        with self.lock, self.path.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(dict(at=round(self.clock(), 3), **row), sort_keys=True) + '\n')


class Gateway:
    def __init__(self, config, *, clock=time.time, secret=None):
        self.config, self.clock = config, clock
        self.store = Store(config.database, clock=clock)
        self.upstream = Upstream(config.upstream, secret if secret is not None else config.secret(), clock=clock)
        self.rate = RateLimits(config.limits, clock)
        self.audit = Audit(config.audit_file, clock)

    def paused(self):
        return self.config.pause_file.exists() or any(Path(path).exists() for path in self.config.stop_files)


class Handler(BaseHTTPRequestHandler):
    server_version = 'gateway'
    sys_version = ''
    timeout = 30  # An idle or stalled connection gives its thread back.

    def log_message(self, *args):pass  # The audit log below is the only record.

    @property
    def gateway(self):return self.server.gateway

    def serve(self):
        started = time.monotonic();self.status = 0;self.user = None
        try:
            self.route()
        except policy.Refused as refused:
            self.send_json(dict(error=refused.message, code=refused.code), refused.status)
        except UpstreamError as error:
            self.send_json(dict(error=f'{str(error).capitalize()}.', code='run_host_unavailable'), 502)
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass
        except Exception as error:
            print('gateway error:', type(error).__name__, file=sys.stderr, flush=True)  # The type only: a message can quote a request.
            self.send_json(dict(error='Something went wrong.', code='internal'), 500)
        finally:
            self.gateway.audit.write(user=self.user, method=self.command, route=policy.audit_route(urlsplit(self.path).path), status=self.status,
                                     ms=round((time.monotonic() - started) * 1000))
    do_GET = do_POST = do_PUT = do_DELETE = serve

    # Answers
    def send_bytes(self, body, content_type, status=200, headers=None):
        self.status = status
        self.send_response(status)
        for name, value in {**BASE_HEADERS, 'Content-Type': content_type, 'Content-Length': str(len(body)), 'Cache-Control': 'no-store', **(headers or {})}.items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, value, status=200, headers=None):
        self.send_bytes(json.dumps(value, ensure_ascii=False).encode(), 'application/json; charset=utf-8', status, headers)

    def redirect(self, location, status=303, cookie=None):
        headers = {'Location': location, **({'Set-Cookie': cookie} if cookie else {})}
        self.send_bytes(b'', 'text/plain; charset=utf-8', status, headers)

    # Who is asking
    def session_token(self):
        cookie = SimpleCookie()
        try:cookie.load(self.headers.get('Cookie', ''))
        except CookieError:return None
        return cookie[COOKIE].value if COOKIE in cookie else None

    def read_body(self, limit=JSON_LIMIT):
        try:size = int(self.headers.get('Content-Length', '0'))
        except ValueError:raise policy.Refused('invalid_body', 'Invalid request.', 400) from None
        if not 0 <= size <= limit:raise policy.Refused('body_too_large', 'Request too large.', 413)
        return self.rfile.read(size)

    def read_json(self):
        if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':raise policy.Refused('invalid_body', 'JSON required.', 400)
        try:return json.loads(self.read_body())
        except ValueError:raise policy.Refused('invalid_body', 'JSON required.', 400) from None

    def cookie(self, value, max_age):
        secure = '; Secure' if self.gateway.config.cookie_secure else ''
        return f'{COOKIE}={value}; Path=/; HttpOnly; SameSite=Strict; Max-Age={max_age}{secure}'

    # Routes
    def route(self):
        parts = urlsplit(self.path);path = parts.path
        if len(self.path) > 2048 or not self.path.startswith('/') or not policy.safe_target(path):raise UNOWNED
        if path == '/login':return self.login(parts)
        token = self.session_token()
        session = self.gateway.store.session(token) if token else None
        if session is None:
            if path.startswith('/api/'):raise policy.Refused('login_required', 'Please log in.', 401)
            return self.redirect('/login')
        self.user = session['user_id']
        # The page's own files are cheap and never reach the harness; losing one would break the page.
        if not path.startswith('/ui/') and not self.gateway.rate.take(self.user):raise policy.Refused('too_many_requests', 'Too many requests. Wait a moment.', 429)
        if self.command != 'GET' and not secrets.compare_digest(self.headers.get('X-Attention-Token', ''), session['csrf']):
            raise policy.Refused('csrf_token_required', 'Reload the page and try again.', 403)
        if path in ('/', '/ui'):return self.redirect('/ui/', 302)
        if path == '/ui/' and self.command == 'GET':return self.page(session)
        if path.startswith('/ui/') and self.command == 'GET':return self.asset(path[4:])
        if path == '/api/session':return self.session_route(session, token)
        if path == '/api/run-options' and self.command == 'GET':return self.options()
        if path == '/api/runs' and self.command == 'GET':return self.runs()
        if path == '/api/notices' and self.command == 'GET':return self.send_json(dict(generated_at=None, notices=[]))
        if path.startswith('/api/provider/'):return self.provider(parts)
        if path == '/api/dev-v2/experiments' and self.command == 'POST':return self.create()
        match = policy.EXPERIMENT.fullmatch(path)
        if match:return self.experiment(parts, *match.groups())
        raise UNOWNED

    def login(self, parts):
        if self.command == 'GET':
            failed = parse_qs(parts.query).get('failed') == ['1']
            return self.send_bytes(LOGIN_PAGE.replace('__MESSAGE__', 'That code is not valid.' if failed else '').encode(), 'text/html; charset=utf-8',
                                   headers={'Content-Security-Policy': "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"})
        if self.command != 'POST':raise UNOWNED
        address = self.client_address[0]
        if not self.gateway.rate.login_allowed(address):raise policy.Refused('too_many_logins', 'Too many attempts. Try again later.', 429)
        code = parse_qs(self.read_body(1024).decode(errors='replace'), max_num_fields=2).get('code', [''])[0].strip()
        user = self.gateway.store.redeem(code) if code else None
        if user is None:
            self.gateway.rate.login_failed(address)
            return self.redirect('/login?failed=1')
        self.user = user
        config = self.gateway.config
        token, _ = self.gateway.store.new_session(user, hours=config.session_hours)
        self.redirect('/ui/', cookie=self.cookie(token, config.session_hours * 3600))

    def page(self, session):
        page = (self.gateway.config.ui_dir / 'index.html').read_text(encoding='utf-8')
        # The session's CSRF token stands where the operator page has its control token, so the page needs no second code path.
        page = page.replace('data-mode="live"', f'data-mode="live" data-control-token="{session["csrf"]}" data-gateway="1"', 1)
        page = page.replace(' media="print" onload="this.media=\'all\'"', '', 1)  # An inline handler would need a looser policy.
        inline = ' '.join("'sha256-%s'" % b64encode(sha256(script.encode()).digest()).decode() for script in SCRIPT.findall(page))
        self.send_bytes(page.encode(), 'text/html; charset=utf-8', headers={'Content-Security-Policy':
            f"default-src 'none'; script-src 'self' {inline}; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; "
            "img-src 'self' data: blob:; media-src 'self' blob:; connect-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"})

    def asset(self, name):
        kind = policy.UI_ASSETS.get(name)
        if kind is None:raise UNOWNED
        self.send_bytes((self.gateway.config.ui_dir / name).read_bytes(), kind)

    def session_route(self, session, token):
        if self.command == 'GET':
            limits = self.gateway.config.limits
            return self.send_json(dict(user=self.user, runs_today=self.gateway.store.runs_today(self.user), runs_per_day=limits.runs_per_day))
        if self.command != 'DELETE':raise UNOWNED
        self.gateway.store.end_session(token)
        self.send_json(dict(ok=True), headers={'Set-Cookie': self.cookie('', 0)})

    def options(self):
        status, value = self.gateway.upstream.json(self.user, 'GET', '/api/run-options')
        if status != 200:raise UpstreamError('the run host did not list its tasks')
        self.send_json(policy.filter_options(value, self.gateway.config.limits))

    def listing(self):
        status, value = self.gateway.upstream.json(self.user, 'GET', '/api/runs')
        if status != 200:raise UpstreamError('the run host did not list its runs')
        return value

    def runs(self):
        self.send_json(policy.filter_runs(self.listing(), self.gateway.store.runs_of(self.user)))

    def provider(self, parts):
        path = parts.path
        if parts.query:raise UNOWNED
        if path == '/api/provider/presets' and self.command == 'GET':
            status, value = self.gateway.upstream.json(self.user, 'GET', path)
            return self.send_json(policy.filter_presets(value) if status == 200 else value, status)
        match = policy.CREDENTIAL.fullmatch(path)
        if not match:raise UNOWNED
        name, action = match.groups()
        allowed = {None: ('GET', 'PUT', 'DELETE') if name else ('GET', 'DELETE'), '/models': ('GET',), '/test': ('POST',)}[action]
        if self.command not in allowed:raise UNOWNED
        if name == 'lab':raise policy.Refused('invalid_key_name', 'That key name is reserved.', 400)
        self.relay(self.path, self.read_body() if self.command in ('POST', 'PUT') else b'')

    def guard_new_run(self):
        if self.gateway.paused():raise policy.Refused('runs_paused', 'New runs are paused right now.', 503)
        limits = self.gateway.config.limits
        if self.gateway.store.runs_today(self.user) >= limits.runs_per_day:
            raise policy.Refused('daily_limit', f'You have used your {limits.runs_per_day} runs for today.', 429)
        self.guard_active()

    def guard_active(self):
        if self.gateway.paused():raise policy.Refused('runs_paused', 'New runs are paused right now.', 503)
        limits = self.gateway.config.limits
        if policy.active_runs(self.listing(), self.gateway.store.runs_of(self.user)) >= limits.active_runs:
            raise policy.Refused('run_active', 'You already have a run going. Finish or cancel it first.', 409)

    def create(self):
        body = self.read_json()
        self.guard_new_run()
        upstream = self.gateway.upstream
        status, options = upstream.json(self.user, 'GET', '/api/run-options')
        if status != 200:raise UpstreamError('the run host did not list its tasks')
        status, credentials = upstream.json(self.user, 'GET', '/api/provider/credentials')
        if status != 200:raise UpstreamError('the run host did not list your keys')
        selection = policy.rebuild(body, user=self.user, options=options, limits=self.gateway.config.limits, credentials=credentials)
        # Created first, started second: a run that cannot start stays owned and can be started from its page.
        status, made = upstream.json(self.user, 'POST', '/api/dev-v2/experiments', dict(selection=selection, start=False))
        if status != 200:return self.send_json(policy.scrub(made), status)
        self.gateway.store.record_run(made['experiment_id'], self.user, self.gateway.config.host_id)
        if body.get('start') is True:
            status, started = upstream.json(self.user, 'POST', f'/api/dev-v2/experiments/{made["experiment_id"]}/run', {})
            if status != 200:return self.send_json(policy.scrub(started), status)
        self.send_json(policy.scrub(made))

    def experiment(self, parts, experiment_id, suffix):
        if not self.gateway.store.owns(self.user, experiment_id):raise UNOWNED
        if self.command == 'GET':
            if not policy.readable(suffix) or not policy.QUERY.fullmatch(parts.query):raise UNOWNED
            return self.relay(self.path, headers={'Range': self.headers['Range']} if 'Range' in self.headers else None)
        if self.command != 'POST' or suffix not in policy.COMMANDS or parts.query:raise UNOWNED
        if self.read_json() != {}:raise policy.Refused('invalid_body', 'This command takes no settings.', 400)
        if suffix == 'run':self.guard_active()
        self.relay(self.path, b'{}')

    def relay(self, target, body=b'', headers=None):
        """One signed call. JSON is scrubbed on its way through; everything else (video, frames) streams untouched."""
        connection, response = self.gateway.upstream.open(self.user, self.command, target, body, headers)
        try:
            kind = response.getheader('Content-Type', '')
            if kind.startswith('application/json'):
                raw = response.read()
                try:return self.send_json(policy.scrub(json.loads(raw)), response.status)
                except ValueError:raise UpstreamError('the run host gave an unreadable answer') from None
            self.status = response.status
            self.send_response(response.status)
            for name, value in BASE_HEADERS.items():self.send_header(name, value)
            for name in PASSED_HEADERS:
                if response.getheader(name):self.send_header(name, response.getheader(name))
            self.send_header('Cache-Control', 'private, no-store')
            self.end_headers()
            while chunk := response.read(65536):self.wfile.write(chunk)
        finally:connection.close()


def create_server(config, *, clock=time.time, secret=None):
    server = ThreadingHTTPServer((config.listen_host, config.listen_port), Handler)
    server.gateway = Gateway(config, clock=clock, secret=secret)
    return server
