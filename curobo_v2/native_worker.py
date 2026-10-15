"""Isolated bootstrap for the existing native kitchen executor."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
if not sys.dont_write_bytecode or any(ROOT.rglob('*.pyc')) or any(ROOT.rglob('*.pyo')):
    raise RuntimeError('Native service requires -B and a source tree without bytecode caches')
sys.path.insert(0,str(ROOT))
from curobo_v2.openarm_runtime import main
if __name__=='__main__':sys.exit(main())
