"""Scan artifacts and process metadata without reporting secret material."""
import base64
from dataclasses import dataclass
import json
import os
from pathlib import Path
import sqlite3
from urllib.parse import quote
from .dev_agent_secret import Secret


@dataclass(frozen=True)
class Hit:
    where: str
    variant: str


def _variants(canary):
    value = canary.reveal() if isinstance(canary, Secret) else canary
    raw = value.encode()
    if not raw: raise ValueError('empty canary')
    result = [('raw',raw),('json',json.dumps(value,ensure_ascii=True)[1:-1].encode()),
              ('json_utf8',json.dumps(value,ensure_ascii=False)[1:-1].encode()),
              ('url',quote(value,safe='').encode()),('hex',raw.hex().encode()),('hex_upper',raw.hex().upper().encode())]
    for offset in range(3):
        encoded = base64.b64encode(b' '*offset+raw)
        # Keep only complete triples made entirely of canary bytes.
        start = 4 if offset else 0
        end = ((len(raw)+offset)//3)*4
        if end>start: result.append(('base64_'+str(offset),encoded[start:end]))
    if len(raw)>=12: result.extend((('prefix',raw[:12]),('suffix',raw[-12:])))
    return result


def scan(canaries, *, roots, sqlite_paths=(), include_processes=True, extra_blobs=()):
    variants = [v for canary in canaries for v in _variants(canary)]; hits = set()
    def check(blob, where):
        if isinstance(blob,str): blob=blob.encode()
        for name,needle in variants:
            if needle in blob: hits.add(Hit(str(where),name))
    def file(path):
        try:
            if path.is_file() and path.stat().st_size<=50*1024*1024: check(path.read_bytes(),str(path))
        except OSError: pass
    for root in map(Path,roots):
        if root.is_file(): file(root); continue
        for directory,dirs,files in os.walk(root,followlinks=False):
            dirs[:] = [d for d in dirs if not (Path(directory)/d).is_symlink()]
            for name in files:
                path=Path(directory)/name
                if not path.is_symlink():file(path)
    for path in map(Path,sqlite_paths):
        for suffix in ('','-wal','-shm'): file(Path(str(path)+suffix))
        try:
            db=sqlite3.connect('file:'+quote(str(path.resolve()),safe='/')+'?mode=ro',uri=True)
            try:
                for line in db.iterdump(): check(line,str(path)+':iterdump')
            finally: db.close()
        except (sqlite3.Error,OSError): pass
    if include_processes:
        for path in Path('/proc').iterdir():
            if not path.name.isdigit():continue
            try:
                if path.stat().st_uid!=os.getuid():continue
                for name in ('environ','cmdline'):file(path/name)
            except OSError:pass
    blobs=extra_blobs.items() if isinstance(extra_blobs,dict) else enumerate(extra_blobs)
    for name,blob in blobs:check(blob,'extra:'+str(name))
    return sorted(hits,key=lambda h:(h.where,h.variant))


def main():
    import argparse
    import sys
    from dataclasses import asdict
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',action='append',default=[])
    parser.add_argument('--sqlite',action='append',default=[])
    parser.add_argument('--no-processes',action='store_true')
    args=parser.parse_args()
    try:
        value=json.load(sys.stdin)
        if not isinstance(value,list) or not value:raise ValueError()
        canaries=[Secret(v) for v in value]
        if not all(canaries):raise ValueError()
    except (ValueError,TypeError):parser.error('stdin must contain a JSON array of canaries')
    hits=scan(canaries,roots=args.root,sqlite_paths=args.sqlite,include_processes=not args.no_processes)
    print(json.dumps([asdict(hit) for hit in hits]))
    return bool(hits)


if __name__=='__main__':raise SystemExit(main())
