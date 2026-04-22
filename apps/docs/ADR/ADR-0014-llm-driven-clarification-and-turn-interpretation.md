# ADR-0014: LLM-Driven Clarification & Turn-Interpretation Layer

## Status
Proposed

## Date
2026-04-22

## Context

현재 NTIS RAG의 "대화 제어 축"은 다단계 heuristic을 먼저 돌리고, 실패했을 때만 LLM을 fallback으로 호출하는 구조다. 이 구조는 오답 위험이 낮은 대신 사용자 체감이 "정적 챗봇"에 가깝다.

다음 두 현상이 동시에 관측됐다.

1. **Disambiguation 라벨 중복** — 동일 이름·동일 kind의 후보 2개가 구분자 없이 `"신동구 (people), 신동구 (people)"`로 노출됨. 사용자는 선택이 불가능하고, LLM-as-selector도 식별 근거가 없다.
2. **Clarification 멘트의 정적성** — 질문 맥락, 직전 focus, 최근 언급 엔티티와 무관하게 동일 하드코딩 문구가 반복된다. LLM은 "판단"에만 관여하고, "말하기(phrasing)"에는 관여하지 않는다.

1번은 `apps/conversation/turn_interpreter.py::build_clarification_payload` (L605-637)에서 라벨이 `f"{label} ({candidate.entity_kind})"`로만 합성되기 때문이다. candidate dedup은 `_candidate_id` (`source + identity_key`)에서 수행되지만, `source`가 다르거나 `person_no`가 다른 두 candidate는 서로 다른 것으로 남고, 표시 레이어에서는 차이가 사라진다. 2번은 `turn_interpreter.py`, `scope_resolver.py`, `followup_resolution.py` 세 파일 전반에 걸쳐 clarification/notice 메시지가 문자열 리터럴로 분산돼 있기 때문이다.

LLM은 이미 두 지점에 존재한다.

- `apps/conversation/turn_interpreter.py` — LLM turn interpretation (reason: `llm_interpretation`)
- `apps/conversation/context_router.py::run_context_router` — heuristic→LLM fallback (`llm_recent_mentions` source, confidence threshold 0.45)

그러나 두 경로 모두 "heuristic이 unresolved/ambiguous를 낼 때만" 동작하므로, 명확해 보이는 후속 질문 대부분에서 LLM이 스킵된다.

본 ADR은 retrieval semantics, planner strategy, L1 의도 계약, pjt_id/pjt_no 분리, raw-payload 비주입 원칙을 전부 보존하면서, 대화 제어 축의 다음 세 영역에 LLM 판단/자연어화를 확장한다.

- (A) Disambiguation 후보 라벨 enrichment
- (B) Clarification prose 자연어 생성
- (C) 후속 질문 해석의 LLM-first화 (heuristic은 검증자로 강등)

## current_state_summary

### 후속 해석 경로
1. `request_facade.build_intent_payload` → hard signal precheck → `turn_trigger` / `turn_interpreter` / `turn_policy`
2. `scope_resolver` → `context_router` (deterministic + LLM fallback) → router anchor
3. `node_rag_search` 진입 → `resolve_followup_from_facts` (evidence layer)
4. retrieval 수행 → `upsert_raw_payload_records`

### LLM 호출 조건
- `turn_interpreter`: `resolve_hard_followup_signal`이 `HardResolutionResult(matched=True)`를 내지 못하고 candidate가 다수일 때만 LLM interpretation 수행.
- `context_router`: deterministic matcher가 `resolved`를 내면 LLM 호출 생략. LLM confidence < 0.45면 heuristic으로 회귀.

### 하드코딩된 안내 문구 (예시, 인벤토리는 별첨 가능)
- `turn_interpreter.py:626` — `"어떤 대상을 가리키는지 확인해 주세요. 예: {names}"`
- `turn_interpreter.py:628` — `"직전 답변은 번호/출처 재사용이 가능한 publishable 목록이 아니었습니다. 대상을 다시 지정해 주세요."`
- `turn_interpreter.py:630` — `"이전 대화의 어떤 대상을 가리키는지 다시 지정해 주세요."`
- `scope_resolver.py:833-835` — `"최근 언급한 과제는 묶음 수준 식별자만 있어..."` / `"최근 언급한 대상이 여러 개입니다..."`
- `followup_resolution.py:333-336` — `"현재 상세 안에서 어떤 대상을 뜻하는지..."` 등

### Clarification payload의 정보 손실 지점
`build_clarification_payload` 내부에서 `ClarificationSuggestion.label`은 `f"{display_name} ({entity_kind})"`로 고정된다. candidate가 가진 `ids_map`(`person_no`, `org_id` 등), `parent_subject_ids`, `view_id`, `display_rank`, 최근 턴 등장 여부는 라벨에 반영되지 않는다.

## pain_points

### 1. 구분 불가능한 disambiguation 옵션
동일 `display_name` + 동일 `entity_kind`를 가진 서로 다른 엔티티(실제 다른 인물, 또는 동명이인, 또는 동일 인물이 서로 다른 데이터 소스에서 fan-out된 경우)가 사용자·LLM 양쪽에서 구분 불가능한 상태로 표면화된다. 운영상 "버그"처럼 보이지만, 근본 원인은 **라벨 스키마가 distinguishing signal을 담을 그릇을 제공하지 않음**이다.

### 2. Clarification 멘트의 맥락 무반영
사용자는 직전 턴에 "신동구 연구자 활동내역"을 질의했고, 후속으로 "해당 연구자의 2024년 활동"을 질의했다. anaphora (`해당 연구자`)가 명시적이고 직전 focus도 people 엔티티로 확정됐음에도, disambiguation이 작동했다는 사실 자체가 "heuristic이 anaphora를 해석할 근거를 갖고 있으면서도 불확실성을 그대로 사용자에게 넘긴" 신호다. 그리고 넘기는 과정에서의 메시지가 맥락과 무관하게 정적이므로, 사용자는 "시스템이 이해하지 못했다"는 인상을 받는다.

### 3. LLM이 의미론적 판단에만 참여, 발화에는 불참여
LLM은 이미 (a) turn interpretation (b) context router fallback 두 곳에서 의미론적 판단을 수행한다. 그러나 "판단 결과를 사용자에게 어떻게 전달할지"는 전적으로 코드 레이어가 담당한다. 이 비대칭 때문에, 판단은 점점 더 유연해지는데 발화는 여전히 1세대 챗봇처럼 느껴진다.

### 4. Heuristic-first의 "쉬운 문항 스킵" 함정
heuristic이 resolved를 내면 LLM은 호출되지 않는다. 이 최적화는 비용/latency 관점에서 합리적이지만, 쉬운 문항일수록 "잘 맞추고는 있으나 왜 그렇게 맞췄는지 대화상 근거를 제시하지 않는" 응답이 나오는 경로로 작동한다. 결과적으로 사용자에게는 "정적 응답"으로 체감된다.

## proposed_target_state

### Scope A — Disambiguation 라벨 enrichment

`build_clarification_payload`가 반환하는 `ClarificationSuggestion`의 스키마와 합성 로직을 분리한다.

- **스키마 확장** — 기존 `{label, candidate_id, entity_kind}` + 신규 `disambiguator: Optional[str]`, `aux: Optional[Dict]`. `aux`에는 `person_no`/`org_id`/`pjt_no`/최근 연관 과제 수/대표 기관명 등 view_state에서 안전하게 도출 가능한 값만 담는다. (LLM이 ID를 새로 생성하지 못하도록, aux는 항상 매핑 결과.)
- **신규 모듈** — `apps/conversation/clarification_labeler.py`
  - 호환 entrypoint: `enrich_candidate_labels(*, candidates, view_state) -> list[tuple[candidate, aux, disambiguator]]`
  - ADR helper: `enrich_candidate_labels_for_summary(*, question, candidates, view_state_summary) -> list[tuple[candidate, aux, disambiguator]]`
  - 기본 경로: heuristic으로 `disambiguator`를 조립 (소속기관, 최근 등장 연도, 묶음 ID 접미 등).
  - 여전히 동일 라벨이 2개 이상 남을 때만 LLM을 호출해 `disambiguator`만 생성. prompt에는 **candidate_id, entity_kind, display_name, ids_map, aux**만 주입하고, raw payload는 주입하지 않는다.
  - LLM 실패/timeout/invalid output 시 deterministic heuristic fallback으로 되돌린다.
- **렌더링** — 최종 label은 `f"{label}{' — ' + disambiguator if disambiguator else ''} ({entity_kind})"` 형태로 합성.

불변: ID 생성 금지, ids_map은 candidate에서만 가져옴, dedup 키(`_candidate_id`) 불변.

### Scope B — Clarification prose 자연어화

하드코딩된 clarification/notice 문자열을 **단일 prose 합성 지점**으로 모은다.

- **신규 모듈** — `apps/conversation/clarification_prose.py`
  - `compose_clarification_message(*, question, blocked_reason, suggestions, focus_entity, view_state_summary, ctx) -> str`
  - 경로 1 (heuristic): 기존 리터럴을 테이블화하여 `blocked_reason → template` 매핑. 이것이 "안전 기본값".
  - 경로 2 (LLM prose): 경로 1의 결과를 "seed"로 전달하여 LLM이 더 자연스럽게 paraphrase + 구체 근거 부착. **seed 의미 변경 금지** 제약을 prompt에 명시.
  - prose 호출은 feature flag `LLM_PROSE_ENABLED`로 게이트.
- **호출 지점 재배선**
  - `turn_interpreter.build_clarification_payload` → `compose_clarification_message` 호출.
  - `scope_resolver`의 동명 블록 → 동일 모듈로 위임.
  - `followup_resolution`의 `clarification_reason` 분기 → 동일.

불변: 의미(blocked_reason, 선택 요구, publishability 전제)는 바뀌지 않는다. 예시 후보(`suggestions`)는 반드시 메시지 안에 그대로 포함되어야 한다(prompt-level requirement).

### Scope C — 후속 질문 해석의 LLM-first화

`request_facade.build_intent_payload`와 `turn_interpreter` 내 "hard signal → LLM fallback" 순서를 **"LLM interpretation 먼저, heuristic을 cross-checker로"** 구조로 뒤집는다. 단 조건부:

- **Trigger gate**: view_state 또는 `SessionMemory.current_context` projection에 candidate/context가 존재하고 hard signal이 miss일 때만 LLM-first 활성화. `current_context`가 공식 truth이며, 여기서 복원되는 projection은 view-state-equivalent context로 인정한다. 명시적 ID(`pjt_id` 등 L1에 잡히는 식별자)가 이미 있으면 LLM은 호출하지 않는다.
- **Validator**: LLM이 반환한 `selected_candidate_ids`와 heuristic이 계산한 `sorted(candidates)[:K]`가 교집합이 비면 `ambiguity_reason="llm_heuristic_divergence"`로 ambiguous 처리 → clarification 경로로 폴백. LLM이 heuristic의 top-K 안에 있는 candidate를 고르면 해당 결과를 신뢰.
- **Cost/latency 가드**: 한 턴에 LLM interpretation은 1회만. `context_router` LLM fallback과 double-charge 방지를 위해 interpretation 결과가 `resolved`면 router는 deterministic만 돌리고 LLM 단계는 스킵.
- **Reason 확장**: `TurnInterpretationResult.reason`에 `llm_first`, `llm_first_validator_ok`, `llm_first_validator_diverged` 값을 추가.

불변: `planner strategy output`(mode/relation/target_cols/join_key_mode)은 turn interpreter가 절대 건드리지 않는다. anchor/entity 선택만 한다.

### Observability

- `DISAMBIGUATION.LABEL.ENRICHED` — {duplicate_before, duplicate_after, disambiguator_source: heuristic|llm|fallback, llm_attempted, reason, fallback_reason?}
- `CLARIFICATION.PROSE` — {blocked_reason, seed_template_id, prose_source: template|llm, generation_latency_ms}
- `TURN.INTERPRETATION.LLM_FIRST` — {trigger: view_state|current_context|hard_miss, validator: ok|diverged|skipped, candidate_count}
- 기존 `CONTEXT.ROUTER`, `CONTEXT.ROUTER.FALLBACK`, `FOCUS.ENTITY.SET`, `FOLLOWUP.FACT_RESOLVED`와 동일한 포맷으로 emit.

## non-negotiables (이 ADR도 유지)

- planner strategy는 요청마다 하나이며 immutable이다 (`mode`/`relation`/`target_cols`/`join_key_mode` answer stage·turn interpreter에서 재결정 금지).
- `SEARCH` recall-first, `LOOKUP`/`JOIN` precision-first gating 유지.
- **raw payload는 prompt에 직접 주입되지 않는다.** 라벨 enrichment·prose 합성 모두 view_state 파생 요약만 사용.
- `pjt_id` (instance) ≠ `pjt_no` (group). LLM은 ID를 새로 생성하지 않는다. 라벨의 aux는 항상 ids_map 매핑값.
- context_router 출력에 `mode/relation/target_cols/join_key_mode` 불포함.
- fallback chat escape hatch 금지, BM25-only shortcut 금지.
- Clarification prose는 blocked_reason의 semantics를 바꾸지 않는다. seed → paraphrase만 허용.

## impact_and_tradeoffs

- **장점**
  - 동명 후보 구분 불가 버그가 구조적으로 해결된다 (disambiguator 슬롯 + LLM enrichment).
  - "정적 챗봇 느낌"이 줄어든다 — 판단뿐 아니라 발화에도 LLM이 관여.
  - LLM-first + heuristic validator 구조는 turn interpretation의 정확도/재현 가능성을 유지하면서 유연성을 올린다.
- **비용**
  - LLM 호출 turn당 최대 +2 (Scope A/B는 조건부, Scope C는 view_state 기반 gate). Gemma/Solar 비용으로 환산 시 한계 수준 증가.
  - Latency: Scope B prose는 사용자 체감에 영향 → streaming 구간에 포함되도록 설계 필요.
- **리스크**
  - LLM prose가 seed의 의미를 미묘하게 왜곡할 경우 사용자가 잘못된 가정을 가질 수 있음 → seed 의미 보존 제약 + eval lane 필수.
  - Scope C의 validator divergence가 지나치게 자주 발동하면 "clarification 과잉"이 생길 수 있음 → top-K 교집합 조건의 K를 튜닝 포인트로 명시.

## compatibility

- 기존 API 응답 명세 (`05_API_응답명세.md`) **변경 없음**. 신규 필드 `ClarificationSuggestion.disambiguator`, `.aux`는 optional로 추가.
- 기존 이벤트 이름 모두 유지. 신규 이벤트만 추가.
- `resolve_followup_from_facts`, `run_context_router` signature 변경 없음.
- feature flag 기본값:
  - `LLM_DISAMBIGUATION_LABEL_ENABLED=false` (dark launch)
  - `LLM_PROSE_ENABLED=false` (dark launch)
  - `TURN_INTERPRETATION_LLM_FIRST=false` (dark launch)
- 세 flag 전부 off일 때 런타임 동작은 현행과 동일하다.

## validation

- `py_compile` 로컬 실행: 신규 모듈 + 호출 지점.
- import smoke, `create_app()` smoke.
- Golden regression lane에 다음 케이스 추가:
  - 동명 후보 2개 케이스 → disambiguator가 라벨에 반영되는지.
  - anaphora ("해당 연구자") 후속 질문 → LLM-first가 heuristic top-K와 교집합을 갖는지, clarification 없이 진행되는지.
  - LLM 실패/timeout → 기존 하드코딩 문구로 polyfill되는지.
- Prompt-level eval: seed→paraphrase pair에서 의미 보존 여부 자동 채점 (small held-out set).

## 오픈 이슈 (후속 ADR 후보)

- Scope A의 `aux` 스키마 엄격화 — 현재는 "view_state에서 도출 가능한 값" 수준의 서술. 실제 필드 집합을 ADR-0015에서 고정.
- LLM-first가 자리 잡으면 `context_router` LLM fallback의 존재 의의 재검토. 두 LLM 경로가 역할이 겹치기 시작하면 통합 또는 한쪽 폐기 고려.
- Prose의 국제화(i18n) 전략 — 현행은 ko-KR 단일. 템플릿 ID 도입 시점에서 다국어 경로 분리 검토.
