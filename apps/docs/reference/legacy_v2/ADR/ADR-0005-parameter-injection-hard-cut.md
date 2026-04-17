# ADR-0005: Workflow / Planner / Retrieval / Chat Parameter-Injection Hard Cut

- Status: Accepted
- Date: 2026-04-02

## Context

Feature-sliced package cutover 이후에도 workflow와 service entrypoint에는 Spring-style parameter injection이 남아 있었다.
특히 `apps/api/app_factory.py`가 `partial(...)`, callback, class injection으로 node를 조립하면서 다음 문제가 반복됐다.

- node owner가 `app_factory`로 역전되어 feature package가 source-of-truth가 아니게 됨
- 테스트가 함수 인자 채우기에 과도하게 묶여 patch-friendly import seam을 살리지 못함
- planner / retrieval / chat 기본값과 runtime helper가 composition root에 남아 drift를 키움

## Decision

다음 규칙으로 hard cut 한다.

- workflow node와 service entrypoint는 실제 runtime input만 인자로 받는다.
- collaborator 함수, class, logger, log_event, builder는 모듈 상단 import로 직접 해결한다.
- `apps/api/workflow_builder.py`는 `WorkflowNodes` 없이 fixed graph를 직접 조립한다.
- `apps/api/app_factory.py`는 composition root만 남기고 node/business logic wrapper를 소유하지 않는다.
- `apps/api/runtime_helpers.py`는 app-level singleton owner로서 `logger`, `measure_latency`, `log_event`, `log_section`를 제공한다.
- 테스트는 fake callable 주입 대신 `unittest.mock.patch` / monkeypatch로 module symbol을 대체한다.

## Consequences

긍정적 효과:

- workflow/service signature가 state-centric으로 단순화된다.
- feature package가 자기 로직과 기본값의 source-of-truth로 돌아온다.
- app_factory와 runtime wiring이 transport/composition root 역할에 집중한다.

주의점:

- runtime importability는 top-level heavy dependency에 다시 민감해질 수 있으므로, startup-only dependency는 가능하면 lazy import를 유지한다.
- decorator가 public signature를 흐리지 않도록 metadata 보존이 필요하다.

## Implementation Notes

- `apps/conversation/request_facade.py`는 `RequestUnderstandingFacade` dataclass를 삭제하고 top-level `build_intent_payload(...)`만 유지한다.
- `apps/api/workflow_nodes.py`, `apps/retrieval/retrieval_workflow.py`, `apps/chat/answer_generation.py`의 workflow nodes는 state-only signature로 정리한다.
- `apps/planner/query_analysis.py`, `apps/planner/planner_service.py`는 module-owned defaults/import를 사용한다.
- `apps/api/runtime.py`는 `initialize_app_runtime(app, *, config)` / `shutdown_app_runtime(app)`만 공개한다.
