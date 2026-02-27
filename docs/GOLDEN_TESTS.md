# GOLDEN_TESTS (Planner/Policy 회귀 기준)

본 문서는 플래너 전략 JSON(`mode/action/relation/join_key_mode`)과 프로젝트 키 정책(`pjt_id`, `pjt_no`)의 회귀 안정성을 보장하기 위한 골든 질의 기준입니다.
릴리즈 전, 아래 골든 질의가 정책과 일치하는지 반드시 검증합니다.

## 1) 골든 질의 축 분류

- **A. SEARCH 토픽형**: 탐색형 질문(누락 방지), 하드 must 필터 최소화.
- **B. LOOKUP 사람/기관형**: 사람/기관명을 이용해 과제/성과를 정확 조회.
- **C. LOOKUP ID 상세형**: `pjt_id` 또는 `pjt_no` 기반 단건/정확 조회.
- **D. JOIN instance/group형**: 과제↔성과 관계형 2-hop 조회.

## 2) 골든 질의 목록 및 기대 구조

> 표기 규칙
> - 필수 필터 키: `필수(예)` / `선택` / `금지`
> - 금지 조건: 정책 위반 시 실패로 간주

### A. SEARCH 토픽형

| ID | 골든 질의 | 기대 mode/action/relation/join_key_mode | 필수 필터 키 | 금지 조건 |
|---|---|---|---|---|
| A-1 | "양자센서 관련 최근 연구 동향 알려줘" | `SEARCH` / `topic` / `null` / `null` | `pjt_id`: 금지, `pjt_no`: 금지 | 이름/기관 기반 LOOKUP 강제 금지, `join_key_mode` 세팅 금지 |
| A-2 | "바이오 소재 국책과제 트렌드 요약해줘" | `SEARCH` / `topic` / `null` / `null` | `pjt_id`: 금지, `pjt_no`: 금지 | 사람명/기관명을 must 필터로 강제하는 행위 금지 |

### B. LOOKUP 사람/기관형

| ID | 골든 질의 | 기대 mode/action/relation/join_key_mode | 필수 필터 키 | 금지 조건 |
|---|---|---|---|---|
| B-1 | "신동구 참여 과제 목록 보여줘" | `LOOKUP` / `list` / `null` / `null` | `pjt_id`: 선택, `pjt_no`: 선택 | 사람/기관 질의를 `JOIN`으로 승격 금지 |
| B-2 | "ETRI 수행 과제만 알려줘" | `LOOKUP` / `list` / `null` / `null` | `pjt_id`: 선택, `pjt_no`: 선택 | `participant_org_name`, `people_affiliation_org_name` 동시 혼용 금지(역할 분리) |
| B-3 | "김재수 연구자의 논문 성과 목록" | `LOOKUP` / `list` / `null` / `null` | `pjt_id`: 선택, `pjt_no`: 선택 | project↔perf 관계 명시 없는 단순 사람 질의를 JOIN으로 처리 금지 |

### C. LOOKUP ID 상세형

| ID | 골든 질의 | 기대 mode/action/relation/join_key_mode | 필수 필터 키 | 금지 조건 |
|---|---|---|---|---|
| C-1 | "pjt_id 1711000001 과제 상세 보여줘" | `LOOKUP` / `detail` / `null` / `null` | `pjt_id`: 필수(예), `pjt_no`: 금지 | 동일 전략에서 `pjt_id`/`pjt_no` 동시 강제 금지 |
| C-2 | "pjt_no 1345000012 과제 목록" | `LOOKUP` / `list` / `null` / `null` | `pjt_no`: 필수(예), `pjt_id`: 금지 | `join_key_mode` 설정 금지 |

### D. JOIN instance/group형

| ID | 골든 질의 | 기대 mode/action/relation/join_key_mode | 필수 필터 키 | 금지 조건 |
|---|---|---|---|---|
| D-1 | "1711015550 과제의 논문 성과 보여줘" | `JOIN` / `list` / `project_perf` / `instance` | `pjt_id`: 필수(예), `pjt_no`: 금지 | JOIN에서 ids_map에 `pjt_id`와 `pjt_no` 동시 존재 금지(XOR 위반) |
| D-2 | "pjt_no 1345000012 그룹 과제에서 나온 특허" | `JOIN` / `list` / `project_perf` / `group` | `pjt_no`: 필수(예), `pjt_id`: 금지 | group 모드에서 Hop2 must를 `pjt_id`로 구성 금지 |
| D-3 | "이 성과(perf_id=PERF-22-0001)가 나온 과제" | `JOIN` / `detail`(또는 `list`) / `perf_project` / `instance` | `pjt_id`: 실행단 파생(필수), `pjt_no`: 금지 | relation이 `perf_project`인데 `head=perf`로 고정하는 오류 금지 |

## 3) 기존 테스트 파일 매핑 (파일명 + 검증 포인트)

| 파일 | 검증 포인트 | 골든 질의 축 연계 |
|---|---|---|
| `tests/test_server3_solar_refine_policy.py` | 우선 컨텍스트 필드에 `pjt_id`, `pjt_no` 노출 유지(식별 키 표시 회귀 방지) | C, D |
| `tests/test_rag_mapper_title_preserve.py` | mapper 단계에서 제목 fallback/보존과 `pjt_id` 메타 유지 | C |
| `tests/test_server3_title_fallback.py` | 응답 조립 시 title fallback 동작 일관성 | A, B(간접) |
| `docs/project_key_env_contract.md` | `RAG_KEY_PJT_ID`, `RAG_KEY_PJT_NO` 분리 계약 및 LOOKUP ID 완화 계약 | C, D |

> 참고: 현재 저장소에는 플래너 JSON의 `mode/action/relation/join_key_mode`를 직접 단정하는 전용 테스트가 부족합니다. 본 문서를 기준으로 추후 `tests/test_planner_golden_queries.py`(가칭) 추가를 권장합니다.

## 4) 릴리즈 규칙: 정책 변경 시 체크리스트 (추가/수정/삭제)

정책/프롬프트/필터 계약이 바뀌면 아래 항목을 **릴리즈마다 반드시 갱신**합니다.

- [ ] **추가(Add)**: 신규 질의 유형이 생기면 해당 축(A/B/C/D)에 골든 질의 1건 이상 추가.
- [ ] **수정(Update)**: 기존 질의의 기대 `mode/action/relation/join_key_mode`가 바뀌면 표와 금지 조건을 함께 수정.
- [ ] **삭제(Remove)**: 더 이상 지원하지 않는 정책은 질의를 삭제하고 삭제 사유(버전/이슈 링크)를 하단 변경 이력에 기록.
- [ ] **매핑 동기화**: 관련 테스트 파일 매핑 표의 검증 포인트를 실제 테스트 상태와 일치시킴.
- [ ] **키 계약 검증**: `pjt_id`, `pjt_no` 필수/금지 규칙이 JOIN/LOOKUP 정책과 충돌하지 않는지 재확인.
- [ ] **릴리즈 게이트**: PR 머지 전 최소 1회 문서 리뷰 체크(플래너 담당 + 검색 담당) 완료.

### 변경 이력

- 2026-02-27: 초안 작성 (A/B/C/D 4축 골든 질의 분류, 테스트 매핑, 릴리즈 체크리스트 추가)
