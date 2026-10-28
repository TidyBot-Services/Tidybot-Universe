"""Opt-in, fixture-scoped guard; synthetic pytest resources remain usable."""
import builtins
import io
import os
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def protected_file_reads(monkeypatch, tmp_path_factory):
    synthetic = tmp_path_factory.getbasetemp().resolve()
    def guarded(original):
        def open_file(file, *args, **kwargs):
            if isinstance(file, (str, bytes, os.PathLike)):
                path = Path(os.path.abspath(os.fsdecode(file)))
                protected = any(s in str(path) for s in (
                    '/openarm365/model/', '/openarm_mobile/model/', '/memory-m3/')) or path.name == 'keys.env'
                if protected and not path.resolve().is_relative_to(synthetic):
                    raise PermissionError('T2a test guard refuses protected file: ' + str(path))
            return original(file, *args, **kwargs)
        return open_file
    for module in (builtins, io, os):
        monkeypatch.setattr(module, 'open', guarded(module.open))
