# 02. 계약과 도메인 규칙

> 이 문서의 규칙은 전부 **"이런 사고를 막으려고"** 존재한다. 그래서 각 규칙 앞에 *막으려는 사고*를 먼저 적었다. 처음 읽을 땐 §1~§4(개념·함정)만 봐도 된다. §5 이후는 정밀 참조다.

## 1. 먼저, 데이터가 어떻게 생겼나

NTIS 데이터는 크게 둘이다:

- **과제(Project)** — 하나의 연구개발 사업.
- **성과(Output)** — 그 과제에서 나온 결과물. **10종**: 논문·특허·소프트웨어·신품종·생명정보·생물자원·화합물·연구보고서·시설장비·기술요약. (타입별 변환 코드: `apps/api/rag_mapper/domains/`)

성과는 과제에서 파생된다("이 과제의 논문/특허를 줘" 같은 질문이 JOIN이다).

## 2. 함정 ①: 비슷해 보이지만 다른 식별자 — `pjt_id` vs `pjt_no`

**막으려는 사고:** 사용자가 특정 과제 하나의 상세를 원했는데, 같은 과제번호를 쓰는 *다른 연도 과제들*까지 섞여 나오는 것.

- `pjt_id` = **개별 과제 한 건**의 고유키. "상세 보여줘"는 반드시 이 축.
- `pjt_no` = 여러 해에 걸친 과제를 **묶는 그룹키**. 통계·성과 묶음의 기본 축.
- `rst_id` = 성과(논문·특허 등)의 키.

**규칙:** 한 질문에서 두 축을 섞지 않는다. 모르는 별칭(`RJT_ID` 등)을 임의 매핑하지 않는다.

## 3. 함정 ②: 같은 단어, 다른 역할 — 기관 3종

**막으려는 사고:** "이 과제 수행기관이 어디야?"에 *참여만 한 기관*이나 *연구자 소속*을 답하는 것. 셋은 전혀 다른 의미다.

- `lead_org_name` — 수행(주관)기관
- `participant_org_name` — 참여기관
- `people_affiliation_org_name` — 참여 인력의 소속기관

(코드에서 `org_role_hint`로 어느 역할인지 고른다.)

## 4. 함정 ③: 동명이인과 "이어 묻기"

**막으려는 사고:** "신동구 연구자" 검색 후 "그럼 2020년 이후 과제는?"이라 물었을 때 — (a) 갑자기 *다른 신동구*를 답하거나, (b) "누구 말씀이세요?"라고 다시 묻거나, (c) 직전 화면에 없던 과제를 끌어오는 것.

이걸 막으려고 시스템은 "지금 우리가 누구 얘기를 하고 있는지"를 상태로 들고 다닌다. 핵심 개념만:

- **이어 묻기 해석 우선순위:** ① 명시 ID → ② 활성 하위 항목 → ③ "1번 과제"/"출처 2" 같은 지칭 → ④ 최근 언급. (지어낸 ID는 절대 못 쓴다 — 직전 화면 후보 중에서만.)
- **새 검색 vs 이어가기 구분:** "신동구 *연구자*"처럼 이름+축이 명시되면 → 새 검색. "2020년 이후는?", "논문만"처럼 조건만 바꾸면 → *현재 주체가 확정돼 있을 때만* 이어가기.
- **동명이인 처리:** 이름만으로는 한 명으로 못 좁히면(`ambiguous_name_only`) 그 상태를 유지하고, *연도·역할*만 바꾼다고 한 명으로 확정하지 않는다. **소속/기관 단서가 와야** 확정된다.
- **같은 종류 안에서만:** people(사람) 질문에 사람 후보가 없으면 과제로 갈아타지 않는다 — 되묻거나 새로 시작한다.

> 정밀 상태 필드(`publication_status`, `identity_status` 등)는 §6 참조.

## 5. 함정 ④: "상세 보여줘"의 위험 (Detail Guard)

**막으려는 사고:** 대상이 여럿/불명확한데 "상세"를 누르면, 아무 거나 하나 골라 상세로 보여주거나, 상세 대신 17~20건짜리 목록을 쏟아내는 것.

- "상세/detail"은 **대상이 정확히 하나로 확정될 때만** 실행한다. 확정 근거 = 명시 ID / 직전 화면 맥락 / 화면에 보인 목록.
- 개수가 "1개"로 정규화돼도, **단일 후보가 확인 안 되면 실행하지 않는다.**
- "상세", "정보", "설명" 같은 *말만으로는* detail이 되지 않는다 — 실제 **앵커(과제번호 같은 키)** 가 있어야 한다. (`apps/planner/query_intent.py`의 `classify_query`)
- detail이 넓은 검색(`SEARCH_RECOVERY`)으로 새지 않는다.

## 6. 정밀 참조 — Smart Coercion 허용/금지

(아래부터는 유지보수자용 상세. 신규자는 건너뛰어도 됨.)

**고쳐도 되는 것:** `limit`/`display_limit`(표시 개수). `action=detail`+`limit>1` → `1/1`. 파서 query materialization, Stage 2 필터 환각 정리(L1 의미 보존). 명시 연구자 anchor+활동 의도 → stagewise planner 건너뛰고 직접 컴파일(`mode=LOOKUP, head=people, action=list, filters.participant_researcher_name=[name], target_cols=[project, perf]`).

**고치면 안 되는 것:** Agent가 확정한 대상 ID·도메인 head, 질의 축·필터의 핵심 의미, 임의의 SEARCH/LOOKUP/JOIN 모드 변경. (예외: 문서화된 결정적 게이트 — `head=people`+명시 연구자 anchor+`list/stats/detail` → `LOOKUP` 고정.) 충돌 시 → Agent에 Observation 반환 또는 사용자 명확화.

## 7. 정밀 참조 — Qdrant 질의 컴파일러

- `participant_researcher_name` 필터는 **확정된 인명**이 `people_terms_to_keep`에 있을 때만 생성.
- 인물 탐색(`people_terms_to_keep=[]`)에서 역할/이력 단어(전문가, 박사급, 후보)는 인명 필터로 승격하지 않는다 — 의미 축(`retrieval_query`)에 남긴다.
- `postprocess.kind=people_discovery`는 신호일 뿐 — 런타임은 project/perf 근거 + `people_preview`를 검색. 인물 후보 projection은 아직 출력 계약으로 승격되지 않음.

## 8. 정밀 참조 — 이어가기 상태 필드 & 저장 키

- **다음 턴 참조 진실 = 단일 키** `conversation:v3:{conversation_id}:session_memory.current_context`. 레거시 키(`v2:*:history`, `last_canonical_evidence`, `view_state` 등)는 로드 폴백이 아니다. Answer/runtime 노드는 명시적 `next_current_context`를 발행해야 하며, 없으면 `EmptyContext`로 fail-closed.
- `SubjectQueryContext.publication_status`: `answer_published` / `answer_withheld_subject_retained` / `clarification_pending`. 내부 오류(`planner_error`/`tool_error`/`schema_error`/`provider_error`)는 새 컨텍스트를 저장하지 않는다.
- `SubjectQueryContext.identity_status`: `ambiguous_name_only`(후보 잔존) / `resolved_with_org`(소속으로 단일) / `resolved`(완전 단일). `ambiguous_name_only`는 비-신원 정제(연도·역할)로 자동 승격 안 됨.
- State card가 노출하는 follow-up 계약: `subject_publication_status`, `subject_identity_status`, `subject_identity_candidate_count`, `subject_continuity_retained`, `answer_publishability`, `subject_refinement_allowed`, `current_subject_confidence`.
- 엔티티 종류 키워드/지시 패턴은 `apps/conversation/entity_registry.py` Pydantic 레지스트리. 로드는 중복 kind·빈 키워드·잘못된 정규식·미승인 키워드 충돌 시 **실패해야 한다.**

## 9. 정밀 참조 — Detail Groundedness 계약 (2026-05-12)

`output_type=detail`은 `detail_structured` 정책으로 검증. **canonical evidence에 1:1 매핑되는 구조 축만** 검증: `pjt_id`, `pjt_no`, `year`, `lead_org_name`, `budget`, `period`. 자유서술(`summary`/`goal`)의 수치(`ISO 20000`, `13만 건`, `17개 부처`, `90% 이상`)는 `unsupported_count`를 유발하면 안 된다. 검증 통과를 위해 `pjt_no`를 지원 `pjt_id` 집합에 추가하지 않는다.

## 10. canonical evidence 경계 (불변)

DB 원본 payload는 **절대 프롬프트에 직접 들어가지 않는다** — 반드시 `canonical_evidence` + `render_profile`을 거친다. 답변 프롬프트엔 안전한 사실 필드만 노출하고, 검색 메타데이터 전체를 노출하지 않는다.
