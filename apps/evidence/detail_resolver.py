from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Optional

from apps.evidence.result_set import RetrievalBundle
from apps.api.streaming.contracts import AnswerArtifact
from apps.conversation.entity_reference import ResolvedEntityRef


@dataclass(frozen=True)
class DetailResolutionResult:
    status: Literal["resolved", "not_found", "insufficient_coverage"]
    entity_ref: ResolvedEntityRef
    bundle: Optional[RetrievalBundle]
    coverage: Any
    answer_artifact: Optional[AnswerArtifact]
