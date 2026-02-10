import unittest

from rag_parts.filters import validate_join_mode_key_inputs


class JoinModeValidationTests(unittest.TestCase):
    def test_group_mode_requires_pjt_nos(self):
        with self.assertRaisesRegex(ValueError, "pjt_nos"):
            validate_join_mode_key_inputs(mode="group", join_ids=[], pjt_nos=[])

    def test_instance_mode_requires_join_ids(self):
        with self.assertRaisesRegex(ValueError, "join_ids"):
            validate_join_mode_key_inputs(mode="instance", join_ids=[], pjt_nos=[])

if __name__ == "__main__":
    unittest.main()
