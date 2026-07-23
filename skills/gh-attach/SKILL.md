---
name: gh-attach
description: "Upload local images, videos, or files to GitHub and get paste-ready markdown (inline image, inline video player, or download link) for an issue, PR, or comment body. Use when you need to embed a screenshot or a recorded video in GitHub from the command line, since gh has no attachment upload. Requires a github.com user_session cookie via GH_SESSION_TOKEN."
argument-hint: <file> [file...] [--repo owner/repo]
allowed-tools: Bash(bash:*), Bash(gh:*), Bash(curl:*), Bash(jq:*), Bash(file:*)
---

# Attach files to GitHub from the CLI

`gh` cannot upload the drag-and-drop attachments the GitHub web UI accepts. This skill reproduces that flow with a small script, so you can turn a local screenshot or video into a paste-ready reference:

- images embed inline: `![name](url)`
- videos render as an inline player: a bare `https://github.com/user-attachments/assets/...` URL
- other files (PDF, zip, log) render as a download link: `[name](url)`

The script is `gh-attach.sh` in this skill's directory.

## Prerequisites

- `curl`, `jq`, `file`, and `gh` on PATH (`sqlite3` too, only for `--firefox`).
- A github.com **`user_session`** cookie. This is NOT the `gh` OAuth token; that token does not work on the attachment endpoint. The script resolves it in this order: `--token`, `$GH_SESSION_TOKEN`, the token file `${XDG_CONFIG_HOME:-~/.config}/gh-attach/token`, then `--firefox`.

### Getting the cookie

It is a browser session cookie. Read it once:

- **Chrome/Edge/Brave**: open a logged-in github.com page, DevTools (F12) → Application → Storage → Cookies → `https://github.com` → copy the `user_session` value.
- **Firefox**: it is stored in plaintext, so `gh-attach.sh --firefox` can read it directly, or:
  `sqlite3 ~/.mozilla/firefox/<profile>/cookies.sqlite "SELECT value FROM moz_cookies WHERE name='user_session' AND host LIKE '%github.com'"`

### Where to keep it

Recommended: a dedicated file the script reads on demand, kept out of any git repo and out of shell configs. On-demand read means the token is not exported into every process's environment.

```bash
mkdir -p ~/.config/gh-attach
printf '%s' '<value>' > ~/.config/gh-attach/token
chmod 600 ~/.config/gh-attach/token
```

Alternatives: export `GH_SESSION_TOKEN` (convenient, but plaintext in whatever config sets it, and visible to every child process), or load it from a secret manager at shell start, e.g. fish: `set -x GH_SESSION_TOKEN (secret-tool lookup service github-session)`. Do not put the raw value in a dotfiles repo or any file inside a project.

## Usage

The script lives at `~/.claude/skills/gh-attach/gh-attach.sh`. For convenience you can symlink it onto your PATH (`ln -s ~/.claude/skills/gh-attach/gh-attach.sh ~/bin/gh-attach`).

```bash
# Infers the repo from the current git workspace
GH_SESSION_TOKEN=... bash ~/.claude/skills/gh-attach/gh-attach.sh screenshot.png

# Multiple files, explicit repo
bash ~/.claude/skills/gh-attach/gh-attach.sh shot1.png demo.webm report.pdf --repo owner/repo

# Firefox convenience (reads the cookie for you)
bash ~/.claude/skills/gh-attach/gh-attach.sh --firefox screenshot.png
```

Each successful upload prints one paste-ready line on stdout. Pipe it straight into a body:

```bash
gh issue create --title "Repro" --body "Steps:

$(bash ~/.claude/skills/gh-attach/gh-attach.sh bug.png)
"
```

To confirm an upload without posting anything, GET the returned URL with the same cookie; a 200 means the asset resolved.

## Security

`user_session` is **password-equivalent**: full, unscoped account access. Treat it like a password.
- Prefer the `GH_SESSION_TOKEN` env var over the `--token` flag (a flag is visible in `ps`). The script never prints or logs the token.
- Do not commit it or paste it into chat/issues.
- It rotates: signing out of GitHub or the session expiring invalidates it. If it leaks, sign out or revoke it at Settings → Sessions.
- For shared/CI use, extract from a dedicated bot account, not your own.

## Caveats

- This uses GitHub's **undocumented** internal upload endpoint. It can change or break without notice; the script degrades to a non-zero exit and a stderr message rather than posting anything.
- The `user_session` must belong to a user with **write access** to the target repo (the upload token only appears on repo pages you can write to).
- Attachments inherit the repo's visibility: uploads to a private repo require auth to view.
- The presigned S3 policy expires quickly (minutes), so each file gets a fresh policy per run.
