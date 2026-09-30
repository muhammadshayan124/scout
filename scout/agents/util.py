"""Small shared helper. The Segment/Channel/etc. columns in db/models.py are declared as
plain String, not SQLAlchemy's Enum type, so a value fetched fresh off a construct (still
the Python enum member) has .value, but the same field read back after a DB round-trip
is already a plain str (SQLAlchemy doesn't reconstruct it) -- confirmed by hitting
AttributeError on company.segment.value in a real test. This normalizes both cases
instead of assuming one.
"""


def enum_value(x) -> str | None:
    if x is None:
        return None
    return x.value if hasattr(x, "value") else x


# Common catch-all/role addresses -- deliverable but not a real person. Shared between
# validate.py (SMTP-deliverability check) and the contact_status classification (is this
# a "deliberate", person-level email or a generic inbox that tends to get ignored).
ROLE_LOCALPARTS = {
    "info", "contact", "admin", "sales", "support", "hello", "office", "team",
}


def is_role_address(address: str) -> bool:
    localpart = address.split("@")[0].lower()
    return localpart in ROLE_LOCALPARTS


def classify_contact_status(person) -> "ContactStatus":  # noqa: F821 -- avoid circular import
    """The three-list rule: a person with at least one deliberate (non-role) email on
    record is 'found'; otherwise they're queued for the Email Finder Agent. Never
    downgrades an already-'unresolved' person back to needs_finder on its own -- only
    the finder agent's own save_finder_result (agents/emailfinder/finder.py) can move
    someone into or out of 'unresolved', so a person the finder already gave up on
    doesn't silently re-enter the queue just because enrichment re-ran.
    """
    from scout.db.models import ContactStatus

    has_deliberate_email = any(not is_role_address(e.address) for e in person.emails)
    if has_deliberate_email:
        return ContactStatus.found
    if enum_value(person.contact_status) == ContactStatus.unresolved.value:
        return ContactStatus.unresolved
    return ContactStatus.needs_finder


def ensure_person(session, company) -> "Person":  # noqa: F821 -- avoid circular import
    """Score and Draft both require a person_id, but a freshly-sourced company (gov_contractor
    especially, per sourcing/sam_gov.py's fix) may have zero Person rows -- no named contact
    found yet. Same placeholder pattern enrichment.py already uses for a discovered-but-
    unnamed email: get-or-create a generic holder rather than blocking scoring/drafting on
    having a named individual first.
    """
    from scout.db.models import Person

    if company.people:
        return company.people[0]
    person = Person(company_id=company.id, name=None, title="Company (no named contact yet)", source="placeholder")
    session.add(person)
    session.flush()
    company.people.append(person)
    return person


def award_summary(raw_jsonb: dict | None) -> dict | None:
    """Pull the useful, human-readable fields out of a SAM.gov raw award record --
    title, amount, awarding agency, NAICS -- for gov_contractor leads that have no
    company website to enrich from (see enrichment/enrich.py's docstring). This is
    genuinely better personalization material than site text: it's the specific, real
    reason this company is a lead at all.
    """
    if not raw_jsonb:
        return None
    award = raw_jsonb.get("award") or {}
    return {
        "opportunity_title": raw_jsonb.get("title"),
        "award_amount": award.get("amount"),
        "award_date": award.get("date"),
        "awarding_agency": raw_jsonb.get("fullParentPathName"),
        "naics_code": raw_jsonb.get("naicsCode"),
        "sam_gov_link": raw_jsonb.get("uiLink"),
    }
