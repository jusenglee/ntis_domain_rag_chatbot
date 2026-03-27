from apps.core import filters


def test_build_project_id_filter_anchor_locked_prefers_pjt_id(monkeypatch):
    class DummyFieldCondition:
        def __init__(self, key, match):
            self.key = key
            self.match = match

    monkeypatch.setattr(filters, "qmodels", type("Q", (), {"FieldCondition": DummyFieldCondition}))
    monkeypatch.setattr(filters, "_build_filter", lambda **kwargs: kwargs)
    monkeypatch.setattr(filters, "make_match_any", lambda values: values)

    compiled = filters.build_project_id_filter(["PJT-1"], ["NO-1"], project_key_policy="anchor_locked_pjt_id")

    keys = [item.key for item in compiled["should"]]
    assert all("pjt_no" not in key for key in keys)
    assert any("pjt_id" in key for key in keys)
