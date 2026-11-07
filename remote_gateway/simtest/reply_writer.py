"""Writes what a simulated person types into the reply box: reads the robot's question on stdin, prints one short reply.

Codex writes it, so a report must call these replies simulated, not human. --canned is for offline runs, with no Codex.
"""
import argparse
from pathlib import Path
import subprocess
import sys
import tempfile

PROMPT = ('You are a person watching a robot arm try a household task. It has failed and asks you for help. '
          'Answer in one or two plain sentences, as advice from what you would see. Do not use coordinates, file names or code. '
          'Reply with the advice only.\n\nThe robot asks: ')


def codex(question, model, timeout):
    with tempfile.TemporaryDirectory(prefix='sim-reply-') as folder:
        answer = Path(folder) / 'answer.txt'
        subprocess.run(['codex', 'exec', '--ephemeral', '--skip-git-repo-check', '-s', 'read-only', '-m', model, '-c', 'model_reasoning_effort=high', '-o', str(answer), '-'],
                       input=(PROMPT + question).encode(), cwd=folder, timeout=timeout, check=True, capture_output=True)
        return answer.read_text(encoding='utf-8').strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--canned', action='store_true')
    parser.add_argument('--model', default='gpt-6.1-sol')
    parser.add_argument('--timeout', type=float, default=100)
    args = parser.parse_args()
    question = sys.stdin.read().strip()
    text = f'Canned reply to: {question[:60]}' if args.canned else codex(question, args.model, args.timeout)
    if not 0 < len(text) <= 2000:raise SystemExit('reply is empty or too long')
    print(text)


if __name__ == '__main__':
    main()
