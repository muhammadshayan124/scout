import email.message

import pytest

from scout.agents.ratelimit.budget import is_suppressed
from scout.agents.reply import classify, imap_watch
from scout.db.models import Channel, Reply, ValidationStatus

from tests.factories import make_draft, make_person


@pytest.mark.parametrize(
    "text",
    [
        "Please unsubscribe me",
        "REMOVE ME from your list",
        "Stop emailing me.",
        "stop contacting us",
        "I want to opt out",
        "opt-out please",
        "Do not contact me again",
        "Take me off this list",
    ],
)
def test_unsubscribe_phrases_are_detected(text):
    assert imap_watch._UNSUBSCRIBE_PATTERNS.search(text)


@pytest.mark.parametrize(
    "text",
    ["Sounds interesting, can we talk Tuesday?", "We are out of office until Monday", "Remove the second paragraph"],
)
def test_ordinary_replies_are_not_unsubscribes(text):
    assert not imap_watch._UNSUBSCRIBE_PATTERNS.search(text)


def test_body_extraction_prefers_plain_text_and_skips_attachments():
    msg = email.message.EmailMessage()
    msg.set_content("plain body")
    msg.add_alternative("<p>html body</p>", subtype="html")
    msg.add_attachment(b"data", maintype="application", subtype="pdf", filename="a.pdf")
    assert imap_watch._extract_body(msg) == "plain body\n"


class _FakeIMAP:
    """Minimal IMAP4_SSL: one unseen message."""

    raw = b""

    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *e):
        return False

    def login(self, *_):
        pass

    def select(self, *_):
        pass

    def search(self, *_):
        return "OK", [b"1"]

    def fetch(self, *_):
        return "OK", [(b"1 (RFC822)", self.raw)]


def _inbox(monkeypatch, sender, subject, body):
    msg = email.message.EmailMessage()
    msg["From"], msg["Subject"] = sender, subject
    msg.set_content(body)
    monkeypatch.setattr(imap_watch.imaplib, "IMAP4_SSL", type("F", (_FakeIMAP,), {"raw": msg.as_bytes()}))


def test_unsubscribe_reply_is_stored_and_suppresses_the_person(session, monkeypatch):
    person = make_person(session, emails=[("ada@example.test", ValidationStatus.valid)])
    _inbox(monkeypatch, "Ada <ada@example.test>", "Re: Hello", "Please remove me from your list.")
    out = imap_watch.check_replies(session)
    assert out["auto_suppressed_person_ids"] == [person.id]
    assert is_suppressed(session, person.id)
    reply = session.query(Reply).one()
    assert reply.classification == "unsubscribe" and reply.needs_human is False


def test_normal_reply_needs_a_human_and_does_not_suppress(session, monkeypatch):
    person = make_person(session, emails=[("ada@example.test", ValidationStatus.valid)])
    _inbox(monkeypatch, "ada@example.test", "Re: Hello", "Interesting, call me.")
    imap_watch.check_replies(session)
    assert not is_suppressed(session, person.id)
    assert session.query(Reply).one().needs_human is True


def test_sender_matching_ignores_address_case(session, monkeypatch):
    # Stored addresses are not normalised, so the match must not be case-sensitive.
    person = make_person(session, emails=[("Ada@Example.test", ValidationStatus.valid)])
    _inbox(monkeypatch, "ada@example.test", "Re: Hello", "Yes please")
    out = imap_watch.check_replies(session)
    assert [m["person_id"] for m in out["matched"]] == [person.id]


def test_unknown_senders_are_reported_not_stored(session, monkeypatch):
    make_person(session, emails=[("ada@example.test", ValidationStatus.valid)])
    _inbox(monkeypatch, "stranger@elsewhere.test", "Hi", "spam")
    out = imap_watch.check_replies(session)
    assert out["unmatched_senders"] == ["stranger@elsewhere.test"]
    assert session.query(Reply).count() == 0


def test_imap_not_configured_raises(session, monkeypatch):
    monkeypatch.setattr(imap_watch.settings, "imap_password", "")
    with pytest.raises(imap_watch.ImapError):
        imap_watch.check_replies(session)


def test_classification_round_trip(session):
    person = make_person(session)
    make_draft(session, person, body="Subject: Hi\n\nOriginal outreach")
    reply = Reply(person_id=person.id, channel=Channel.email, body="Sounds good")
    session.add(reply)
    session.flush()

    ctx = classify.get_reply_classification_context(session, reply.id)
    assert ctx["reply_body"] == "Sounds good" and ctx["original_draft_body"].endswith("Original outreach")

    saved = classify.save_reply_classification(session, reply.id, "interested", needs_human=True)
    assert saved.classification == "interested" and saved.needs_human is True
    assert classify.get_reply_classification_context(session, 12345) is None
    with pytest.raises(ValueError):
        classify.save_reply_classification(session, 12345, "x", False)
