# NTIS RAG 검색전략 설계 문서 v1.1  
*(SEARCH / LOOKUP / JOIN + LLM Planner — 사용자 질의 현실 대응 강화판)*

작성일: 2026-02-09 (Asia/Seoul)

세션 패치 템플릿: [docs/SESSION_PATCH_TEMPLATE.md](docs/SESSION_PATCH_TEMPLATE.md)

---

## 1. 목적

NTIS 도메인(과제/성과) RAG에서 질의 유형에 따라 **SEARCH / LOOKUP / JOIN**을 명확히 분리하고,  
LLM 플래너가 **단 하나의 Strategy(JSON 계약)** 를 확정하면 실행 레이어(retrieval / filters / rerank)는 이를 **재해석·재결정하지 않고 그대로 수행**한다.

목표:
- **일관성**: 같은 입력 → 같은 전략/필터/결과 경향
- **재현성**: 로그만으로 “왜 이렇게 나왔는지” 재구성 가능
- **디버깅 가능성**: 문제 발생 시 “플래너 vs 실행 레이어” 책임 분리

---

## 2. 데이터 전제 및 키 정의 (동일 + 계약 보강)

### 2.1 데이터 범위
- 데이터는 **과제(project)** 와 **성과(perf)** 로 구성
- 모든 문서에 참여 객체 포함
  - 참여인력: `prtcp_mp[]`
  - 참여기관: `prtcp_org[]`

### 2.2 과제 키 (불변 계약)
- `PJT_ID`: 과제 고유번호 (**단일 시행 인스턴스 키**)
- `PJT_NO`: 동일 과제를 연도별로 시행한 인스턴스를 묶는 **그룹 ID**

#### 📌 계약 추가
- 모든 **project 문서 payload 최상위**에 `pjt_id`가 **반드시 존재**
- 없다면 **데이터셋 오류로 간주**하며, **fallback 금지**

### 2.3 기관 필드 의미 (3종 기관 구분: 필수)
기관 조건은 의미가 달라 filters에서 반드시 분리한다.

| 의미 | filters 키 | 실제 필드(매핑) |
|---|---|---|
| 수행기관(메인 수행기관) | `lead_org_name` | `org_nm` |
| 참여기관(공동 참여기관) | `participant_org_name` | `prtcp_org[].org_nm` *(nested)* |
| 참여인력 소속기관(affiliation) | `people_affiliation_org_name` | `prtcp_mp[].blng_org_nm` *(nested)* |

### 2.4 하이브리드 검색 구성
- Qdrant 하이브리드:
  - sparse: **BM25** (named vector: `bm25`)
  - dense: **e5*** (named vector: `e5*`)
- 최종 정렬:
  - Qdrant 랭킹 + 리랭커(약한 RRF + 강한 키워드/소프트필터/패밀리 보너스)

---

## 3. 핵심 원칙 (불변 계약)

### 3.1 플래너 단일·불변 계약
- 플래너는 질의마다 **단 하나의 Strategy** 출력
- 실행 레이어는 `mode / relation / filters`를 **재결정하지 않는다**
- 허용: **전략 컴파일**
  - `filters → Qdrant Filter` 변환
  - topK 적용
  - rerank 실행

### 3.2 SEARCH vs LOOKUP/JOIN 역할 고정

| Mode | 목적 | 서버단 필터(server-side must) |
|---|---|---|
| SEARCH | 탐색 / 누락 방지 | ❌ must 금지 |
| LOOKUP | 정확 조회 | ✅ must 허용 |
| JOIN | 관계형 2-hop | ✅ must 필수 |

---

## 4. Mode 정의 + 사용자 질문 유형 매핑

### 4.1 SEARCH (탐색형 – 누락 방지 최우선)

#### 언제 SEARCH인가?
다음 중 하나라도 해당하면 SEARCH:

1) **ID 없음**  
- `PJT_ID / PJT_NO / 성과ID` 없음  
- 예: “AI 관련 과제”, “딥러닝 논문”, “해양 신발 자동화 개발”

2) **토픽/키워드 기반 질의**  
- 명사 다수, 긴 문장, rare token 다수  
- 예: “신발 생산 자동화기기 개발”, “해양 로봇 협력 항법 무선 인지 네트워크”

3) **관계형이지만 기준 불명확**  
- refer 대상이 명확하지 않음  
- 예: “그 과제의 성과”, “이 논문이랑 관련된 연구”

#### SEARCH 필터 원칙 (강제)
- ❌ **server-side must 금지**
- 허용:
  - `must_not`: 명백히 다른 컬렉션/도메인 제외 정도
  - 사람/기관/성과유형(tag): **리랭커 보너스**로만 처리
- 📌 **SEARCH에서 사람 이름이 must로 들어가면 설계 위반**

---

### 4.2 LOOKUP (정확형 – 사용자 실질 요구 최다)

#### 언제 LOOKUP인가?
다음 중 하나면 **무조건 LOOKUP 우선**:

A) **ID 기반 질문**  
- 예: “1711015550 과제 상세”, “REP-2025-0001 보고서”
- 요구: `ids_map.pjt_id` / `ids_map.rct_id` **must**

B) **목록/통계/상세 요청**  
- 예: “ETRI 과제 목록”, “김재수 참여 과제 몇 건?”, “2021~2023 논문 통계”
- 규칙: `action = list / detail / stats → LOOKUP`

C) **사람/기관 기반 질문 (중요)**  
- 예: “김재수 참여 과제”, “ETRI 수행 과제”, “삼성 참여 성과”
- 📌 이유:
  - 사람/기관은 모든 문서 내부 객체로 존재
  - SEARCH로 보내면 **이름 토큰 오염**으로 엉뚱한 결과 섞일 위험 큼

#### LOOKUP server-side 필터 우선순위
1) `PJT_ID == X`
2) `PJT_NO == X`
3) `PJT_NO == X AND stan_yr == Y`
4) 성과 식별자 (`doi / issn / patent_no / perf_id` 등)

#### 사람/기관 필터 강도 규칙
- ID 있음 → **must**
- 이름만 있음 →
  - `should`(정규화/변형 포함)
  - `min_should=1` (**하드 게이트**)
- 📌 동명이인 가능성 ↑ → `confidence` ↓ + 추가단서 유도

---

### 4.3 JOIN (관계형 – 진짜 필요한 경우만)

#### JOIN 정의
- 한쪽 엔티티 기준으로 반대쪽 엔티티를 가져오는 **2-hop**
- 기본은 **project ↔ perf 전용**
- 📌 사람/기관 → 과제/성과는 기본 JOIN 아님 (**LOOKUP**)

---

## 5. JOIN 세부 분기 + 사용자 질문 대응

### 5.1 project → perf

#### (A) 인스턴스 기반 (PJT_ID 있음)
- 예: “1711015550 성과 목록”
- Hop2(perf): `pjt_id == PJT_ID` **must**
- `join_key_mode=instance`는 `ids_map.pjt_id`가 있거나 Hop1에서 pjt_id를 추출 가능한 경우만 허용
- `join_key_mode=instance`인데 `ids_map.pjt_no`만 존재하면 **계약 위반으로 실행 전 차단**

#### (B) 그룹 기반 (PJT_NO만 있음)
- 예:
  - “PJT-2020-XXXX 동일과제 성과 전체”
  - “연도별 성과 이력”
- Hop1(project): `pjt_no == X`로 인스턴스들 조회 → **PJT_ID 목록 확보**
- `join_key_mode=group`은 `ids_map.pjt_no`가 있을 때만 허용
- `join_key_mode=group`인데 `ids_map.pjt_no`가 비어 있으면 정규화 단계에서 `instance`로 보정하고 파싱 경고를 남김
- Hop2(perf):
  - 최우선: `pjt_no == X` **must** (perf에 저장돼 있다면)
  - fallback(정상 설계 내 fallback): `pjt_id IN {Hop1에서 얻은 PJT_ID들}` **must**

> 주의: 여기서의 “fallback”은 **전략 변경이 아니라**, 동일 JOIN 전략 내에서 **정해진 우선순위**로 필터를 선택하는 것을 의미한다. (Mode 변경/SEARCH 재시도 같은 fallback은 금지)

### 5.2 perf → project
- 예: “이 논문이 나온 과제”
- Hop1(perf): 성과 식별자 **must**
- Hop2(project): `perf.pjt_id → project.pjt_id` **must**
- 요청 시 그룹(PJT_NO) 확장 가능

---

## 6. 기관/인물 필터 매핑 (동일 + 실전 예시)

| 사용자 표현 | filters |
|---|---|
| “ETRI 수행 과제” | `lead_org_name=["ETRI"]` |
| “삼성 참여 과제” | `participant_org_name=["삼성"]` |
| “ETRI 소속 연구자 과제” | `people_affiliation_org_name=["ETRI"]` |
| “김재수 참여 과제” | `participant_researcher_name=["김재수"]` |

추가:
- 참여인력 ID가 있으면: `participant_researcher_id=["...hm_id..."]` must

---

## 7. 하이브리드 + 리랭킹 정책 (운영 관점)

### SEARCH
- 후보 넓게(topK↑)
- 필터는 **보너스/게이트(soft)**

### LOOKUP/JOIN
- server-side 필터로 **오염 차단**
- 하이브리드 검색은 **항상 유지**
- 📌 LOOKUP이라도 **BM25-only 금지**

---

## 8. 플래너 출력 계약 (v1.1)

```json
{
  "mode": "SEARCH|LOOKUP|JOIN",
  "head": "project|perf|people|org|support",
  "relation": "project_perf|perf_project|null",
  "target_cols": ["project","perf"],
  "join_key_mode": "instance|group|null",
  "ids_map": {},
  "filters": {},
  "limit": 20,
  "retrieval_query": "...",
  "confidence": 0.92
}
```

권장(선택, 운영 편의):
- `action` 필드 명시(`list|detail|stats|topic`)

필수 계약(코드 동기화):
- `mode=JOIN`이면 `join_key_mode`는 `instance|group` 중 하나여야 함
- `join_key_mode=group`이면 `ids_map.pjt_no`가 필요 (`pjt_id` only는 허용 불가)
- `join_key_mode=instance`이면 `ids_map.pjt_id`를 사용해야 하며 `pjt_no` only는 계약 위반

---

## 9. 사용자 질문 → 전략 매핑 (강화)

| 사용자 질문 | mode | 비고 |
|---|---|---|
| “AI 관련 과제” | SEARCH | 토픽 |
| “김재수 참여 과제” | LOOKUP | 사람 |
| “ETRI 수행 과제” | LOOKUP | 기관 |
| “1711015550 과제 상세” | LOOKUP | ID |
| “1711015550 성과” | JOIN | instance |
| “PJT-2020-XXXX 성과 전체” | JOIN | group |
| “신발 생산 자동화 개발” | SEARCH | 긴 질의 |
| “2021~2023 ETRI 논문 통계” | LOOKUP | stats |

---

## 10. 운영 체크포인트 (현실판)

- [ ] 사람 이름 질의가 SEARCH로 가지 않는가  
- [ ] LOOKUP에서 이름은 `min_should=1`로 게이트되는가  
- [ ] JOIN에서 `PJT_ID / PJT_NO` 분기가 코드로 고정돼 있는가  
- [ ] fallback 전략(모드 변경/재시도)이 존재하지 않는가  
- [ ] Strategy가 하위 레이어에서 수정되지 않는가  
- [ ] project payload 최상위 `pjt_id`가 항상 존재하는가(없으면 데이터 오류로 처리되는가)

---

## 부록 A. “설계 위반” 예시 (금지 패턴)

- SEARCH인데 사람 이름이 server-side must로 들어감  
- JOIN인데 `pjt_id`와 `pjt_no`를 동시에 사용/혼합  
- LOOKUP/JOIN인데 BM25-only(또는 dense=0)로 우회  
- `pjt_id`가 payload 최상위에 없는데도 “대충 다른 키로 찾아서” 진행(데이터 오류 은폐)

