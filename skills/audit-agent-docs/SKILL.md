---
name: audit-agent-docs
description: "Audit the instruction files agents load every session, CLAUDE.md and AGENTS.md and SKILL.md, against Anthropic's skill-authoring guidance and progressive-disclosure rules. Measures where the words actually are, then reports stale references to files that no longer exist, prose restating a guard the repo already runs, blocks duplicated across files, case-study bloat, and invalid frontmatter. Use when: audit my skills, review CLAUDE.md, is my CLAUDE.md too long, my agent instructions are getting bloated, check a skill against best practices, before adding a new skill. For stripping LLM-marker writing out of shipped code and public docs, use deslop instead."
argument-hint: "[paths to skill dirs and/or CLAUDE.md files; blank audits the global skills folder and the repo's CLAUDE.md]"
---

# Audit agent docs

Instruction files drift the same way everywhere. A SKILL.md accumulates detail until the model reads past the part that mattered, and a CLAUDE.md accumulates the story behind each rule until the rules are outnumbered by their evidence. The cost is invisible from inside a session, because it is paid by every later session and never by the one doing the writing.

## Principles

1. **Measure before choosing what to audit.** Attention goes to whichever section is top of mind, which is usually the one already trimmed twice. Get the per-section word counts first and audit where the mass actually is.
2. **Never ask whether a rule is worth its tokens.** The model running the audit is often the model that wrote the rule, and it will say yes. Ask mechanical questions with an external anchor, and require evidence for *keeping* rather than for cutting. Step 3 is the mechanical form of that for anything the file defines.
3. **Run it cold.** A session that just added three rules should not judge them. Run at the start of a session, or from a subagent with no memory of the edits.
4. **Propose, never auto-apply.** The output is a table the owner accepts or rejects line by line.
5. **Put the judgement step in the procedure, not in a rules list.** A procedure anchored on a grep can only process what the grep matched, so a rule about what grep cannot see never runs. Reading is a numbered step below, not a rule to remember.

## Procedure

### 0. Measure

`$SKILL_DIR` is the directory that holds this SKILL.md. The harness gives that location when it loads the skill. Never search the filesystem for it.

```bash
node "$SKILL_DIR/scripts/audit.mjs" claude ./CLAUDE.md
```

Read the `section` lines first and say where the mass is before choosing what to audit. A file whose largest section has never been reviewed is telling you where to go.
Use size to set reading order. Step 2 covers every section.

### 1. Run the mechanical checks

```bash
node "$SKILL_DIR/scripts/audit.mjs" both \
  --skills <skills repository>/skills \
  --claude <the CLAUDE.md> \
  --root <skills repository>
```

**Point `--skills` at the source repo, not `~/.claude/skills`.** That directory also holds plugin-marketplace skills, which differ per machine and belong to nobody working here. A count taken from it cannot be compared against another machine's or acted on, and two people auditing the same skills will report different totals and both be right. Quote plugin-skill findings separately, and say that is what they are.

`--root` enables the two checks that need the repo: whether the files, directories, and symbols the instructions name still exist, and whether a rule restates a guard the repo already executes. Junctions and symlinks are followed, so a doc inside a linked-in package still counts as present. Without `--root` those two are skipped and everything else still runs. A `--root` with no path is an error.

A skill path can be one skill directory or a directory of skills. A directory with its own `SKILL.md` counts as one skill. The summary prints how many skills and CLAUDE.md files it read. A path that does not exist, or a named category that finds nothing to read, is an error. In `both` mode you can leave out one of `--skills` and `--claude`, but not both.

The script exits non-zero on an error, so it also works as a pre-commit gate. [references/rubric.md](references/rubric.md) carries every rule, its threshold, and its source.

### 2. Read the whole file

The script matches shapes. These need a reader, and this step is where most of the real findings come from:

- **For each rule, name the concrete failure that would happen without it.** No nameable failure means flag it. This is the keeping-evidence test from principle 2.
- **Is the example carrying the rule, or is it the incident that prompted the edit?** The rule states the general class. The incident belongs in a test or a linked doc.
- **Does it describe a state that is gone?** The script catches the common phrases. Read for the rest: a "now" that contrasts with a past, or a note about a renamed field or a removed step. Cut the sentence. Git history keeps the earlier state.
- **Would a tool enforce this better?** A rule a linter, type, test, or assertion can check should be that instead. Prose only hopes someone reads it.
- **Does the description trigger?** Read it as the only thing a fresh session sees. Would it fire on the words the author types, and stay quiet on a neighbour's work?
- **Does it contradict a sibling?** Two files covering adjacent ground must agree on the boundary and say so.
- **Does an always-loaded file repeat topic guidance or define a glossary, term table, or prohibited-term list?** Replace these blocks with a trigger and reference. Flag repeated audience exceptions too. Keep each policy and vocabulary list in one source, however short the block is, to prevent drift.

### 3. Count the readers before proposing a cut

An instruction file defines things that leave traces elsewhere: a template field, an allowed status value, an ID scheme, a naming convention, a directory. Before a cut for any of those lands in the table, find out who reads it.

```bash
# who writes it, across the artifacts the file governs
grep -rlE '^- <field>:' <artifact dir> | wc -l
# who cites it, from outside that family
git grep -n '<the token it produces>' -- ':!<artifact dir>'
# who parses it
git grep -n '<field>' -- '*.py' '*.mjs' '*.ts' '*.sh'
```

Three outcomes, hardest to cut first:

- **A script or test parses it.** Cutting or rewording breaks a tool, and usually silently, because a parser that matches nothing reports an empty result rather than an error. Keep it, and add the check that fails when the two stop agreeing.
- **Something outside the artifact family cites it.** It has a reader. Keep it.
- **Only the artifacts themselves carry it.** It is a habit, not a contract. Now ask whether another source already states the same fact. If one does, that is the cut, and the duplicate is the thing to remove.

Frequency decides nothing on its own. A field written into every artifact and read by nothing is the strongest cut in the file. One written rarely but cited from elsewhere is not a cut at all.

When a cut is approved, existing artifacts keep the old shape. Say so in the file, and name the next edit of that artifact as the moment to normalise it. A sweep across settled records costs review churn and gains nothing.

### 4. Group and propose

Sort every candidate into cut, compress, move to a subdoc, replace with a pointer to an existing guard, or keep. Emit one table: section, current words, proposed words, action, and a one-line reason.

Group mechanical findings by rule rather than by file, so a rule broken fifteen times reads as one decision instead of fifteen.

Separate fixes needing no judgement (invalid frontmatter, dead links, duplicated blocks, stale paths) from those needing the owner (what a rule should say, whether an example earns its place, which of two overlapping files owns a boundary). Apply the first set on approval; propose the second and wait.

### 5. Re-measure

Report the delta against step 0. That number is the feedback the writing model never otherwise receives.

## The trap to avoid

Do not restructure and trim in the same pass. Trimming in place lowers the count without changing why it grew, so the next pass starts over. Move the evidence out first, then measure.

## Changing the script

`scripts/audit.mjs` has a test harness. Run it after any edit to a check:

```bash
node "$SKILL_DIR/scripts/test.mjs"
```

It audits the paired fixtures in `fixtures/skills/` and `fixtures/claude/` and exits non-zero on a failure, so it also serves as a pre-commit gate.

Every case is two fixtures that differ only in the property under test: one that must trip the rule, one that must not. That pairing is what makes a passing run evidence. A green fixture alone proves nothing, because a check that never fires passes it too, which is why each pair also asserts that both halves were read.

Adding a check means adding a pair. Prove it by breaking the check both ways: remove it and the red half must fall silent, widen it and the green half must go red.
