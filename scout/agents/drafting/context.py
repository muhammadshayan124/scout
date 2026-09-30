"""Drafting Agent -- same rule as scoring/context.py: no message-writing logic lives
here. This only assembles what Claude Code needs to write a real draft (enrichment,
segment, the value prop for that segment, prior score/rationale) and persists whatever
Claude Code writes. See ARCHITECTURE.md §1, §3.5.

SEGMENT_VALUE_PROPS is reference config, not a prompt template -- it states WHAT the
offer is per segment (decided in ARCHITECTURE.md §3.1/§10), not HOW to phrase a message
about it. That phrasing judgment stays with whoever calls save_draft.
"""

from sqlalchemy.orm import Session

from scout.agents.events import log_event
from scout.agents.util import award_summary, enum_value
from scout.db.models import Channel, Company, Draft, DraftStatus, Person, Score

SEGMENT_VALUE_PROPS = {
    "ae_firm": "AI-assisted RFP analysis and proposal drafting for architecture/"
    "engineering firms -- faster turnaround on RFP responses without sacrificing the "
    "specificity that wins them.",
    "gov_contractor": "AI-assisted RFP monitoring and bid/no-bid analysis for government "
    "contractors -- surfaces relevant new opportunities and gives a fast, data-backed "
    "read on whether a given RFP is worth pursuing.",
}


def get_drafting_context(session: Session, person_id: int) -> dict | None:
    person = session.get(Person, person_id)
    if person is None:
        return None
    company: Company | None = person.company
    segment = enum_value(company.segment) if company else None

    latest_score: Score | None = (
        session.query(Score)
        .filter_by(person_id=person_id)
        .order_by(Score.scored_at.desc())
        .first()
    )

    return {
        "person_id": person.id,
        "person_name": person.name,
        "person_title": person.title,
        "company_name": company.name if company else None,
        "segment": segment,
        "value_prop": SEGMENT_VALUE_PROPS.get(segment),
        "enrichment_texts": [e.blob_jsonb.get("text") for e in person.enrichments],
        # Real, honest personalization hook for gov_contractor leads specifically --
        # SAM.gov gives no company website to enrich from, but the award itself (what
        # they won, from whom, how much) is genuinely better material than generic site
        # copy would be. None for ae_firm leads, where site enrichment is the real source.
        "award_context": award_summary(company.raw_jsonb) if segment == "gov_contractor" and company else None,
        "score": latest_score.score if latest_score else None,
        "score_rationale": latest_score.rationale if latest_score else None,
        "contact_emails": [e.address for e in person.emails if e.validation_status != "invalid"],
    }


def save_draft(session: Session, person_id: int, channel: Channel | str, body: str) -> Draft:
    """Persist a draft Claude Code has written. Status is always 'draft' -- nothing this
    function does sends anything or changes that status; see ARCHITECTURE.md §3.6 on why
    the send step is a deliberately separate, later phase.
    """
    row = Draft(person_id=person_id, channel=channel, body=body, status=DraftStatus.draft)
    session.add(row)

    log_event(
        session,
        actor="drafting",
        action="save_draft",
        entity_type="person",
        entity_id=person_id,
        input_data={"channel": enum_value(channel)},
        output_data={"chars": len(body)},
    )
    return row
