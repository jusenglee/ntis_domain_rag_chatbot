# RAG 운영 환경 변수 가이드 (검색 전략/계약 중심)

본 문서는 **검색 전략(SEARCH/LOOKUP/JOIN)·플래너 계약·운영 토글**과 직접적으로 관련된 환경변수만 우선 정리합니다.
(세부 성능 튜닝 파라미터는 별도 섹션에 요약)

작성일: 2026-02-23 (Asia/Seoul)

---

## 1. 모델 컨텍스트/출력 토큰

```bash
# Gemma 컨텍스트 길이 (Triton/VLLM 설정과 동일하게 맞춰주세요)
export GEMMA_MAX_MODEL_LEN=32768

# Gemma 출력 토큰 상한 (운영 요구사항에 맞게 조정)
export GEMMA_MAX_TOKENS=8192
```

---

## 2. 컬렉션 제어

### 2.1 allowlist (검증 목적)

```bash
# 플래너가 선택할 수 있는 컬렉션 허용 목록(검증용)
# 기본값: "ntis_project_v1,ntis_perf_v1"
export RAG_COLLECTION_ALLOWLIST="ntis_project_v1,ntis_perf_v1"
```

- allowlist는 **실행 target_cols를 재결정하지 않습니다.**
- 플래너가 내린 target_cols가 allowlist 밖이면 “계약/정책 위반”으로 로그/차단할 수 있습니다.

### 2.2 강제 컬렉션(운영 스위치)

```bash
# 강제로 특정 컬렉션만 검색하게 제한(운영 긴급 스위치)
# 빈 값이면 비활성
export RAG_SEARCH_COLLECTIONS="ntis_project_v1"
```

---

## 3. 계약/스키마 엄격도

```bash
# 플래너 전략(mode/relation/target_cols/filter_spec)의 “단일·불변”을 강제(기본 1)
export RAG_STRICT_STRATEGY_CONSISTENCY=1

# intent_payload 스키마가 어긋나면 즉시 예외로 중단(기본 0)
export RAG_FAIL_FAST_SCHEMA=0
```

---

## 4. LOOKUP 필터 정책(정확도/재현성)

```bash
# LOOKUP에서 사람/기관 등 조건을 server-side로 어느 정도 강제할지
# - hard: 가능한 한 must/게이트를 강제(기본)
# - must_one_then_should: 단일 사람 이름이면 must 승격 + 나머지는 should
# - off: 서버단 필터를 최소화(권장하지 않음)
export RAG_LOOKUP_FILTER_POLICY=hard

# LOOKUP에서 title_terms 적용 정책
# - soft: should로 적용(기본)
# - hard: must로 적용(단, detail lookup에서만 허용)
export RAG_LOOKUP_TITLE_FILTER_POLICY=soft
```

---

## 5. relation 힌트 처리(SEARCH/LOOKUP에서 relation이 내려오는 경우)

```bash
# mode=SEARCH/LOOKUP인데 relation 힌트가 존재할 때의 처리
# - filter: relation을 JOIN으로 실행하지 않고, 가능한 범위에서 필터로만 반영(기본)
# - join: JOIN 실행을 허용(권장: 제한적 운영/실험에서만)
export RAG_RELATION_LOOKUP_POLICY=filter
```

---

## 6. promotion(SEARCH 결과 기반 자동 승격)

```bash
# 기본 정책: disable (현재 구현은 enable이어도 passthrough)
export RAG_PROMOTION_MODE=disable
```

---

## 7. 검색 실패 시 fallback chat (계약 위반/운영 예외)

```bash
# 기본 0: “검색 실패 시 chat fallback 없음” 계약 유지
# 1이면 결과 계약 위반 시에도 fallback chat을 허용(운영 예외/긴급용)
export RAG_FORCE_FALLBACK_CHAT=0
```

---

## 8. (요약) 성능 튜닝 주요 파라미터

- topK:
  - `RAG_TOPK_DENSE`, `RAG_TOPK_LEX`, `RAG_TOPK_LEX_CAND`
- rerank:
  - `RAG_RERANK_K`, `RAG_RRF_K`
- 컨텍스트 빌드:
  - `RAG_MAX_CONTEXT_ITEMS`, `RAG_CTX_HARD_LIMIT`, `RAG_CTX_PER_DOC_MAX_CHARS`

> 상세 튜닝은 “검색 정책(preset) 변경”으로 분류되며, 변경 시 `policy_version`(SEARCH_POLICY_VERSION) 증가를 권장합니다.