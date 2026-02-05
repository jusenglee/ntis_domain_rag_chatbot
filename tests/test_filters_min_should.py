import unittest
from dataclasses import dataclass

import rag_parts.filters as filters


class _FakeMatchAny:
    def __init__(self, any=None, any_values=None):
        self.any = any if any is not None else any_values


class _FakeMatchValue:
    def __init__(self, value=None):
        self.value = value


class _FakeFieldCondition:
    def __init__(self, key=None, match=None, range=None):
        self.key = key
        self.match = match
        self.range = range


class _FakeMinShould:
    def __init__(self, min_count=None, count=None):
        if min_count is not None:
            self.min_count = min_count
        elif count is not None:
            self.min_count = count
        else:
            raise TypeError("min_count required")


class _FakeFilter:
    def __init__(self, must=None, should=None, must_not=None, min_should=None):
        self.must = must
        self.should = should
        self.must_not = must_not
        self.min_should = min_should


class _FakeNestedFilter:
    def __init__(self, key=None, filter=None):
        self.key = key
        self.filter = filter


class _FakeNestedCondition:
    def __init__(self, nested=None, key=None, filter=None):
        self.nested = nested
        self.key = key
        self.filter = filter


@dataclass
class _FakeQModels:
    MatchAny = _FakeMatchAny
    MatchValue = _FakeMatchValue
    FieldCondition = _FakeFieldCondition
    Filter = _FakeFilter
    MinShould = _FakeMinShould
    NestedFilter = _FakeNestedFilter
    NestedCondition = _FakeNestedCondition


class FilterMinShouldTests(unittest.TestCase):
    def setUp(self) -> None:
        self._orig_qmodels = filters.qmodels
        filters.qmodels = _FakeQModels()

    def tearDown(self) -> None:
        filters.qmodels = self._orig_qmodels

    def test_people_filter_name_only_has_min_should(self) -> None:
        flt = filters.build_people_filter(filters.PeopleFilterInput(people_terms=["홍길동"]))
        self.assertIsNotNone(flt)
        self.assertEqual(getattr(flt.min_should, "min_count", None), 1)
        self.assertEqual(len(flt.should or []), 1)

    def test_people_filter_ids_prioritized_to_must(self) -> None:
        flt = filters.build_people_filter(
            filters.PeopleFilterInput(people_terms=["홍길동"], person_ids=["P-001"])
        )
        self.assertIsNotNone(flt)
        self.assertEqual(len(flt.must or []), 1)
        self.assertEqual((flt.must or [])[0].key, "prtcp_mp[].hm_id")
        self.assertEqual(getattr(flt.min_should, "min_count", None), 1)

    def test_org_filter_has_min_should(self) -> None:
        flt = filters.build_org_filter(filters.OrgFilterInput(terms=["농촌진흥청"]))
        self.assertIsNotNone(flt)
        self.assertEqual(getattr(flt.min_should, "min_count", None), 1)

    def test_compile_filter_serializes_min_should(self) -> None:
        flt = filters.compile_filter(
            {
                "should": [
                    {"field": "prtcp_mp[].hm_nm", "match_any": ["홍길동"]},
                ],
                "min_should": 1,
            }
        )
        self.assertIsNotNone(flt)
        self.assertEqual(getattr(flt.min_should, "min_count", None), 1)


    def test_same_org_name_serializes_to_distinct_role_filters(self) -> None:
        term = ["한국과학기술연구원"]

        lead_filter = filters.build_org_filter(filters.OrgFilterInput(terms=term, role="lead"))
        participant_filter = filters.build_prtcp_org_nested_filter(
            filters.OrgFilterInput(terms=term, role="participant")
        )
        affiliation_filter = filters.build_people_filter(filters.PeopleFilterInput(org_terms=term))

        self.assertIsNotNone(lead_filter)
        self.assertIsNotNone(participant_filter)
        self.assertIsNotNone(affiliation_filter)

        lead_keys = {cond.key for cond in (lead_filter.should or []) if getattr(cond, "key", None)}
        self.assertIn("org_nm", lead_keys)
        self.assertIn("meta_basic.pjt_prfrm_org_nm", lead_keys)

        participant_should = list(getattr(participant_filter, "should", None) or [participant_filter])
        participant_nested_keys = {
            getattr(getattr(cond, "nested", None), "key", None)
            for cond in participant_should
        }
        self.assertIn("prtcp_org", participant_nested_keys)

        affiliation_keys = {cond.key for cond in (affiliation_filter.should or []) if getattr(cond, "key", None)}
        self.assertIn("prtcp_mp[].blng_org_nm", affiliation_keys)

    def test_join_filter_group_mode_prefers_pjt_no_in_must(self) -> None:
        flt = filters.build_join_filter(
            filters.JoinFilterInput(
                join_ids=["PJT-ID-1"],
                pjt_nos=["PJT-NO-1"],
                join_key_mode="group",
            )
        )
        self.assertIsNotNone(flt)
        self.assertEqual(len(flt.must or []), 1)
        self.assertEqual((flt.must or [])[0].key, "pjt_no")
        self.assertEqual(getattr((flt.must or [])[0].match, "any", None), ["PJT-NO-1"])

    def test_join_filter_group_mode_falls_back_to_pjt_id_must(self) -> None:
        flt = filters.build_join_filter(
            filters.JoinFilterInput(
                join_ids=["PJT-ID-1", "PJT-ID-2"],
                join_key_mode="group",
            )
        )
        self.assertIsNotNone(flt)
        self.assertEqual(len(flt.must or []), 1)
        self.assertEqual((flt.must or [])[0].key, "pjt_id")
        self.assertEqual(getattr((flt.must or [])[0].match, "any", None), ["PJT-ID-1", "PJT-ID-2"])

    def test_join_filter_instance_mode_prioritizes_pjt_id_must(self) -> None:
        flt = filters.build_join_filter(
            filters.JoinFilterInput(
                join_ids=["PJT-ID-1"],
                pjt_nos=["PJT-NO-1"],
                join_key_mode="instance",
            )
        )
        self.assertIsNotNone(flt)
        self.assertEqual(len(flt.must or []), 1)
        self.assertEqual((flt.must or [])[0].key, "pjt_id")

    def test_join_hop2_uses_group_mode_for_perf_with_must_constraints(self) -> None:
        with open("rag_pipeline.py", "r", encoding="utf-8") as fp:
            src = fp.read()
        self.assertIn('join_key_mode = "group" if hop2_col == COL_PERF else "instance"', src)
        self.assertIn("pjt_nos=join_pjt_nos", src)
        self.assertIn("join_ids=join_pjt_ids or join_ids", src)

    def test_lookup_path_keeps_name_only_gate_enabled(self) -> None:
        with open("rag_pipeline.py", "r", encoding="utf-8") as fp:
            src = fp.read()
        self.assertIn("lookup_has_name_filters = bool(people_terms or people_ids or org_terms)", src)
        self.assertIn("apply_name_filters = mode != \"lookup\" or lookup_has_ids or lookup_has_name_filters", src)


if __name__ == "__main__":
    unittest.main()
