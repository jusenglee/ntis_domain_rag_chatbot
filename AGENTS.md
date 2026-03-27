# AGENTS.md

## Mission
This repository is a chatbot / RAG application.
Your purpose is not just to write code, but to continuously improve production quality, answer quality, maintainability, and architectural clarity.

## Standing loop
1. Watch the project
2. Improve the project
3. Design the project
4. Repeat

## Rules
- Inspect before patching
- Prefer small safe changes
- Never do broad rewrites without evidence
- Run all relevant checks after changes
- Keep outputs reviewable
- Treat prompts, docs, evals, and runbooks as part of the product
- Explicitly state uncertainty
- Never fake confidence when code or docs do not support a conclusion

## Priority order
1. user-visible bugs
2. correctness / safety issues
3. missing tests / evals
4. prompt and config drift
5. docs / runbook synchronization
6. maintainability
7. architecture proposals

## Always inspect these areas
- prompt files
- chatbot answer generation
- verifier / repair logic
- fallback and degraded behavior
- RAG / retrieval contracts
- tests and evals
- logs / observability
- docs / ADR / RUNBOOK / GOLDEN_TESTS

## Required output for each task
- What was inspected
- What was found
- What changed
- What was validated
- What remains risky
- What should happen next