---
name: consult
description: "Hand off a hard problem to multiple independent agents using different models in parallel, synthesize their answers, and run a falsification round on any design proposal before it is built. Use when: stuck on a hard problem, no obvious solution, want multi-model perspectives, consult panel, brainstorm with models, second opinion on tricky design, want a proposal attacked before committing to it. Saves context tokens by running consultations in isolated subagents."
argument-hint: "[models...] e.g. 'opus gpt gemini' or blank for current model + sonnet"
---

# Multi-Agent Consult

Hand off a problem or prompt to multiple independent subagents running on different models, then synthesize their responses. Useful for hard problems where the main session has no obvious solution and you want diverse perspectives without polluting the main context.

**This skill replaces manual copy-pasting between chat windows.** The agent dispatches the same prompt to every requested model in parallel, waits for all responses, and combines them into a single synthesis. The user should never have to copy a prompt or response by hand.

## When to Use

- Stuck on a tricky design, debugging, or architecture problem with no clear answer
- Want a panel of independent opinions on an ambiguous decision
- The problem is hard enough that one model's answer is unreliable
- Want to compare how different models approach the same prompt
- You catch yourself about to paste the same question into multiple model chats

## When not to use

- Simple questions with one clear answer (just answer them)
- Tasks that require shared state or sequential reasoning across agents
- Code review of changes (use `pre-commit-review` instead)

## Procedure

### 1. Clarify the Problem

Before launching subagents, make sure the problem statement is self-contained. Each subagent starts with zero conversation context, so the prompt must include everything needed to reason about the problem.

If the user's request is vague (e.g. "ask the models what they think"), restate the problem in your own words and confirm with the user before dispatching. A bad prompt to four models wastes four times the tokens.

For non-trivial consults, specifically confirm with the user: (a) the candidate option set you intend to send (labels and brief descriptions), (b) any options to rule out upfront so the panel doesn't waste cycles on them, (c) which models to query. A 10-second confirm avoids re-running 3 subagents.

### 2. Parse Model Arguments

The user may specify models in the argument. Parse the argument to identify requested models:

The shorthands, and how to pass a model to `runSubagent`, are in
[../references/models.md](../references/models.md). Read it rather than assuming a
version; `pre-commit-review` shares that file, so neither skill carries its own copy.

If multiple models are specified, run one subagent per model **in parallel** in a single tool-call block.

If no models are specified, use the blank-argument default from that table. If the current session model is one of those, omit the `model` parameter for it.

### Dispatch route: OpenAI models via the codex CLI (shell)

Where `runSubagent` cannot target an OpenAI model (e.g. Claude Code), dispatch GPT through the
codex CLI as a shell task: `codex exec --sandbox read-only "<self-contained prompt>"` (uses the
CLI's configured default model, currently GPT-5.6 Sol; override with `-c model=...`). Gotchas,
each learned from a real run:

- A deep run outlives a 10-minute foreground shell timeout. Run it in the background and read
  the output file on completion.
- Do not pipe large diffs or files through stdin. The read-only sandbox lets codex read the
  repo itself: name a commit hash or file paths and let it run its own git and reads - it also
  finds surrounding context stdin would have hidden.
- Output is a session transcript with the answer at the tail. Demand a strict deliverable
  format in the prompt ("ranked findings, file:line, concrete failure scenario") so the tail
  is directly usable without parsing the transcript.
- Calibration: one deep code review is roughly 200k tokens and 25 minutes. Right for a
  risky-feature gate or a genuinely hard design question; wrong for routine checks.

### Handoff files for consults that run outside any harness

When the consult is an external web chat that cannot touch the repo, the handoff file stands in
for the repository and must carry what its reader can no longer go and check:

- file:line evidence for every load-bearing claim, each marked measured or reasoned.
- The requirement list marked as attackable, in as many words: "if a premise here is wrong,
  saying so is the most valuable thing you can return."
- The dead-end ledger in varied / held-fixed form, with the constraint each failure established.

However good the handoff, the answer is frame-bound by construction. Re-verify its premises
against the source before anything is built on it.

### 3. Construct the Consult Prompt

Write a single self-contained prompt that you will send to every subagent. Include:

- **Problem statement**: What is being asked. Be precise.
- **Context, and the repository itself**: Give the consult the source, not only your summary of it. Quote your conclusions inline, but have the consult verify the premises at the source: list the entry-point files as absolute paths and require that every load-bearing claim it relies on be checked against the code rather than accepted from the prompt. The fact that decides a hard problem is usually one the summary dropped - an invariant stated in a comment, a helper that already exists, a wiring detail.
- **Frame-bound consults**: A consult that cannot reach the source, such as an external chat, can only rearrange the frame you handed it. Say so in the synthesis, and treat its silence about source-level constraints as unknown rather than as clearance. Its premises must be re-verified against the code before anything is built on its answer.
- **What's been tried**: For each dead end, what you varied, what you held fixed, and the constraint its failure established - not only the verdict. The explanation attached to a dead end is a separate claim from the observation and can be wrong on its own, and a wrong one quietly forbids every later idea that shares the innocent variable.
- **What you want back**: Concrete deliverable. A diagnosis? A design proposal? Multiple candidate solutions with tradeoffs? Pseudocode? A direct answer?
- **Scope limits**: Read-only vs. write. Almost always read-only for a consult. Tell the subagent explicitly: "Do not edit files. Return your analysis as text."

Subagents should reason independently. Do not tell them what other models are being asked or what answer is expected. Each one should form its view from the prompt alone.

For a design or a stuck problem, as opposed to a lookup, give one panel member a different brief from the rest. Ask it to derive the requirement and invariant list independently from the source and from the product's own behaviour, then answer two questions: which stated requirement could move, and what is missing from this list? Tell it outright that breaking the question's frame is the most valuable thing it can return. The other members answer inside the frame as usual. Divergence between the frame-attacker and the rest is signal, not noise: a question can carry the losing assumption inside it, and a panel that all answers within one will agree its way into the wrong design.

Deduplicate candidate options before dispatching. If two labeled options are functionally equivalent (e.g. "F = A"), collapse them. Redundant options waste panel attention and clutter the synthesis.

### 4. Launch Consult Subagents

For each requested model, call `runSubagent` with:

- `description`: "Consult (`<model-short-name>`)" - use distinct descriptions per model
- `model`: The model string from the table in Step 2 (omit for current session model)
- `agentName`: Use `Explore` if the agent is available and the consult is purely read-only research. Otherwise omit to use the default agent. Pick whichever fits the task better.
- `prompt`: The self-contained consult prompt from Step 3

Launch all subagents in a single tool-call block so they run in parallel. Do not serialize them. Do not ask the user to relay anything to other chats; the whole point is automation. Wait for every subagent to return before synthesizing.

### 5. Synthesize Results

After all subagents return, present a synthesis to the user. Do not dump the full raw responses. Aim for:

**Convergence summary** (1-3 sentences): Where do the models agree? Convergence across independent models is a strong signal.

If your consult prompt defined a labeled option set (A/B/C/...), briefly restate each label's meaning in the synthesis. The user did not see the prompt sent to the subagents and cannot map labels back to options.

**Divergence table**: Where do they disagree, and on what?

| Aspect | Opus | Sonnet | GPT | Gemini |
|--------|------|--------|-----|--------|
| Recommended approach | A | A | B | A |
| Root cause hypothesis | X | X | Y | X |

**Notable unique insights**: Findings only one model raised that seem worth surfacing, with attribution.

**Your read**: A short take from the main session on which direction looks strongest and why, or which questions remain open. Be willing to disagree with the panel if you have reason to.

Then **ask the user** what they want next:
- Proceed with one of the proposed approaches
- Drill into a specific model's reasoning
- Run a follow-up consult with a refined prompt
- Move on with your own synthesis

### 6. The Falsification Round (design consults)

A proposal that has not survived a kill attempt is a hypothesis, not a recommendation. Before adopting the panel's leading proposal, dispatch a fresh consult - fresh context, repo access - whose only brief is to kill it:

- Name every consumer of every value the proposal changes, and show each one was checked. "Downstream reads were checked" is the sentence that hides the one that was not.
- Hunt the invariant that breaks it, in the code and in the product's behaviour.
- State in the prompt that a bare "looks good" is a failed consult. The value is in what it breaks.

When a proposal dies, extract the invariant that killed it and add it to the prompt's constraint ledger. The kill is design input: the next round starts from the updated ledger rather than from scratch. Two or three rounds of propose, falsify, revise routinely beat any single answer, including one from a stronger model.

The loop costs real tokens and wall time. Spend it where a wrong design ships expensively; a lookup does not need it.

### 7. Context Management

The subagent responses can be large. After synthesizing, do not re-quote the full raw responses in later turns. Refer back to specific points by model name and topic if the user asks follow-up questions. If a follow-up needs the full response, re-fetch by running a new targeted consult rather than dragging the original text forward.

### 8. Spot-Check the Panel

Models can make systematic errors, especially when the prompt subtly leads them. Before treating a convergent recommendation as truth:

- If the panel cites specific code, line numbers, function names, or library APIs, verify at least one citation against the actual file or docs. Hallucinated specifics are the most common multi-model failure mode.
- If all models agree on something surprising, ask: did the prompt frame the answer? Convergence on a leading question is noise, not signal.
- Be willing to disagree with the panel in your synthesis if a citation doesn't check out or a constraint they missed makes their recommendation wrong.
- Verifying a citation is the minimum. For a proposal you intend to build, the falsification round is the real check: agreement between models is not a test of the proposal, only of the frame they shared.
