# Evidence Prompt Packing

이 문서는 evidence packing 상세 메모다. 현행 검증 기준은 `03_운영과_환경.md`, `04_회귀기준과_점검.md`를 따른다.

## Owner Map
- raw payload canonical memory: `apps/conversation/raw_payload_store.py`
- fact-first follow-up resolution: `apps/conversation/fact_followup_resolver.py`
- score gate: `apps/evidence/context_score_gate.py`
- derived facts: `apps/evidence/derived_facts_builder.py`
- prompt envelope: `apps/evidence/prompt_evidence_envelope.py`
- pack / re-pack: `apps/evidence/context_packer.py`
- compression: `apps/evidence/context_compression_service.py`
- integrity / lineage: `apps/evidence/evidence_integrity.py`, `apps/evidence/evidence_lineage.py`
- tokenizer owner: `apps/platform/solar_tokenizer_adapter.py`

## Final Contract
- search projection is minimal and is not canonical truth.
- canonical truth for follow-up is compressed raw payload memory.
- model-facing prompt evidence is a serialized JSON array string.
- internal source of truth is `prompt_units`.
- `context = json.dumps(prompt_units, ensure_ascii=False, separators=(",", ":"))`
- `used_tokens` is measured from serialized `context`.

## Envelope Shape
- `identity`
- `stable_base`
- `facts`
- `query_bonus`
- `previews`
- `compression`

Rules:
- `identity` is immutable.
- `stable_base` is always retained.
- `query_bonus` is additive and query-sensitive.
- `prtcp_mp` / `prtcp_org` raw arrays never enter prompt units.
- previews are role-aware, not raw truncation dumps.

## Fact Layers
- eager facts:
  - `participant_count`
  - `participant_org_count`
  - `lead_researcher_names`
  - `people_preview`
  - `org_preview`
- lazy facts:
  - `role_histogram`
  - `affiliation_topk`
  - `matched_people_subset`
  - `matched_org_subset`

## Budget and Verify Policy
- evidence-only prompt budget: `RAG_EVIDENCE_TOKEN_BUDGET=20000`
- production token counting uses Solar tokenizer first.
- exact verify runs at:
  - near-boundary provisional insert
  - final emit
  - compressed re-pack
- provisional packing does not stop on overflow.
- overflow candidates move to queue and later shorter units are still considered.

## Compression Policy
- queue max: `RAG_OVERFLOW_QUEUE_SIZE`
- compressed docs max: `RAG_CONTEXT_COMPRESS_MAX_DOCS`
- compression can only change narrative fields in `stable_base` and `query_bonus`
- `identity`, `facts`, and `previews` must remain intact
- integrity mismatch discards the compressed artifact

## Follow-up Policy
- order:
  - active anchor
  - fact short-circuit
  - raw payload / hydrate
  - exact lookup rerun
  - then envelope build + pack

## Current Validation
- validation source of truth: `03_운영과_환경.md`, `04_회귀기준과_점검.md`
- 이 문서는 packing 구조 메모이며, pytest lane과 운영 gate를 직접 소유하지 않는다.
