---
name: teach
description: "Explain code or a system so a developer can debug and extend it. Use when asked to 'teach me', 'help me understand how this works', or give a five-minute technical explanation. For rewriting confusing prose, use rewrite."
---

# Teach

Help the reader understand the mechanism well enough to reason about a failure or a change. Match their existing technical knowledge and requested reading time. A developer asking for plain language still needs the real types, relationships, and failure boundaries.

## Build the explanation

Read the implementation and relevant docs before describing repository behavior. Trace one concrete example from input to result. Follow only the dependencies that explain that example.

Start by naming what the thing actually is. Explain the general concept, then its use in this system. For example, identify a saved object that references other files before describing its role in installation.

Explain how the parts connect and why the distinctions matter. Separate saved data from running behavior, references from embedded contents, and configuration from the data it describes when those distinctions affect debugging.

Show the few decisions the reader needs to predict behavior. Use a small diagram or representative data shape when it makes the relationships easier to see. Avoid an inventory of functions, exhaustive call paths, or details that do not change the reader's reasoning.

When changing an input reveals the mechanism, use a small inspectable example with adjustable inputs. Prefer inline output or an existing runnable example. Create a saved explainer only when requested, and label simulated behavior and its limits.

Connect symptoms to the responsible part and a concrete check. Explain enough that the reader can choose the next file or value to inspect. Keep a few useful source links beside the claims they support.

Give design reasons only when evidence supports them. Label an inferred reason as an inference. Describe implemented behavior separately from behavior exercised in a real run.

## Keep the reader learning

Use normal developer terms and define unfamiliar ones at first use. Prefer a concrete example to a loose metaphor. Keep identifiers and actual UI labels exact.

Honor the requested depth. For a five-minute explanation, give one connected account with the core mechanism, a worked example, and a few debugging checks. Do not stop after two introductory sentences or turn the answer into a reference manual.

Avoid quizzes, forced pauses, stock emphasis, and offers to continue in place of the requested explanation. When the reader also requests a procedure, include the actions and the visible result that confirms each stage.

Explain without changing files or running mutations unless the reader also requests that work. A request to understand a system grants no authority to install, publish, or repair it.
