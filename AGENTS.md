# AGENTS.md (AI Agent Operating Guidelines)

이 저장소에서 작업하는 AI 에이전트(Codex, Claude 등)가 반드시 준수해야 하는 운영 지침이다.

---

## 1. 지식 베이스 (Knowledge Base)

시스템의 설계 원칙과 비즈니스 로직은 아래 문서를 최우선 진실원(Source of Truth)으로 삼는다. 작업 전 반드시 해당 문서를 먼저 읽고 맥락을 파악한다.

*   **[`apps/docs/README.md`](./apps/docs/README.md)**: 전체 문서 인덱스
*   **[`apps/docs/01_ARCHITECTURE.md`](./apps/docs/01_ARCHITECTURE.md)**: 권한분리 3계층 (ADR-0018)
*   **[`apps/docs/02_CONTRACTS_AND_RULES.md`](./apps/docs/02_CONTRACTS_AND_RULES.md)**: SearchTask, 식별자(pjt_id/no), FinalGuard 검증
*   **[`apps/docs/ADR/ADR-0018_Three_Layer_Authority_Separation.md`](./apps/docs/ADR/ADR-0018_Three_Layer_Authority_Separation.md)**: 현행 아키텍처 결정 (필독)

---

## 2. 작업 기본 원칙

*   **Surgical Changes:** 코드 수정 시 가급적 작고 안전한 변경을 선호한다. 근거 없는 대규모 리팩토링은 지양한다.
*   **Inspect Before Patch:** 수정 전 반드시 관련 파일과 테스트 코드를 먼저 분석한다.
*   **Validation First:** 변경 후에는 문법 오류, 임포트 오류 등을 확인하고 가능하다면 관련 검증 스크립트를 실행한다.
*   **No Faking:** 코드나 문서가 뒷받침하지 않는 결론을 임의로 내리지 않는다. 불확실한 경우 명확히 밝힌다.

---

## 3. 핵심 도메인 제약 (Critical Constraints)

*   **`pjt_id` vs `pjt_no`**: 둘은 다른 의미를 가진 식별자다. 절대 혼용하지 않는다.
*   **Organization Roles**: 수행기관, 참여기관, 인력 소속기관은 서로 다른 의미를 가진다.
*   **No Raw Dumps**: 검색 결과의 raw payload를 프롬프트에 그대로 넣지 않는다. 반드시 `canonical_evidence`를 거쳐야 한다.

---

## 4. 작업 보고 형식 (Reporting Format)

모든 작업 완료 후에는 아래 항목을 포함하여 보고한다.

1.  **작업 내용:** 무엇을 수정/추가했는지
2.  **변경 사유:** 왜 해당 방식이 최선이라고 판단했는지
3.  **검증 결과:** 어떤 확인 과정을 거쳤는지 (Syntax, Import, Test 등)
4.  **잔존 리스크:** 작업 후 발생할 수 있는 잠재적 이슈나 추가 검토가 필요한 부분
5.  **다음 단계:** 이어서 수행하면 좋은 작업 제안

---

## 5. 금지 사항

*   UTF-8 이외의 인코딩 사용 금지 (한글 깨짐 주의).
*   SEARCH / LOOKUP / JOIN의 아키텍처적 경계를 임의로 무너뜨리는 행위.
*   `apps/docs` 문서와 일치하지 않는 방향으로의 코드 수정.
