# Model providers and execution

This reference owns provider rules for native children and fresh CLI sessions. [models.md](models.md) owns model shorthands. Each workflow retains its roles, authorization, review limits, and spend limits.

## Before selecting a transport

Read the active runtime's model and provider metadata. A model name alone does not establish its billing provider or availability. Read only needed configuration fields. Never print credentials. Prefer the runtime's effective settings over a configuration default when profiles or command overrides apply.

Check the native tool's model and effort parameters and the runner's catalog when available. Catalog presence does not prove a completed child task. Preserve an explicit requested model and provider. Stop when that combination is unavailable. Never substitute another model or provider.

## Native children

Use native children when the harness supports the requested combination and the workflow permits that transport. Omit model overrides for the current model. Use explicit overrides only when the provider and tool support them. Match the selected tier to the workflow's role.

A full-history fork inherits the parent's model and effort when the tool requires inheritance. Never attach overrides to that fork. Use a supported isolated-context mode when an authorized task requires another model. Give it the complete bounded brief.

For Claude Code, select the role's supported model name or ID. Resolve its current build and alias rules through [models.md](models.md).

## Codex on Amazon Bedrock

Omit `model` and `reasoning_effort` from native child spawns. The child inherits the configured provider-qualified model. Tier preferences do not authorize an unsupported override.

Before spawning, inspect the active model with:

```text
codex debug models
```

The supported Bedrock delegation path requires `multi_agent_version: v1`. The `v2` encrypted task envelope produces this provider error:

```text
Invalid 'input': value did not match any expected variant
```

When the active catalog reports `v2`, stop native delegation. A supported `model_catalog_json` override must pin that model to `v1` before a fresh Codex session starts. Apply configuration changes only within the author's authorization. Do not alter a live session's catalog or treat a saved override as a completed delegation check.

Complete one minimal child task before a large fan-out on a newly claimed model/provider combination. If it fails, diagnose the first error. Do not retry alternate names. Record the provider, exact model, CLI/catalog identity, and completed result. Availability and this check are execution preconditions, not claims established by reading this reference.

## Fresh CLI sessions

Use the workflow's dispatch transport for a requested model the native tool cannot reach. A fresh session does not inherit native child restrictions. It still requires provider availability, a bounded prompt, and the workflow's supported return path. An immutable review uses its required read-only transport.

Use [../dispatch/SKILL.md](../dispatch/SKILL.md) for launcher flags, sandboxing, completion routes, and failure handling. Resolve shorthands through [models.md](models.md). The launcher qualifies bare GPT IDs with `openai.` for Amazon Bedrock and preserves already qualified IDs. This spelling establishes neither availability nor delegation support. Never fall back to another model after launch failure.
