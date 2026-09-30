"""Email Finder Agent -- like scoring/drafting, deliberately contains no search logic of
its own. Per the same ARCHITECTURE.md §1 split every other reasoning step follows: this
module only gathers context for, and persists the result of, a web-research step that
Claude Code performs (WebSearch/WebFetch against public pages -- company sites, federal
contractor directories, SAM.gov entity data -- never cookie-authenticated social scraping;
see the explicit decision to keep LinkedIn/social lookups public-page-only, same reasoning
as the project's standing "no LinkedIn automation" call).

Runs only against people already sitting in contact_status='needs_finder' (set by
enrich_company_site's classify_contact_status call) -- the ones ordinary site enrichment
couldn't resolve to a deliberate, non-role email.
"""

from sqlalchemy.orm import Session

from scout.agents.events import log_event
from scout.agents.util import award_summary, classify_contact_status, enum_value, is_role_address
from scout.db.models import Company, ContactStatus, EmailAddress, Person


def get_finder_context(session: Session, person_id: int) -> dict | None:
    """Everything needed to go find this person's (or their company's) real email:
    company identity/location for search queries, any award context (gov_contractor's
    best lead-in), whatever enrichment text already exists, and the generic addresses
    already on file so they're not rediscovered and mistaken for a new find.
    """
    person = session.get(Person, person_id)
    if person is None:
        return None
    company: Company | None = person.company

    return {
        "person_id": person.id,
        "person_name": person.name,
        "person_title": person.title,
        "contact_status": enum_value(person.contact_status),
        "prior_finder_note": person.finder_note,
        "company_id": company.id if company else None,
        "company_name": company.name if company else None,
        "company_domain": company.domain if company else None,
        "segment": enum_value(company.segment) if company else None,
        "city": company.city if company else None,
        "state": company.state if company else None,
        "already_on_file": [
            {"address": e.address, "role_address": is_role_address(e.address)}
            for e in person.emails
        ],
        "enrichment_texts": [e.blob_jsonb.get("text") for e in person.enrichments],
        "award_context": award_summary(company.raw_jsonb)
        if company and enum_value(company.segment) == "gov_contractor"
        else None,
    }


def save_finder_result(
    session: Session,
    person_id: int,
    email: str | None,
    source: str | None = None,
    note: str | None = None,
    contact_name: str | None = None,
    contact_title: str | None = None,
) -> dict:
    """Persist what the finder step (Claude Code's web research) turned up.

    email=<address> -- moves the person back to the first list ('found'): saves the
    address (status='unchecked', same as any other freshly-discovered email -- still
    needs the Validation Agent before it can ever be sent to) and, if this research also
    turned up who the person actually is, corrects name/title (this is exactly how the
    [Lead 2] contact's name was corrected from a stale "[contact E]" to the real CEO
    "[contact D]").

    email=None -- moves the person to the third list ('unresolved'): parked with `note`
    explaining what was checked and came up empty, rather than left stuck in the
    needs_finder queue indefinitely.
    """
    person = session.get(Person, person_id)
    if person is None:
        return {"error": f"no person with id {person_id}"}

    if contact_name:
        person.name = contact_name
    if contact_title:
        person.title = contact_title

    if email:
        if is_role_address(email):
            return {
                "error": "refusing to save a role/generic address as a finder result "
                "-- that's exactly what this agent exists to avoid; use enrichment "
                "instead if a generic inbox is genuinely the best available"
            }
        exists = (
            session.query(EmailAddress)
            .filter_by(person_id=person.id, address=email)
            .one_or_none()
        )
        if not exists:
            session.add(EmailAddress(person_id=person.id, address=email))
        person.finder_note = None
        session.flush()
        person.contact_status = classify_contact_status(person)

        log_event(
            session, actor="emailfinder", action="save_finder_result",
            entity_type="person", entity_id=person.id,
            input_data={"email": email, "source": source},
            reasoning_summary=note,
        )
        return {
            "person_id": person.id,
            "contact_status": person.contact_status.value
            if hasattr(person.contact_status, "value")
            else person.contact_status,
            "email": email,
        }

    person.contact_status = ContactStatus.unresolved
    person.finder_note = note

    log_event(
        session, actor="emailfinder", action="save_finder_result",
        entity_type="person", entity_id=person.id,
        input_data={"email": None},
        reasoning_summary=note,
    )
    return {"person_id": person.id, "contact_status": "unresolved", "note": note}
