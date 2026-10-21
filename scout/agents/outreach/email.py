"""Email Outreach Agent (ARCHITECTURE.md §3.6) -- the first channel that actually sends
anything. Two entry points, deliberately separate:

- send_test_email: raw SMTP connectivity/credentials check against an address you specify.
  Not gated by rate limits/suppression/drafts -- it's a plumbing test, not outreach.
- send_outreach_email: the real, gated sender. Every check in can_send() (kill switch,
  suppression, daily cap) runs before anything goes out, and it refuses to send to an
  email that hasn't been confirmed 'valid' by the Validation Agent -- not 'unchecked', not
  'risky'. That's a deliberately conservative default: this mailbox has no dedicated
  domain backing it (ARCHITECTURE.md §3.6), so a bounce spike hits the same inbox used for
  everything else. Loosening this is a config decision to make consciously later, not a
  default to ship with.
"""

import smtplib
from email.mime.text import MIMEText

from sqlalchemy.orm import Session

from scout.agents.events import log_event
from scout.agents.ratelimit.budget import can_send, record_send
from scout.config import settings
from scout.db.models import Channel, Draft, DraftStatus, EmailAddress, OutreachEvent, Person


class OutreachError(RuntimeError):
    pass


def _smtp_send(to_address: str, subject: str, body: str) -> None:
    if not (settings.smtp_username and settings.smtp_password and settings.smtp_from_address):
        raise OutreachError(
            "SMTP not configured -- set SMTP_USERNAME, SMTP_PASSWORD (a Gmail App "
            "Password, not your account password), and SMTP_FROM_ADDRESS in .env"
        )

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = settings.smtp_from_address
    msg["To"] = to_address

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as smtp:
        smtp.starttls()
        smtp.login(settings.smtp_username, settings.smtp_password)
        smtp.sendmail(settings.smtp_from_address, [to_address], msg.as_string())


def send_test_email(to_address: str, subject: str = "scout: SMTP test", body: str = "This is a connectivity test, not outreach.") -> dict:
    """Pure plumbing test -- sends a real email to `to_address` via the configured
    account, with no rate limiting, suppression check, or draft involved. Use this to
    confirm SMTP credentials work before ever calling send_outreach_email. Intended for
    sending to an address you control (yourself), not a lead.
    """
    _smtp_send(to_address, subject, body)
    return {"sent_to": to_address, "subject": subject}


def _extract_subject(body: str) -> tuple[str, str]:
    """Drafts are saved as 'Subject: ...\\n\\n<body>' (see drafting/context.py usage
    pattern) -- split that back out. Falls back to a generic subject if the draft wasn't
    written in that shape.
    """
    lines = body.split("\n", 1)
    if lines[0].lower().startswith("subject:"):
        subject = lines[0][len("subject:"):].strip()
        rest = lines[1].lstrip("\n") if len(lines) > 1 else ""
        return subject, rest
    return "Following up", body


def send_outreach_email(session: Session, draft_id: int) -> dict:
    """The real, gated send. Every failure mode returns a clear, non-throwing result
    (sent=False, reason=...) rather than raising, so a caller can check many drafts in a
    loop without a single failure stopping the batch -- except SMTP config errors, which
    do raise, since those mean nothing in this batch can possibly send.
    """
    draft = session.get(Draft, draft_id)
    if draft is None:
        return {"sent": False, "reason": "no_such_draft"}
    if draft.status != DraftStatus.draft:
        return {"sent": False, "reason": f"draft_already_{draft.status}"}
    if draft.channel != Channel.email:
        # This sender speaks SMTP only; a draft written for another channel must never go out as email.
        return {"sent": False, "reason": "not_an_email_draft"}

    person = session.get(Person, draft.person_id)
    valid_emails = [e for e in person.emails if e.validation_status == "valid"]
    if not valid_emails:
        return {"sent": False, "reason": "no_validated_email (must be status='valid', not just not-invalid)"}
    target = valid_emails[0]

    allowed, reason = can_send(session, Channel.email, settings.warmup_start(), person.id)
    if not allowed:
        return {"sent": False, "reason": reason}

    subject, body = _extract_subject(draft.body)
    _smtp_send(target.address, subject, body)

    record_send(session, Channel.email, settings.warmup_start())
    draft.status = DraftStatus.sent
    event = OutreachEvent(person_id=person.id, channel=Channel.email, draft_id=draft.id, result="sent")
    session.add(event)

    log_event(
        session, actor="outreach.email", action="send",
        entity_type="draft", entity_id=draft.id,
        output_data={"to": target.address, "subject": subject},
    )
    return {"sent": True, "to": target.address, "subject": subject}
