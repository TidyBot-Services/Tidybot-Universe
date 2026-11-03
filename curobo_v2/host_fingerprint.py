"""Best-effort host diagnostics shared by setup and the planner."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

DRIVER_MIN = '570.124.06'
GLIBC_MIN = '2.35'
SUPPORTED_OS = ('22.04', '24.04')
LIBRARIES = ('libcuda.so.1', 'libc.so.6', 'libnvidia-ml.so.1',
             'libnvidia-ptxjitcompiler.so.1')


def _digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def host_libraries():
    output = ''
    # The planner service runs with a reduced environment, so PATH may not reach sbin.
    for ldconfig in ('ldconfig', '/sbin/ldconfig', '/usr/sbin/ldconfig'):
        try:
            output = subprocess.run([ldconfig, '-p'], capture_output=True, text=True,
                                    check=False, timeout=10).stdout
            break
        except (OSError, subprocess.SubprocessError):
            continue
    found = {}
    for token in LIBRARIES:
        candidates = [line.rsplit('=>', 1)[1].strip() for line in output.splitlines()
                      if line.strip().startswith(token + ' ') and '=>' in line]
        found[token] = None
        for candidate in candidates:
            try:
                path = Path(candidate).resolve()
                if path.is_file():
                    found[token] = {'path': str(path), 'sha256': _digest(path)}
                    break
            except OSError:
                continue
    return found


def host_fingerprint():
    release = {}
    try:
        for line in Path('/etc/os-release').read_text().splitlines():
            if '=' in line:
                key, value = line.split('=', 1)
                release[key] = value.strip('"')
    except OSError:
        pass
    driver = gpu = None
    smi = shutil.which('nvidia-smi') or '/usr/bin/nvidia-smi'
    try:
        result = subprocess.run([smi, '--query-gpu=driver_version,name',
                                 '--format=csv,noheader'], capture_output=True, text=True,
                                check=False, timeout=30)
        if result.returncode == 0 and result.stdout.strip():
            driver, gpu = (part.strip() for part in result.stdout.splitlines()[0].split(',', 1))
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    if driver is None:
        try:
            match = re.search(r'Kernel Module.*?\s(\d+\.\d+(?:\.\d+)?)\s',
                              Path('/proc/driver/nvidia/version').read_text())
            driver = match.group(1) if match else None
        except OSError:
            pass
    try:
        glibc = os.confstr('CS_GNU_LIBC_VERSION')
    except (OSError, ValueError):
        glibc = None
    return {'os': {key: release.get(key) for key in ('VERSION_ID', 'PRETTY_NAME')},
            'kernel': os.uname().release, 'glibc': glibc,
            'driver': driver, 'gpu': gpu, 'libraries': host_libraries(),
            'created_at': datetime.now(timezone.utc).isoformat()}


def fingerprint_sha256(value):
    raw = json.dumps({key: item for key, item in value.items() if key != 'created_at'},
                     sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    return hashlib.sha256(raw).hexdigest()


def _version(value):
    match = re.search(r'\d+(?:\.\d+)*', value or '')
    return tuple(int(part) for part in match.group().split('.')) if match else None


def gate(value):
    failures, warnings = [], []
    driver = value.get('driver')
    glibc = value.get('glibc')
    os_version = (value.get('os') or {}).get('VERSION_ID')
    if _version(driver) is None or _version(driver) < _version(DRIVER_MIN):
        failures.append(f'NVIDIA driver {driver or "unreadable"}; requires >= {DRIVER_MIN}. Upgrade the NVIDIA driver.')
    if _version(glibc) is None or _version(glibc) < _version(GLIBC_MIN):
        failures.append(f'glibc {glibc or "unreadable"}; requires >= {GLIBC_MIN}. Upgrade to Ubuntu 22.04 or 24.04.')
    if os_version not in SUPPORTED_OS:
        warnings.append(f'Ubuntu {os_version or "unreadable"}; supported: {", ".join(SUPPORTED_OS)}. Upgrade to a supported Ubuntu release.')
    return failures, warnings
