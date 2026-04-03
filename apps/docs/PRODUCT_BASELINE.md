# Product Baseline

이 문서는 historical product baseline 메모다.

## 현재 상태
- manifest-driven pytest baseline은 retired 상태다.
- `scripts/run_baseline_checks.ps1`는 current release gate가 아니다.
- 현재 worktree의 active gate는 smoke validation이다.

## current gate
- `py_compile`
- import smoke
- `create_app()` smoke
- optional workflow graph smoke when `langgraph` is available

## historical note
- 과거 pytest/baseline 성공 기록은 `SESSION_HANDOFF.md`의 dated history를 참조한다.
- baseline inventory를 다시 운영 기준으로 복구하려면, 먼저 새 tests tree와 새 validation owner를 별도 설계로 확정해야 한다.
