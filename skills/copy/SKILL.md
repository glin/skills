---
name: copy
description: Use when the user asks to copy your last/previous response, answer, or output to the clipboard (e.g. "/copy", "copy that", "copy to clipboard", "put that on my clipboard").
---

# Copy Response to Clipboard

Copy your immediately preceding response to the system clipboard, for the Claude
Code VS Code extension where there's no one-click copy button.

Write the **verbatim raw markdown** of your last message to a temp file (avoids
shell-escaping bugs), then pipe it to the first clipboard tool that exists.
Confirm in one line; do not echo the content back.

```bash
tmp="$(mktemp)"
cat > "$tmp" <<'CLAUDE_COPY_EOF'
<your most recent response, verbatim>
CLAUDE_COPY_EOF

if   command -v pbcopy   >/dev/null 2>&1; then pbcopy < "$tmp"                    # macOS
elif command -v wl-copy  >/dev/null 2>&1; then wl-copy < "$tmp"                   # Wayland
elif command -v xclip    >/dev/null 2>&1; then xclip -selection clipboard < "$tmp" # X11
elif command -v xsel     >/dev/null 2>&1; then xsel --clipboard --input < "$tmp"   # X11 (minimal)
elif command -v clip.exe >/dev/null 2>&1; then clip.exe < "$tmp"                  # WSL/Windows
else echo "No clipboard tool found." >&2; fi

rm -f "$tmp"
```

This repo's global instructions specify fish, which has no heredocs: run the block
via `bash -c '...'`, or write the temp file with `printf` instead.
