"""Gateway settings. Where the harness runs is one value here, so moving it to another host is a config change."""
from dataclasses import dataclass, field, fields
import json
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
    help_requests: int = 3
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

    def __post_init__(self):
        # Agent runs accept 1-10 versions and 20 minutes to 6 hours; a limit outside that could never be honoured.
        if not 1 <= self.versions <= 10 or not 1200 <= self.wall_seconds <= 21600 or self.wall_seconds % 60:
            raise ValueError('versions must be 1-10 and wall_seconds whole minutes from 1200 to 21600')
        if not 30 <= self.human_reply_seconds <= 600:raise ValueError('human_reply_seconds must be 30-600')
        if not 1 <= self.help_requests <= 10 or self.runs_per_day < 1 or self.active_runs < 1:
            raise ValueError('help_requests, runs_per_day and active_runs must be positive')
        if type(self.run_slots) is not int or not 1 <= self.run_slots <= 16:raise ValueError('run_slots must be 1-16')

    def public(self):
        """What New run shows; the same shape as the main UI's own limits. There is no time limit, so the wall field is fixed."""
        return dict(versions=self.versions, wall=NOMINAL_WALL_SECONDS, help=self.help_requests, maxHelp=self.help_requests, noTimeLimit=True,
                    versionsRange=f'1–{self.versions} · Remote limit', wallRange='No time limit · stops after 2 hours without progress',
                    note=f'Remote limit: {self.versions} versions · {self.help_requests} help requests · no time limit. Up to {self.run_slots} runs are open at once and take turns on the one robot; '
                         'waiting for the robot does not count. A run with no new Try, version or help request for 2 hours stops itself. '
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
    # Kept for the monitor, which shows who holds it. The gateway no longer takes it: the harness takes the run host's own
    # lock for one execution at a time (benchmarks/attention_harness/sim_timeshare.py).
    sim_lock: Path | None = None
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
    lock = value.pop('sim_lock', None)
    return Config(state_dir=Path(value.pop('state_dir')), ui_dir=Path(value.pop('ui_dir')), stop_files=tuple(value.pop('stop_files', ())),
                  sim_lock=Path(lock) if lock else None, limits=limits, **value)
