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
    reject_min_should = False

    def __init__(self, must=None, should=None, must_not=None, min_should=None):
        if self.__class__.reject_min_should and min_should is not None:
            raise TypeError("min_should unsupported")
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
        _FakeFilter.reject_min_should = False
        filters.qmodels = _FakeQModels()

    def tearDown(self) -> None:
        filters.qmodels = self._orig_qmodels

    def _extract_nested_filter(self, flt):
        self.assertIsNotNone(flt)
        nested_cond = (flt.must or [None])[0]
        self.assertIsNotNone(nested_cond)
        nested_obj = getattr(nested_cond, "nested", None)
        if nested_obj is not None:
            self.assertEqual(getattr(nested_obj, "key", None), "prtcp_mp")
            return getattr(nested_obj, "filter", None)
        self.assertEqual(getattr(nested_cond, "key", None), "prtcp_mp")
        return getattr(nested_cond, "filter", None)

    def test_people_filter_name_only_has_nested_min_should(self) -> None:
        flt = filters.build_people_filter(filters.PeopleFilterInput(people_terms=["홍길동"]))
        nested = self._extract_nested_filter(flt)
        self.assertEqual(getattr(nested.min_should, "min_count", None), 1)
        self.assertEqual(len(nested.should or []), 1)
        self.assertEqual((nested.should or [])[0].key, "hm_nm")

    def test_people_filter_ids_prioritized_to_nested_must(self) -> None:
        flt = filters.build_people_filter(
            filters.PeopleFilterInput(people_terms=["홍길동"], person_ids=["P-001"])
        )
        nested = self._extract_nested_filter(flt)
        self.assertEqual(len(nested.must or []), 1)
        self.assertEqual((nested.must or [])[0].key, "hm_id")
        self.assertEqual(getattr(nested.min_should, "min_count", None), 1)

    def test_people_filter_promote_one_to_nested_must(self) -> None:
        flt = filters.build_people_filter(
            filters.PeopleFilterInput(people_terms=["홍길동"], promote_one_must=True)
        )
        nested = self._extract_nested_filter(flt)
        self.assertEqual(len(nested.must or []), 1)
        self.assertEqual((nested.must or [])[0].key, "hm_nm")
        self.assertFalse(bool(nested.should))

    def test_build_filter_fallback_preserves_should_gate_when_min_should_unsupported(self) -> None:
        _FakeFilter.reject_min_should = True
        flt = filters._build_filter(
            must=None,
            should=[_FakeFieldCondition(key="a", match=_FakeMatchValue(value="x"))],
            must_not=None,
            min_should=1,
        )
        self.assertIsNotNone(flt)
        self.assertEqual(len(flt.must or []), 1)
        self.assertIsInstance((flt.must or [])[0], _FakeFilter)
        self.assertEqual(len(((flt.must or [])[0].should or [])), 1)

    def test_people_filter_promote_one_to_must(self) -> None:
        flt = filters.build_people_filter(
            filters.PeopleFilterInput(people_terms=["홍길동"], promote_one_must=True)
        )
        self.assertIsNotNone(flt)
        self.assertEqual(len(flt.must or []), 1)
        self.assertEqual((flt.must or [])[0].key, "prtcp_mp[].hm_nm")
        self.assertFalse(bool(flt.should))

    def test_people_filter_and_mode_no_min_should(self) -> None:
        flt = filters.build_people_filter(
            filters.PeopleFilterInput(people_terms=["김재수", "박민수"], min_should=None)
        )
        self.assertIsNotNone(flt)
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
        self.assertIn("FILTER_MISS_SUSPECTED", src)


if __name__ == "__main__":
    unittest.main()
