"""Gateway settings. Where the harness runs is one value here, so moving it to another host is a config change."""
from dataclasses import dataclass, field, fields
import json
import math
from pathlib import Path
import stat

LOOPBACK = ('127.0.0.1', '::1', 'localhost')
# The value a Remote run records as its wall budget. The harness's time-share mode does not enforce it.
NOMINAL_WALL_SECONDS = 21600


@dataclass(frozen=True)
class Limits:
    versions: int = 3
    # No longer a limit: Remote runs have no time limit (2026-11-08). Kept so older gateway.json files still load.
    wall_seconds: int = 3600
    help_requests: int = 5
    human_reply_seconds: int = 120  # How long an interactive run waits for its owner before the Advisor answers.
    runs_per_day: int = 3
    active_runs: int = 1
    # Runs open at once on the host (Dev writing code, waiting for a model or a person). The robot itself is one token,
    # taken by the harness for one execution at a time. Set from measured GPU memory and disk; see the time-share plan.
    run_slots: int = 3
    # Opening one run page makes dozens of calls (views, evidence, video ranges); 5 a second with a burst of 20 broke the page.
    requests_per_second: float = 10
    request_burst: int = 60
    # A live picture alone is about 12 calls a second (each frame reads its identity, then the frame), so it has its own allowance.
    live_requests_per_second: float = 30
    live_request_burst: int = 120
    logins_per_hour: int = 10
    logins_per_code_per_hour: int = 5
    logins_global_per_hour: int = 200

    def __post_init__(self):
        # Agent runs accept 1-10 versions and 20 minutes to 6 hours; a limit outside that could never be honoured.
        if not 1 <= self.versions <= 10 or not 1200 <= self.wall_seconds <= 21600 or self.wall_seconds % 60:
            raise ValueError('versions must be 1-10 and wall_seconds whole minutes from 1200 to 21600')
        if not 30 <= self.human_reply_seconds <= 600:raise ValueError('human_reply_seconds must be 30-600')
        if not 1 <= self.help_requests <= 10 or self.runs_per_day < 1 or self.active_runs < 1:
            raise ValueError('help_requests, runs_per_day and active_runs must be positive')
        if type(self.run_slots) is not int or not 1 <= self.run_slots <= 16:raise ValueError('run_slots must be 1-16')
        for name in ('logins_per_hour', 'logins_per_code_per_hour', 'logins_global_per_hour'):
            if type(getattr(self, name)) is not int or getattr(self, name) < 1:raise ValueError(name + ' must be a positive integer')

    def public(self):
        """What New run shows; the same shape as the main UI's own limits. There is no time limit, so the wall field is fixed."""
        return dict(versions=self.versions, wall=NOMINAL_WALL_SECONDS, help=self.help_requests, maxHelp=self.help_requests, noTimeLimit=True,
                    versionsRange=f'1–{self.versions} · Remote limit', wallRange='No time limit · stops after 2 hours without progress',
                    # 11-09 (user): no note under 05. Its fields already show the limits and the 2-hour stop, and 07 Queue explains the shared robot.
                    note='')


@dataclass(frozen=True)
class Config:
    state_dir: Path
    upstream: str
    ui_dir: Path
    host_id: str = 'local'
    listen_host: str = '127.0.0.1'
    listen_port: int = 8861
    cookie_secure: bool = True
    trusted_proxy: bool = False
    disk_path: Path | None = None
    min_free_gb: float = 0
    retention_days: int = 7
    lab_codex: dict | None = None
    session_hours: int = 24
    stop_files: tuple = ()
    # Kept for the monitor, which shows who holds it. The gateway no longer takes it: the harness takes the run host's own
    # lock for one execution at a time (benchmarks/attention_harness/sim_timeshare.py).
    sim_lock: Path | None = None
    limits: Limits = field(default_factory=Limits)

    def __post_init__(self):
        # A tunnel or reverse proxy faces the network; the gateway itself never does.
        if self.listen_host not in LOOPBACK:raise ValueError('the gateway listens on loopback only')
        if type(self.trusted_proxy) is not bool:raise ValueError('trusted_proxy must be a boolean')
        if type(self.min_free_gb) not in (int, float) or not math.isfinite(self.min_free_gb) or self.min_free_gb < 0:raise ValueError('min_free_gb must be finite and nonnegative')
        if type(self.retention_days) is not int or self.retention_days < 1:raise ValueError('retention_days must be a positive integer')
        if self.lab_codex is not None:
            value = self.lab_codex
            if not isinstance(value, dict) or set(value) != {'model', 'effort', 'runs_per_day', 'stop_percent'}:raise ValueError('invalid lab_codex settings')
            # Eval and Advisor currently fix Codex effort at high, so only offer a setting all three roles can honour.
            if value['model'] not in ('gpt-6.1-sol', 'gpt-6-astra') or value['effort'] != 'high':raise ValueError('unsupported lab_codex model or effort (must be high)')
            if type(value['runs_per_day']) is not int or value['runs_per_day'] < 1:raise ValueError('lab_codex runs_per_day must be a positive integer')
            if type(value['stop_percent']) not in (int, float) or not math.isfinite(value['stop_percent']) or not 0 < value['stop_percent'] <= 100:raise ValueError('invalid lab_codex stop_percent')

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
    lock = value.pop('sim_lock', None)
    disk = value.pop('disk_path', None)
    return Config(state_dir=Path(value.pop('state_dir')), ui_dir=Path(value.pop('ui_dir')), stop_files=tuple(value.pop('stop_files', ())),
                  sim_lock=Path(lock) if lock else None, disk_path=Path(disk) if disk else None, limits=limits, **value)
