"""MCP server wiring, same shape as warden/src/warden/server.py: thin @mcp.tool()
wrappers around plain functions in scout.agents.*, which own all the actual logic and are
independently testable without any MCP machinery involved.

Per ARCHITECTURE.md §1 (option B): this server exposes primitives only. All reasoning
(scoring, drafting, deciding what to do next) happens on the Claude Code side that calls
these tools -- nothing here makes an LLM call itself.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from scout.agents.drafting.context import get_drafting_context as _get_drafting_context
from scout.agents.drafting.context import save_draft as _save_draft
from scout.agents.emailfinder.finder import get_finder_context as _get_finder_context
from scout.agents.emailfinder.finder import save_finder_result as _save_finder_result
from scout.agents.enrichment.enrich import enrich_company_site
from scout.agents.outreach.email import send_outreach_email as _send_outreach_email
from scout.agents.outreach.email import send_test_email as _send_test_email
from scout.agents.ratelimit.budget import get_or_create_budget, is_killswitch_engaged
from scout.agents.ratelimit.budget import set_killswitch as _set_killswitch
from scout.agents.ratelimit.budget import suppress as _suppress
from scout.agents.reply.classify import get_reply_classification_context as _get_reply_classification_context
from scout.agents.reply.classify import save_reply_classification as _save_reply_classification
from scout.agents.reply.imap_watch import check_replies as _check_replies
from scout.agents.scoring.context import get_scoring_context as _get_scoring_context
from scout.agents.scoring.context import save_score as _save_score
from scout.agents.sourcing import acec_tx, sam_gov
from scout.agents.util import ensure_person
from scout.agents.validation.validate import validate_email as _validate_email
from scout.config import settings
from scout.db.models import Channel, Company, Person
from scout.db.session import get_session

mcp = FastMCP("scout")


@mcp.tool()
def source_gov_contractors(
    naics_code: str | None = None,
    states: list[str] | None = None,
    days_back: int = 90,
) -> dict[str, Any]:
    """Source government-contractor leads from SAM.gov contract award data (US only).
    naics_code narrows by industry (e.g. '541330' = engineering services). states
    defaults to all US states/territories if omitted. Read-only, no outreach."""
    records = sam_gov.fetch_awards(naics_code=naics_code, states=states, days_back=days_back)
    with get_session() as session:
        companies = sam_gov.ingest_awards(session, records)
        return {
            "records_fetched": len(records),
            "companies_created": len(companies),
            "company_names": [c.name for c in companies],
        }


@mcp.tool()
def source_ae_firms(state: str = "TX", max_pages: int = 500) -> dict[str, Any]:
    """Source architecture/engineering firm leads from the national ACEC directory,
    filtered to `state`. Defaults to Texas (first-pass scope, ARCHITECTURE.md §10).
    Read-only, no outreach."""
    records = acec_tx.fetch_firms(state=state, max_pages=max_pages)
    with get_session() as session:
        companies = acec_tx.ingest_firms(session, records, state=state)
        return {
            "records_fetched": len(records),
            "companies_created": len(companies),
            "company_names": [c.name for c in companies],
        }


@mcp.tool()
def enrich_lead(company_id: int) -> dict[str, Any]:
    """Fetch and extract text from a company's own website for personalization context.
    Read-only, lowest-risk step in the pipeline."""
    with get_session() as session:
        company = session.get(Company, company_id)
        if not company:
            return {"error": f"no company with id {company_id}"}
        blob = enrich_company_site(session, company)
        if blob is None:
            return {"error": "no domain/url on record, or fetch failed"}
        return {"company": company.name, "chars_extracted": len(blob["text"])}


@mcp.tool()
def ensure_lead_contact(company_id: int) -> dict[str, Any]:
    """Get (or create a placeholder for) a person_id to score/draft against for a
    company. Needed because scoring/drafting are keyed on person_id, but a freshly-sourced
    company -- gov_contractor ones especially, since SAM.gov gives no real company contact
    (see sourcing/sam_gov.py) -- may not have a named individual yet. Call this before
    get_scoring_context/get_drafting_context if you don't already have a person_id."""
    with get_session() as session:
        company = session.get(Company, company_id)
        if not company:
            return {"error": f"no company with id {company_id}"}
        person = ensure_person(session, company)
        return {"person_id": person.id, "person_name": person.name, "company": company.name}


@mcp.tool()
def validate_lead_emails(person_id: int) -> dict[str, Any]:
    """Run real SMTP-level validation (syntax -> MX -> SMTP handshake, nothing sent) on
    every not-yet-checked email address for a person. Not optional before outreach --
    protects sender reputation. Read-only against the target mail server (no message
    sent)."""
    with get_session() as session:
        person = session.get(Person, person_id)
        if not person:
            return {"error": f"no person with id {person_id}"}
        results = []
        for email in person.emails:
            if email.validation_status in ("unchecked", None):
                status = _validate_email(session, email)
                results.append({"address": email.address, "status": status.value})
        return {"person": person.name, "checked": results}


@mcp.tool()
def get_finder_context(person_id: int) -> dict[str, Any]:
    """Fetch everything needed to go find a real, deliberate (non-role) email for a
    person stuck in contact_status='needs_finder': company identity/location for search
    queries, award context, existing enrichment text, and generic addresses already on
    file. The actual search (WebSearch/WebFetch against public pages -- company sites,
    federal contractor directories, SAM.gov entity data; never cookie-authenticated
    social scraping) is a reasoning step done by whoever calls this -- see
    save_finder_result."""
    with get_session() as session:
        ctx = _get_finder_context(session, person_id)
        return ctx or {"error": f"no person with id {person_id}"}


@mcp.tool()
def save_finder_result(
    person_id: int,
    email: str | None = None,
    source: str | None = None,
    note: str | None = None,
    contact_name: str | None = None,
    contact_title: str | None = None,
) -> dict[str, Any]:
    """Persist what get_finder_context's research turned up. Pass email=<address> (plus
    source/contact_name/contact_title if discovered) to move the person back to the
    'found' list -- the address still needs validate_lead_emails before it can be sent
    to. Pass email=None with a `note` explaining what was checked to park the person as
    'unresolved' instead of leaving them stuck in the needs_finder queue."""
    with get_session() as session:
        return _save_finder_result(
            session, person_id, email,
            source=source, note=note, contact_name=contact_name, contact_title=contact_title,
        )


@mcp.tool()
def get_scoring_context(person_id: int) -> dict[str, Any]:
    """Fetch everything needed to score a lead's fit: company, segment, enrichment text.
    Scoring itself is a reasoning step done by whoever calls this, not by scout -- see
    save_score."""
    with get_session() as session:
        ctx = _get_scoring_context(session, person_id)
        return ctx or {"error": f"no person with id {person_id}"}


@mcp.tool()
def save_score(person_id: int, score: int, rationale: str, qualified: bool) -> dict[str, Any]:
    """Persist a scoring verdict (0-100 score, rationale, qualified) already decided
    from get_scoring_context's output."""
    with get_session() as session:
        row = _save_score(session, person_id, score, rationale, qualified)
        return {"score_id": row.id, "person_id": person_id, "score": score, "qualified": qualified}


@mcp.tool()
def get_drafting_context(person_id: int) -> dict[str, Any]:
    """Fetch everything needed to draft an outreach message: segment value prop,
    enrichment text, latest score/rationale, validated contact emails. Drafting itself is
    a reasoning step done by whoever calls this -- see save_draft."""
    with get_session() as session:
        ctx = _get_drafting_context(session, person_id)
        return ctx or {"error": f"no person with id {person_id}"}


@mcp.tool()
def save_draft(person_id: int, channel: str, body: str) -> dict[str, Any]:
    """Persist a drafted outreach message. Status is always 'draft' -- this never sends
    anything (see ARCHITECTURE.md §3.6/§8 -- sending is a separate, later phase)."""
    with get_session() as session:
        row = _save_draft(session, person_id, channel, body)
        return {"draft_id": row.id, "person_id": person_id, "channel": channel, "status": "draft"}


@mcp.tool()
def get_email_send_status() -> dict[str, Any]:
    """Show today's email send budget/cap, current warmup day, and whether the kill
    switch is engaged. Check this before sending anything."""
    with get_session() as session:
        engaged, _ = is_killswitch_engaged(session)
        warmup_start = settings.warmup_start()
        budget = get_or_create_budget(session, Channel.email, warmup_start)
        return {
            "killswitch_engaged": engaged,
            "warmup_start_date": warmup_start.isoformat(),
            "days_into_warmup": (budget.date - warmup_start).days,
            "sent_today": budget.sent_count,
            "cap_today": budget.cap,
        }


@mcp.tool()
def set_kill_switch(engaged: bool, reason: str = "") -> dict[str, Any]:
    """Halt (or clear the halt on) all outreach agents instantly. Checked before every
    single send -- see get_email_send_status."""
    with get_session() as session:
        _set_killswitch(session, engaged, reason)
        return {"killswitch_engaged": engaged, "reason": reason}


@mcp.tool()
def suppress_lead(person_id: int, reason: str) -> dict[str, Any]:
    """Add a person to the suppression list -- checked before every send, no exceptions.
    Use for bounces, unsubscribe requests, or anyone who should never be contacted."""
    with get_session() as session:
        _suppress(session, person_id, reason)
        return {"person_id": person_id, "suppressed": True, "reason": reason}


@mcp.tool()
def send_test_email(to_address: str, subject: str = "scout: SMTP test", body: str = "Connectivity test.") -> dict[str, Any]:
    """Send a real test email via SMTP to an address you specify -- not gated by rate
    limits, suppression, or a draft. Use this to confirm SMTP credentials work, sending to
    an address you control. NOT for outreach -- see send_outreach_email for the real,
    gated sender."""
    return _send_test_email(to_address, subject, body)


@mcp.tool()
def send_outreach_email(draft_id: int) -> dict[str, Any]:
    """Send a drafted email for real, through every safety gate: kill switch, suppression
    list, daily rate cap, and a hard requirement that the target email is confirmed
    'valid' (not just 'unchecked' or 'risky'). Sending a real message to a real person --
    make sure that's actually intended before calling this."""
    with get_session() as session:
        return _send_outreach_email(session, draft_id)


@mcp.tool()
def check_replies(limit: int = 50) -> dict[str, Any]:
    """Fetch unseen mailbox messages, match senders against known lead emails, and store
    new replies. Unsubscribe requests are detected and suppressed automatically -- no
    reasoning call needed for those. Everything else is stored with needs_human=true,
    ready for get_reply_classification_context to triage."""
    with get_session() as session:
        return _check_replies(session, limit)


@mcp.tool()
def get_reply_classification_context(reply_id: int) -> dict[str, Any]:
    """Fetch a reply's body plus the original draft it's replying to, for classifying
    intent (interested/not_interested/out_of_office/question/...). That classification is
    a reasoning step done by whoever calls this -- see save_reply_classification."""
    with get_session() as session:
        ctx = _get_reply_classification_context(session, reply_id)
        return ctx or {"error": f"no reply with id {reply_id}"}


@mcp.tool()
def save_reply_classification(reply_id: int, classification: str, needs_human: bool) -> dict[str, Any]:
    """Persist a reply's classification. needs_human=true keeps it in the queue you
    actually look at (GET /replies?needs_human=true); set false once it's handled or
    doesn't need a response (e.g. an out-of-office autoreply)."""
    with get_session() as session:
        row = _save_reply_classification(session, reply_id, classification, needs_human)
        return {"reply_id": row.id, "classification": classification, "needs_human": needs_human}


if __name__ == "__main__":
    mcp.run()
