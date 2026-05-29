"""GroundingChecker 구현체 패키지.

CriticAgent의 LLM-as-Judge grounding 검증을 위한 구현체. 휴리스틱 단어 매칭 금지,
LLM이 답변과 evidence를 의미적으로 비교.
"""

from apps.pipeline.agents.grounding.llm_judge import LLMJudgeChecker

__all__ = ["LLMJudgeChecker"]
