# Model shorthands

The single source for the model shorthands used by `consult` and `pre-commit-review`.
Both skills point here rather than carrying a copy, because two copies means two
places to update and one that gets forgotten.

## Shorthands

| Shorthand | Model |
|-----------|-------|
| (none/blank) | Claude Opus 5 + Claude Sonnet 5 in parallel |
| `opus` | Claude Opus 5 |
| `sonnet` | Claude Sonnet 5 |
| `fable` | Claude Fable 5 |
| `haiku` | Claude Haiku 4.5 |
| `gpt` | GPT-5.6 Sol |
| `codex` | GPT-5.3 Codex |
| `gemini` | Gemini 3.1 Pro |
| `flash` | Gemini 3.5 Flash |
| `all` | opus + sonnet + gpt + gemini in parallel |

A shorthand names a family and a generation, not a wire format. Resolve it to the
newest build of that family the harness actually offers, and prefer the harness's own
model list over this table when the two disagree: this table is a declaration that
someone updated it, while the model list is what the runtime will accept.

## Passing the model

The parameter format varies by environment. Check what the subagent tool expects:

- If the tool documents a format like `"Name (Vendor)"`, append the vendor, for
  example `Claude Opus 5 (copilot)`.
- If it accepts plain names or IDs, use the name or ID directly, for example
  `claude-opus-5`.
- When unsure, read the subagent tool's `model` parameter description.

Omit the `model` parameter entirely when the requested model is the one the session
is already running.

## When the harness cannot reach OpenAI models

Some harnesses cannot target an OpenAI model through their subagent tool. Each skill
documents its own shell dispatch route for that case, because the prompt contract and
the failure modes differ between a consult and a review.
