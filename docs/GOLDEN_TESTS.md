# 골든 테스트 (GOLDEN TESTS)

이 문서는 현재 runtime의 최소 golden-query invariant를 기록합니다.

## 현재 기준선

현재 baseline은 다음을 전제로 합니다.
- `apps/core/query_intent.py`가 heuristic intent extraction을 제공합니다.
- planner contract enforcement는 `apps/core/planner_contract.py`에 유지됩니다.
- execution mode selection은 final assembled strategy 기준으로 검증됩니다.

## Golden Queries

| ID | Query | Expected mode | Notes |
|---|---|---|---|
| G001 | `AI related projects` | `search` | broad topic query |
| G002 | `1711015550 project detail` | `lookup` | exact project id lookup |
| G003 | `PJT-2020-1234-5678 related outputs` | `join` | group project seed plus perf/output cue should stay join-classified |
| G004 | `Kim researcher projects` | `lookup` | English researcher cue should still become person-constrained lookup/list behavior |
| G005 | `ETRI papers stats 2021 2023` | `lookup` | stats-style perf lookup |

## Invariants

### SEARCH
- SEARCH는 org/person filter를 server-side hard requirement로 두면 안 됩니다.
- SEARCH는 aggressive must filter 대신 recall과 ranking signal을 우선해야 합니다.

### LOOKUP
- 명시적 identifier는 server-side lookup filter로 전환되어야 합니다.
- 이름 기반 lookup은 exact-id lookup보다 느슨해야 합니다.
- `Kim researcher projects` 같은 영어 researcher cue도 person-constrained lookup/list intent로 유지되어야 합니다.

### Planner Entry
- 비어 있는 heuristic precheck payload가 planner 실행을 막으면 안 됩니다.
- org term, year, perf type, title term 같은 non-id heuristic hint도 planner를 거쳐야 합니다.
- explicit identifier precheck는 불필요한 planner round-trip을 건너뛸 수 있습니다.

### Canonical Evidence
- source payload에 둘 다 있으면 `pjt_id`와 `pjt_no`를 각각 독립적으로 보존해야 합니다.
- lead/participant/affiliation organization semantics를 분리해서 유지해야 합니다.
- render profile resolution은 people cue가 있는 project lookup을 generic project context가 아닌 people-oriented context로 분류해야 합니다.
- answer context building은 canonical evidence만 사용해야 하며, 필요 시 raw retrieved document를 요청 시점에 canonicalize 해야 합니다.
- knowledge sufficiency의 previous-context reasoning은 canonicalized context text만 사용해야 합니다.
- planner previous-context prompting과 previous-anchor seed extraction은 canonical evidence snapshot만 사용해야 합니다.

### JOIN
- JOIN은 relation intent와 유효한 join seed를 모두 요구합니다.
- relation query는 별도 `action=relation` dialect가 아니라 `action=list` + `output_type=relation`으로 정규화되어야 합니다.
- JOIN에서 `head`는 relation target과 같아야 합니다.
- `join_key_mode=instance`와 `join_key_mode=group`은 상호 배타적이어야 합니다.
- JOIN hop2는 무관한 org/title hard gate를 추가하면 안 됩니다.

### Streaming
- reasoning chunk가 최종 사용자 노출 content에 섞이면 안 됩니다.
- `ttft_any_ms`와 `ttft_content_ms`는 별도로 추적되어야 합니다.
- direct-answer mode는 하나의 답변을 여러 model stream으로 중복 송출하면 안 됩니다.
- 비어 있는 streamed content가 조용히 성공한 답변처럼 처리되면 안 됩니다.

## Validation 방향

권장 테스트:
- mode, relation, join-key invariant를 검증하는 contract test
- streaming과 route behavior를 검증하는 runtime test
- planner assembly immutability regression check
- canonical evidence와 render-profile regression check

### Health
- `/health`는 compiled graph가 있으면 ready를 반환해야 하며, Redis/KV degradation만으로 실패하면 안 됩니다.
- `/health/details`는 degraded dependency 상태를 payload에 계속 보여줘야 합니다.

