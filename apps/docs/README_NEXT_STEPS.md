# NEXT STEPS

1. repo 루트에 `AGENTS.md` 복사
2. `docs/` 아래 파일 4개 복사
3. Watcher / Improver / Architect 자동화 프롬프트 뒤에 `docs/CODEX_AUTOMATION_APPEND.md` 내용을 덧붙임
4. 첫 1주일은 자동 머지 금지
5. 첫 3회 실행은 아래 순서로 검토
   - Watcher: 실제 파일 경로 / 테스트 명령 / 위험 지도 만들기
   - Improver: 가장 작은 P0/P1 한 건만 패치
   - Architect: ADR 1개만 작성
6. SESSION_HANDOFF.md를 매 실행마다 append
7. GOLDEN_TESTS.md를 실제 테스트 코드로 옮기기
