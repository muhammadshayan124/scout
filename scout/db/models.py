"""Data model, per ARCHITECTURE.md §4. Postgres via SQLAlchemy 2.0 ORM."""

import enum
from datetime import datetime, date

from sqlalchemy import (
    ForeignKey,
    String,
    Text,
    Boolean,
    Integer,
    Float,
    DateTime,
    Date,
    UniqueConstraint,
    Index,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Segment(str, enum.Enum):
    ae_firm = "ae_firm"
    gov_contractor = "gov_contractor"


class DraftStatus(str, enum.Enum):
    draft = "draft"
    queued = "queued"
    sent = "sent"


class ValidationStatus(str, enum.Enum):
    valid = "valid"
    invalid = "invalid"
    risky = "risky"
    unchecked = "unchecked"


class Channel(str, enum.Enum):
    email = "email"
    linkedin = "linkedin"
    social_other = "social_other"


class ContactStatus(str, enum.Enum):
    """Where a person sits in the email-discovery pipeline (three lists per the user's
    design): enrichment finds a deliberate, non-role email -> found. Enrichment finds
    nothing (or only a generic info@/contact@-style address) -> needs_finder, queued for
    the dedicated Email Finder Agent. The finder agent also comes up empty -> unresolved,
    parked rather than left stuck in the queue forever."""

    found = "found"
    needs_finder = "needs_finder"
    unresolved = "unresolved"


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(500))
    domain: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(50))  # 'sam_gov' | 'acec_tx' | ...
    segment: Mapped[Segment] = mapped_column(String(30))
    industry: Mapped[str | None] = mapped_column(String(255), nullable=True)
    size_bucket: Mapped[str | None] = mapped_column(String(50), nullable=True)
    geography: Mapped[str | None] = mapped_column(String(100), nullable=True)
    city: Mapped[str | None] = mapped_column(String(255), nullable=True)
    state: Mapped[str | None] = mapped_column(String(50), nullable=True)
    country: Mapped[str | None] = mapped_column(String(100), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    raw_jsonb: Mapped[dict | None] = mapped_column(JSONB, nullable=True)  # full source record
    scraped_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    people: Mapped[list["Person"]] = relationship(back_populates="company")

    __table_args__ = (
        # city included deliberately: large firms (AECOM etc.) register each regional
        # office separately with the same name in the same state -- verified live against
        # the ACEC directory (AECOM has 5 distinct TX offices). Dedup on name+source+state
        # alone would silently drop all but one as a false duplicate.
        UniqueConstraint(
            "name", "source", "state", "city", name="uq_company_name_source_state_city"
        ),
        Index("ix_companies_segment", "segment"),
    )


class Person(Base):
    __tablename__ = "people"

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id"), nullable=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    linkedin_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(50))
    scraped_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    contact_status: Mapped[ContactStatus] = mapped_column(
        String(20), default=ContactStatus.needs_finder
    )
    finder_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    company: Mapped["Company"] = relationship(back_populates="people")
    enrichments: Mapped[list["Enrichment"]] = relationship(back_populates="person")
    emails: Mapped[list["EmailAddress"]] = relationship(back_populates="person")
    scores: Mapped[list["Score"]] = relationship(back_populates="person")
    drafts: Mapped[list["Draft"]] = relationship(back_populates="person")

    __table_args__ = (Index("ix_people_contact_status", "contact_status"),)


class Enrichment(Base):
    __tablename__ = "enrichment"

    id: Mapped[int] = mapped_column(primary_key=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("people.id"))
    blob_jsonb: Mapped[dict] = mapped_column(JSONB)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    person: Mapped["Person"] = relationship(back_populates="enrichments")


class EmailAddress(Base):
    __tablename__ = "emails"

    id: Mapped[int] = mapped_column(primary_key=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("people.id"))
    address: Mapped[str] = mapped_column(String(320))
    validation_status: Mapped[ValidationStatus] = mapped_column(
        String(20), default=ValidationStatus.unchecked
    )
    checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    person: Mapped["Person"] = relationship(back_populates="emails")

    __table_args__ = (UniqueConstraint("person_id", "address", name="uq_email_person_address"),)


class Score(Base):
    __tablename__ = "scores"

    id: Mapped[int] = mapped_column(primary_key=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("people.id"))
    score: Mapped[int] = mapped_column(Integer)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    qualified: Mapped[bool] = mapped_column(Boolean, default=False)
    scored_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    person: Mapped["Person"] = relationship(back_populates="scores")


class Draft(Base):
    __tablename__ = "drafts"

    id: Mapped[int] = mapped_column(primary_key=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("people.id"))
    channel: Mapped[Channel] = mapped_column(String(20))
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[DraftStatus] = mapped_column(String(20), default=DraftStatus.draft)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    person: Mapped["Person"] = relationship(back_populates="drafts")


class OutreachEvent(Base):
    __tablename__ = "outreach_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("people.id"))
    channel: Mapped[Channel] = mapped_column(String(20))
    draft_id: Mapped[int | None] = mapped_column(ForeignKey("drafts.id"), nullable=True)
    sent_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    result: Mapped[str | None] = mapped_column(String(50), nullable=True)


class Reply(Base):
    __tablename__ = "replies"

    id: Mapped[int] = mapped_column(primary_key=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("people.id"))
    channel: Mapped[Channel] = mapped_column(String(20))
    body: Mapped[str] = mapped_column(Text)
    classification: Mapped[str | None] = mapped_column(String(50), nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    needs_human: Mapped[bool] = mapped_column(Boolean, default=False)


class SuppressionListEntry(Base):
    __tablename__ = "suppression_list"

    id: Mapped[int] = mapped_column(primary_key=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("people.id"))
    reason: Mapped[str] = mapped_column(String(100))
    added_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (UniqueConstraint("person_id", name="uq_suppression_person"),)


class RateBudget(Base):
    __tablename__ = "rate_budget"

    channel: Mapped[Channel] = mapped_column(String(20), primary_key=True)
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    sent_count: Mapped[int] = mapped_column(Integer, default=0)
    cap: Mapped[int] = mapped_column(Integer)


class SystemFlag(Base):
    """Small key/value table for cross-cutting state that needs to be toggleable from
    outside a single process -- the kill switch (ARCHITECTURE.md §3.7/§6: "POST
    /killswitch ... not just a config file") being the reason this exists rather than a
    file or env var.
    """

    __tablename__ = "system_flags"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AgentEvent(Base):
    """Append-only audit log -- the 'save the whole timeline' table. See ARCHITECTURE.md §4/§7."""

    __tablename__ = "agent_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    session_id: Mapped[str] = mapped_column(String(64))
    actor: Mapped[str] = mapped_column(String(100))
    action: Mapped[str] = mapped_column(String(100))
    entity_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    entity_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input_jsonb: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    output_jsonb: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    reasoning_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (Index("ix_agent_events_entity", "entity_type", "entity_id"),)
