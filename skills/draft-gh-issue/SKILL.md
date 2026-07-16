---
name: draft-gh-issue
description: "Draft a GitHub issue (title + body) matching the target repo's issue template and conventions. Draft-only by default; creates the issue ONLY when explicitly told to open/create/file/submit. Use when: draft an issue, write a bug report, file an issue, open an issue, feature request, prep an issue for repo X."
argument-hint: "[optional: target 'owner/repo', or 'open'/'create' to actually file it]"
---

# Draft GitHub Issue

Produce a GitHub issue title and body that matches the target repository's issue template and house style.

## Hard Rules

Behavior:
- Draft-only by default. Do NOT run `gh issue create` or any mutating command unless the user explicitly says open/create/file/submit this issue.
- When explicitly told to create, use `gh issue create --repo OWNER/REPO --title "..." [--label ...] --body-file PATH`. Use `--body-file` (never `--body`) so backticks survive the shell. Confirm the target repo and any labels first, then return the created issue URL.
- Read-only commands are always fine: `gh issue view/list`, `gh repo view`, `gh search`, `git log/diff`.

Formatting invariants (these are the whole point of this skill; get them right every time):
- **No YAML frontmatter in the body.** A repo issue template opens with a `--- name/about/labels ---` block. That is form configuration, not content. Use the template's section *headings* only; never paste its frontmatter into the issue.
- **No hard line-wrapping.** GitHub renders a single newline inside a paragraph as a line break, so editor-style wrapping at ~80/90 columns produces ragged mid-sentence breaks. Write each paragraph as ONE physical line. Only code fences and list items keep their own line structure. (Repo markdown files are different: wrapping there is fine.)
- **No em dashes.** Per the deslop skill and the user's standing rule. Use commas, colons, parentheses, or "to" for ranges.
- **Cross-repo references need a resolvable form.** A bare `#123` only links within the repo the issue lives in. When you reference an issue/PR in a *different* repo (common: the issue is filed in repo A about work in repo B), write `owner/repo#123` or a full `https://github.com/owner/repo/(issues|pull)/123` URL. Verify the number and type (issue vs pull) before linking. Link direction respects visibility: a private repo may link public ones, but anything in a public repo must never reference a private repo (no `owner/repo#N`, no URLs); describe private context generically and cross-link from the private side.
- **Concise, humane prose.** Apply the `deslop` skill's prevention rules and prose tell-list (no "simply/robust/seamless/leverage/comprehensive", no bold-lead-in bullet walls where prose belongs, no "## Overview" boilerplate). Say the thing plainly. Cut qualifier soup.
- **Written for a scanning reader, not a wall of text.** Keep paragraphs to one to three sentences and lead each section with its conclusion. Facts that enumerate (environments, impact per environment, repro conditions) go in bullets, with a bold lead label (**Root cause:**, **Impact:**) when it names the bullet's subject. Exact error messages and commands go in code fences. Cut detail another engineer or agent can recover by reading the code (call-chain walkthroughs, file-by-file mechanism); keep what the investigation uniquely established (trigger conditions, versions, root-cause commit, judgment calls). Dense-but-accurate paragraphs are still a failure: concise means scannable, not merely information-dense.

Voice (distilled from how the user rewrites AI-drafted issues):
- Open with a short first-person lede giving context ("Found while working on...") and honest status ("haven't finished the fix, still thinking about what to do").
- Problem-focused: no proposed-fix or solution section unless the user asks for one. Keep root-cause file:line references.
- Bug/regression issues: Impact gets its own section, split per environment (dev/CI vs production), saying plainly whether it is real misbehavior or a safety check firing as designed. When the root cause is a known change, show the exact causal diff hunk and the call chain that arms it, naming the causing PR (example: rstudio/package-manager#18993).
- Guardrails/follow-ups, when asked for: group as bundled with the fix, upstream, and considered-and-rejected, one-line reason each; a rejection with its reason is as informative as an acceptance.
- Suggest a screenshot when the problem is visible in a UI. An @-mention delegating confirmation to a colleague is welcome.
- Apply the `rewrite` skill's Tier 1 banned vocabulary.

## Procedure

### 1. Gather context

- Identify the target repo. If the issue is about a running system or a bug you investigated, the repo is usually where the *fix* lands, which may differ from your current working dir. Confirm with the user if ambiguous.
- Collect the concrete evidence the issue needs: exact repro commands, real command output/error text, affected versions, file:line root-cause references. Prefer verified output over description. Do not fabricate repro runs or output.

### 2. Detect the repo's issue conventions

Check, in parallel where possible:
- Issue templates: files under `.github/ISSUE_TEMPLATE/` (and `.github/ISSUE_TEMPLATE.md`). If several exist, pick the one matching the change (bug report for a bug, etc.); if unclear, ask. Use its section headings as the body skeleton.
- Recent issues for tone/length (only if `gh` is authenticated): `gh issue list --repo OWNER/REPO --state all --limit 5 --json number,title,body`.
- `CONTRIBUTING` for any issue requirements.

### 3. Draft the title

- Plain, specific, no trailing period. Describe the observable problem, not the root cause, unless the repo's issues favor otherwise.
- Front-load the subsystem/symptom so it's scannable in a list.

### 4. Draft the body

- Fill the template's sections in order. Keep a heading even if short; mark a genuinely empty section `N/A`.
- If no template, use a minimal structure: a one-paragraph problem statement, Steps to reproduce (concise bash block), Expected vs actual, and any root-cause/fix notes if known.
- Put repro steps in a single fenced `bash` block; keep it minimal (no decorative variables). Show real output as a separate fenced block or inline comments.
- Apply every formatting invariant above. Before finishing, self-check: grep the draft for `—` (em dash), for a leaked `---`/`name:`/`about:` frontmatter block at the top, for bare `#\d` cross-repo refs, for private-repo references when the target repo is public, and for wrapped paragraphs (a prose line that ends without sentence-final punctuation and continues on the next line).

### 5. Output

- Default: write the draft to a file only if the user asked; otherwise reply inline with the title in a fenced `title` block and the body in a fenced `markdown` block. Add a one-line footer of assumptions (target repo, template used, any guessed labels).
- If explicitly told to create: follow the create rule above (confirm, `--body-file`, return URL).

If intent or target repo is unclear, ask one focused question before drafting.
