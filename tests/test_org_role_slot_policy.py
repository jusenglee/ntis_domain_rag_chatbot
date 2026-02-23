from pathlib import Path
import sys

sys.path.append(str(Path(__file__).resolve().parents[1]))

from rag_parts.query_intent import normalize_org_terms, classify_query


def test_normalize_org_terms_expands_alias_and_legal_prefix() -> None:
    terms = normalize_org_terms(["(주) ETRI", "한국전자통신연구원(대전)"])

    assert "ETRI" in terms
    assert "한국전자통신연구원" in terms


def test_classify_query_separates_org_role_slots() -> None:
    lead_intent = classify_query("ETRI 수행 과제 목록", ["ETRI", "수행", "과제", "목록"])
    part_intent = classify_query("ETRI 참여 과제 목록", ["ETRI", "참여", "과제", "목록"])
    aff_intent = classify_query("ETRI 소속 연구자 과제 목록", ["ETRI", "소속", "연구자", "과제", "목록"])

    assert lead_intent.lead_org_terms
    assert not lead_intent.participant_org_terms
    assert not lead_intent.people_affiliation_org_terms

    assert part_intent.participant_org_terms
    assert not part_intent.lead_org_terms

    assert aff_intent.people_affiliation_org_terms
    assert aff_intent.org_role == "affiliation"
