"""List candidate test cases from Git-visible files for a human-audited ledger."""

import argparse
import fnmatch
import json
from pathlib import Path
import re
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--glob", action="append", required=True, help="Test-file glob, repeatable")
    parser.add_argument("--case-regex", required=True, help="Pattern with optional 'name' group")
    args = parser.parse_args()
    root = args.root.resolve()
    pattern = re.compile(args.case_regex)
    found = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=root,
        check=True,
        stdout=subprocess.PIPE,
    )
    files = [Path(name.decode("utf-8")) for name in found.stdout.split(b"\0") if name]
    count = 0
    selected = 0
    for relative in sorted(files):
        if not any(fnmatch.fnmatch(relative.as_posix(), glob) for glob in args.glob):
            continue
        path = root / relative
        if not path.is_file() or path.is_symlink():
            continue
        selected += 1
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for match in pattern.finditer(line):
                name = match.groupdict().get("name") or match.group(0)
                print(json.dumps({"file": relative.as_posix(), "line": line_number, "case": name}))
                count += 1
    print(json.dumps({"files": selected, "candidate_cases": count, "reconcile_with_runner": True}))


if __name__ == "__main__":
    main()
