# ADR-0018 레거시 제거 매니페스트

본 문서는 ADR-0018(권한 분리 3계층 아키텍처) 적용 후 신규 `apps.pipeline` 의존 그래프에서
빠지는 레거시 모듈/디렉터리 목록이다. 새 `main → app_factory → routes/runtime`에서
어떤 파일도 import하지 않음을 확인했으니, 동일 브랜치(`판단-검색-에이전트`)에서 일괄 삭제해도 안전하다.

> Claude Code 안전 분류기가 대량 삭제를 차단했으므로, 본 매니페스트를 보고 사용자가 직접 `git rm -r`로 정리하면 된다.
> 모든 경로는 저장소 루트 기준 상대경로다.

## 1. 패키지 단위 통째 삭제 (디렉터리)

```bash
git rm -r apps/planner/
git rm -r apps/api/contracts/
git rm -r apps/api/rag_mapper/domains/
git rm -r apps/retrieval/tools/
```

## 2. 개별 파일 삭제

### apps/api/

```bash
git rm apps/api/workflow_builder.py
git rm apps/api/workflow_nodes.py
git rm apps/api/runtime_helpers.py
git rm apps/api/oracle_request_defaults.py
git rm apps/api/request_overrides.py
git rm apps/api/rag_mapper/mapping_config.py
git rm apps/api/rag_mapper/rag_mapper.py
```

### apps/chat/

```bash
git rm apps/chat/answer_generation.py
git rm apps/chat/answer_merge.py
git rm apps/chat/execution_trace_summary.py
git rm apps/chat/llm_json.py
git rm apps/chat/llm_streaming.py
```

### apps/conversation/ (dialogue/agent/anchor/scope/turn 계열)

```bash
git rm apps/conversation/agent_contracts.py
git rm apps/conversation/agent_dialogue_router.py
git rm apps/conversation/agent_dialogue_router_validation.py
git rm apps/conversation/agent_flags.py
git rm apps/conversation/agent_observation.py
git rm apps/conversation/agent_tool_executor.py
git rm apps/conversation/agent_tools.py
git rm apps/conversation/anchor_constraint_compiler.py
git rm apps/conversation/anchor_resolution.py
git rm apps/conversation/clarification_labeler.py
git rm apps/conversation/clarification_prose.py
git rm apps/conversation/context_router.py
git rm apps/conversation/conversation_state_card.py
git rm apps/conversation/conversation_store.py
git rm apps/conversation/entity_reference.py
git rm apps/conversation/fact_followup_resolver.py
git rm apps/conversation/followup_anchor.py
git rm apps/conversation/followup_resolution.py
git rm apps/conversation/memory_observer.py
git rm apps/conversation/raw_payload_store.py
git rm apps/conversation/request_facade.py
git rm apps/conversation/scope_resolver.py
git rm apps/conversation/turn_interpreter.py
git rm apps/conversation/turn_policy.py
git rm apps/conversation/turn_trigger.py
```

### apps/evidence/ (canonical_evidence 외 전부)

```bash
git rm apps/evidence/canonical_context.py
git rm apps/evidence/citation_registry.py
git rm apps/evidence/context_build_policy.py
git rm apps/evidence/context_compression_service.py
git rm apps/evidence/context_helpers.py
git rm apps/evidence/context_packer.py
git rm apps/evidence/context_renderer.py
git rm apps/evidence/context_score_gate.py
git rm apps/evidence/derived_facts_builder.py
git rm apps/evidence/detail_contract.py
git rm apps/evidence/detail_resolver.py
git rm apps/evidence/evidence_integrity.py
git rm apps/evidence/evidence_lineage.py
git rm apps/evidence/memory_facts_resolver.py
git rm apps/evidence/prompt_evidence_envelope.py
git rm apps/evidence/rag_result_assembly.py
git rm apps/evidence/render_profile.py
git rm apps/evidence/result_set.py
git rm apps/evidence/source_reference.py
```

### apps/platform/ (settings/storage/triton/openai_compat/langchain_compat 외)

```bash
git rm apps/platform/log_keys.py
git rm apps/platform/metrics.py
git rm apps/platform/pipeline_steps.py
git rm apps/platform/rag_constants.py
git rm apps/platform/rag_types.py
git rm apps/platform/runtime_strategy_policy.py
git rm apps/platform/schemas.py
git rm apps/platform/solar_tokenizer_adapter.py
```

### apps/retrieval/ (rag_store 외 전부)

```bash
git rm apps/retrieval/execution_manager.py
git rm apps/retrieval/filters.py
git rm apps/retrieval/rag_base_orchestration.py
git rm apps/retrieval/rag_collection_retrieval.py
git rm apps/retrieval/rag_compile_runtime.py
git rm apps/retrieval/rag_dense_runtime_support.py
git rm apps/retrieval/rag_dispatch_runtime.py
git rm apps/retrieval/rag_execution_policy.py
git rm apps/retrieval/rag_executor_support.py
git rm apps/retrieval/rag_filter_policy.py
git rm apps/retrieval/rag_hydration_runtime.py
git rm apps/retrieval/rag_join_orchestration.py
git rm apps/retrieval/rag_join_runtime.py
git rm apps/retrieval/rag_pipeline.py
git rm apps/retrieval/rag_postprocess_policy.py
git rm apps/retrieval/rag_rank_runtime.py
git rm apps/retrieval/rag_rerank_support.py
git rm apps/retrieval/rag_retriever.py
git rm apps/retrieval/rag_runtime_observability.py
git rm apps/retrieval/rag_runtime_prelude.py
git rm apps/retrieval/rag_runtime_safety.py
git rm apps/retrieval/rag_search_policy.py
git rm apps/retrieval/rag_strategy_guard.py
git rm apps/retrieval/result_contract.py
git rm apps/retrieval/result_policy.py
git rm apps/retrieval/retrieval.py
git rm apps/retrieval/retrieval_workflow.py
git rm apps/retrieval/runtime_routing.py
```

## 3. 보존 (변경 없음)

다음 디렉터리/파일은 새 파이프라인이 직접 또는 간접적으로 의존하므로 유지한다.

| 영역 | 파일 |
|------|------|
| 진입점 | `apps/api/main.py`, `app_factory.py`, `routes.py`, `runtime.py` |
| SSE | `apps/api/streaming/*` (3 files) |
| 도메인 enum | `apps/api/rag_mapper/__init__.py`, `schema_types.py` |
| LLM 클라이언트 | `apps/chat/llm_runtime.py`, `apps/platform/openai_compat_llm.py`, `triton_client.py`, `triton_llm.py`, `settings.py`, `storage.py`, `langchain_compat.py` |
| Qdrant 리소스 | `apps/retrieval/rag_store.py` |
| Session 모델 | `apps/conversation/session_memory.py`, `view_state.py`, `entity_registry.py` |
| Canonical evidence | `apps/evidence/canonical_evidence.py` |
| 신규 패키지 | `apps/pipeline/` (전체) |
| 문서/프롬프트 | `apps/docs/`, `apps/prompts/` |

## 4. 테스트 정리

기존 `tests/` 하위 중 다음 카테고리는 새 파이프라인에 맞지 않으므로 별도 검토 후 제거 권장:

- `tests/test_planner_*` (12+ files)
- `tests/test_request_facade_*` (4 files)
- `tests/test_answer_*` (state_consistency, groundedness, merge, manifest 등)
- `tests/test_execution_manager_*` (6 files)
- `tests/test_rag_retriever_*`, `test_retrieval_*`, `test_search_policy_*`
- `tests/test_lookup_join_hybrid_runtime_failure.py`
- `tests/golden/test_agentic_dialogue_shindonggu.py`
- `tests/conversation/test_agentic_*.py`, `test_clarification_*.py`, `test_followup_*.py`,
  `test_turn_*.py`, `test_workflow_next_current_context.py` 등

신규 회귀 시나리오는 `tests/pipeline/`에 추가됨 (4개 회귀 케이스 + 단위 테스트).

## 5. 실행 후 검증

```bash
# 삭제 후 import graph 확인
python -c "from apps.api.app_factory import create_app; print('OK')"

# pytest로 새 파이프라인 테스트 확인
pytest tests/pipeline/ -q
```
