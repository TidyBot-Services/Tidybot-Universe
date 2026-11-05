"""Gateway settings. Where the harness runs is one value here, so moving it to another host is a config change."""
from dataclasses import dataclass, field, fields
import json
from pathlib import Path
import stat

LOOPBACK = ('127.0.0.1', '::1', 'localhost')


@dataclass(frozen=True)
class Limits:
    versions: int = 3
    wall_seconds: int = 3600
    help_requests: int = 3
    runs_per_day: int = 3
    active_runs: int = 1
    # Opening one run page makes dozens of calls (views, evidence, video ranges); 5 a second with a burst of 20 broke the page.
    requests_per_second: float = 10
    request_burst: int = 60
    logins_per_hour: int = 10

    def __post_init__(self):
        # Agent runs accept 1-10 versions and 1-6 hours; a limit outside that could never be honoured.
        if not 1 <= self.versions <= 10 or not 3600 <= self.wall_seconds <= 21600 or self.wall_seconds % 3600:
            raise ValueError('versions must be 1-10 and wall_seconds whole hours from 3600 to 21600')
        if not 1 <= self.help_requests <= 10 or self.runs_per_day < 1 or self.active_runs < 1:
            raise ValueError('help_requests, runs_per_day and active_runs must be positive')

    def public(self):
        """What New run shows; the same shape as the main UI's own limits."""
        hours = self.wall_seconds // 3600
        time = '1 hour' if hours == 1 else f'{hours} hours'
        return dict(versions=self.versions, wall=self.wall_seconds, help=self.help_requests, maxHelp=self.help_requests,
                    versionsRange=f'1–{self.versions} · Remote limit', wallRange=('1 hour' if hours == 1 else f'1–{hours} hours') + ' · Remote limit',
                    note=f'Remote limit: {self.versions} versions · {time} · {self.help_requests} help requests, because everyone shares one robot. '
                         'Results are not comparable with the official 10-version runs.')


@dataclass(frozen=True)
class Config:
    state_dir: Path
    upstream: str
    ui_dir: Path
    host_id: str = 'local'
    listen_host: str = '127.0.0.1'
    listen_port: int = 8861
    cookie_secure: bool = True
    session_hours: int = 24
    stop_files: tuple = ()
    limits: Limits = field(default_factory=Limits)

    def __post_init__(self):
        # A tunnel or reverse proxy faces the network; the gateway itself never does.
        if self.listen_host not in LOOPBACK:raise ValueError('the gateway listens on loopback only')

    @property
    def database(self):return self.state_dir / 'gateway.sqlite3'
    @property
    def audit_file(self):return self.state_dir / 'audit.jsonl'
    @property
    def secret_file(self):return self.state_dir / 'gateway.secret'
    @property
    def pause_file(self):return self.state_dir / 'public-pause'

    def secret(self):
        path = self.secret_file
        if stat.S_IMODE(path.stat().st_mode) & 0o077:raise ValueError('the gateway secret must be readable by its owner only')
        value = path.read_bytes()
        if len(value) < 32:raise ValueError('the gateway secret is too short')
        return value


def load(path):
    value = json.loads(Path(path).read_text(encoding='utf-8'))
    known = {f.name for f in fields(Config)}
    if not isinstance(value, dict) or set(value) - known:raise ValueError('unknown gateway settings')
    limits = Limits(**value.pop('limits', {}))
    return Config(state_dir=Path(value.pop('state_dir')), ui_dir=Path(value.pop('ui_dir')), stop_files=tuple(value.pop('stop_files', ())),
                  limits=limits, **value)
