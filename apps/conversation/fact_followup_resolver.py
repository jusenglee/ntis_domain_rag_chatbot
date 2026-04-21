"""Compatibility shim — logic moved to `apps.evidence.memory_facts_resolver`.

ADR-0013 후속 정리에 따라 raw payload → answer artifact 변환 로직은 Evidence
계층으로 이전됐다. 이 모듈은 기존 경로로 import하는 외부 코드(사용자 스크립트,
실험적 worktree 등)를 깨뜨리지 않기 위한 단순 re-export shim이다.

신규 코드는 다음 경로에서 직접 import한다:

    from apps.evidence.memory_facts_resolver import (
        resolve_followup_from_facts,
        diagnose_followup_fact_miss,
    )

shim 자체는 로직을 두지 않으며, 유지보수는 이전 대상 모듈에서만 이뤄진다.
"""
from __future__ import annotations

from apps.evidence.memory_facts_resolver import (
    diagnose_followup_fact_miss,
    resolve_followup_from_facts,
)

__all__ = [
    "resolve_followup_from_facts",
    "diagnose_followup_fact_miss",
]
