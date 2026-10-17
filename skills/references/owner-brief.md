# Owner requirements in worker briefs

Every implementer, reviewer, consultant, and falsification worker receives the owner's relevant messages and their source references. Include later corrections and restrictions. Copy exact excerpts when their wording defines scope. Label summaries as summaries and keep the source available.

Separate owner requirements, explicitly approved agent proposals, and unapproved agent inferences. Quote the owner's approval and identify the proposal it approves. A plan or reviewer suggestion alone grants no authority.

Name the authorized outcome, exclusions, repository baseline, owned paths, checks, return path, and limits. Preserve these requirements when refreshing or narrowing a brief.

When a brief tells a worker how to launch a session or run a tool, write the full command line. Use only flags that the installed tool's `--help` lists. A flag copied from a sibling tool, such as `arc start --agent-run` given to `dispatch.py`, stops the worker until the owner answers.

Before proposing or adding a visible control, dialog, panel, or interaction, compare it with the owner's request. Reviewers perform the same comparison for every added surface. If an inferred requirement needs an unauthorized visible addition, pause that addition and request approval. Continue independent approved work.

## Session labels and links

For agent launches and fresh-session handoffs in Arc, inherit the launching session's current labels unless the author specifies another set.
Read the parent labels before launch and confirm the child's saved labels afterward through the scoped Arc CLI.
Use the installed `arc-sessions` skill for window selection, label changes and delivery outcomes.
Add inherited labels without removing unrelated labels unless the author requests an exact replacement.
Keep labels separate from the session name and reader or agent ownership.
Report launched sessions with named Arc session links when their destinations are verified in the invoking window.
Follow the installed skill's link syntax and identity checks.
Otherwise report the name, complete session ID and scope limitation as plain text.
Use the complete raw session ID from accepted launch metadata or scoped listing, without a provider prefix.
If Arc cannot confirm or save labels, keep the existing worker.
Record the pending metadata and report the limitation alongside the verified link or plain-text session identity.
