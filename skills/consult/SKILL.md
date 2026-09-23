---
name: consult
description: "Hand off a hard problem to multiple independent agents using different models in parallel, synthesize their answers, and run a falsification round on any design proposal before it is built. Use when: stuck on a hard problem, no obvious solution, want multi-model perspectives, consult panel, brainstorm with models, second opinion on tricky design, want a proposal attacked before committing to it. Saves context tokens by running consultations in isolated subagents."
argument-hint: "[models...] e.g. 'opus sol gemini' or blank for current model + sonnet"
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

A measured claim in the brief carries the two measurements behind it. A brief that says a prediction was falsified must give the predicted value, the observed value, and the timestamps of both. Every model reasons from that claim, so a wrong one buys the same wrong answer as many times as there are panel members, and the error hides in the agreement between them.

For non-trivial consults, specifically confirm with the user: (a) the candidate option set you intend to send (labels and brief descriptions), (b) any options to rule out upfront so the panel doesn't waste cycles on them, (c) which models to query. A 10-second confirm avoids re-running 3 subagents.

### 2. Parse Model Arguments

The user may specify models in the argument. Parse the argument to identify requested models:

The shorthands, and how to pass a model to `runSubagent`, are in [../references/models.md](../references/models.md). Read it rather than assuming a version; `pre-commit-review` shares that file, so neither skill carries its own copy.

If multiple models are specified, run one subagent per model **in parallel** in a single tool-call block.

If no models are specified, use the blank-argument default from that table. If the current session model is one of those, omit the `model` parameter for it.

### Dispatch route: OpenAI models via the codex CLI (shell)

Where `runSubagent` cannot target an OpenAI model (e.g. Claude Code), dispatch GPT through the codex CLI as a shell task: `codex exec --sandbox read-only "<self-contained prompt>"`. It uses whatever model `$CODEX_HOME/config.toml` (by default `~/.codex/config.toml`) sets as the default, so read that file rather than assuming a variant. Override per run with `-c model=...`. Record the variant you actually got wherever model evidence is kept, because the configured default moves. Gotchas, each learned from a real run:

- A deep run outlives a 10-minute foreground shell timeout. Run it in the background and read the output file on completion.
- Do not pipe large diffs or files through stdin. The read-only sandbox lets codex read the repo itself: name a commit hash or file paths and let it run its own git and reads - it also finds surrounding context stdin would have hidden.
- From a background or non-interactive shell, close stdin explicitly (`< /dev/null`). With stdin open, `codex exec` prints "Reading additional input from stdin..." and blocks until EOF, which in a background shell never arrives.
- Output is a session transcript with the answer at the tail. Demand a strict deliverable format in the prompt ("ranked findings, file:line, concrete failure scenario") so the tail is directly usable without parsing the transcript.
- Keep skill trigger phrases out of the prompt. Codex loads skills from the same directory and matches them on their description line. A prompt that opened "this is a pre-commit review" made it `cat` that skill file. It then spent the whole turn following the instructions for dispatching a review instead of doing one. Say what you want done. Never say what the job is called. Add a line telling it to load no skill file.
- Check the output, never the exit code. A run that did no work still exits 0. Confirm that the tail holds the deliverable you asked for before you read anything into it.
- Background it once, not twice. A trailing `&` inside a harness call that already backgrounds the command detaches the run. The harness then reports the job complete while codex is still writing. Let the harness own the job, or wait on a marker the run writes itself (`; echo "EXIT:$?" >> out`).
- Calibration: one deep code review is roughly 200k tokens and 25 minutes. Right for a risky-feature gate or a genuinely hard design question; wrong for routine checks.

### 3. Construct the Consult Prompt

Write a self-contained prompt for the panel; one member gets a variant (see the frame-attacker below). Include:

- **Problem statement**: What is being asked. Be precise.
- **Context, and the repository itself**: Give the consult the source, not only your summary of it. Quote your conclusions inline, but list the entry-point files as absolute paths and require every load-bearing claim to be checked against the code rather than accepted from the prompt. The fact that decides a hard problem is usually the one the summary dropped: an invariant stated in a comment, a helper that already exists, a wiring detail.
- **What's been tried**: For each dead end, what you varied, what you held fixed, and the constraint its failure established - not only the verdict. The explanation attached to a dead end is a separate claim from the observation and can be wrong on its own, and a wrong one quietly forbids every later idea that shares the innocent variable.
- **What you want back**: Concrete deliverable. A diagnosis? A design proposal? Multiple candidate solutions with tradeoffs? Pseudocode? A direct answer?
- **Scope limits**: Read-only vs. write. Almost always read-only for a consult. Tell the subagent explicitly: "Do not edit files. Return your analysis as text."

**When the consult cannot reach the source** (an external web chat), the prompt is all it has: mark every load-bearing claim measured or reasoned with its file:line, and say in as many words that the requirement list is attackable - "if a premise here is wrong, saying so is the most valuable thing you can return." Its answer is still frame-bound by construction, so treat its silence about source-level constraints as unknown rather than as clearance, say so in the synthesis, and re-verify its premises against the code before anything is built on it.

Subagents should reason independently. Do not tell them what other models are being asked or what answer is expected. Each one should form its view from the prompt alone.

**The frame-attacker.** On a design or a stuck problem, as opposed to a lookup, give one member a different brief: derive the requirement and invariant list independently from the source and the product's own behaviour, then answer which stated requirement could move, and what is missing from the list. Tell it outright that breaking the question's frame is the most valuable thing it can return. The rest answer inside the frame as usual. A question can carry the losing assumption inside it, and a panel answering wholly within one will agree its way into the wrong design, so divergence here is signal rather than noise.

Deduplicate candidate options before dispatching. If two labeled options are functionally equivalent (e.g. "F = A"), collapse them. Redundant options waste panel attention and clutter the synthesis.

### 4. Launch Consult Subagents

For each requested model, call `runSubagent` with:

- `description`: "Consult (`<model-short-name>`)" - use distinct descriptions per model
- `model`: The model string from the table in Step 2 (omit for current session model)
- `agentName`: Use `Explore` if the agent is available and the consult is purely read-only research. Otherwise omit to use the default agent. Pick whichever fits the task better.
- `prompt`: The consult prompt from Step 3 - the frame-attacker variant for one member on a design consult

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

**What the frame-attacker moved**: Which stated requirement it says can give, and what it found missing. This does not fit the divergence table and is lost if you force it in. If it endorses the frame, report that too - a weak all-clear, not a null result.

**Your read**: A short take from the main session on which direction looks strongest and why, or which questions remain open. Be willing to disagree with the panel if you have reason to.

Then **ask the user** what they want next:
- Run the falsification round (step 6) on the leading proposal - the default for anything you intend to build
- Proceed with one of the proposed approaches
- Drill into a specific model's reasoning
- Run a follow-up consult with a refined prompt
- Move on with your own synthesis

### 6. The Falsification Round (design consults)

A proposal that has not survived a kill attempt is a hypothesis, not a recommendation. Before adopting the panel's leading proposal, dispatch a fresh consult - fresh context, repo access - whose only brief is to kill it:

- Name every consumer of every value the proposal changes, and show each one was checked. "Downstream reads were checked" is the sentence that hides the one that was not.
- Hunt the invariant that breaks it, in the code and in the product's behaviour.
- State in the prompt that a bare "looks good" is a failed consult. The value is in what it breaks.

When a proposal dies, extract the invariant that killed it and add it to the prompt's constraint ledger. The kill is design input: the next round starts from the updated ledger rather than from scratch. Two or three rounds of propose, falsify, revise beat any single answer, including one from a stronger model.

**A revised proposal is a new proposal.** The round that killed its predecessor does not cover it. Attacking version N and then building version N+1 is how an endorsed design reaches production unexamined.

The loop costs real tokens and wall time. Spend it where a wrong design ships expensively; a lookup does not need it.

### 7. Context Management

The subagent responses can be large. After synthesizing, do not re-quote the full raw responses in later turns. Refer back to specific points by model name and topic if the user asks follow-up questions. If a follow-up needs the full response, re-fetch by running a new targeted consult rather than dragging the original text forward.

### 8. Spot-Check the Panel

Models can make systematic errors, especially when the prompt subtly leads them. Before treating a convergent recommendation as truth:

- If the panel cites specific code, line numbers, function names, or library APIs, verify at least one citation against the actual file or docs. Hallucinated specifics are the most common multi-model failure mode.
- If all models agree on something surprising, ask: did the prompt frame the answer? Convergence on a leading question is noise, not signal.
- Verifying a citation is the minimum. For a proposal you intend to build, the falsification round is the real check: agreement between models tests the frame they shared, not the proposal.
