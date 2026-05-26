# 사건 원인 분석 보고서 — 2026-05-12

| 항목 | 내용 |
|---|---|
| 사건 식별자 | INCIDENT_2026-05-12_retrieval_and_dialog |
| 분석 기간 | 2026-05-12 |
| 트리거 쿼리 | Turn 1 "한국과학기술정보연구원에서 시행한 과제들을 목록은?" → Turn 2 "7번 항목의 상세 정보 요청" |
| 영향 범위 | 동일 쿼리 패턴(특정 기관의 과제 목록 + 후속 순번 참조) — 사용자에게 fallback(상태 일치 실패로 답변 차단) 메시지 노출 + Turn 2 internal_error(에이전트 내부 오류) 종결 |
| 작성 분량 | 변경 파일 14개, 코드 차이 약 +887 / -584 라인 |

---

## 1. 사건 개요

NTIS Agentic RAG 챗봇이 "한국과학기술정보연구원(KISTI)의 과제 목록"이라는 평이한 검색 쿼리에서:

1. **검색 단계에서 의료 도메인 과제가 상위로 leak** → 두 답변 모델(Solar/Gemma) 모두 환각·항목 수 불일치 → `state_consistency`(상태 일치 검증) 실패 → `selected_model: fallback`(두 답변 모두 거부, 안내 메시지로 대체)으로 종결.
2. **후속 turn의 순번 참조(`rank:7`)가 manifest 부재로 실패** → `manifest_item_not_found`(목록에서 해당 순번을 찾지 못함) 경고 → 동일 호출 재시도 → `agent_loop_guard_triggered`(에이전트 동일 호출 반복 차단) → `agent_internal_error`(에이전트 내부 오류) → 잘린 응답.
3. 동시에 **Redis 미가용 상태에서 file KV(파일 기반 키-값 저장소)로 절체된 후 영구히 그 모드에 갇혀** 있으며 만료된 KV 항목이 디스크에 lazy(읽을 때만 정리)로만 남는 운영 갭이 노출됨.

본 보고서는 위 세 갈래 사건의 공통 근본 원인을 추적하고 적용된 수정과 잔존 위험을 정리한다.

---

## 2. 관찰된 증상

### 2.1 Turn 1 — 검색 단계
- `PLANNER.STAGE2`(플래너 2단계 출력 로그)에 `filters.lead_org_name: ["한국과학기술정보연구원"]`, `confidence: 0.96`(신뢰도).
- `PLANNER.PIPELINE.changed_filter_fields: {}`(플래너 머지 단계의 필터 변경 추적, 비어 있음).
- `RAG.COL.RETRIEVE`(컬렉션별 검색 호출 로그)에 `qfilter: null`, `qfilter_source: "search_unfiltered"`(앵커 미발동·서버 필터 없음).
- 상위 hits에 의료 과제 `1465009298` "본원의 객혈환자를 대상으로 시행한 경동맥색전술 증례분석"(국립의료원), `1465009438` "슬관절 전치환술시 골결손에 시행한 경골부 금속 블록 보강술"(국립의료원), `1711192802` "자궁경부봉축술…"(가톨릭대) 포함.
- `LLM.RESULT`(답변 모델 선택 결과): `solar_state.status: "unsupported_count"`(Solar 응답 항목 수가 가시 항목 수 초과), `gemma_state.status: "unsupported_count"`(Gemma 동일), `selection_reason: "both_models_state_inconsistent"`.
- 사용자 가시 응답: fallback 안내 메시지(약 60자).

### 2.2 Turn 2 — 순번 참조
- Turn 1 종료 직후 `MEMORY.SNAPSHOT save ... view_recent_mentions: 10, view_subject_index_size: 144`(메모리 스냅샷, 직전 turn의 mention과 subject index 보존).
- Turn 2 시작 시 `view_recent_mentions: 1, view_subject_index_size: 0` — **manifest 펼침이 사라짐**.
- `AGENT.DECISION`(에이전트 결정): `lookup_specific_entity(entity_ref="rank:7", ...)`.
- `manifest_item_not_found` 경고 → `_render_tool_retry_feedback`(에이전트 도구 재시도 피드백 메시지) 안내 추가 → agent 동일 인자 재시도 → `AGENT.LOOP_GUARD.TRIGGERED`(에이전트 동일 호출 반복 차단) → `AGENT.INTERNAL_ERROR`(에이전트 내부 오류).
- 응답 길이 약 34자(잘린 일반 오류 메시지).

### 2.3 운영
- 시작 로그 `Redis connection failed: Error 11001 ... redis8:6379. getaddrinfo failed.` 후 file KV로 절체.
- `local_kvstore/` 디렉토리에 만료 항목이 영구히 남는 lazy delete(읽을 때만 정리) 정책만 존재.
- `MEMORY.SNAPSHOT`(메모리 상태 스냅샷)에 백엔드 식별 차원이 없어 운영자가 redis/file 어느 모드인지 즉시 구분 불가.

---

## 3. 근본 원인 분석

### 원인 #1 — 검색 모드의 server-side 앵커 필터가 설계상 비활성

`apps/retrieval/rag_filter_policy.py:117-118` 에 `if context.mode != "lookup": return None`, `apps/retrieval/rag_collection_retrieval.py:113-115` 에 `if mode == "search": qfilter = None`. 두 곳이 이중 가드로 search 모드의 서버 필터를 강제로 끄도록 작성되어 있었음. 이는 broad recall(폭넓은 후보 수집) 우선의 설계 의도지만, planner가 명시적으로 `lead_org_name`을 anchor로 줬을 때조차도 적용되지 않아 BM25 어휘 매칭에 의존 — 의료 동조어(공통 단어 "시행한")가 상위 점수를 차지하는 원인.

### 원인 #2 — 플래너 단계 1.5 어휘와 다운스트림 매처 어휘 불일치

`apps/planner/planner_runtime.py:451-458` 에서 `org_role_hint`(주관기관 역할 힌트)에 `lead_org`/`participant_org`/`affiliation_org` 접미사 표기를 사용. 그러나 `apps/conversation/anchor_resolution.py:45-50` 의 정규화와, 이번에 신설한 앵커-스트릭트 게이트 조건(`apps/retrieval/rag_filter_policy.py`)이 모두 `("lead", "performer", "performing")` 짧은 형식만 수용. 결과적으로 stage1.5의 `lead_org`가 흘러가면 정규화·게이트 양쪽에서 매치 실패 — 앵커-스트릭트가 1차 수정 후에도 여전히 닫혀 있었음.

### 원인 #3 — ADR-0015(주제 유지 원칙) 의도와 구현의 비대칭

`apps/api/contracts/visible_answer_manifest_publication.py:117-133` 에 의해 `state_consistency`(상태 일치 검증)가 실패하면 `publication_status: "blocked_state_consistency"`(답변 일관성 실패로 발행 차단) + `published_manifest=None`. 그런데 `SubjectQueryContext`(주제 유지 컨텍스트)의 `result_manifest`(주제 결과 목록)는 채워지지만, `view_state_from_current_context`(현재 컨텍스트에서 뷰 상태 복원)의 SubjectQueryContext 분기가 그 manifest를 view_state(뷰 상태)에 펼치지 않음. ADR-0015는 "subject 유지, 답변만 보류"인데 manifest까지 같이 비워져 후속 turn의 순번 참조 경로가 완전히 끊김.

### 원인 #4 — manifest 부재 시 사전 차단 없는 재시도 → 루프 가드 폭주

`apps/conversation/agent_tool_executor.py:185-214` 의 `_resolve_entity_ref_from_manifest`(에이전트의 manifest 항목 해소)는 `PublishedManifestContext`(정상 발행 목록)만 인식. SubjectQueryContext는 외면. 또한 manifest 부재가 확인된 후에도 사전 차단 없이 `_render_tool_retry_feedback`(에이전트 도구 재시도 피드백 메시지)에 텍스트 안내만 추가 — agent가 재시도하면 `_loop_guard_triggered`(에이전트 동일 호출 반복 차단)에 걸려 사용자에게는 의미 없는 일반 오류 메시지가 emit.

### 원인 #5 — 운영 측면의 KV 관리 갭

`apps/api/runtime.py:90-127` 의 `_initialize_kv_store_with_fallback`(KV 스토어 초기화·폴백 헬퍼)는 단발 시도. Redis 복구 시 자동 절체 없음. `apps/platform/storage.py` 의 `FileKVStore`(파일 기반 KV 저장소)는 lazy delete만 — 접근하지 않는 만료 키가 디스크에 누적. 백엔드 식별 차원(`backend_name`)도 부재.

### 원인 #6 — 두 답변 모델의 항목 수 불일치(부수적)

두 모델 모두 retrieval 결과 17개를 받아 응답을 만들 때 Solar는 10개로 정리하면서 항목 매핑이 어긋나 `parsed_item_count: 19`, Gemma는 16개로 부풀려 `parsed_item_count: 16`(존재하지 않는 7~16번 hallucination). `state_consistency` 검증이 이를 잡아내어 정상 차단했으나, 입력 자체가 노이즈를 포함했기 때문에 환각 표면이 확대됨. 즉 원인 #1·#2가 해소되면 #6의 빈도도 자연 감소.

---

## 4. 기여 요인

1. **Drift fallback의 과민 발동** — `RAG.RETRIEVAL_QUERY.RESOLUTION`(검색 쿼리 해소 로그)에서 `drift_detected: true`, `drift_reasons: ["topic_terms_lost","people_terms_lost"]`로 planner_query(플래너 쿼리)를 버리고 raw_query(원문 질의)로 폴백. 원문에 포함된 "시행한"이라는 동조어가 의료 도메인 BM25 점수를 끌어올리는 직접 트리거.
2. **client-side rerank의 weak signal** — `RAG.RERANK.SCORE_RANGE_NORM`에서 `_filter_score` 정규화 평균 0.0055. 일단 들어온 노이즈를 reranker(재순위 매기기)로 뒤집기 곤란.
3. **관측성 미스리딩** — `changed_filter_fields: {}` 로그가 "필터가 머지조차 안 됨"으로 오독을 유도. 실제로는 머지는 됐고 server에서 무시. `apps/planner/planner_service.py:259`의 `filter_fields = ("ids_map",)`(추적 필드 튜플)에 ids_map만 들어가 있었기 때문.

---

## 5. 적용된 수정

| 원인 | 수정 위치 | 변경 요지 |
|---|---|---|
| #1 | `apps/retrieval/rag_filter_policy.py`, `apps/retrieval/rag_collection_retrieval.py` | search 모드에서도 `mode=search` + `base_route=project` + `planner_org_filter_present` + `org_role∈{lead,lead_org,performer,performing}` + `org_filter≠None` + `anchor_strict_conf_ok`(앵커 강제 적용 최소 신뢰도 통과) 일 때 `IRD_NAI_PJT_INFO` tag(과제 정보 태그) + `org_filter` 합성 후 `_apply_extra_filters`(공통 후처리 필터). col_perf(성과물 컬렉션) 분기도 같은 패턴 확장. `qfilter_source` 차원을 RAG.COL.RETRIEVE 로그에 추가. people-strict 분기도 같이 도입. |
| #1·#2 | `apps/retrieval/rag_runtime_prelude.py` | `planner_confidence`(플래너 신뢰도)와 `anchor_strict_conf_ok`(앵커 강제 신뢰도 통과) 계산 추가. 환경변수 `RAG_ANCHOR_STRICT_MIN_CONF`(앵커 강제 적용 최소 신뢰도, 기본 0.8). `RuntimePreludeResult`(런타임 사전 결과 데이터)에 필드 전파. |
| #2 | `apps/conversation/anchor_resolution.py:45-52` | `org_role_hint`의 `_org` 접미사 변형(`lead_org`, `participant_org`, `affiliation_org`)을 정규화 분기에서 함께 수용. |
| #2 | `apps/retrieval/rag_filter_policy.py` | 앵커-스트릭트 게이트 조건의 `org_role` 집합에 `lead_org` 추가. |
| #3 | `apps/conversation/session_memory.py` 의 `view_state_from_current_context` | `SubjectQueryContext` 분기에서 `result_manifest`(주제 결과 목록)가 살아 있고 가시 항목 수>0이면 `visible_answer_manifest`, `active_scope.scope_kind="list"`(활성 범위·목록 종류), `recent_mentions`(최근 언급 항목)에 펼침. `last_query_contract.followup_rights`(후속 질문 권한)에 `ordinal_allowed`(순번 참조 허용) 반영. |
| #4 | `apps/conversation/agent_tool_executor.py` 의 `_resolve_entity_ref_from_manifest` | `PublishedManifestContext`뿐 아니라 `SubjectQueryContext`도 인식. 단 `followup_rights.ordinal_allowed`가 켜진 경우만 통과. |
| #4 | `apps/api/workflow_nodes.py` 의 `node_agent_internal_error`(에이전트 내부 오류 응답 노드) | 마지막 관측 경고에 따라 응답 분기. `manifest_item_not_found` / `agent_loop_guard_triggered` / 기타로 사용자 가시 메시지를 한국어로 차별화. |
| #5 | `apps/api/runtime.py` | `_redis_reconnect_loop`(Redis 재연결 백그라운드 루프) 신설(환경변수 `REDIS_RECONNECT_INTERVAL_SECONDS`, 기본 30초). `_file_kv_sweeper_loop`(파일 KV 만료 청소 루프) 신설(환경변수 `FILE_KV_SWEEP_INTERVAL_SECONDS`, 기본 300초). shutdown hook에서 두 task 모두 cancel. |
| #5 | `apps/platform/storage.py` | `KVStore.backend_name`(백엔드 식별자) 클래스 속성 추가(`memory`/`file`/`redis`). `FileKVStore.sweep_expired`(만료 파일 일괄 삭제) 신설. |
| #5 | `apps/conversation/memory_observer.py` | `log_memory_snapshot`(메모리 스냅샷 로그) 파라미터에 `kv_store`(현재 KV 스토어 객체) 추가, `MEMORY.SNAPSHOT` 로그에 `kv_backend` 차원 노출. `apps/api/workflow_nodes.py` 두 호출 지점에서 `state.kv_store` 전달. |
| #3·#6(부수) | `apps/planner/planner_service.py:257-269` | `tracked_fields`(추적 필드 튜플)와 `filter_fields`(필터 추적 필드 튜플)에 `lead_org_terms`, `participant_org_terms`, `org_terms`, `org_role`, `people_terms`, `perf_types`, `year_from`, `year_to` 확장. `PLANNER.PIPELINE.changed_filter_fields` 로그의 가시성 회복. |

---

## 6. 검증 결과

라이브 재현 로그(2026-05-12 11:39 시작, 11:45 Turn 1, 11:47 Turn 2)에서 확인:

- `[file_kv] swept 30 expired entries` — `FileKVStore.sweep_expired`(만료 파일 일괄 삭제) 정상 작동.
- `MEMORY.SNAPSHOT ... "kv_backend": "file"` 두 turn 모두 노출 — 백엔드 식별 차원 정상.
- Turn 2 load: `view_manifest_items: 10` — `SubjectQueryContext`(주제 유지 컨텍스트)의 manifest 펼침이 view_state(뷰 상태)에 정상 반영. agent가 `rank:7` → 정확한 항목 제목 "슬관절 전치환술시 골결손에 시행한 경골부 금속 블록 보강술" 해소 → detail lookup 정상 → Solar 응답 3.6초 정상 emit.
- 그러나 Turn 1의 `qfilter_source: "search_unfiltered"`(앵커 미발동·서버 필터 없음)는 그대로였음 → 원인 #2(어휘 불일치) 보강 후 재검증 대기.

---

## 7. 잔존 위험

1. **`planner_runtime` 어휘 통일 미완** — `lead_org` 같은 접미사 표기가 다른 매처 경로에 더 있을 수 있음. grep 기반 일제 점검 권장.
2. **Drift fallback 잠재 회귀** — planner_query(플래너 쿼리)가 멀쩡한데도 raw_query(원문 질의)로 회귀하는 trigger가 그대로. 의료 도메인 외 다른 동조어가 또 leak할 수 있음.
3. **Gemma 환각 경향** — retrieval 노이즈를 줄여도 Gemma가 항목을 부풀리는 경향 자체는 별 이슈. 프롬프트에 `visible_count`(가시 항목 수) 강제 안내가 추가로 필요할 수 있음.
4. **File→Redis 절체 시 in-flight 일관성** — 마이그레이션 도구 없음. 절체 직후 짧은 구간은 file write/redis read miss 가능.
5. **`sweep_expired` 성능** — `local_kvstore/`에 수만~수십만 파일이 쌓이면 listdir+open 비용이 커짐. 외부 cron이나 짧은 interval로 분리 필요할 수 있음.
6. **`agent_loop_guard_triggered` 이후 사용자 응답 경로** — 이번 수정으로 일반 fallback 한국어 메시지는 emit되지만, 후속 turn 회복 시나리오(재검색 권유 등)는 별도 설계 필요.

---

## 8. 권장 사항

1. (즉시) Turn 1 재현 검증 — `qfilter_source: "policy_anchor_strict"`(앵커-스트릭트 정책 적용), 상위 10건의 `lead_org_name`(주관기관명) 100% KISTI, `selected_model`(선택된 모델) fallback 탈피.
2. (단기) `planner_runtime`/`anchor_resolution` 어휘 통일 PR — `_org` 접미사를 stage1.5 출력 단계에서 일괄 정규화하거나, 모든 매처에 `_org` 변형을 등록.
3. (단기) Gemma 환각 안전망 — `RAG.CTX`(검색 컨텍스트 요약) 단계에서 `visible_count`를 프롬프트 변수로 강제 노출, "정확히 N개 항목" 한국어 제약 추가.
4. (중기) ADR-0016(Shock Absorber 원칙) 구현 완성도 점검 — agent decision 단계 외 LLM 비결정성 흡수 갭 식별.
5. (중기) `MEMORY.SNAPSHOT` 외 `REQ.START`/`REQ.SUMMARY`/`STREAM.DONE` 등 핵심 운영 로그에도 `kv_backend` 차원 확산.
6. (장기) Redis↔file 마이그레이션 유틸 + 절체 직후 file 백엔드 read-only 정책으로 데이터 일관성 보강.

---

## 부록 A — 이번 세션 변경 파일

| 파일 | 변경 라인 |
|---|---|
| apps/api/runtime.py | +147 |
| apps/api/workflow_nodes.py | +21 |
| apps/conversation/agent_tool_executor.py | +19 |
| apps/conversation/anchor_resolution.py | ±132 |
| apps/conversation/memory_observer.py | +3 |
| apps/conversation/session_memory.py | +78 |
| apps/planner/planner_service.py | ±969 |
| apps/platform/storage.py | +46 |
| apps/platform/triton_client.py | +2 |
| apps/retrieval/rag_base_orchestration.py | +2 |
| apps/retrieval/rag_collection_retrieval.py | +10 |
| apps/retrieval/rag_filter_policy.py | +37 |
| apps/retrieval/rag_pipeline.py | +1 |
| apps/retrieval/rag_runtime_prelude.py | +4 |

## 부록 B — 신설 환경변수

| 변수 | 기본값 | 의미 |
|---|---|---|
| `RAG_ANCHOR_STRICT_MIN_CONF` | 0.8 | 앵커-스트릭트 server filter를 허용할 플래너 최소 신뢰도 |
| `REDIS_RECONNECT_INTERVAL_SECONDS` | 30 | file KV로 절체된 상태에서 Redis 재연결 ping 주기(초) |
| `FILE_KV_SWEEP_INTERVAL_SECONDS` | 300 | FileKVStore의 만료 파일 일괄 청소 주기(초) |
