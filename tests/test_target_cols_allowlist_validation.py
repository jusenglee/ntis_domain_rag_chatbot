import unittest
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from rag_parts.planner_contract import StrategyViolation

rag_pipeline = pytest.importorskip("rag_pipeline")


class TargetColsAllowlistValidationTests(unittest.TestCase):
    def test_build_plan_relation_target_cols_ignores_allowlist_redecision(self):
        intent = SimpleNamespace(
            action="relation",
            base_route="project",
            relation=("project", "perf"),
            join_key_mode=None,
            output_type=None,
        )

        with patch.object(rag_pipeline, "RAG_COLLECTION_ALLOWLIST", ["support"]), patch.object(
            rag_pipeline,
            "relation_target_collections",
            return_value=["project", "perf"],
        ):
            plan, reason = rag_pipeline._build_plan(intent)

        self.assertEqual(reason, "relation_action")
        self.assertEqual(list(plan.target_collections), ["project", "perf"])

    def test_assert_allowlist_only_raises_explicit_violation_for_disallowed_collection(self):
        with self.assertRaises(StrategyViolation) as exc:
            rag_pipeline._assert_allowlist_only(
                target_cols=["project", "perf"],
                allow_cols=["project"],
                source="unit_test",
            )

        self.assertEqual(exc.exception.error_code, "PLANNER_TARGET_COLS_ALLOWLIST_VIOLATION")

    def test_assert_allowlist_only_allows_when_subset(self):
        rag_pipeline._assert_allowlist_only(
            target_cols=["project"],
            allow_cols=["project", "perf"],
            source="unit_test",
        )


if __name__ == "__main__":
    unittest.main()
