from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional


@dataclass(frozen=True)
class ResultContractOutcome:
    status: Literal["ok", "normal_no_result", "insufficient_hits", "low_score", "contract_violation"]
    empty_result_policy: str
    reason: Optional[str] = None
    user_message: Optional[str] = None
    retryable: bool = False
