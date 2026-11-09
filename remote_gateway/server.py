"""The HTTP side: login, the page, and the few routes a browser may use. Anything not listed here answers 404."""
from base64 import b64encode
from hashlib import sha256
from html import escape
from http import HTTPStatus
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from ipaddress import ip_address
import json
from pathlib import Path
import re
import secrets
import shutil
import sys
import threading
import time
from urllib.parse import parse_qs, urlsplit

from . import policy
from .pages import AVAILABILITY_SCRIPT, LOGIN_PAGE
from .store import Store
from .upstream import Upstream, UpstreamError

COOKIE = 'ab_session'
JSON_LIMIT = 8192
# The page matches each camera frame to its identity with the X- headers, and throws away a frame that comes without them.
PASSED_HEADERS = ('Content-Type', 'Content-Length', 'Content-Range', 'Accept-Ranges', 'ETag', 'Last-Modified',
                  'X-Frame-Sequence', 'X-Frame-Version', 'X-Frame-Sha256', 'X-Frame-Age', 'X-Episode-Id', 'X-Preview-State')
BASE_HEADERS = {'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer', 'X-Frame-Options': 'DENY'}
UNOWNED = policy.Refused('not_found', 'Not found.', 404)
SETTLE_SECONDS = 60  # How long a stopped run's processes may take to exit.
PUBLIC = 'public'  # Who the harness sees for anything anyone may read. It holds no keys.
SCRIPT = re.compile(r'<script>(.*?)</script>', re.S)


class RateLimits:
    """Per person, a token bucket; failed logins counted by address, code prefix and hour."""
    def __init__(self, limits, clock):
        self.limits, self.clock, self.lock = limits, clock, threading.Lock()
        self.login_lock = threading.Lock()
        self.buckets, self.failures = {}, {}

    def take(self, user, live=False):
        rate, burst = (self.limits.live_requests_per_second, self.limits.live_request_burst) if live else (self.limits.requests_per_second, self.limits.request_burst)
        with self.lock:
            now = self.clock();tokens, last = self.buckets.get((user, live), (burst, now))
            tokens = min(burst, tokens + (now - last) * rate)
            if tokens < 1:
                self.buckets[(user, live)] = (tokens, now)
                return False
            self.buckets[(user, live)] = (tokens - 1, now)
            return True

    def login_keys(self, address, code):
        # Keep no clear invite prefix, even in memory. Shared prefixes limit guessing the rest of a code.
        return (('address', address), ('code', sha256(code[:6].encode()).hexdigest()), ('global', ''))

    def login_allowed(self, address, code=''):
        with self.lock:
            hour = int(self.clock() // 3600)
            self.failures = {key: value for key, value in self.failures.items() if key[1] == hour}
            caps = (self.limits.logins_per_hour, self.limits.logins_per_code_per_hour, self.limits.logins_global_per_hour)
            return all(self.failures.get((key, hour), 0) < cap for key, cap in zip(self.login_keys(address, code), caps))

    def login_failed(self, address, code=''):
        with self.lock:
            for group in self.login_keys(address, code):
                key = (group, int(self.clock() // 3600))
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

        # Runs started in the last 30 seconds, which the host may not list as running yet: they hold their slot meanwhile.
        self.dispatching = threading.Lock();self.settling_until = 0.0;self.starting = {}

    def paused(self):
        return self.config.pause_file.exists() or any(Path(path).exists() for path in self.config.stop_files)

    def unavailable(self, *, codex=False):
        if self.paused():return policy.Refused('runs_paused', 'New runs are paused right now.', 503)
        if self.config.disk_path is not None:
            try:free = shutil.disk_usage(self.config.disk_path).free / 1024 ** 3
            except OSError:return policy.Refused('disk_low', 'New runs are paused: free disk space could not be checked.', 503)
            if free < self.config.min_free_gb:return policy.Refused('disk_low', 'New runs are paused: free disk space is below the required minimum.', 503)
        lab = self.config.lab_codex
        # A run recorded as Lab Codex was created while lab_codex was on; once it is off, such runs do not start.
        # The test gateway offers Codex without lab_codex, so its runs are never recorded as Lab Codex and are not stopped here.
        if codex and lab is None:return policy.Refused('lab_source_not_offered', 'Lab Codex runs are not offered right now.', 400)
        if codex and lab and self.store.codex_today() >= lab['runs_per_day']:
            return policy.Refused('codex_daily_limit', f'Lab Codex has reached its UTC daily limit of {lab["runs_per_day"]} runs. PARCC and your own keys remain available.', 429)
        return None

    def listing(self):
        status, value = self.upstream.json(PUBLIC, 'GET', '/api/runs')
        if status != 200:raise UpstreamError('the run host did not list its runs')
        self.store.observe_finished(value, policy.ENDED_STATES)
        return value

    def board(self, viewer, listing=None):
        listing = listing if listing is not None else self.listing()
        board = policy.board(listing, owners=self.store.owners(), nicknames=self.store.nicknames(), queue=self.store.queue(), viewer=viewer, limits=self.config.limits, now=self.clock(), start_errors=self.store.start_failures(), run_order=self.store.run_order())
        reason = self.unavailable(codex=self.config.lab_codex is not None or any(self.store.codex_run(row['experiment_id']) for row in board['queue']))
        board['availability'] = dict(code=reason.code, message=reason.message) if reason else None
        return board

    def open_runs(self, listing):
        """Runs holding a slot: those the host lists as running, and those started moments ago that it may not list yet."""
        now = self.clock();rows = {row.get('experiment_id'): row for row in listing.get('dev_sessions', [])}
        self.starting = {eid: until for eid, until in self.starting.items() if until > now and not (eid in rows and policy._state(rows[eid]) in policy.ENDED_STATES)}
        return {eid for eid, row in rows.items() if policy.running(row)} | set(self.starting)

    def dispatch(self):
        """Start the next run in line if a run slot is free. Returns what the host said about that run, or None if nothing started.
        The robot itself is not held here: the harness takes it for one execution at a time."""
        # A start can hold the host for 20 seconds or more. Whoever joins the line meanwhile keeps their place and is answered at once; the loop starts them.
        if not self.dispatching.acquire(blocking=False):return None
        try:return self.start_next()
        finally:self.dispatching.release()

    def start_next(self):
        line = self.store.queue()
        listing = self.listing()
        if len(self.open_runs(listing)) >= self.config.limits.run_slots or not line:return None
        head = line[0]
        reason = self.unavailable(codex=self.store.codex_run(head['experiment_id']))
        if reason:return None
        status, value = self.upstream.json(head['user_id'], 'POST', f'/api/dev-v2/experiments/{head["experiment_id"]}/run', {})
        if status != 200:print('dispatch refused:', status, str(value.get('error') if isinstance(value, dict) else value)[:200], file=sys.stderr, flush=True)
        # A stopped run is listed as ended before its processes are gone, so for a while a refusal keeps this one's place and the next pass retries.
        # The host counting one more open run than the list shows is the same race: the place is kept.
        if status == 409 and (self.clock() < self.settling_until or 'run slots are in use' in str(value.get('error') if isinstance(value, dict) else value)):
            return None
        self.store.dequeue(head['experiment_id'])
        if status == 200:
            self.store.started(head['experiment_id'])
            self.starting[head['experiment_id']] = self.clock() + 30
        else:
            # Whoever waited in line may be away; the reason stays on the run for them to read.
            self.store.start_failed(head['experiment_id'], str(value.get('error') if isinstance(value, dict) and value.get('error') else f'The run host refused the start (HTTP {status}).')[:300])
        return head['experiment_id'], status, value

    def loop(self, stop):
        while not stop.wait(3):
            try:self.dispatch()
            except Exception as error:print('dispatch error:', type(error).__name__, file=sys.stderr, flush=True)


EMAIL = re.compile(r'[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}')


def valid_email(value):
    """A plain address (name@domain.tld), lower-cased; None otherwise. Nothing is sent to it."""
    value = value.strip().lower()
    return value if len(value) <= 254 and EMAIL.fullmatch(value) else None


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

    def client_ip(self):
        peer = self.client_address[0]
        try:
            if self.gateway.config.trusted_proxy and ip_address(peer).is_loopback:
                return str(ip_address(self.headers.get('X-Real-IP', '')))
        except ValueError:pass
        return peer

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
        if session is None and not self.open_to_all(path):raise policy.Refused('login_required', 'Please log in.', 401)
        self.user = session['user_id'] if session else None
        # The page's own files are cheap and never reach the harness; losing one would break the page.
        if not path.startswith('/ui/') and not self.gateway.rate.take(self.user or self.client_ip(), live=path.endswith(('/live', '/live/frame'))):raise policy.Refused('too_many_requests', 'Too many requests. Wait a moment.', 429)
        if session and self.command != 'GET' and not secrets.compare_digest(self.headers.get('X-Attention-Token', ''), session['csrf']):
            raise policy.Refused('csrf_token_required', 'Reload the page and try again.', 403)
        # 11-10 (user): the site opens on Home; its New run leads a visitor to the invite page.
        if path in ('/', '/ui'):return self.redirect('/ui/home.html', 302)
        if path == '/ui/' and self.command == 'GET':return self.page(session)
        if path in policy.UI_PAGES and self.command == 'GET':return self.page(session, policy.UI_PAGES[path])
        if path.startswith('/ui/') and self.command == 'GET':return self.asset(path[4:])
        if path == '/api/session':return self.session_route(session, token)
        if path == '/api/home/status' and self.command == 'GET':
            status, value = self.gateway.upstream.json(PUBLIC, 'GET', path)
            return self.send_json(policy.filter_home(value) if status == 200 else policy.scrub(value), status)
        if path == '/api/home/scene' and self.command == 'GET':return self.relay(policy.home_scene(parts.query))
        if path == '/api/lobby' and self.command == 'GET':return self.send_json(policy.scrub(self.gateway.board(self.user)))
        if path == '/api/run-options' and self.command == 'GET':return self.options()
        if path == '/api/runs' and self.command == 'GET':return self.runs()
        if path == '/api/notices' and self.command == 'GET':return self.send_json(dict(generated_at=None, notices=[]))
        if path.startswith('/api/provider/'):return self.provider(parts)
        if path == '/api/dev-v2/experiments' and self.command == 'POST':return self.create()
        match = policy.EXPERIMENT.fullmatch(path)
        if match:return self.experiment(parts, *match.groups())
        raise UNOWNED

    def open_to_all(self, path):
        """Anyone may watch: the page and everyone's runs. Everything that starts, changes or uses a key needs an invite."""
        if self.command != 'GET':return False
        return path in ('/', '/ui', '/ui/', '/api/run-options', '/api/runs', '/api/lobby', '/api/notices', '/api/home/status', '/api/home/scene') or path.startswith('/ui/') or policy.EXPERIMENT.fullmatch(path) is not None

    def login(self, parts):
        if self.command == 'GET':
            failed = parse_qs(parts.query).get('failed', [''])[0]
            message = {'1': 'That code is not valid.', 'email': 'Enter a valid email address.'}.get(failed, '')
            return self.send_bytes(LOGIN_PAGE.replace('__MESSAGE__', message).encode(), 'text/html; charset=utf-8',
                                   headers={'Content-Security-Policy': "default-src 'none'; style-src 'unsafe-inline' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"})
        if self.command != 'POST':raise UNOWNED
        address = self.client_ip()
        form = parse_qs(self.read_body(1024).decode(errors='replace'), max_num_fields=3)
        code = form.get('code', [''])[0].strip()
        # 11-09 (user): an email is required. It is checked before the code, so a code is never bound to a person without one.
        email = valid_email(form.get('email', [''])[0])
        if email is None:return self.redirect('/login?failed=email')
        # Check and record together: concurrent guesses must not all pass the last available failure slot.
        with self.gateway.rate.login_lock:
            if not self.gateway.rate.login_allowed(address, code):raise policy.Refused('too_many_logins', 'Too many attempts. Try again later.', 429)
            user = self.gateway.store.redeem(code) if code else None
            if user is None:self.gateway.rate.login_failed(address, code)
        if user is None:
            return self.redirect('/login?failed=1')
        self.gateway.store.record_email(user, email)
        self.user = user
        config = self.gateway.config
        token, _ = self.gateway.store.new_session(user, hours=config.session_hours)
        # 11-10 (user): signing in lands on Home; its New run then opens the form. (The Run page would open on
        # the newest run, often someone else's, which read as being dropped into a stranger's run.)
        self.redirect('/ui/home.html', cookie=self.cookie(token, config.session_hours * 3600))

    def page(self, session, name='index.html'):
        page = (self.gateway.config.ui_dir / name).read_text(encoding='utf-8')
        # The session's CSRF token stands where the operator page has its control token, so the page needs no second code path.
        marks = f'data-control-token="{session["csrf"]}"' if session else 'data-guest="1"'
        page = page.replace('data-mode="live"', f'data-mode="live" {marks} data-gateway="1" data-remote="1"', 1)
        if name != 'index.html':page = page.replace('<body', f'<body data-gateway="1"{"" if session else " data-guest=" + chr(34) + "1" + chr(34)}', 1)
        reason = self.gateway.unavailable(codex=self.gateway.config.lab_codex is not None)
        notice = f'<p id="gateway-availability" role="status" style="position:fixed;bottom:12px;left:12px;right:12px;z-index:1000;margin:0;padding:12px;background:#211a0d;color:#ffe2a8;border:1px solid #9e7531;text-align:center"{"" if reason else " hidden"}>{escape(reason.message) if reason else ""}</p>'
        page = re.sub(r'(<body\b[^>]*>)', lambda match:match[0] + notice, page, count=1)
        page = page.replace('</body>', AVAILABILITY_SCRIPT + '</body>', 1)
        page = page.replace(' media="print" onload="this.media=\'all\'"', '', 1)  # An inline handler would need a looser policy.
        inline = ' '.join("'sha256-%s'" % b64encode(sha256(script.encode()).digest()).decode() for script in SCRIPT.findall(page))
        self.send_bytes(page.encode(), 'text/html; charset=utf-8', headers={'Content-Security-Policy':
            f"default-src 'none'; script-src 'self' {inline}; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; "
            "img-src 'self' data: blob:; media-src 'self' blob:; connect-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"})

    def asset(self, name):
        kind = policy.ui_asset(name)
        file = self.gateway.config.ui_dir / name
        if kind is None or not file.is_file():raise UNOWNED
        self.send_bytes(file.read_bytes(), kind)

    def session_route(self, session, token):
        if self.command == 'GET':
            limits = self.gateway.config.limits
            return self.send_json(dict(user=self.user, runs_today=self.gateway.store.runs_today(self.user), runs_per_day=limits.runs_per_day))
        if self.command != 'DELETE':raise UNOWNED
        self.gateway.store.end_session(token)
        self.send_json(dict(ok=True), headers={'Set-Cookie': self.cookie('', 0)})

    def options(self):
        status, value = self.gateway.upstream.json(self.user or PUBLIC, 'GET', '/api/run-options')
        if status != 200:raise UpstreamError('the run host did not list its tasks')
        self.send_json(policy.filter_options(value, self.gateway.config.limits, lab_codex=self.gateway.config.lab_codex))

    def runs(self):
        listing = self.gateway.listing()
        self.send_json(policy.filter_runs(listing, self.gateway.store.owners(), self.gateway.board(self.user, listing)))

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
        reason = self.gateway.unavailable()
        if reason:raise reason
        limits = self.gateway.config.limits
        if self.gateway.store.runs_today(self.user) >= limits.runs_per_day:
            raise policy.Refused('daily_limit', f'You have used your {limits.runs_per_day} runs for today.', 429)
        self.guard_active()

    def guard_active(self, experiment_id=None):
        reason = self.gateway.unavailable(codex=self.gateway.store.codex_run(experiment_id) if experiment_id else False)
        if reason:raise reason
        limits = self.gateway.config.limits
        queued = sum(row['user_id'] == self.user for row in self.gateway.store.queue())
        if policy.active_runs(self.gateway.listing(), self.gateway.store.runs_of(self.user)) + queued >= limits.active_runs:
            raise policy.Refused('run_active', 'You already have a run going. Finish or cancel it first.', 409)

    def create(self):
        body = self.read_json()
        self.guard_new_run()
        upstream = self.gateway.upstream
        status, options = upstream.json(self.user, 'GET', '/api/run-options')
        if status != 200:raise UpstreamError('the run host did not list its tasks')
        status, credentials = upstream.json(self.user, 'GET', '/api/provider/credentials')
        if status != 200:raise UpstreamError('the run host did not list your keys')
        settings = dict(lab_codex=self.gateway.config.lab_codex) if self.gateway.config.lab_codex is not None else {}
        selection = policy.rebuild(body, user=self.user, options=options, limits=self.gateway.config.limits, credentials=credentials, **settings)
        # Counted and capped only under this gateway's own lab_codex switch (see unavailable()).
        codex = self.gateway.config.lab_codex is not None and policy.uses_lab_codex(selection)
        reason = self.gateway.unavailable(codex=codex)
        if reason:raise reason
        # Created first, started second: a run that cannot start stays owned and can be started from its page.
        status, made = upstream.json(self.user, 'POST', '/api/dev-v2/experiments', dict(selection=selection, start=False))
        if status != 200:return self.send_json(policy.scrub(made), status)
        self.gateway.store.record_run(made['experiment_id'], self.user, self.gateway.config.host_id, lab_codex=codex)
        if body.get('start') is True:
            refused = self.join_queue(made['experiment_id'])
            if refused:return self.send_json(policy.scrub(refused[1]), refused[0])
        self.send_json(policy.scrub(made))

    def join_queue(self, experiment_id):
        """Into the line, and straight onto the robot if it is free. A host that refuses the start says so to the person who asked."""
        self.gateway.store.start_retried(experiment_id)
        self.gateway.store.enqueue(experiment_id, self.user)
        started = self.gateway.dispatch()
        if started and started[0] == experiment_id and started[1] != 200:return started[1], started[2]
        return None

    def experiment(self, parts, experiment_id, suffix):
        if self.command == 'GET':
            if not self.gateway.store.known(experiment_id) or not policy.readable(suffix) or not policy.QUERY.fullmatch(parts.query):raise UNOWNED
            return self.relay(self.path, headers={'Range': self.headers['Range']} if 'Range' in self.headers else None)
        if not self.gateway.store.owns(self.user, experiment_id):raise UNOWNED
        if self.command == 'POST' and suffix == 'help-reply' and not parts.query:
            return self.relay(self.path, json.dumps(policy.help_reply(self.read_json())).encode())
        if self.command != 'POST' or suffix not in policy.COMMANDS or parts.query:raise UNOWNED
        if self.read_json() != {}:raise policy.Refused('invalid_body', 'This command takes no settings.', 400)
        if suffix == 'run':
            self.guard_active(experiment_id)
            refused = self.join_queue(experiment_id)
            if refused:return self.send_json(policy.scrub(refused[1]), refused[0])
            return self.send_json(dict(experiment_id=experiment_id, queued=True))
        if suffix == 'cancel':
            self.gateway.store.dequeue(experiment_id)
            self.gateway.settling_until = self.gateway.clock() + SETTLE_SECONDS
        self.relay(self.path, b'{}')

    def relay(self, target, body=b'', headers=None):
        """One signed call. JSON is scrubbed on its way through; everything else (video, frames) streams untouched."""
        connection, response = self.gateway.upstream.open(self.user or PUBLIC, self.command, target, body, headers)
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
