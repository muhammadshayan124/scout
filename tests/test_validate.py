import smtplib

import pytest

from scout.agents.validation import validate
from scout.db.models import ValidationStatus

from tests.factories import make_person


@pytest.mark.parametrize(
    "address, ok",
    [
        ("ada@acme.com", True),
        ("a.b+tag@sub.acme.co.uk", True),
        ("no-at-sign.com", False),
        ("two@@acme.com", False),
        ("ada@nodot", False),
        ("ada@acme.c", False),
        ("", False),
    ],
)
def test_syntax_check(address, ok):
    assert validate._check_syntax(address) is ok


class _FakeSMTP:
    """Stands in for smtplib.SMTP; rcpt() returns whatever the test configures."""

    reply = (250, b"2.1.5 OK")
    raises = None

    def __init__(self, *args, **kwargs):
        if self.raises:
            raise self.raises

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def helo(self, *_):
        pass

    def mail(self, *_):
        pass

    def rcpt(self, _address):
        return self.reply

    def rset(self):
        pass


def _smtp(monkeypatch, reply=None, raises=None):
    fake = type("Fake", (_FakeSMTP,), {"reply": reply or _FakeSMTP.reply, "raises": raises})
    monkeypatch.setattr(smtplib, "SMTP", fake)


@pytest.mark.parametrize(
    "reply, verdict",
    [
        ((250, b"OK"), "accepted"),
        ((550, b"5.1.1 User unknown"), "rejected"),
        ((550, b"5.1.1 The email account does not exist"), "rejected"),
        # The environment-specific failure that originally dropped valid leads:
        ((550, b"5.7.1 Service unavailable; client host blocked using Spamhaus"), "inconclusive"),
        ((451, b"4.7.1 Try again later"), "inconclusive"),
        ((550, b"Rejected for unspecified policy reasons"), "inconclusive"),
    ],
)
def test_smtp_reply_classification(monkeypatch, reply, verdict):
    _smtp(monkeypatch, reply=reply)
    assert validate._smtp_check("mx.example.test", "x@example.test")[0] == verdict


def test_connection_errors_are_inconclusive_not_invalid(monkeypatch):
    _smtp(monkeypatch, raises=smtplib.SMTPConnectError(421, "nope"))
    verdict, detail = validate._smtp_check("mx.example.test", "x@example.test")
    assert verdict == "inconclusive" and "SMTPConnectError" in detail


def _validate(session, monkeypatch, address, *, mx="mx.example.test", verdict=("accepted", "smtp_250")):
    monkeypatch.setattr(validate, "_get_mx_host", lambda domain: mx)
    monkeypatch.setattr(validate, "_smtp_check", lambda host, addr, timeout=10: verdict)
    p = make_person(session, emails=[(address, ValidationStatus.unchecked)])
    email = p.emails[0]
    return validate.validate_email(session, email), email


def test_accepted_personal_address_is_valid(session, monkeypatch):
    status, email = _validate(session, monkeypatch, "ada@example.test")
    assert status == ValidationStatus.valid and email.checked_at is not None


def test_accepted_role_address_is_only_risky(session, monkeypatch):
    # Catch-all servers accept anything, so a role address is never promoted to valid.
    status, _ = _validate(session, monkeypatch, "info@example.test")
    assert status == ValidationStatus.risky


def test_rejected_is_invalid_and_inconclusive_stays_unchecked(session, monkeypatch):
    assert _validate(session, monkeypatch, "a@example.test", verdict=("rejected", "x"))[0] == ValidationStatus.invalid
    assert _validate(session, monkeypatch, "b@example.test", verdict=("inconclusive", "x"))[0] == ValidationStatus.unchecked


def test_no_mx_record_is_invalid(session, monkeypatch):
    assert _validate(session, monkeypatch, "a@example.test", mx=None)[0] == ValidationStatus.invalid


def test_bad_syntax_is_invalid_without_any_network_lookup(session, monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("must not look up MX for a malformed address")

    monkeypatch.setattr(validate, "_get_mx_host", boom)
    p = make_person(session, emails=[("not-an-email", ValidationStatus.unchecked)])
    assert validate.validate_email(session, p.emails[0]) == ValidationStatus.invalid
