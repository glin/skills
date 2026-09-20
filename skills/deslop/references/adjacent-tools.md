# Tools that do a nearby job, and what deslop takes from them

Read this before adopting another slop filter, or before widening a rule because another tool has it. Each entry says what the tool is, where it overlaps, and what was taken.

## anti-slop

`github.com/miqdadbadjuber/anti-slop`. Looked at 2026-08-29.

A skill package plus a pasteable prompt file. It ships 38 numbered rules in three tiers, six sub-skills, and a mandatory "Delivery Gate": a PASS or FAIL checklist the agent prints before it ships.

**It is a visual-design filter, not a prose filter.** The rules cover gradients, glassmorphism, glow, border radius, capsule badges, icon choice, illustration sources, colour palette size, WCAG contrast, mobile tap targets, empty and loading states, keyboard focus, and three dials for energy, rhythm and motion. Three of the 38 touch writing: no em dash, no generic call to action, and a buzzword list.

**The overlap is one word.** Its buzzword list is "AI Powered, Next Generation, Revolutionary, Seamless, Cutting Edge, Intelligent, Ultimate, Powerful, Effortless". Only `seamless` is in `prose-tell`. The rest are marketing copy for a landing page, and a repository that ships an inference API has legitimate uses for `powerful` and `intelligent`. They are not added.

**Its gate is the pattern this collection distrusts.** The Delivery Gate is the model answering 50 questions about its own output and printing PASS. A number produced by the mechanism that is supposed to enforce the thing being measured says nothing. `scan.mjs` exists so that a hit is a match against a pattern and not a claim, and so that a clean run is evidence about the patterns rather than about the file.

**What reading it was worth.** Comparing its rule list against this one found that `prose-tell` was missing `simply`, which rule 5 has always named, and that rule 1 named `probe` with no pattern behind it. Both are fixed. The defect was in this skill, and the other tool found it by being read beside it.

## iso-24495

`github.com/GaZmagik/iso-24495`. Looked at 2026-08-29.

Seven skills plus an output style, packaged for Claude Code and Codex, built on the plain-language principles behind ISO 24495. Its own README says the published parts of the standard are proprietary and were not consulted directly.

**Its writing caps are looser than ASD-STE100.** Its output style stops a sentence at 30 words and asks for a mean at or under 20. ASD-STE100 stops a descriptive sentence at 25 and an instruction at 20. Anyone already writing to the ASD-STE100 limits would be relaxing them by adopting it.

**Different job.** Its subject is document structure: relevance, findability, layered detail, opening blocks. deslop's subject is the marker vocabulary that says a model wrote the line. Neither replaces the other, and nothing was taken.
