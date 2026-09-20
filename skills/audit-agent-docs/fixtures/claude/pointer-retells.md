## Session and context budget

- Read `~/.claude/docs/llm-cost-and-caching.md` for the cache-pricing model, how to measure real spend from local transcripts, and the evidence behind the compact-vs-fresh-session call above: cold-cache resumes cost a fraction of a percent of spend, so pick between compact and fresh on whether the next task shares context, not on cache-avoidance.
