from __future__ import annotations

from pathlib import Path

CONTROL_FILES = [
    "apps/core/query_intent.py",
    "apps/core/schemas.py",
    "apps/core/planner_contract.py",
    "apps/core/canonical_evidence.py",
    "apps/core/rag_runtime_prelude.py",
    "apps/api/services/planner_service.py",
    "apps/api/services/rag_result_assembly.py",
    "apps/api/services/rag_retriever.py",
    "apps/api/contracts/runtime_contracts.py",
    "apps/api/services/context_renderer.py",
    "apps/core/retrieval.py",
    "apps/api/services/llm_json.py",
    "apps/api/services/runtime_helpers.py",
    "apps/api/services/workflow_nodes.py",
    "apps/api/services/workflow_builder.py",
    "apps/api/services/retrieval_workflow.py",
    "apps/api/services/answer_generation.py",
    "apps/api/services/answer_merge.py",
    "apps/api/app_factory.py",
    "apps/api/rag_mapper/rag_mapper.py",
    "prompts/planner_stage2_v1.md",
    "docs/README.md",
    "docs/CONTRACT.md",
    "docs/MODE_DECISION_GUIDE.md",
    "docs/RUNBOOK.md",
    "docs/GOLDEN_TESTS.md",
    "docs/DRIFT_CHECKLIST_V3.md",
    "tests/test_planner_service.py",
]

EXPECTED_DOC_V3_TOKENS = {
    "docs/CONTRACT.md": [
        "candidate_keys.project_key",
        "project_key_policy=ambiguous_or",
        "join_key_mode=deferred",
        "IntentPayloadV3",
        "intent_payload_version=\"v3\"",
    ],
    "docs/RUNBOOK.md": [
        "candidate_keys.project_key",
        "project_key_policy",
        "join_key_mode=deferred",
        "dual_branch_used",
        "IntentPayloadV3",
        "intent_payload_version=\"v3\"",
    ],
    "docs/GOLDEN_TESTS.md": [
        "a4412354543",
        "join_key_mode=deferred",
    ],
    "docs/DRIFT_CHECKLIST_V3.md": [
        "IntentPayloadV3 = transport version + semantic version dual promotion",
        "apps/core/query_intent.py",
        "apps/core/rag_pipeline.py",
        "apps/core/retrieval.py",
        "apps/api/services/llm_json.py",
        "apps/api/services/runtime_helpers.py",
        "apps/api/services/workflow_nodes.py",
        "P0:",
    ],
}

SUSPICIOUS_TOKENS = [
    "??臾몄꽌",
    "?낅땲",
    "怨쇱젣踰덊샇",
    "?곌뎄",
    "怨쇱젣",
]

EXPECTED_UTF8_LABELS = {
    "apps/api/contracts/runtime_contracts.py": [
        "과제고유번호",
        "과제번호",
    ],
    "apps/api/services/context_renderer.py": [
        "가-힣",
        "normalize_researcher_token",
    ],
    "apps/core/schemas.py": [
        "IntentPayloadV3",
        "Mutable runtime context derived from normalized intent.",
    ],
    "apps/core/planner_contract.py": [
        "Planner and executor contract validation helpers.",
        "pjt_id",
    ],
    "apps/core/canonical_evidence.py": [
        "Collect canonical ids while keeping `pjt_id`, `pjt_no`, and `rst_id` separate.",
        "build_canonical_evidence",
    ],
    "apps/core/rag_runtime_prelude.py": [
        "RuntimePreludeRequest",
        "Execution truth assembled by runtime prelude for downstream retrieval.",
    ],
    "prompts/planner_stage2_v1.md": [
        "과제고유번호",
        "과제번호",
        "a4412354543",
    ],
    "docs/DRIFT_CHECKLIST_V3.md": [
        "one file at a time",
        "QuestionAnalysis v3",
    ],
}


def _contains_forbidden_control_char(text: str) -> bool:
    for ch in text:
        code = ord(ch)
        if code in (9, 10, 13):
            continue
        if code < 32:
            return True
    return False


def test_control_files_do_not_contain_suspicious_mojibake_or_control_chars():
    for rel in CONTROL_FILES:
        text = Path(rel).read_text(encoding="utf-8")
        assert not _contains_forbidden_control_char(text), f"{rel} still contains a forbidden control character"
        for token in SUSPICIOUS_TOKENS:
            assert token not in text, f"{rel} still contains suspicious mojibake token: {token}"


def test_core_utf8_guard_tokens_remain_intact():
    for rel, tokens in EXPECTED_UTF8_LABELS.items():
        text = Path(rel).read_text(encoding="utf-8")
        for token in tokens:
            assert token in text, f"{rel} lost required UTF-8 guard token: {token}"


def test_runbook_and_agents_track_current_vocab():
    runbook = Path("docs/RUNBOOK.md").read_text(encoding="utf-8")
    agents = Path("AGENTS.md").read_text(encoding="utf-8")
    for token in [
        "perf_to_project_to_perf",
        "pattern_analysis",
        "multi_hop_bundle",
        "candidate_keys.project_key",
        "join_key_mode=deferred",
        "IntentPayloadV3",
        "intent_payload_version=\"v3\"",
        "strategy_version=\"v3\"",
    ]:
        assert token in runbook
    for token in ["summary", "detail", "list", "stats", "relation", "comparison", "series"]:
        assert token in agents


def test_question_analysis_v3_tokens_survive_utf8_roundtrip():
    for rel, tokens in EXPECTED_DOC_V3_TOKENS.items():
        text = Path(rel).read_text(encoding="utf-8")
        for token in tokens:
            assert token in text, f"{rel} lost required QuestionAnalysis v3 token: {token}"


