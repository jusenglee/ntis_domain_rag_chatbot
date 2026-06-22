# 06. 테스트와 회귀

> "뭘 돌리면 '안 깨졌다'가 보장되나"를 다룬다. **먼저 §0의 경고를 읽어라** — 이 브랜치의 테스트 트리에는 함정이 있다.

## 0. ⚠️ 먼저 알아야 할 것 (인수자 필독)

- `tests/`는 `.gitignore`에 들어 있어 **버전관리되지 않는다**(로컬 디스크에만 있음). 인수 후 "테스트를 버전관리에 넣을지" **정책 결정**이 필요하다.
- 현재 로컬 `tests/`에는 **다른 브랜치용 테스트가 섞여** 있다. 그래서 그냥 `pytest tests`를 돌리면 수집 에러로 멈춘다:
  - `tests/pipeline/`(23개)는 이 브랜치에 없는 `apps.pipeline`을 임포트 → 수집 에러.
  - `tests/test_stream_model_key_contract.py`도 없는 심볼 임포트 → 수집 에러.
  - **이 둘을 제외하면 38개가 깨끗하게 통과(38 passed)** — 이게 이 브랜치의 실효 게이트다.

## 1. 그래서 뭘 돌리나 (검증 게이트)

운영 게이트는 **스모크 + 타깃 pytest 서브셋**이다(전체 수집 아님).

```powershell
$env:PYTHONPATH='.'
# 스모크
python -m compileall -q apps
python -c "from apps.api.app_factory import create_app; create_app()"
# 이 브랜치에서 실제로 도는 테스트만 (foreign pipeline 제외)
python -m pytest tests --ignore=tests/pipeline --ignore=tests/test_stream_model_key_contract.py -q -p no:cacheprovider
```

실효 테스트 위치: `tests/`(루트 4개) + `tests/conversation/`. 환경: conda env `ntis_domain_rag_chatbot`(Python 3.12) — 전역 python엔 의존성이 없을 수 있다.

## 2. 절대 깨지면 안 되는 불변식

코드를 고친 뒤 이게 여전히 참인지 확인한다. (왜 중요한지는 [02 계약](./02_CONTRACTS_AND_RULES.md) 참고.)

- `SEARCH/LOOKUP/JOIN` 의미는 안 바뀐다.
- 맨 앞 판단(Dialogue Agent)의 의도(Action/Subject/Axes)를 하위가 거부하지 않는다.
- `pjt_id`/`pjt_no` 혼용 금지, canonical evidence 경계 유지.
- 이어 묻기에서 출처/목록/현재대상 진실이 보존된다.
- 보정(Smart Coercion)은 표시/개수에만 한정.
- detail = `1/1 정규화 + 단일 후보 가드`.

## 3. 정밀 참조 — Agentic 골든 게이트 (G1–G11)

수용 기준: **현재 주체 이어 묻기는 되묻지 않고 바로 도구 호출로 이어진다.**

- **G1/G2** 연구자 활동 후 기간 정제 → `refine_current_subject`(`year_from`/`year_to` 보존).
- **G3** "연구책임자로 활동한 과제만" → `refine_current_subject`(`role=연구책임자`, `target=project`).
- **G4** 되묻기 후 짧은 이름 입력 → 새 질의가 아닌 직전 미해결 제약의 주체 보충.
- **G5** 현재 주체 없음 + 동명이인 다수 → `ask_user_for_clarification`.
- **G6** `PJT_NO=...` 성과 전체 → `pjt_no` group 의미 유지, `pjt_id` 날조 금지.
- **G7** 기관 활동 후 특정 과제 상세 → 현재 화면 후보로 동작(특허/성과 목록이 과제 detail로 새면 실패).
- **G8** 제목 지정 과제 detail이 broad project search 17건으로 새면 실패.
- **G9** 파서 불일치로 detail count가 20으로 덮이면 실패.
- **G10** 단일 후보 미확정인데 LLM detail 스트리밍 시작하면 실패.
- **G11** `output_type=detail` 자유서술 수치(`ISO 20000`, `13만 건`, `17개 부처`, `90% 이상`)를 `unsupported_count`로 판정하면 실패(detail groundedness는 `pjt_id`/`pjt_no`/`year`/`lead_org_name`/`budget`/`period`만 검증).

## 4. 정밀 참조 — UX 회귀 신호

현재 주체 정제 중 아래 문구가 나오면 회귀(이어 묻기를 못 알아들은 것):
`무엇을 기준으로 좁힐지 먼저 목록이나 상세 대상을 정해 주세요.` / `질문을 조금 더 구체적으로 작성해 주세요.` / `대상을 다시 지정해 주세요.`

Shock Absorber 회귀 상태: `1 items`, `detail_count_contract_violation`, `SEARCH_RECOVERY on detail-like query`, `blocked_state_consistency after unrelated list expansion`.
