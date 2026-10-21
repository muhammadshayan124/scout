from datetime import date

import pytest

from scout.agents.outreach import email as outreach
from scout.agents.ratelimit.budget import get_or_create_budget, set_killswitch, suppress
from scout.config import settings
from scout.db.models import Channel, DraftStatus, OutreachEvent, ValidationStatus

from tests.factories import make_draft, make_person


@pytest.fixture
def sent(monkeypatch):
    """Records what would have been emailed; nothing leaves the process."""
    calls = []
    monkeypatch.setattr(outreach, "_smtp_send", lambda to, subject, body: calls.append((to, subject, body)))
    # Pin the ramp to day 0 (cap 5) regardless of when the suite runs.
    monkeypatch.setattr(settings, "email_warmup_start_date", date.today().isoformat())
    return calls


def _ready(session, **kw):
    person = make_person(session, emails=[("ada@example.test", ValidationStatus.valid)])
    return person, make_draft(session, person, **kw)


def test_happy_path_sends_once_and_records_everything(session, sent):
    person, draft = _ready(session)
    result = outreach.send_outreach_email(session, draft.id)
    assert result == {"sent": True, "to": "ada@example.test", "subject": "Hello"}
    assert sent == [("ada@example.test", "Hello", "Body text.")]
    assert draft.status == DraftStatus.sent
    assert session.query(OutreachEvent).filter_by(person_id=person.id, result="sent").count() == 1
    assert get_or_create_budget(session, Channel.email, date.today()).sent_count == 1


def test_a_draft_cannot_be_sent_twice(session, sent):
    _, draft = _ready(session)
    outreach.send_outreach_email(session, draft.id)
    again = outreach.send_outreach_email(session, draft.id)
    assert again["sent"] is False and "already" in again["reason"]
    assert len(sent) == 1


@pytest.mark.parametrize("status", [ValidationStatus.unchecked, ValidationStatus.risky, ValidationStatus.invalid])
def test_only_valid_emails_pass_the_gate(session, sent, status):
    person = make_person(session, emails=[("ada@example.test", status)])
    draft = make_draft(session, person)
    result = outreach.send_outreach_email(session, draft.id)
    assert result["sent"] is False and result["reason"].startswith("no_validated_email")
    assert sent == []


def test_killswitch_suppression_and_cap_each_block_sending(session, sent):
    person, draft = _ready(session)

    set_killswitch(session, True)
    assert outreach.send_outreach_email(session, draft.id)["reason"] == "killswitch_engaged"
    set_killswitch(session, False)

    suppress(session, person.id, "unsubscribe")
    assert outreach.send_outreach_email(session, draft.id)["reason"] == "person_suppressed"

    other = make_person(session, name="Grace", emails=[("grace@example.test", ValidationStatus.valid)])
    other_draft = make_draft(session, other)
    budget = get_or_create_budget(session, Channel.email, date.today())
    budget.sent_count = budget.cap
    assert outreach.send_outreach_email(session, other_draft.id)["reason"].startswith("daily_cap_reached")
    assert sent == []


def test_missing_draft(session, sent):
    assert outreach.send_outreach_email(session, 9999) == {"sent": False, "reason": "no_such_draft"}


def test_non_email_drafts_are_never_sent_by_smtp(session, sent):
    _, draft = _ready(session, channel=Channel.linkedin)
    result = outreach.send_outreach_email(session, draft.id)
    assert result["sent"] is False and result["reason"] == "not_an_email_draft"
    assert sent == []


def test_subject_extraction():
    assert outreach._extract_subject("Subject: Hi there\n\nBody") == ("Hi there", "Body")
    assert outreach._extract_subject("No subject line here") == ("Following up", "No subject line here")
    assert outreach._extract_subject("subject: lower\n\n\nBody") == ("lower", "Body")


def test_smtp_not_configured_raises_instead_of_silently_skipping(monkeypatch):
    monkeypatch.setattr(settings, "smtp_password", "")
    with pytest.raises(outreach.OutreachError):
        outreach._smtp_send("a@example.test", "s", "b")
