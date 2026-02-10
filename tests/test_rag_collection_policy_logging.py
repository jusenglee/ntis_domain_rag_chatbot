import unittest
from pathlib import Path


class RagCollectionPolicyLoggingTests(unittest.TestCase):
    def test_collection_policy_log_uses_non_fallback_field_name(self):
        source = Path("rag_pipeline.py").read_text(encoding="utf-8")

        self.assertIn("collection_policy_reason=collection_policy_reason", source)
        self.assertNotIn("fallback_reason=fallback_reason", source)


if __name__ == "__main__":
    unittest.main()
