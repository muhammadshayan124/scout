"""Monitoring UI data contract (ARCHITECTURE.md §6) -- read layer only, so whichever
frontend gets built later (a Next.js dashboard, matching the portfolio stack, was the
suggestion) has clean endpoints. This is the "you build the frontend" half of that plan;
this file is the backend it talks to.
"""

from datetime import date, timedelta
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func
from sqlalchemy.orm import joinedload

from scout.agents.outreach.email import OutreachError, send_outreach_email
from scout.agents.ratelimit.budget import get_or_create_budget, is_killswitch_engaged
from scout.agents.ratelimit.budget import set_killswitch as _set_killswitch
from scout.agents.util import award_summary, enum_value
from scout.config import settings
from scout.db.models import (
    AgentEvent,
    Channel,
    Company,
    Draft,
    EmailAddress,
    Enrichment,
    OutreachEvent,
    Person,
    Reply,
    Score,
)
from scout.db.session import get_session

app = FastAPI(title="scout monitoring API")

# Local-only dashboard talks to this over http://localhost -- no real auth boundary here,
# so CORS is wide open on purpose rather than maintained as an allowlist.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _count(session, model, **filters) -> int:
    q = session.query(func.count(model.id))
    for k, v in filters.items():
        q = q.filter(getattr(model, k) == v)
    return q.scalar() or 0


@app.get("/funnel")
def funnel() -> dict[str, Any]:
    """Counts at each pipeline stage, all-time and today -- sourced -> enriched ->
    validated -> qualified -> drafted -> sent -> replied.
    """
    today_start = date.today()
    with get_session() as session:
        def stage(model, date_field, **filters):
            all_time = _count(session, model, **filters)
            today = (
                session.query(func.count(model.id))
                .filter(getattr(model, date_field) >= today_start)
                .filter(*[getattr(model, k) == v for k, v in filters.items()])
                .scalar() or 0
            )
            return {"all_time": all_time, "today": today}

        return {
            "sourced": stage(Company, "scraped_at"),
            "enriched": stage(Enrichment, "fetched_at"),
            "qualified": stage(Score, "scored_at", qualified=True),
            "drafted": stage(Draft, "created_at"),
            "sent": stage(OutreachEvent, "sent_at"),
            "replied": stage(Reply, "received_at"),
        }


@app.get("/budget")
def budget() -> dict[str, Any]:
    """Today's send count vs. cap per channel, and the current warmup day."""
    with get_session() as session:
        warmup_start = settings.warmup_start()
        row = get_or_create_budget(session, Channel.email, warmup_start)
        engaged, _ = is_killswitch_engaged(session)
        return {
            "channel": "email",
            "sent_today": row.sent_count,
            "cap_today": row.cap,
            "days_into_warmup": (row.date - warmup_start).days,
            "killswitch_engaged": engaged,
        }


@app.get("/activity")
def activity(limit: int = 20) -> dict[str, Any]:
    """Recent events feed -- outreach sends, replies, errors, everything logged to
    agent_events (ARCHITECTURE.md §4/§7's timeline table)."""
    with get_session() as session:
        rows = (
            session.query(AgentEvent)
            .order_by(AgentEvent.ts.desc())
            .limit(limit)
            .all()
        )
        return {
            "events": [
                {
                    "ts": r.ts.isoformat(),
                    "actor": r.actor,
                    "action": r.action,
                    "entity_type": r.entity_type,
                    "entity_id": r.entity_id,
                    "input": r.input_jsonb,
                    "output": r.output_jsonb,
                    "reasoning_summary": r.reasoning_summary,
                }
                for r in rows
            ]
        }


@app.get("/replies")
def replies(needs_human: bool | None = None) -> dict[str, Any]:
    """The queue that actually needs you -- pass needs_human=true to see only unresolved
    replies (default: all)."""
    with get_session() as session:
        q = session.query(Reply).order_by(Reply.received_at.desc())
        if needs_human is not None:
            q = q.filter(Reply.needs_human == needs_human)
        rows = q.all()
        return {
            "replies": [
                {
                    "id": r.id,
                    "person_id": r.person_id,
                    "channel": r.channel,
                    "classification": r.classification,
                    "needs_human": r.needs_human,
                    "received_at": r.received_at.isoformat() if r.received_at else None,
                    "body_preview": (r.body or "")[:200],
                }
                for r in rows
            ]
        }


@app.post("/killswitch")
def set_killswitch(engaged: bool, reason: str = "") -> dict[str, Any]:
    """Flip the global halt flag from the UI, not just a config file -- every outreach
    agent checks this before acting (ARCHITECTURE.md §3.7)."""
    with get_session() as session:
        _set_killswitch(session, engaged, reason)
        return {"killswitch_engaged": engaged, "reason": reason}


def _person_stage(person: Person) -> str:
    """Which funnel stage (§6) a person is furthest into -- drives the Leads table's
    status column without a dedicated pipeline-state column."""
    if person.drafts:
        latest = max(person.drafts, key=lambda d: d.created_at)
        if enum_value(latest.status) == "sent":
            return "sent"
        return "drafted"
    if person.scores:
        return "qualified" if max(person.scores, key=lambda s: s.scored_at).qualified else "scored"
    if person.enrichments:
        return "enriched"
    return "sourced"


@app.get("/leads")
def leads(
    segment: str | None = None,
    qualified: bool | None = None,
    contact_status: str | None = None,
) -> dict[str, Any]:
    """The main working table -- one row per person, joined with their company, latest
    score, latest draft, and reply status. This is 'leads and stuff' for the dashboard.
    contact_status filters to one of the three email-discovery lists: found (has a
    deliberate email), needs_finder (queued for the Email Finder Agent), unresolved
    (finder came up empty, parked)."""
    with get_session() as session:
        q = (
            session.query(Person)
            .options(
                joinedload(Person.company),
                joinedload(Person.scores),
                joinedload(Person.drafts),
                joinedload(Person.emails),
            )
        )
        if segment:
            q = q.join(Company).filter(Company.segment == segment)
        if contact_status:
            q = q.filter(Person.contact_status == contact_status)
        rows = q.all()

        out = []
        for p in rows:
            latest_score = max(p.scores, key=lambda s: s.scored_at, default=None)
            latest_draft = max(p.drafts, key=lambda d: d.created_at, default=None)
            if qualified is not None:
                is_q = bool(latest_score and latest_score.qualified)
                if is_q != qualified:
                    continue
            out.append({
                "person_id": p.id,
                "name": p.name,
                "title": p.title,
                "company_id": p.company_id,
                "company_name": p.company.name if p.company else None,
                "segment": enum_value(p.company.segment) if p.company else None,
                "city": p.company.city if p.company else None,
                "state": p.company.state if p.company else None,
                "emails": [e.address for e in p.emails],
                "email_status": [enum_value(e.validation_status) for e in p.emails],
                "contact_status": enum_value(p.contact_status),
                "finder_note": p.finder_note,
                "score": latest_score.score if latest_score else None,
                "qualified": latest_score.qualified if latest_score else None,
                "draft_id": latest_draft.id if latest_draft else None,
                "draft_status": enum_value(latest_draft.status) if latest_draft else None,
                "stage": _person_stage(p),
            })
        return {"leads": out}


@app.get("/contact-queue")
def contact_queue() -> dict[str, Any]:
    """Counts for the three email-discovery lists -- found / needs_finder / unresolved --
    for the Leads page's tab badges."""
    with get_session() as session:
        rows = (
            session.query(Person.contact_status, func.count(Person.id))
            .group_by(Person.contact_status)
            .all()
        )
        counts = {enum_value(status): n for status, n in rows}
        return {
            "found": counts.get("found", 0),
            "needs_finder": counts.get("needs_finder", 0),
            "unresolved": counts.get("unresolved", 0),
        }


@app.get("/leads/{person_id}")
def lead_detail(person_id: int) -> dict[str, Any]:
    """Full drill-down for one lead -- score rationale, draft body, source award context,
    reply thread, everything the dashboard's detail drawer needs in one call."""
    with get_session() as session:
        p = (
            session.query(Person)
            .options(
                joinedload(Person.company),
                joinedload(Person.scores),
                joinedload(Person.drafts),
                joinedload(Person.emails),
                joinedload(Person.enrichments),
            )
            .filter(Person.id == person_id)
            .first()
        )
        if p is None:
            raise HTTPException(status_code=404, detail="person not found")

        replies = (
            session.query(Reply)
            .filter(Reply.person_id == person_id)
            .order_by(Reply.received_at.desc())
            .all()
        )

        return {
            "person_id": p.id,
            "name": p.name,
            "title": p.title,
            "linkedin_url": p.linkedin_url,
            "contact_status": enum_value(p.contact_status),
            "finder_note": p.finder_note,
            "company": {
                "id": p.company.id,
                "name": p.company.name,
                "domain": p.company.domain,
                "segment": enum_value(p.company.segment),
                "city": p.company.city,
                "state": p.company.state,
                "source": p.company.source,
                "award_summary": award_summary(p.company.raw_jsonb),
            } if p.company else None,
            "emails": [
                {"address": e.address, "status": enum_value(e.validation_status)}
                for e in p.emails
            ],
            "scores": [
                {
                    "id": s.id,
                    "score": s.score,
                    "qualified": s.qualified,
                    "rationale": s.rationale,
                    "scored_at": s.scored_at.isoformat(),
                }
                for s in sorted(p.scores, key=lambda s: s.scored_at, reverse=True)
            ],
            "drafts": [
                {
                    "id": d.id,
                    "channel": enum_value(d.channel),
                    "status": enum_value(d.status),
                    "body": d.body,
                    "created_at": d.created_at.isoformat(),
                }
                for d in sorted(p.drafts, key=lambda d: d.created_at, reverse=True)
            ],
            "replies": [
                {
                    "id": r.id,
                    "channel": enum_value(r.channel),
                    "classification": r.classification,
                    "needs_human": r.needs_human,
                    "received_at": r.received_at.isoformat() if r.received_at else None,
                    "body": r.body,
                }
                for r in replies
            ],
        }


@app.post("/drafts/{draft_id}/send")
def send_draft(draft_id: int) -> dict[str, Any]:
    """Fire the real, gated sender (agents/outreach/email.py) on one draft -- every check
    in can_send() (killswitch, suppression, daily cap) plus the validated-email
    requirement runs before anything goes out. Returns sent=False with a reason instead
    of raising for any gate failure; only SMTP config errors raise (nothing could send)."""
    with get_session() as session:
        try:
            result = send_outreach_email(session, draft_id)
        except OutreachError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return result


@app.get("/companies")
def companies(segment: str | None = None, limit: int = 500) -> dict[str, Any]:
    """Raw sourced-company list (§4) -- mostly gov_contractor/ae_firm rows that haven't
    necessarily produced a Person yet, unlike /leads which is person-centric."""
    with get_session() as session:
        q = session.query(Company).order_by(Company.scraped_at.desc())
        if segment:
            q = q.filter(Company.segment == segment)
        rows = q.limit(limit).all()
        return {
            "companies": [
                {
                    "id": c.id,
                    "name": c.name,
                    "domain": c.domain,
                    "segment": enum_value(c.segment),
                    "source": c.source,
                    "city": c.city,
                    "state": c.state,
                    "people_count": len(c.people),
                    "scraped_at": c.scraped_at.isoformat(),
                }
                for c in rows
            ],
            "total": q.count(),
        }
