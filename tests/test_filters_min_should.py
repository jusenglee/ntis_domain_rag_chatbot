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

    def test_lookup_path_keeps_name_only_gate_enabled(self) -> None:
        with open("rag_pipeline.py", "r", encoding="utf-8") as fp:
            src = fp.read()
        self.assertIn("lookup_has_name_filters = bool(people_terms or people_ids or org_terms)", src)
        self.assertIn("apply_name_filters = mode != \"lookup\" or lookup_has_ids or lookup_has_name_filters", src)


if __name__ == "__main__":
    unittest.main()
