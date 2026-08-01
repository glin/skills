---
name: audit-agent-docs
description: "Audit the instruction files agents load every session, CLAUDE.md and AGENTS.md and SKILL.md, against Anthropic's skill-authoring guidance and progressive-disclosure rules. Measures where the words actually are, then reports stale references to files that no longer exist, prose restating a guard the repo already runs, blocks duplicated across files, case-study bloat, and invalid frontmatter. Use when: audit my skills, review CLAUDE.md, is my CLAUDE.md too long, my agent instructions are getting bloated, check a skill against best practices, before adding a new skill. For stripping LLM-marker writing out of shipped code and public docs, use deslop instead."
argument-hint: "[paths to skill dirs and/or CLAUDE.md files; blank audits the global skills folder and the repo's CLAUDE.md]"
---

# Audit agent docs

Instruction files drift the same way everywhere. A SKILL.md accumulates detail until
the model reads past the part that mattered, and a CLAUDE.md accumulates the story
behind each rule until the rules are outnumbered by their evidence. The cost is
invisible from inside a session, because it is paid by every later session and never
by the one doing the writing.

## Principles

1. **Measure before choosing what to audit.** Attention goes to whichever section is
   top of mind, which is usually the one already trimmed twice. Get the per-section
   word counts first and audit where the mass actually is.
2. **Never ask whether a rule is worth its tokens.** The model running the audit is
   often the model that wrote the rule, and it will say yes. Ask mechanical questions
   with an external anchor, and require evidence for *keeping* rather than for cutting.
3. **Run it cold.** A session that just added three rules should not judge them. Run
   at the start of a session, or from a subagent with no memory of the edits.
4. **Propose, never auto-apply.** The output is a table the owner accepts or rejects
   line by line.
5. **Put the judgement step in the procedure, not in a rules list.** A procedure
   anchored on a grep can only process what the grep matched, so a rule about what
   grep cannot see never runs. Reading is a numbered step below, not a rule to
   remember.

## Procedure

### 0. Measure

```bash
node scripts/audit.mjs claude ./CLAUDE.md
```

Read the `section` lines first and say where the mass is before choosing what to
audit. A file whose largest section has never been reviewed is telling you where to go.

### 1. Run the mechanical checks

```bash
node scripts/audit.mjs both \
  --skills ~/.claude/skills \
  --claude ./CLAUDE.md \
  --root .
```

`--root` enables the two checks that need the repo: whether the files, directories,
and symbols the instructions name still exist, and whether a rule restates a guard
the repo already executes. Junctions and symlinks are followed, so a doc inside a
linked-in package still counts as present. Without `--root` those two are skipped and
everything else still runs.

The script exits non-zero on an error, so it also works as a pre-commit gate.
[references/rubric.md](references/rubric.md) carries every rule, its threshold, and
its source.

### 2. Read the whole file

The script matches shapes. These need a reader, and this step is where most of the
real findings come from:

- **For each rule, name the concrete failure that would happen without it.** No
  nameable failure means flag it. This is the keeping-evidence test from principle 2.
- **Is the example carrying the rule, or is it the incident that prompted the edit?**
  The rule states the general class. The incident belongs in a test or a linked doc.
- **Would a tool enforce this better?** A rule a linter, type, test, or assertion can
  check should be that instead. Prose only hopes someone reads it.
- **Does the description trigger?** Read it as the only thing a fresh session sees.
  Would it fire on the words the user types, and stay quiet on a neighbour's work?
- **Does it contradict a sibling?** Two files covering adjacent ground must agree on
  the boundary and say so.

### 3. Group and propose

Sort every candidate into cut, compress, move to a subdoc, replace with a pointer to
an existing guard, or keep. Emit one table: section, current words, proposed words,
action, and a one-line reason.

Group mechanical findings by rule rather than by file, so a rule broken fifteen times
reads as one decision instead of fifteen.

Separate fixes needing no judgement (invalid frontmatter, dead links, duplicated
blocks, stale paths) from those needing the owner (what a rule should say, whether an
example earns its place, which of two overlapping files owns a boundary). Apply the
first set on approval; propose the second and wait.

### 4. Re-measure

Report the delta against step 0. That number is the feedback the writing model never
otherwise receives.

## The trap to avoid

Do not restructure and trim in the same pass. Trimming in place lowers the count
without changing why it grew, so the next pass starts over. Move the evidence out
first, then measure.
