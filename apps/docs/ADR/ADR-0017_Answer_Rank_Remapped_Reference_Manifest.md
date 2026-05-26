# ADR-0017: Answer-Rank Remapped Reference Manifest

## 상태 (Status)
제안됨 (Proposed)

## 날짜 (Date)
2026-05-14

## 배경 (Background)
현재 NTIS RAG 시스템은 `SessionMemory.current_context`를 다음 턴 참조의 공식 진실원으로 사용한다. 사용자가 "출처 [6]", "1번 과제", "그 항목"처럼 이전 답변의 노출 순번을 참조하면, 런타임은 직전 답변에서 발행된 `PublishedManifestContext`를 기준으로 해당 항목을 해석해야 한다.

2026-05-14 세션에서 다음 흐름이 관측되었다.

1. 사용자가 `가장 최신의 LLM 관련 논문을 찾아줘`를 요청했다.
2. 시스템은 `search_ntis_domain`을 선택했고, `ntis_perf_v1`에서 검색 결과 10건을 확보했다.
3. 답변 생성 이후 `unsupported_item_identity`가 발생했고, 최종 답변 manifest 발행은 `blocked_state_consistency`로 차단되었다.
4. 이어서 사용자가 `출처 [6] 의 상세정보 요청`을 보냈다.
5. 다음 턴 로드 시 `current_context_type=empty`였고, `[6]`을 공식 manifest 항목으로 해석하지 못해 `manifest_item_not_found`가 발생했다.

이 문서는 위 세션에서 발생한 문제의 배경, 원인, 결과, 향후 해결 계획을 기록한다. 이 ADR은 소스코드 변경을 포함하지 않는다.

## 문제 파악 (Observed Problem)
대표 로그 흐름은 다음과 같다.

| 단계 | 관측 내용 |
|---|---|
| 최초 검색 요청 | `request_id=2919ceab-2b78-4d6c-a9ca-c80077708d08-f2122cf5` |
| 도구 결정 | `AGENT.DECISION`: `search_ntis_domain` |
| 검색 결과 | `docs_found=10`, `target_cols=["ntis_perf_v1"]` |
| 답변 검증 | `state_consistency.status=unsupported_item_identity` |
| 발행 차단 | `visible_answer_manifest_status=blocked_state_consistency` |
| 후속 참조 요청 | `출처 [6] 의 상세정보 요청` |
| 메모리 로드 | `current_context_type=empty`, `history_turns=0`, `view_recent_mentions=0` |
| 후속 참조 실패 | `manifest_item_not_found` |

`unsupported_item_identity`는 "답변에 노출된 항목 정체성을 내부 evidence snapshot 항목과 안정적으로 대응시킬 수 없다"는 의미다. 이 상태에서는 사용자가 본 출처 번호를 다음 턴의 공식 참조 대상으로 발행할 수 없으므로, `PublishedManifestContext`가 저장되지 않는다.

## 원인 (Root Cause)
근본 원인은 검색 실패가 아니라 **답변 노출 번호와 내부 evidence identity 간 매핑 실패**다.

현재 파이프라인은 retrieval snapshot의 순위와 최종 답변의 노출 순위가 일치한다고 가정하는 경향이 있다. 그러나 LLM 답변 생성 과정에서는 다음 일이 발생할 수 있다.

* LLM이 검색 결과 중 일부만 선택한다.
* LLM이 사용자 질문에 맞게 항목 순서를 재정렬한다.
* LLM이 답변 본문에서 `[4]`, `[6]`, `[10]`처럼 원본 snapshot 순위 또는 혼합된 번호를 인용한다.
* 최종 답변의 "사용자가 본 N번째 항목"과 내부 retrieval snapshot의 `rank=N`이 더 이상 같은 대상을 가리키지 않는다.

이 경우 시스템은 답변 항목을 원본 evidence와 대응시키는 보정 manifest를 만들지 못하고 `unsupported_item_identity`로 fail-closed 한다. fail-closed 자체는 잘못된 출처를 발행하지 않기 위한 안전한 동작이지만, 후속 참조를 위한 공식 문맥이 비어 다음 턴 사용성이 깨진다.

보조적으로, `current_context=empty` 상태를 로드할 때 세션 전체가 유효하지 않은 것처럼 취급되면 history나 view projection까지 손실될 수 있다. 이 문제는 후속 참조 실패를 더 회복하기 어렵게 만들 수 있으나, 본 사건의 1차 원인은 manifest 미발행이다.

## 결과 (Impact)
사용자는 최초 답변에서 출처 번호를 보았지만, 시스템은 그 번호 체계를 다음 턴의 공식 truth로 저장하지 못했다.

그 결과는 다음과 같다.

* `출처 [6]`이 사용자가 본 6번째 출처가 아니라 해석 불가능한 참조가 되었다.
* `lookup_specific_entity`는 `manifest_item_not_found`로 실패했다.
* title 기반 fallback도 confident match를 만들지 못해 상세 조회를 복구하지 못했다.
* 사용자 관점에서는 "방금 보여준 출처를 다시 물었는데 시스템이 잊은 것처럼 보이는" 대화 단절이 발생했다.

## 결정 방향 (Decision Direction)
향후 해결 방향은 retrieval snapshot 순위를 사용자-visible 출처 번호로 강제하는 것이 아니라, **최종 답변 기준의 출처 번호 체계를 새로 발행하는 것**이다.

시스템은 다음 두 순위를 명시적으로 분리해야 한다.

| 개념 | 의미 |
|---|---|
| `published_rank` | 최종 답변에서 사용자가 본 출처/항목 번호 |
| `source_snapshot_rank` | 원본 retrieval snapshot 또는 canonical evidence 내부 순번 |

답변 발행 단계는 LLM의 최종 답변 항목을 parsing하고, 각 항목을 원본 canonical evidence에 매핑한 뒤, 사용자-visible 순서 기준으로 새 `PublishedManifestContext`를 발행해야 한다. 후속 질문의 `출처 [N]`은 `published_rank=N`으로 해석하고, 실제 상세 조회나 내부 evidence 접근은 연결된 `source_snapshot_rank`와 식별자 맵을 통해 수행한다.

프론트 노출 계약인 `ReferenceItem`은 기존 `tag / id / title` 구조를 유지한다. 단, `id`의 의미는 `tag`에 따라 고정한다.

| `tag` | `ReferenceItem.id` 의미 |
|---|---|
| `IRD_NAI_PJT_INFO` | `pjt_id` |
| 논문/성과 계열 tag | `rst_id` |

`pjt_no`는 과제 그룹 축 식별자로 보존할 수 있지만, 개별 출처 항목의 primary `id`로 사용하지 않는다. 이는 `pjt_id`와 `pjt_no`를 혼용하지 않는 기존 식별자 계약을 따른다.

## 향후 계획 (Plan)
소스코드 수정이 허용되는 별도 작업에서 다음 방향을 검토한다.

1. answer-state consistency 단계에서 최종 답변 항목을 원본 evidence와 매핑하는 remap 경로를 추가한다.
2. remap이 성공하면 `blocked_state_consistency`가 아니라 답변 기준 `PublishedManifestContext`를 발행한다.
3. 발행 manifest에는 최소한 `published_rank`, `source_snapshot_rank`, `tag`, `id_axis`, `id`, `title`의 의미가 보존되도록 한다.
4. 후속 참조 해석은 `출처 [N]`을 `published_rank=N`으로 먼저 resolve한다.
5. `ReferenceItem.id`는 `tag=IRD_NAI_PJT_INFO`일 때 `pjt_id`, 논문/성과 계열일 때 `rst_id`로 유지한다.
6. `current_context=empty` 로드가 정상적인 빈 문맥으로 처리되도록, 세션 history나 view projection을 불필요하게 초기화하는지 별도 검토한다.

## 검증 계획 (Validation Plan)
구현 시 다음 회귀 시나리오를 확인해야 한다.

* LLM 답변 순서가 retrieval snapshot 순서와 달라도 `PublishedManifestContext`가 발행되는지 확인한다.
* 사용자가 `출처 [6]`을 물었을 때, 원본 snapshot 6번이 아니라 사용자가 본 6번째 항목으로 resolve되는지 확인한다.
* `tag=IRD_NAI_PJT_INFO`에서는 `ReferenceItem.id=pjt_id`인지 확인한다.
* 논문/성과 계열에서는 `ReferenceItem.id=rst_id`인지 확인한다.
* `pjt_no`가 primary `ReferenceItem.id`로 사용되지 않는지 확인한다.
* remap이 불가능한 경우에는 기존처럼 발행을 차단해 잘못된 출처를 만들지 않는지 확인한다.
* `current_context=empty`를 로드해도 정상 세션 상태가 불필요하게 폐기되지 않는지 확인한다.

## 비목표 (Non-Goals)
이 ADR은 다음을 수행하지 않는다.

* 소스코드 수정
* 런타임 동작 변경
* API 응답 계약 변경
* 테스트 코드 수정
* 기존 ADR 또는 운영 문서의 대규모 재작성

## 관련 문서 (Related Documents)
* [01 아키텍처와 흐름](../01_ARCHITECTURE.md)
* [02 실행 계약과 전략 규칙](../02_CONTRACTS_AND_RULES.md)
* [ADR-0016: Agent Contract Shock Absorber](./ADR-0016_Agent_Contract_Shock_Absorber.md)
