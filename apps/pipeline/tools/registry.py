"""Phase 1 — 표준 도구 registry 빌더.

운영 진입점이나 테스트가 ToolExecutor를 만들 때 호출:
    >>> registry = build_default_registry()
    >>> executor = ToolExecutor(registry=registry, context=ctx)

새 도구 모듈을 추가하면 여기 entries 함수만 import 해서 합치면 된다.
"""

from __future__ import annotations

from typing import Dict

from apps.pipeline.tools.contracts import ToolEntry
from apps.pipeline.tools.lookup_tools import lookup_tool_entries
from apps.pipeline.tools.manifest_tools import manifest_tool_entries
from apps.pipeline.tools.response_tools import response_tool_entries
from apps.pipeline.tools.retrieval_tools import retrieval_tool_entries


def build_default_registry() -> Dict[str, ToolEntry]:
    """표준 도구 registry — Phase 5 Step 4 기준 9개:
    search 3 + lookup 2 + manifest 2 + response 2.
    """
    entries: list[ToolEntry] = []
    entries.extend(retrieval_tool_entries())
    entries.extend(lookup_tool_entries())
    entries.extend(manifest_tool_entries())
    entries.extend(response_tool_entries())

    registry: Dict[str, ToolEntry] = {}
    for entry in entries:
        if entry.spec.name in registry:
            raise ValueError(f"duplicate tool name in registry: {entry.spec.name!r}")
        registry[entry.spec.name] = entry
    return registry
