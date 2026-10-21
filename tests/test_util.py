from scout.agents.util import award_summary, classify_contact_status, enum_value, ensure_person, is_role_address
from scout.db.models import Company, ContactStatus, Segment, ValidationStatus

from tests.factories import make_person


def test_role_addresses_are_recognised_case_insensitively():
    assert is_role_address("info@acme.com")
    assert is_role_address("Sales@acme.com")
    assert not is_role_address("ada.lovelace@acme.com")


def test_known_limitation_role_word_inside_a_longer_localpart_is_not_caught():
    # Documented in AGENTS.md: matching is exact on the local part. This test pins the
    # behaviour so a future substring-aware change is a conscious decision.
    assert not is_role_address("supportindustio@acme.com")


def test_person_with_a_personal_email_is_found(session):
    p = make_person(session, emails=[("ada@acme.com", ValidationStatus.unchecked)])
    assert classify_contact_status(p) == ContactStatus.found


def test_person_with_only_role_emails_needs_the_finder(session):
    p = make_person(session, emails=[("info@acme.com", ValidationStatus.unchecked)])
    assert classify_contact_status(p) == ContactStatus.needs_finder


def test_unresolved_is_sticky_unless_a_deliberate_email_appears(session):
    p = make_person(session, contact_status=ContactStatus.unresolved)
    assert classify_contact_status(p) == ContactStatus.unresolved


def test_enum_value_handles_both_enum_members_and_round_tripped_strings():
    assert enum_value(Segment.ae_firm) == "ae_firm"
    assert enum_value("ae_firm") == "ae_firm"
    assert enum_value(None) is None


def test_ensure_person_creates_a_placeholder_once(session):
    company = Company(name="Solo LLC", source="test", segment=Segment.gov_contractor)
    session.add(company)
    session.flush()
    first = ensure_person(session, company)
    second = ensure_person(session, company)
    assert first is second
    assert first.name is None and first.source == "placeholder"


def test_award_summary_extracts_fields_and_tolerates_missing_data():
    raw = {
        "title": "Janitorial services",
        "award": {"amount": "460800", "date": "2026-03-01"},
        "fullParentPathName": "DEPT OF DEFENSE",
        "naicsCode": "561720",
        "uiLink": "https://sam.gov/x",
    }
    s = award_summary(raw)
    assert s["award_amount"] == "460800" and s["naics_code"] == "561720"
    assert award_summary(None) is None
    assert award_summary({"title": "x"})["award_amount"] is None
    assert award_summary({}) is None
