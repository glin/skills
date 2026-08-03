# Audit rubric

## Contents

- Sources
- Skill rules: frontmatter
- Skill rules: body and structure
- Skill rules: content
- CLAUDE.md rules
- What the script deliberately does not check

## Sources

- Anthropic, [Skill authoring best practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices)
- Anthropic, The Complete Guide to Building Skills for Claude
- The `skill-creator` skill in `anthropics/skills`
- Community reporting on CLAUDE.md sizing and progressive disclosure

Where the sources disagree, the platform docs win, except on naming: the docs prefer
gerund names (`processing-pdfs`), and also warn against inconsistent patterns inside
one collection. A collection already built on verb-object names stays that way.

## Skill rules: frontmatter

| Rule | Threshold | Level |
|---|---|---|
| `SKILL.md` exists, spelled exactly | required | error |
| Directory name is kebab-case | `^[a-z0-9]+(-[a-z0-9]+)*$` | error |
| `name` matches the directory | exact | error |
| `name` length | 64 chars | error |
| `name` character set | lowercase, digits, hyphens | error |
| `name` reserved words | no "claude", no "anthropic" | error |
| `description` present | non-empty | error |
| `description` length | 1024 chars | error |
| `description` contains no angle brackets | none | error |
| `description` written in third person | no "I", no "You" | warn |
| `description` says when to use, not only what | a trigger phrase | warn |
| `description` carries enough trigger terms | 80 chars | warn |
| Frontmatter fields are recognised | `name`, `description`, `argument-hint`, `allowed-tools`, `disable-model-invocation`, `license`, `compatibility`, `metadata`, `model` | warn |

The description is the only part loaded before the skill triggers, so it is the
whole discovery surface. It should carry the words a user would actually type. An
exclusion clause naming the neighbouring skill prevents the common failure where two
skills both look right.

## Skill rules: body and structure

| Rule | Threshold | Level |
|---|---|---|
| Body length | 500 lines | error over, warn at 400 |
| Body word count | 5000 words | warn |
| Links from SKILL.md resolve | all | error |
| Reference files stay one hop from SKILL.md | no onward links | warn |
| Reference files carry a contents list | over 100 lines | warn |
| Bundled files are named in SKILL.md | all | info |
| No `README.md` inside the skill folder | none | warn |

The 500-line limit is the documented performance threshold. Below it, splitting a
file buys nothing and costs a hop. Above it, split by domain so an unrelated task
never loads the wrong half.

One hop matters because Claude previews rather than fully reads files reached
through a chain, so a fact two hops out may be read partially or not at all.

## Skill rules: content

| Rule | Detection | Level |
|---|---|---|
| Same block of text in two skills | 25+ identical words | warn |
| Currency depends on someone remembering | "keep them identical", "bump quarterly", "last reviewed" | warn |
| Words capitalised for emphasis | capitalised word that also appears lower case in the same file | warn |
| Relative paths use forward slashes | backslash in a non-absolute, non-variable path | warn |
| Time-sensitive phrasing | "as of", "currently", "recently", a bare date | info |

Shouting is detected by comparing cases within one file. An acronym never appears in
lower case, so it passes without an allowlist. The objection to capitals is not
style: a bare imperative gives the model nothing to generalise from, so it cannot
apply the rule to a case the skill did not spell out. State the rule, then why.

A duplicated block is worse than a long one. Two copies means two places to update
and one that gets forgotten, and the forgotten one keeps being read as current.

## CLAUDE.md rules

| Rule | Detection | Level |
|---|---|---|
| Stale reference | a named file or directory that does not exist in the repo | warn |
| Prose restating an executable guard | a symbol the repo defines, named in the prose | info |
| Parenthetical carrying a case study | over 25 words inside parentheses | warn |
| Dated provenance | "confirmed 2026-07-02", "fixed 2026-06-07" | info |
| A pointer next to a retelling | bullet over 45 words that also cites a file | warn |
| Near-duplicate bullets | 50% shared 6-word runs | warn |
| Pasted code where a reference would do | code block over 12 lines | info |
| Per-section size and share | reported, not gated | stat |
| Size and instruction count | reported, not gated | stat |

The stale-reference and guard checks need `--root`. Both follow junctions and
symlinks, since a linked-in package is a common home for referenced docs, and both
skip whatever the repo's own ignore rules exclude, so build output and caches never
reach the index. A reference counts as present if it resolves as written, resolves
relative to the instruction file, or matches a file anywhere in the repo by name, so
a filename cited without its directory is not reported. Only tokens ending in a
trailing slash or in a file extension are considered, either a common extension or
one the repo itself uses, which keeps property accesses and dotted identifiers out
of the results.

The guard check is the highest-value mechanical check because acting on it needs no
taste: if a rule names a validator or test the build already runs, the rule can be
one line pointing at it. It reports at info because a name match is not proof the
prose is redundant, only that it is worth reading for redundancy.

Per-section share is what step 0 of the procedure runs on. It exists because audit
attention lands on whatever is top of mind rather than on whatever is large, and the
two are rarely the same file region.

Size is reported rather than gated because word count cannot tell a valuable rule
from a wasteful one. It stays in the report because it is the feedback the writer
never gets: a bloated file arrives pre-loaded and is never blamed for the slowness
or the miss it causes.

The instruction count is the number to watch. Models follow on the order of 150 to
200 instructions reliably, and the harness system prompt already spends part of that
budget, so a file with 190 bullets and paragraphs is at the ceiling before the user
has said anything.

A pointer is the fix, not the defect. `See docs/x.md` replacing a story is
progressive disclosure working. The defect is the story plus the pointer, which pays
for the evidence twice. That is why the rule keys on length rather than on the
presence of a path.

Dated provenance reports at info rather than warn. A date on a rule about external
behaviour is a claim about when that behaviour was last checked, which is worth
having in a file that is deliberately a ledger. Treat each one as a prompt to
re-confirm the rule, not as text to delete.

## What the script deliberately does not check

- Whether a rule earns its tokens. This needs an eval, and building one is hard.
- Whether two rules should merge.
- Whether the phrasing is clear.
- Whether an example is doing real work.
- Whether the file is still true about the world.
- Whether a field or convention the file defines still has a reader. Answering it needs
  the artifact directory and the pattern the field writes, which vary per repository.
  Step 3 of the procedure does it with two greps.

The last one is the highest-value check in the whole audit and it is entirely a
reading pass. Everything a skill asserts about a model name, a tool name, a path, or
a harness behaviour should be confirmed against the current state before the audit
closes.
