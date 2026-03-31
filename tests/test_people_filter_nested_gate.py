from apps.core import filters as filters_module
from apps.core.filters import PeopleFilterInput, build_people_filter


def test_build_people_filter_uses_same_nested_object_conjunctive_semantics_for_name_and_affiliation():
    class DummyMatchAny:
        def __init__(self, any=None, any_values=None):
            self.any = any if any is not None else any_values

    class DummyMatchValue:
        def __init__(self, value):
            self.value = value

    class DummyMatchText:
        def __init__(self, text):
            self.text = text

    class DummyFieldCondition:
        def __init__(self, key, match=None, range=None):
            self.key = key
            self.match = match
            self.range = range

    class DummyFilter:
        def __init__(self, must=None, should=None, must_not=None, min_should=None):
            self.must = must
            self.should = should
            self.must_not = must_not
            self.min_should = min_should

    class DummyNested:
        def __init__(self, key, filter):
            self.key = key
            self.filter = filter

    class DummyNestedCondition:
        def __init__(self, nested):
            self.nested = nested

    dummy_qmodels = type(
        "Q",
        (),
        {
            "MatchAny": DummyMatchAny,
            "MatchValue": DummyMatchValue,
            "MatchText": DummyMatchText,
            "FieldCondition": DummyFieldCondition,
            "Filter": DummyFilter,
            "Nested": DummyNested,
            "NestedCondition": DummyNestedCondition,
        },
    )

    original_qmodels = filters_module.qmodels
    original_nested = filters_module.Nested
    original_nested_condition = filters_module.NestedCondition
    try:
        filters_module.qmodels = dummy_qmodels
        filters_module.Nested = DummyNested
        filters_module.NestedCondition = DummyNestedCondition

        result = build_people_filter(
            PeopleFilterInput(
                people_terms=["신동구"],
                org_terms=["한국과학기술정보연구원"],
            )
        )
    finally:
        filters_module.qmodels = original_qmodels
        filters_module.Nested = original_nested
        filters_module.NestedCondition = original_nested_condition

    nested = result.must[0].nested.filter
    assert nested.should is None
    assert len(nested.must) == 2
    assert getattr(nested.must[0], "key", None) == "hm_nm"

    org_gate = nested.must[1]
    assert org_gate.must is not None
    assert len(org_gate.must) == 1
    assert org_gate.must[0].should is not None
    assert all(getattr(cond, "key", None) == "blng_org_nm" for cond in org_gate.must[0].should)


def test_build_people_filter_keeps_exact_person_id_must_when_name_terms_are_also_present():
    class DummyMatchAny:
        def __init__(self, any=None, any_values=None):
            self.any = any if any is not None else any_values

    class DummyMatchValue:
        def __init__(self, value):
            self.value = value

    class DummyMatchText:
        def __init__(self, text):
            self.text = text

    class DummyFieldCondition:
        def __init__(self, key, match=None, range=None):
            self.key = key
            self.match = match
            self.range = range

    class DummyFilter:
        def __init__(self, must=None, should=None, must_not=None, min_should=None):
            self.must = must
            self.should = should
            self.must_not = must_not
            self.min_should = min_should

    class DummyNested:
        def __init__(self, key, filter):
            self.key = key
            self.filter = filter

    class DummyNestedCondition:
        def __init__(self, nested):
            self.nested = nested

    dummy_qmodels = type(
        "Q",
        (),
        {
            "MatchAny": DummyMatchAny,
            "MatchValue": DummyMatchValue,
            "MatchText": DummyMatchText,
            "FieldCondition": DummyFieldCondition,
            "Filter": DummyFilter,
            "Nested": DummyNested,
            "NestedCondition": DummyNestedCondition,
        },
    )

    original_qmodels = filters_module.qmodels
    original_nested = filters_module.Nested
    original_nested_condition = filters_module.NestedCondition
    try:
        filters_module.qmodels = dummy_qmodels
        filters_module.Nested = DummyNested
        filters_module.NestedCondition = DummyNestedCondition

        result = build_people_filter(
            PeopleFilterInput(
                people_terms=["김봉준"],
                person_ids=["PERSON-1"],
                org_terms=["소속A"],
            )
        )
    finally:
        filters_module.qmodels = original_qmodels
        filters_module.Nested = original_nested
        filters_module.NestedCondition = original_nested_condition

    nested = result.must[0].nested.filter
    nested_keys = [getattr(condition, "key", None) for condition in nested.must if getattr(condition, "key", None)]

    assert "hm_nm" in nested_keys
    assert "hm_id" in nested_keys
