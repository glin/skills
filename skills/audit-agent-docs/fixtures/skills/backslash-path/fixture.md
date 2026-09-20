---
name: backslash-path
description: Red fixture for the windows-path rule, naming a skill-relative file with backslashes. Use when the audit test harness runs, never in real work.
---

# Backslash path

Run `scripts\audit.mjs` before you start. A skill-relative path written that way
breaks on every platform that is not Windows.
