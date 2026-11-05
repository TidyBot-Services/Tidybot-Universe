#!/usr/bin/env python3
"""Run the agent sandbox preflight in a throwaway directory. Runs under the UI environment."""
from __future__ import annotations

from pathlib import Path
import socket
import sys
import tempfile

from benchmarks.attention_harness.dev_agent_isolation import verify_runtime


def main() -> None:
    with tempfile.TemporaryDirectory(dir=Path(sys.argv[1]) / 'data/run') as temp:
        workspace, private = Path(temp) / 'ws', Path(temp) / 'private'
        workspace.mkdir()
        private.mkdir()
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            listener.listen()
            verify_runtime(workspace, private, planner_port=listener.getsockname()[1])


if __name__ == '__main__':
    main()
