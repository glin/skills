# Skill Editing Guidelines

When editing skills in this repo:
- Keep examples generic. Do not use repo-specific details from the session that triggered the edit.
- After applying improvements from a real session, review all additions for hyperspecificity.
- Skills should work across any codebase, not just the one that exposed the issue.
- A change to a rule in `orchestrate`, `delegate` or `dispatch` adds its reason to the repository's `docs/orchestration.md` in the same commit.
- Never line wrap Markdown. One paragraph is one line, and one list item is one line. A wrapped line hides its own text from a search, and an editor wraps for reading anyway.
- Name no skill that lives outside this repository, in a file or in a commit message. Write "a coordinator" or "a reviewer" instead.
