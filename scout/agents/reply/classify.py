"""Reply classification -- reasoning half of the Reply/Response Agent. Same rule as
scoring/context.py and drafting/context.py: no classification logic lives here, only
context-fetch and result-save. Classifying whether a reply is genuinely interested vs.
a polite decline vs. an out-of-office autoresponder needs real language understanding,
not keyword matching (unlike unsubscribe detection, which imap_watch.py already handles
deterministically per ARCHITECTURE.md §3.8's explicit "no LLM judgment call needed there").
"""

from sqlalchemy.orm import Session

from scout.agents.events import log_event
from scout.db.models import Company, Draft, Person, Reply


def get_reply_classification_context(session: Session, reply_id: int) -> dict | None:
    reply = session.get(Reply, reply_id)
    if reply is None:
        return None
    person: Person | None = session.get(Person, reply.person_id)
    company: Company | None = person.company if person else None

    original_draft = (
        session.query(Draft)
        .filter_by(person_id=reply.person_id, channel=reply.channel)
        .order_by(Draft.created_at.desc())
        .first()
    )

    return {
        "reply_id": reply.id,
        "reply_body": reply.body,
        "received_at": reply.received_at.isoformat() if reply.received_at else None,
        "person_name": person.name if person else None,
        "company_name": company.name if company else None,
        "original_draft_body": original_draft.body if original_draft else None,
    }


def save_reply_classification(
    session: Session, reply_id: int, classification: str, needs_human: bool
) -> Reply:
    """classification is a free-text label (e.g. 'interested', 'not_interested',
    'out_of_office', 'question') decided by whoever called get_reply_classification_context
    -- not constrained to an enum here, since reply intent doesn't reduce cleanly to a
    fixed set the way validation status or draft status do.
    """
    reply = session.get(Reply, reply_id)
    if reply is None:
        raise ValueError(f"no reply with id {reply_id}")
    reply.classification = classification
    reply.needs_human = needs_human

    log_event(
        session, actor="reply.classify", action="save_classification",
        entity_type="reply", entity_id=reply_id,
        output_data={"classification": classification, "needs_human": needs_human},
    )
    return reply
