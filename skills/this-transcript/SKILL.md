---
name: this-transcript
description: Find and read the transcript file of the current Claude Code or Codex session. Use when: look up this conversation, read my own transcript, what did I say earlier, find the session file, get the transcript of the current convo, read this session's JSONL.
---

# Read the transcript of this session

The session names itself. Never sort a directory by modification time, because a parallel session writes later than you do.

## Find the file

Both agents publish their session id. Glob for it. The glob takes 0.04 seconds.

```bash
f=$(ls "${CLAUDE_CONFIG_DIR:-$HOME/.claude}"/projects/*/$CLAUDE_CODE_SESSION_ID.jsonl)        # Claude Code
f=$(ls "${CODEX_HOME:-$HOME/.codex}"/sessions/*/*/*/rollout-*-$CODEX_SESSION_ID.jsonl)   # Codex
```

Check `$CODEX_SESSION_ID` first when both are set. A Codex session started from a Claude Code shell inherits `CLAUDE_CODE_SESSION_ID` from its parent, and that value names the wrong session.

Do not build the directory name from the working directory. The Claude directory slug comes from the `cwd` that the agent recorded, so its drive letter case and its worktree path will not match what `pwd` gives you.

## Read it

Both files are JSONL, one record per line. Search first, and parse only what you need.

```bash
grep -c '"type":"assistant"' "$f"
grep -o '"name":"[A-Za-z]*"' "$f" | sort | uniq -c | sort -rn
```

## List the user turns

```bash
python3 - "$f" <<'EOF'
import json, io, sys
skip = ("<command-name>", "<local-command-stdout>", "<command-message>",
        "<local-command-caveat>", "<task-notification>")
for line in io.open(sys.argv[1], encoding="utf-8"):
    r = json.loads(line)
    if r.get("isSidechain"):
        continue
    a = r.get("attachment") or {}
    if a.get("type") == "queued_command":
        parts = a.get("prompt") or []
    elif r.get("type") == "user" and not r.get("isMeta"):
        c = r.get("message", {}).get("content")
        parts = [{"text": c}] if isinstance(c, str) else c if isinstance(c, list) else []
    else:
        continue
    t = " ".join(p.get("text", "") for p in parts
                 if isinstance(p, dict) and p.get("type", "text") == "text").strip()
    if not t or t.startswith(skip):
        continue
    print(r.get("timestamp", "")[:19], t.split("\n")[0][:70])
EOF
```

## Traps

- A message the user sends mid-turn is not a `user` record. It arrives as `type: attachment` with `attachment.type: queued_command`, and its text sits in `attachment.prompt`. A turn lister that reads only `user` records drops it silently.
- A `user` record is not always a user turn. Slash commands, their output, and injected context all arrive as `user`. Drop `isMeta`, drop `isSidechain`, and drop the tags in the snippet above.
- Other record types share the file: `queue-operation`, `attachment`, `atis-latch`, `last-prompt`, `ai-title`, `mode`, `system`.
- Every Claude record carries `cwd` and `sessionId`. Read them to confirm the file, because two working directories can produce one directory slug.
- A Codex rollout carries `id` and `session_id` in its first record. `id` names the rollout. `session_id` names a thread that can span many rollouts, so never resolve a file by it.
- Codex writes with a delay of about six seconds. The last few seconds of the session are not in the file yet.
