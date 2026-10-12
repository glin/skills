#!/usr/bin/env python3
"""Return Arc schema 2 cost JSON for a session or transcript."""
from __future__ import annotations
import sys
from arc_cost import main as arc_main, normalize_target

def main(args: list[str] | None = None) -> int:
    forwarded = list(sys.argv[1:] if args is None else args)
    if forwarded:
        forwarded[0] = normalize_target(forwarded[0])
    return arc_main(forwarded)

if __name__ == "__main__":
    raise SystemExit(main())
