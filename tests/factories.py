import itertools

from scout.db.models import (
    Channel,
    Company,
    ContactStatus,
    Draft,
    DraftStatus,
    EmailAddress,
    Person,
    Segment,
)

_ids = itertools.count(1)


def make_person(session, *, emails=(), name="Ada Lovelace", contact_status=ContactStatus.needs_finder):
    company = Company(name=f"Acme Engineering {next(_ids)}", source="test", segment=Segment.ae_firm, state="TX", city="Austin")
    session.add(company)
    session.flush()
    person = Person(company_id=company.id, name=name, source="test", contact_status=contact_status)
    session.add(person)
    session.flush()
    for address, status in emails:
        session.add(EmailAddress(person_id=person.id, address=address, validation_status=status))
    session.flush()
    session.refresh(person)
    return person


def make_draft(session, person, *, body="Subject: Hello\n\nBody text.", channel=Channel.email, status=DraftStatus.draft):
    draft = Draft(person_id=person.id, channel=channel, body=body, status=status)
    session.add(draft)
    session.flush()
    return draft
