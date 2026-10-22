# Model shorthands

The single source for the model shorthands used by `consult`, `review`, `delegate` and `dispatch`. Each skill points here rather than carrying a copy, because two copies means two places to update and one that gets forgotten.

## Shorthands

| Shorthand | Model |
|-----------|-------|
| (none/blank) | `review`: the rule set's default reviewers, or one reviewer on the current model when it names none. `consult`: the current model, run twice in parallel. |
| `opus` | Claude Opus 5.5 |
| `sonnet` | Claude Sonnet 5.5 |
| `fable` | Claude Fable 5.1 |
| `haiku` | Claude Haiku 5.5 |
| `sol` | GPT-6.1 Sol |
| `terra` | GPT-5.6 Terra |
| `astra` | GPT-6 Astra |
| `luna` | GPT-6 Luna |
| `gemini` | Gemini 3.1 Pro |
| `flash` | Gemini 3.5 Flash |

`haiku` selects `claude-haiku-5-5`. Pass this explicit ID to Claude native children. `dispatch.py` and `arc run` expand the shorthand. `sonnet` passes through to the Claude CLI's own alias, and an explicit `claude-sonnet-5` ID still selects Sonnet 5. `sol` selects `gpt-6.1-sol`. `luna` selects `gpt-6-luna`. `astra` selects `gpt-6-astra`. `terra` selects `gpt-5.6-terra`. GPT-6 Luna serves small reviews that do not need a deep review.

Explicit IDs retain their version. [model-providers.md](model-providers.md) owns provider-qualified IDs, availability checks, native inheritance, and dispatch selection.

## Passing the model

Follow [model-providers.md](model-providers.md) before choosing native children or fresh CLI sessions. The parameter format varies by environment. Check what the subagent tool expects:

- If the tool documents a format like `"Name (Vendor)"`, append the vendor, for example `Claude Opus 5.5 (copilot)`.
- If it accepts plain names or IDs, use the name or ID directly, for example `claude-opus-5-5`.
- When unsure, read the subagent tool's `model` parameter description.

## When the harness cannot reach OpenAI models

Some harnesses cannot target an OpenAI model through their subagent tool. Each skill documents its own shell dispatch route for that case, because the prompt contract and the failure modes differ between a consult and a review.

## When adding or advancing a model

Use this checklist only when changing the shared model selection.

- Check the runner's catalog when it exposes one, and the provider's documented IDs, supported effort, and delegation support.
- Follow [model-providers.md](model-providers.md) for each newly claimed model/provider combination and record its completed delegation check.
- Update this reference, `dispatch.py` and Arc's standalone model table together. Preserve explicit IDs and versioned aliases.
- Update the owning price table with exact model and provider boundaries, context bands, cache rates, and service tiers. Arc owns the rate table.
- Extend `session-cost-audit/scripts/test_transcript_usage.py` and `test_arc_cost_parity.py`. Run both with `ARC_REPO` pointing to the owner's updated checkout.
- Check downstream pricing adapters forward the source and window to Arc. Consume its report instead of loading or copying rates.
- Run both launchers' behavior tests and validate changed skills. Prove new drift guards fail with the old mapping restored.
