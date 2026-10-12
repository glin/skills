#!/usr/bin/env bash
set -euo pipefail
skill_dir="$(cd "$(dirname "$0")" && pwd)"
if ! python3 -c 'import sys; assert sys.version_info >= (3,10)' >/dev/null 2>&1; then
  echo "callback monitoring requires Python 3.10. Keep the result for explicit recovery." >&2
  exit 2
fi
exec python3 "$skill_dir/watch_claude.py" "$@"
