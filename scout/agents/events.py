"""One call site for writing to agent_events, so every agent's timeline is captured the
same way instead of scattered ad-hoc logging. See ARCHITECTURE.md §4/§7.
"""

from sqlalchemy.orm import Session

from scout.config import settings
from scout.db.models import AgentEvent


def log_event(
    session: Session,
    *,
    actor: str,
    action: str,
    entity_type: str | None = None,
    entity_id: int | None = None,
    input_data: dict | None = None,
    output_data: dict | None = None,
    reasoning_summary: str | None = None,
) -> AgentEvent:
    event = AgentEvent(
        session_id=settings.session_id(),
        actor=actor,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        input_jsonb=input_data,
        output_jsonb=output_data,
        reasoning_summary=reasoning_summary,
    )
    session.add(event)
    session.flush()  # get event.id without committing the outer transaction
    return event
