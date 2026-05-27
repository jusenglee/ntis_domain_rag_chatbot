# Contracts And Rules

This document lists the contracts that must stay stable while the agent evolves.

## Search Contract

Defined in `apps/pipeline/contracts.py`.

`SearchTask` is the only contract that `SearchAgent` executes. It carries:

- `action`: `list`, `detail`, `stats`, `topic`, or `download`.
- `target`: `project`, `perf`, `people`, `org`, or `support`.
- `axis`: optional identifier axis.
- `subject`: structured person or organization anchor.
- `identifiers`: structured identifier bundle.
- `filters`: typed domain filters.
- `strategy`: exact lookup, subject anchor, hybrid search, or aggregate.
- `collections`: explicit Qdrant collections.
- `retrieval_query`: natural language helper text, not authority.

Validators enforce:

- `detail` forces `limit=1` and `display_limit=1`.
- `exact_lookup` requires identifiers.
- `subject_anchor` requires a subject.
- `aggregate` requires `aggregate_by`.
- `collections` must not be empty.

## Identifier Rules

Do not mix identifier axes.

| Axis | Meaning |
| --- | --- |
| `pjt_id` | canonical internal project id |
| `pjt_no` | project number, not equivalent to `pjt_id` |
| `rst_id` | research output id |
| `person_no` | participant person id |
| `org_id` | organization id |

Priority for exact lookup is:

```text
pjt_id > rst_id > pjt_no > person_no > org_id
```

## Filter Rules

`FilterBundle` owns domain filters:

- `year_from`, `year_to`
- `lead_org_name`
- `participant_org_name`
- `participant_person_name`
- `perf_type`
- `domain_keywords`
- `exclude_org_name`
- `exclude_perf_type`
- `exclude_person_name`

Tool args must map to these names. `coparticipants` may be accepted as a legacy
alias, but `participant_person_name` is the canonical tool/search field.

## Evidence Rules

`CanonicalEvidence` is the only answer-facing evidence unit.

It carries:

- stable identity and source type,
- tag and structured ids,
- title and summary,
- facts, roles, child entities,
- provenance,
- retrieval score and snapshot rank.

Raw Qdrant payloads must not be sent directly to answer prompts or API
responses.

## Tool Contract

Defined in `apps/pipeline/tools/contracts.py`.

- `ToolSpec` describes name, short description, input schema, output schema,
  cost hint, and preconditions.
- `ToolCall` is the Planner's requested call.
- `Observation` is the executor result.
- `ToolExecutor` catches handler exceptions and returns
  `Observation(status="error")`.

The executor does not currently enforce `ToolSpec.preconditions`
programmatically. Those preconditions are prompt guidance plus tool-handler
responsibility.

## Planner Contract

Defined in `apps/pipeline/agents/planner_contracts.py`.

`PlannerStep.action` is one of:

- `call_tool`
- `answer`
- `clarify`

For `call_tool`, `tool` must be populated.
For `clarify`, `clarification_question` must be populated.

`PlanState` accumulates decisions and observations for one user turn and
terminates on `answer`, `clarify`, `max_steps`, `duplicate_call`, or `error`.

## Answer Contract

Public answer shape is `AnswerArtifact` from
`apps/api/streaming/contracts.py`.

Supported `answer_kind` values are:

- `llm_streamed`
- `llm_collected`
- `detail_cache`
- `detail_profile`
- `clarification`
- `no_result`
- `direct_answer`
- `error`

`response.unsupported` currently flows as a direct final text with artifact meta
`kind=agentic_unsupported`; the literal `answer_kind` does not have a separate
`unsupported` value.

## Session Contract

`SessionState` has three logical slots:

- `current_subject`
- `published_manifest`
- `focused_detail`

Current persistence uses `SessionMemory` under key:

```text
pipeline:v1:{conversation_id}:session
```

Important implementation constraint:

`SessionStateAdapter.to_session_memory` compresses the three slots back into
one `current_context` for KV storage. `SubjectQueryContext` can carry a result
manifest, and `DetailAnchorContext` now carries cached detail fields, but the KV
format is still not a fully independent three-slot store.

Any code that claims "lossless three-slot KV persistence" must first update
`apps/pipeline/session_store.py` and the adapter contract.
