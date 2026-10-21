"""Reply/Response Agent, mechanical half (ARCHITECTURE.md §3.8): watches the mailbox via
IMAP, matches incoming senders against known lead emails, and stores new replies. Only the
message-fetching and unsubscribe-detection are done here -- per the architecture's split
(same one Scoring/Drafting use, ARCHITECTURE.md §1), classifying reply *intent*
(interested/not-interested/OOO) is a reasoning call and lives in classify.py's
context/save pair instead, not here.

Unsubscribe detection is the one exception explicitly called out as not needing LLM
judgment (§3.8: "no LLM judgment call needed there") -- it's handled here, deterministically,
and immediately adds to the suppression list rather than waiting for a reasoning pass.
"""

import email
import imaplib
import re
from email.header import decode_header
from email.utils import parseaddr

from sqlalchemy import func
from sqlalchemy.orm import Session

from scout.agents.events import log_event
from scout.agents.ratelimit.budget import suppress
from scout.config import settings
from scout.db.models import Channel, EmailAddress, Reply

_UNSUBSCRIBE_PATTERNS = re.compile(
    r"\bunsubscribe\b|\bremove me\b|\bstop (?:contacting|emailing)\b|\bopt[\s-]?out\b|"
    r"\bdo not (?:contact|email) me\b|\btake me off\b",
    re.IGNORECASE,
)


class ImapError(RuntimeError):
    pass


def _decode(value: str | None) -> str:
    if not value:
        return ""
    parts = decode_header(value)
    return "".join(
        (chunk.decode(enc or "utf-8", errors="replace") if isinstance(chunk, bytes) else chunk)
        for chunk, enc in parts
    )


def _extract_body(msg: email.message.Message, max_chars: int = 4000) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and not part.get("Content-Disposition"):
                charset = part.get_content_charset() or "utf-8"
                return part.get_payload(decode=True).decode(charset, errors="replace")[:max_chars]
        return ""
    charset = msg.get_content_charset() or "utf-8"
    payload = msg.get_payload(decode=True)
    return payload.decode(charset, errors="replace")[:max_chars] if payload else ""


def check_replies(session: Session, limit: int = 50) -> dict:
    """Fetch unseen messages, match each sender against a known lead email, store a Reply
    row for matches, and auto-suppress anything that reads as an unsubscribe request --
    all without touching whether the mailbox is otherwise organized (marks messages Seen,
    doesn't move/delete anything).
    """
    if not (settings.imap_username and settings.imap_password):
        raise ImapError(
            "IMAP not configured -- set IMAP_USERNAME and IMAP_PASSWORD (the same Gmail "
            "App Password used for SMTP works here too) in .env"
        )

    matched, unmatched, auto_suppressed = [], [], []

    with imaplib.IMAP4_SSL(settings.imap_host, settings.imap_port) as imap:
        imap.login(settings.imap_username, settings.imap_password)
        imap.select(settings.imap_mailbox)

        status, data = imap.search(None, "UNSEEN")
        if status != "OK":
            raise ImapError(f"IMAP search failed: {status}")

        message_ids = data[0].split()[:limit]
        for mid in message_ids:
            status, msg_data = imap.fetch(mid, "(RFC822)")
            if status != "OK" or not msg_data or not msg_data[0]:
                continue
            msg = email.message_from_bytes(msg_data[0][1])

            _, sender_addr = parseaddr(msg.get("From", ""))
            sender_addr = sender_addr.lower().strip()
            subject = _decode(msg.get("Subject"))
            body = _extract_body(msg)

            email_row = (
                session.query(EmailAddress).filter(func.lower(EmailAddress.address) == sender_addr).first()
                if sender_addr else None
            )
            if email_row is None:
                unmatched.append(sender_addr or "(no From address)")
                continue

            is_unsubscribe = bool(_UNSUBSCRIBE_PATTERNS.search(f"{subject}\n{body}"))
            reply = Reply(
                person_id=email_row.person_id,
                channel=Channel.email,
                body=f"Subject: {subject}\n\n{body}",
                classification="unsubscribe" if is_unsubscribe else None,
                needs_human=not is_unsubscribe,
            )
            session.add(reply)
            session.flush()
            matched.append({"person_id": email_row.person_id, "from": sender_addr, "reply_id": reply.id})

            if is_unsubscribe:
                suppress(session, email_row.person_id, "unsubscribe request in reply")
                auto_suppressed.append(email_row.person_id)

    log_event(
        session, actor="reply.imap_watch", action="check_replies",
        output_data={"matched": len(matched), "unmatched": len(unmatched), "auto_suppressed": len(auto_suppressed)},
    )
    return {"matched": matched, "unmatched_senders": unmatched, "auto_suppressed_person_ids": auto_suppressed}
