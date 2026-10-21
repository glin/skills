#!/usr/bin/env python3
"""Resolve the installed shared closure checker without traversing skill junctions."""
import importlib.util
import os
from pathlib import Path
import sys


def shared(root=None):
    root = Path(root or os.environ.get('CLOSURE_SHARED_ROOT', Path.home() / '.agents/skills'))
    script = root / 'references/scripts/closure.py'
    if not script.is_file():
        raise ValueError(f'Unmet prerequisite: shared closure checker at {script}. Set CLOSURE_SHARED_ROOT to the shared skills directory.')
    spec = importlib.util.spec_from_file_location('shared_closure', script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main(argv=None):
    try:
        return shared().main(argv)
    except (OSError, ValueError) as error:
        print(f'closure: {error}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
