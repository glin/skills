# Model shorthands

The single source for the model shorthands used by `consult`, `pre-commit-review`, `delegate` and `dispatch`. Each skill points here rather than carrying a copy, because two copies means two places to update and one that gets forgotten.

## Shorthands

| Shorthand | Model |
|-----------|-------|
| (none/blank) | `pre-commit-review`: one reviewer on the current model. `consult`: Claude Opus 5.5 + Claude Sonnet 5 in parallel. |
| `opus` | Claude Opus 5.5 |
| `sonnet` | Claude Sonnet 5 |
| `fable` | Claude Fable 5.1 |
| `haiku` | Claude Haiku 4.5 |
| `sol` | GPT-6 Sol |
| `sol6` | GPT-6 Sol |
| `terra` | GPT-5.6 Terra |
| `astra` | GPT-6 Astra |
| `luna` | GPT-6 Luna |
| `luna6` | GPT-6 Luna |
| `codex` | GPT-5.3 Codex |
| `gemini` | Gemini 3.1 Pro |
| `flash` | Gemini 3.5 Flash |
| `all` | opus + sonnet + sol + gemini in parallel |

`sol` and `sol6` select `gpt-6-sol`. `luna` and `luna6` select `gpt-6-luna`. `astra` selects `gpt-6-astra`. Luna is the cheaper build of Sol's generation, for a review small enough that a deep one is waste.

`dispatch` adds the `openai.` prefix for Bedrock. `sol`, `sol6`, `luna` and `luna6` select their GPT-6 models on either provider. The `terra` shorthand selects GPT-5.6 Terra on either provider.

## Passing the model

The parameter format varies by environment. Check what the subagent tool expects:

- If the tool documents a format like `"Name (Vendor)"`, append the vendor, for example `Claude Opus 5.5 (copilot)`.
- If it accepts plain names or IDs, use the name or ID directly, for example `claude-opus-5-5`.
- When unsure, read the subagent tool's `model` parameter description.

Omit the `model` parameter entirely when the requested model is the one the session is already running.

## When the harness cannot reach OpenAI models

Some harnesses cannot target an OpenAI model through their subagent tool. Each skill documents its own shell dispatch route for that case, because the prompt contract and the failure modes differ between a consult and a review.
