"""Validation Agent -- SMTP-level email verification (ARCHITECTURE.md §3.3), Reacher-style:
syntax -> MX lookup -> SMTP handshake up to RCPT TO, no message actually sent (MAIL FROM /
RCPT TO / then RSET+QUIT, never DATA).

Not optional at $0 budget: every bounce above ~2% degrades the ability to send at all
(see ARCHITECTURE.md §3.6 on the personal-mailbox reputation risk). This is what protects
that, not the rate limiter.
"""

import re
import smtplib
import socket
from datetime import datetime

import dns.resolver
from sqlalchemy.orm import Session

from scout.agents.events import log_event
from scout.agents.util import is_role_address
from scout.db.models import EmailAddress, ValidationStatus

_EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$")


def _check_syntax(address: str) -> bool:
    return bool(_EMAIL_RE.match(address))


def _get_mx_host(domain: str) -> str | None:
    try:
        answers = dns.resolver.resolve(domain, "MX")
        # Lowest preference value = highest priority mail server.
        best = min(answers, key=lambda r: r.preference)
        return str(best.exchange).rstrip(".")
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer, dns.exception.Timeout):
        return None


# Phrases indicating the *sending IP's* reputation was rejected, not the recipient
# address -- confirmed for real against this environment: Microsoft 365 rejected a
# legitimate-looking info@ address with "550 5.7.1 ... blocked using Spamhaus", which is
# a statement about the connecting IP, not about whether that mailbox exists. Treating
# that identically to a real "no such user" rejection would silently mark valid leads as
# invalid and drop them from the pipeline -- confirmed this was happening before this fix.
_REPUTATION_MARKERS = (
    "spamhaus", "blocked", "reputation", "denied", "not authorized",
    "service unavailable", "5.7.1", "5.7.0",
)
# Phrases that genuinely assert the mailbox doesn't exist.
_NONEXISTENT_MARKERS = (
    "does not exist", "no such user", "user unknown", "unknown user",
    "invalid recipient", "mailbox not found", "no mailbox",
)


def _smtp_check(mx_host: str, address: str, timeout: int = 10) -> tuple[str, str]:
    """Connect to the real mail server and ask if the mailbox would accept mail, without
    sending anything. Returns (verdict, detail) where verdict is one of:
    'accepted', 'rejected', 'inconclusive'. Some servers accept every RCPT TO regardless
    of validity ("catch-all" domains) -- a real, known limitation of this technique, not
    a bug; it's why 'accepted' maps to 'risky' rather than 'valid' for role addresses
    (see validate_email below).
    """
    try:
        with smtplib.SMTP(mx_host, 25, timeout=timeout) as smtp:
            smtp.helo("scout-validator.local")
            smtp.mail("verify@scout-validator.local")
            code, resp = smtp.rcpt(address)
            smtp.rset()
            resp_text = (resp.decode("utf-8", "ignore") if isinstance(resp, bytes) else str(resp)).lower()

            if code in (250, 251):
                return "accepted", f"smtp_{code}"
            if any(m in resp_text for m in _REPUTATION_MARKERS):
                return "inconclusive", f"smtp_{code}:sender_ip_reputation"
            if code in (450, 451, 452) or not any(m in resp_text for m in _NONEXISTENT_MARKERS):
                # 4xx = temporary by definition; a 5xx with no clear non-existence
                # language is ambiguous, not a confirmed rejection -- don't guess.
                return "inconclusive", f"smtp_{code}:ambiguous"
            return "rejected", f"smtp_{code}:no_such_user"
    except (smtplib.SMTPException, socket.error, socket.timeout) as exc:
        return "inconclusive", f"smtp_error:{type(exc).__name__}"


def validate_email(session: Session, email: EmailAddress) -> ValidationStatus:
    """Run the real check and persist the result on the EmailAddress row."""
    address = email.address

    if not _check_syntax(address):
        status = ValidationStatus.invalid
        log_event(
            session, actor="validation.email", action="checked",
            entity_type="email", entity_id=email.id,
            output_data={"status": status.value, "reason": "bad_syntax"},
        )
        email.validation_status = status
        email.checked_at = datetime.utcnow()
        return status

    domain = address.split("@")[1]
    mx_host = _get_mx_host(domain)
    if not mx_host:
        status = ValidationStatus.invalid
        log_event(
            session, actor="validation.email", action="checked",
            entity_type="email", entity_id=email.id,
            output_data={"status": status.value, "reason": "no_mx_record"},
        )
        email.validation_status = status
        email.checked_at = datetime.utcnow()
        return status

    verdict, detail = _smtp_check(mx_host, address)
    if verdict == "accepted":
        status = ValidationStatus.risky if is_role_address(address) else ValidationStatus.valid
    elif verdict == "rejected":
        status = ValidationStatus.invalid
    else:  # inconclusive -- e.g. sender-IP reputation blocked the whole session
        status = ValidationStatus.unchecked

    log_event(
        session, actor="validation.email", action="checked",
        entity_type="email", entity_id=email.id,
        output_data={"status": status.value, "detail": detail, "mx_host": mx_host},
    )
    email.validation_status = status
    email.checked_at = datetime.utcnow()
    return status
