"""Scoring Agent -- deliberately contains NO scoring logic. Per ARCHITECTURE.md §1
(option B) and §3.4, scoring is a reasoning call that lives on the Claude Code side of the
MCP boundary: this module only fetches the context a scorer needs and persists whatever
score/rationale/qualified verdict Claude Code produces. Keeping the actual judgment out of
this file is what makes it free to run (no LLM API billed inside scout itself) and keeps
this code trivially testable without mocking an LLM.
"""

from sqlalchemy.orm import Session

from scout.agents.events import log_event
from scout.agents.util import award_summary, enum_value
from scout.db.models import Company, Person, Score


def get_scoring_context(session: Session, person_id: int) -> dict | None:
    """Everything needed to score a lead's fit: company info, segment, and whatever
    enrichment text has been gathered so far. Returns None if the person doesn't exist.
    """
    person = session.get(Person, person_id)
    if person is None:
        return None
    company: Company | None = person.company

    return {
        "person_id": person.id,
        "person_name": person.name,
        "person_title": person.title,
        "company_name": company.name if company else None,
        "segment": enum_value(company.segment) if company else None,
        "city": company.city if company else None,
        "state": company.state if company else None,
        "enrichment_texts": [e.blob_jsonb.get("text") for e in person.enrichments],
        "award_context": award_summary(company.raw_jsonb)
        if company and enum_value(company.segment) == "gov_contractor"
        else None,
    }


def save_score(
    session: Session,
    person_id: int,
    score: int,
    rationale: str,
    qualified: bool,
) -> Score:
    """Persist a scoring verdict that Claude Code (the reasoning side) has already
    produced from get_scoring_context's output.
    """
    row = Score(person_id=person_id, score=score, rationale=rationale, qualified=qualified)
    session.add(row)

    log_event(
        session,
        actor="scoring",
        action="save_score",
        entity_type="person",
        entity_id=person_id,
        input_data={"score": score, "qualified": qualified},
        reasoning_summary=rationale,
    )
    return row
