#!/usr/bin/env bash
# Decide whether a reviewed commit survives a rebase, or needs review again.
# Gathers evidence and prints one verdict. It never certifies safety on its own:
# a `disjoint` verdict still requires the cheap interaction check the calling skill names.
#
# Usage:
#   carry-review.sh --repo R --old-base A --candidate C --new-base B --rebased D \
#                   [--verify "npm test"] [--verify-timeout SECONDS] [--conflicted] [--out DIR]
#
# --out defaults to carry-review/<rebased SHA> inside the repository's git directory,
# so the evidence outlives the script and a rerun replaces it. --verify-timeout
# defaults to 1800 seconds and needs the timeout command.
#
# Verdicts, worst first: conflict | verify-failed | unverified | overlap | patch-differs | disjoint
# Exit status: 0 for disjoint, 1 for every verdict that needs a reviewer, 2 for a usage error.
set -uo pipefail

repo=""; old_base=""; candidate=""; new_base=""; rebased=""; verify=""; conflicted=0; out=""; verify_timeout=1800
while [ $# -gt 0 ]; do
  case "$1" in
    --repo) repo="$2"; shift 2 ;;
    --old-base) old_base="$2"; shift 2 ;;
    --candidate) candidate="$2"; shift 2 ;;
    --new-base) new_base="$2"; shift 2 ;;
    --rebased) rebased="$2"; shift 2 ;;
    --verify) verify="$2"; shift 2 ;;
    --verify-timeout) verify_timeout="$2"; shift 2 ;;
    --out) out="$2"; shift 2 ;;
    --conflicted) conflicted=1; shift ;;
    *) echo "carry-review: unknown argument $1" >&2; exit 2 ;;
  esac
done
for v in repo old_base candidate new_base rebased; do
  eval "val=\$$v"
  [ -n "$val" ] || { echo "carry-review: --${v//_/-} is required" >&2; exit 2; }
done
cd "$repo" || { echo "carry-review: cannot enter $repo" >&2; exit 2; }
case "$verify_timeout" in
  ''|*[!0-9]*) echo "carry-review: --verify-timeout takes whole seconds" >&2; exit 2 ;;
esac
git rev-parse --git-dir >/dev/null 2>&1 || { echo "carry-review: $repo is not a git repository" >&2; exit 2; }
for rev in "$old_base" "$candidate" "$new_base" "$rebased"; do
  git rev-parse --verify --quiet "$rev^{commit}" >/dev/null || { echo "carry-review: $rev is not a commit" >&2; exit 2; }
done
if [ -z "$out" ]; then
  common=$(cd "$(git rev-parse --git-common-dir 2>/dev/null)" 2>/dev/null && pwd) \
    || { echo "carry-review: $repo is not a git repository" >&2; exit 2; }
  short=$(git rev-parse --short=12 "$rebased" 2>/dev/null) \
    || { echo "carry-review: $rebased is not a commit" >&2; exit 2; }
  out="$common/carry-review/$short"
  rm -rf "$out"
fi
mkdir -p "$out" || exit 2

norm() { git diff "$1" "$2" -- . | sed 's/\r$//'; }

norm "$old_base" "$candidate" > "$out/candidate.diff"
norm "$new_base" "$rebased"   > "$out/rebased.diff"
norm "$old_base" "$new_base"  > "$out/target.diff"

git diff --name-only "$old_base" "$candidate" | sort -u > "$out/candidate.files"
git diff --name-only "$old_base" "$new_base"  | sort -u > "$out/target.files"
comm -12 "$out/candidate.files" "$out/target.files" > "$out/overlap.files"

echo "evidence: $out"
echo "candidate files: $(wc -l < "$out/candidate.files" | tr -d ' ')"
echo "target files since old base: $(wc -l < "$out/target.files" | tr -d ' ')"
echo "overlapping files: $(wc -l < "$out/overlap.files" | tr -d ' ')"
[ -s "$out/overlap.files" ] && sed 's/^/  overlap: /' "$out/overlap.files"

verdict=""
[ "$conflicted" = 1 ] && verdict="conflict"

# Overlap outranks a patch difference: an edit in the same file also moves the
# candidate's context lines, and "both sides touched this file" is the useful finding.
if [ -z "$verdict" ] && [ -s "$out/overlap.files" ]; then
  verdict="overlap"
fi

if [ -z "$verdict" ]; then
  if cmp -s "$out/candidate.diff" "$out/rebased.diff"; then
    echo "patch: identical across the rebase"
  else
    echo "patch: changed across the rebase"
    diff "$out/candidate.diff" "$out/rebased.diff" > "$out/patch-delta.txt" 2>/dev/null
    echo "patch delta: $out/patch-delta.txt"
    verdict="patch-differs"
  fi
fi

if [ -n "$verify" ]; then
  if command -v timeout >/dev/null 2>&1; then
    echo "verify: running $verify, limit ${verify_timeout}s"
    timeout --kill-after=30 "$verify_timeout" sh -c "$verify" > "$out/verify.log" 2>&1
  else
    echo "verify: running $verify with no limit, because the timeout command is missing"
    sh -c "$verify" > "$out/verify.log" 2>&1
  fi
  status=$?
  if [ "$status" = 0 ]; then
    echo "verify: pass ($out/verify.log)"
  else
    if [ "$status" = 124 ]; then
      echo "verify: TIMEOUT after ${verify_timeout}s ($out/verify.log)"
    else
      echo "verify: FAIL ($out/verify.log)"
    fi
    [ "$verdict" = "conflict" ] || verdict="verify-failed"
  fi
else
  echo "verify: not run, so no verdict below conflict is safe to act on"
  [ "$verdict" = "conflict" ] || verdict="unverified"
fi

verdict="${verdict:-disjoint}"
echo "VERDICT=$verdict"
[ "$verdict" = "disjoint" ] && exit 0
exit 1
