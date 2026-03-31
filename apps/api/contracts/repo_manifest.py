from __future__ import annotations

import argparse
import json
from typing import Any


PLANNER_PROMPT_DEFAULTS = {
    "stage1": "v2",
    "stage15": "v1",
    "stage2": "v2",
}

BASELINE_INVENTORY = {
    "collect_only_pytest_args": [
        "--collect-only",
        "-q",
        "--ignore-glob=pytest-cache-files-*",
        "--ignore-glob=tests/pytest-cache-files-*",
        "-p",
        "no:cacheprovider",
    ],
    "shared_pytest_args": [
        "-p",
        "no:cacheprovider",
    ],
    "core_contract_subset": [
        "tests/test_planner_stagewise.py",
        "tests/test_retrieval_workflow_detail_runtime.py",
        "tests/test_rag_anchor_truth.py",
        "tests/test_rag_anchor_truth_active_only.py",
        "tests/test_request_facade_followup_seed_priority.py",
        "tests/test_request_facade_strategy_meta_focus_entity.py",
        "tests/test_retrieval_workflow_detail_cache_gate.py",
        "tests/test_contract_debt_paydown.py",
        "tests/test_runtime_helpers_stream_bypass.py",
        "tests/test_answer_merge_bypass.py",
        "tests/test_api_routes_reference_payload.py",
        "tests/test_repo_contract_defaults.py",
    ],
    "eval_fixture_tests": [
        "tests/test_eval_fixture_schema.py",
        "tests/test_answer_groundedness_verdict.py",
    ],
    "required_files": [
        "eval/sample_queries.jsonl",
        "eval/answer_groundedness_cases.jsonl",
        "docs/PRODUCT_BASELINE.md",
    ],
}


def get_repo_contract_manifest() -> dict[str, Any]:
    return {
        "planner_prompt_defaults": dict(PLANNER_PROMPT_DEFAULTS),
        "baseline_inventory": {
            key: list(value) if isinstance(value, list) else value
            for key, value in BASELINE_INVENTORY.items()
        },
    }


def _emit_manifest_section(section: str | None) -> dict[str, Any]:
    manifest = get_repo_contract_manifest()
    if not section:
        return manifest
    normalized = str(section or "").strip().lower()
    if normalized not in manifest:
        raise KeyError(f"unknown manifest section: {section}")
    return manifest[normalized]


def main() -> None:
    parser = argparse.ArgumentParser(description="Emit the NTIS repo contract manifest as JSON.")
    parser.add_argument("--section", default=None, help="Optional top-level section name to emit.")
    args = parser.parse_args()
    payload = _emit_manifest_section(args.section)
    print(json.dumps(payload, ensure_ascii=False))


if __name__ == "__main__":
    main()
