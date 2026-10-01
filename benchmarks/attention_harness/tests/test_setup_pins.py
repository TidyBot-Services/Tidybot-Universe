"""Installation must not silently downgrade the independent simulator."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_requirements_matches_robosuite_service_lock():
    lock = json.loads((ROOT / "robosuite-service.lock.json").read_text())
    expected = f"tidybot-robosuite-sim @ git+{lock['repository']}@{lock['revision']}"
    assert (ROOT / "requirements-robosuite.txt").read_text().splitlines()[0] == expected


def test_setup_installs_robosuite_only_from_requirements():
    script = (ROOT / "setup_env.sh").read_text()
    assert '"$SCRIPT_DIR/requirements-robosuite.txt"' in script
    assert "git+https://github.com/TidyBot-Services/robosuite_sim.git@" not in script
