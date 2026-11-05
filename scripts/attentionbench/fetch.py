"""Resumable downloads with progress. Standard library only, so it honours the proxy variables."""
from __future__ import annotations

import http.client
from pathlib import Path
import time
import urllib.error
import urllib.request

BLOCK = 4 * 1024 * 1024
PROGRESS_SECONDS = 30
# Temporary answers from a server; any other HTTP error will not change on a retry.
RETRY_STATUS = {408, 425, 429, 500, 502, 503, 504}


def _copy(response, stream, label, have, size, emit, clock):
    last = clock()
    while True:
        block = response.read(BLOCK)
        if not block:
            return have
        stream.write(block)
        have += len(block)
        if clock() - last >= PROGRESS_SECONDS:
            last = clock()
            share = f' ({100 * have // size}%)' if size else ''
            emit(f'[download] {label}: {have / 1e9:.2f} GB{share}')


def download(url: str, target: Path, *, size: int | None = None, label: str | None = None, attempts: int = 8,
             opener=urllib.request.urlopen, sleep=time.sleep, emit=print, clock=time.monotonic) -> Path:
    """Fetch url to target through target.part, so an interrupted run continues where it stopped."""
    target = Path(target)
    label = label or target.name
    part = target.with_name(target.name + '.part')
    if target.is_file() and (size is None or target.stat().st_size == size):
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    emit(f'[download] {label}: {url}' + (f' ({size / 1e9:.2f} GB)' if size else ''))
    for attempt in range(1, attempts + 1):
        have = part.stat().st_size if part.is_file() else 0
        if size is not None and have > size:
            part.unlink()
            have = 0
        try:
            if size is None or have < size:
                headers = {'Range': f'bytes={have}-'} if have else {}
                with opener(urllib.request.Request(url, headers=headers), timeout=60) as response:
                    if have and response.status != 206:
                        have = 0  # The server ignored the range and is sending the whole file.
                    with part.open('ab' if have else 'wb') as stream:
                        have = _copy(response, stream, label, have, size, emit, clock)
            if size is None or have == size:
                part.replace(target)
                return target
            raise OSError(f'got {have} of {size} bytes')
        except urllib.error.HTTPError as exc:
            if exc.code == 416:
                part.unlink(missing_ok=True)
            elif exc.code not in RETRY_STATUS:
                raise
            failure = exc
        except (OSError, http.client.HTTPException) as exc:
            failure = exc
        if attempt == attempts:
            raise OSError(f'{label}: download failed after {attempts} attempts: {failure}')
        emit(f'[download] {label}: {failure}; retry {attempt} of {attempts - 1}')
        sleep(min(60, 2 ** attempt))
    raise AssertionError('unreachable')
