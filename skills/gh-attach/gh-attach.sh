#!/usr/bin/env bash
# gh-attach: upload files to GitHub's user-attachments store and print
# ready-to-paste markdown (inline image, inline video player, or download link).
#
# Reproduces the flow the GitHub web UI uses for drag-and-drop attachments.
# It is an undocumented internal endpoint and can change without notice.
# Attachments are scoped to the repo's visibility (private repo -> private asset).
#
# Auth: needs your github.com `user_session` cookie. This is PASSWORD-EQUIVALENT
# (full, unscoped account access) and is NOT the gh OAuth token. It is never
# printed or logged by this script. Resolution order (first match wins):
#   1. --token <value>
#   2. $GH_SESSION_TOKEN
#   3. ${XDG_CONFIG_HOME:-~/.config}/gh-attach/token   (recommend chmod 600)
#   4. Firefox cookies.sqlite  (only if --firefox is passed)
#
# Usage:
#   gh-attach [--repo owner/repo] [--token T | --firefox] FILE [FILE...]
#   GH_SESSION_TOKEN=... gh-attach shot.png
#
# Requires: curl, jq, file, gh (and sqlite3 only for --firefox).
set -euo pipefail

repo="" ; token="${GH_SESSION_TOKEN:-}" ; use_firefox=0 ; files=()
while [ $# -gt 0 ]; do
  case "$1" in
    --repo) repo="$2"; shift 2;;
    --token) token="$2"; shift 2;;
    --firefox) use_firefox=1; shift;;
    -h|--help) sed -n '2,21p' "$0"; exit 0;;
    --) shift; while [ $# -gt 0 ]; do files+=("$1"); shift; done;;
    -*) echo "gh-attach: unknown flag: $1" >&2; exit 2;;
    *) files+=("$1"); shift;;
  esac
done
[ ${#files[@]} -gt 0 ] || { echo "gh-attach: no files given" >&2; exit 2; }

# Infer repo from the current git workspace if not given.
[ -n "$repo" ] || repo=$(gh repo view --json nameWithOwner -q .nameWithOwner 2>/dev/null) || true
[ -n "$repo" ] || { echo "gh-attach: could not determine repo; pass --repo owner/repo" >&2; exit 2; }

# Dedicated token file (kept out of git repos and shell configs). Read on demand,
# so the token is not exported into every process's environment.
if [ -z "$token" ]; then
  cfg="${XDG_CONFIG_HOME:-$HOME/.config}/gh-attach/token"
  [ -f "$cfg" ] && token=$(tr -d '\r\n' < "$cfg")
fi

# Optional convenience: pull the cookie from Firefox's (plaintext) cookie store.
# Copy the DB first so a running Firefox lock does not block the read.
if [ -z "$token" ] && [ "$use_firefox" = 1 ]; then
  db=$(ls ~/.mozilla/firefox/*/cookies.sqlite \
       "$HOME/Library/Application Support/Firefox/Profiles/"*/cookies.sqlite 2>/dev/null | head -1) || true
  if [ -n "$db" ] && command -v sqlite3 >/dev/null; then
    tmpdb=$(mktemp "${TMPDIR:-/tmp}/ghattach.XXXXXX"); cp "$db" "$tmpdb"
    token=$(sqlite3 "$tmpdb" \
      "SELECT value FROM moz_cookies WHERE name='user_session' AND host LIKE '%github.com' LIMIT 1") || true
    rm -f "$tmpdb"
  fi
fi
[ -n "$token" ] || { echo "gh-attach: no session token; set GH_SESSION_TOKEN, write ${XDG_CONFIG_HOME:-$HOME/.config}/gh-attach/token, or pass --token/--firefox" >&2; exit 2; }

CK="Cookie: user_session=$token; __Host-user_session_same_site=$token"
GH_HDRS=(-H "$CK" -H 'accept: application/json' -H 'origin: https://github.com' \
         -H "referer: https://github.com/$repo" -H 'x-requested-with: XMLHttpRequest')

rid=$(gh api "repos/$repo" --jq .id)

# Step 0: scrape the uploadToken from the repo page (needs write access + a valid session).
uploadToken=$(curl -sSL -H "$CK" "https://github.com/$repo" \
  | grep -oE '"uploadToken":"[^"]+"' | head -1 | cut -d'"' -f4) || true
[ -n "$uploadToken" ] || { echo "gh-attach: no uploadToken (bad session or no write access to $repo)" >&2; exit 1; }

upload_one() {
  local f="$1" name size mime policy upload_url href asset_upload_url finalize_tok
  [ -f "$f" ] || { echo "gh-attach: not a file: $f" >&2; return 1; }
  name=$(basename "$f"); size=$(wc -c < "$f" | tr -d ' '); mime=$(file --mime-type -b "$f")

  # Step 1: request the S3 presigned policy.
  policy=$(curl -sS -X POST https://github.com/upload/policies/assets "${GH_HDRS[@]}" \
    -F "name=$name" -F "size=$size" -F "content_type=$mime" \
    -F "authenticity_token=$uploadToken" -F "repository_id=$rid")
  upload_url=$(jq -r '.upload_url // empty' <<<"$policy")
  href=$(jq -r '.asset.href // empty' <<<"$policy")
  asset_upload_url=$(jq -r '.asset_upload_url // empty' <<<"$policy")
  finalize_tok=$(jq -r '.asset_upload_authenticity_token // empty' <<<"$policy")
  [ -n "$upload_url" ] && [ -n "$href" ] || {
    echo "gh-attach: policy request failed for $name: $(jq -rc '.errors? // .' <<<"$policy" 2>/dev/null)" >&2
    return 1; }

  # Step 2: upload to S3. Form fields in the server-given order, file LAST.
  local args=() k v
  while IFS=$'\t' read -r k v; do args+=(-F "$k=$v"); done \
    < <(jq -r '.form | to_entries[] | "\(.key)\t\(.value)"' <<<"$policy")
  curl -sS -X POST "$upload_url" -H 'origin: https://github.com' "${args[@]}" -F "file=@$f" -o /dev/null

  # Step 3: finalize (without this the href 404s).
  curl -sS -X PUT "https://github.com$asset_upload_url" "${GH_HDRS[@]}" \
    -F "authenticity_token=$finalize_tok" -o /dev/null

  # Print a ready-to-paste reference by kind.
  case "$mime" in
    image/*) echo "![$name]($href)";;
    video/*) echo "$href";;              # bare URL -> GitHub renders an inline player
    *)       echo "[$name]($href)";;
  esac
}

rc=0
for f in "${files[@]}"; do upload_one "$f" || rc=1; done
exit $rc
