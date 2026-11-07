"""What this test's runs take on disk, and removing them. Lists by default; removes only with --delete.

Only folders named after a run in this test's own gateway database are ever touched, so another instance's runs cannot be hit.
"""
import argparse
from pathlib import Path
import re
import sqlite3
import subprocess

RUN = re.compile(r'devexp-[a-f0-9]{24}')


def own_runs(database):
    with sqlite3.connect(f'file:{database}?mode=ro', uri=True) as db:
        return [row[0] for row in db.execute('SELECT experiment_id FROM runs ORDER BY created_at') if RUN.fullmatch(row[0])]


def shell(prefix, command):
    return subprocess.run(f'{prefix} {command}' if prefix else command, shell=True, capture_output=True, text=True, timeout=300)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, required=True, help="this test's gateway.sqlite3")
    parser.add_argument('--run-root', required=True)
    parser.add_argument('--host-prefix', default='')
    parser.add_argument('--delete', action='store_true')
    args = parser.parse_args()
    total = 0
    for run in own_runs(args.database):
        path = f'{args.run_root.rstrip("/")}/{run}'
        size = shell(args.host_prefix, f'du -sb {path}')
        if size.returncode:
            print(f'不存在  {run}');continue
        count = int(size.stdout.split()[0]);total += count
        print(f'{count / 2**30:8.2f}G  {path}')
        if args.delete:
            done = shell(args.host_prefix, f'rm -rf {path}')
            print('  已删除' if done.returncode == 0 else f'  删除失败：{done.stderr.strip()}')
    print(f'合计 {total / 2**30:.2f}G' + ('' if args.delete else '（只列出，没有删除；删除要加 --delete）'))


if __name__ == '__main__':
    main()
