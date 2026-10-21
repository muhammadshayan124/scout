# scout

Personal lead-gen / enrichment / outreach agent system. See [ARCHITECTURE.md](ARCHITECTURE.md)
for the full design. Phases 1-4 are built: Sourcing, Enrichment, Validation, Scoring,
Drafting, Rate Limiter, Email Outreach, Reply/Response Agent, and the monitoring API. The
send mechanism exists and is gated (kill switch, suppression list, daily cap, requires a
confirmed-valid email) but hasn't actually sent anything yet -- see "What's real vs.
what's still a gap" below.

## Setup

```bash
python -m venv .venv
./.venv/Scripts/pip install -e .
./.venv/Scripts/python -m playwright install chromium

cp .env.example .env
# fill in SAM_GOV_API_KEY -- from sam.gov itself (Account Details -> Public API Key),
# NOT api.data.gov -- confirmed those keys 404 on this endpoint despite looking valid

docker compose up -d          # local Postgres on port 5433
./.venv/Scripts/python -m scout.db.init_db
```

## Try it

```python
# Government contractors, sourced live from SAM.gov (needs SAM_GOV_API_KEY in .env)
from scout.agents.sourcing import sam_gov
from scout.db.session import get_session

records = sam_gov.fetch_awards(naics_code="541330", days_back=30)  # 541330 = engineering services
with get_session() as session:
    companies = sam_gov.ingest_awards(session, records)

# Architecture/engineering firms, scraped live from the ACEC national directory (Texas)
from scout.agents.sourcing import acec_tx

records = acec_tx.fetch_firms(state="TX")
with get_session() as session:
    companies = acec_tx.ingest_firms(session, records, state="TX")

# Enrichment: pull real text from a company's own site for personalization
from scout.agents.enrichment.enrich import enrich_company_site
with get_session() as session:
    company = session.query(Company).first()
    blob = enrich_company_site(session, company)
```

```python
# Validation: real SMTP-level check (nothing sent) on any unchecked email for a person
from scout.agents.validation.validate import validate_email
with get_session() as session:
    for email in person.emails:
        validate_email(session, email)

# Scoring + Drafting: get_*_context fetches everything needed; the actual scoring/drafting
# judgment is yours (or Claude Code's, if scout is run as an MCP server -- see
# ARCHITECTURE.md §1) -- save_score/save_draft persist whatever you decide.
from scout.agents.scoring.context import get_scoring_context, save_score
from scout.agents.drafting.context import get_drafting_context, save_draft
```

```python
# Rate limiter: the shared gate every send passes through -- kill switch, suppression,
# daily cap tied to the warmup ramp. Fully live-tested against the real DB, no credentials
# needed for this part.
from scout.agents.ratelimit.budget import can_send, record_send, set_killswitch, suppress

# Email outreach: send_test_email is ungated (sends to whoever you tell it to -- use your
# own address). send_outreach_email is the real, gated sender -- requires SMTP_* set in
# .env and refuses anything short of a confirmed 'valid' email status.
from scout.agents.outreach.email import send_test_email, send_outreach_email
```

```python
# Reply agent: check_replies fetches unseen mail and matches senders to known leads
# (needs IMAP_* in .env -- same Gmail App Password as SMTP). Unsubscribe requests are
# auto-detected and suppressed with no reasoning call; everything else waits for
# get_reply_classification_context / save_reply_classification, same pattern as scoring.
from scout.agents.reply.imap_watch import check_replies
from scout.agents.reply.classify import get_reply_classification_context, save_reply_classification
```

Or run all seventeen primitives as MCP tools:

```bash
./.venv/Scripts/python -m scout.mcp_server
```

The monitoring API (funnel/budget/activity/replies/killswitch -- the backend for a future
dashboard):

```bash
./.venv/Scripts/python -m uvicorn scout.api:app --port 8420
```

## Tests

```bash
pip install -e ".[dev]"
pytest
```

63 tests cover the mechanical, safety-critical parts: the warmup ramp boundaries, the
kill switch, suppression and daily cap, every branch of the SMTP-verdict classifier
(including the sender-IP-reputation case that once silently dropped valid leads), the
send gate (only `valid` addresses, one send per draft, email-channel drafts only), and
unsubscribe detection and suppression on reply.

They run on in-memory SQLite (JSONB rendered as JSON), so CI needs no Postgres. A tripwire
replaces `smtplib`/`imaplib` for every test and `settings` is overridden, so the suite
cannot send mail or read a local `.env`, even on a machine holding live credentials. The
suite found two bugs, both fixed: a draft written for another channel would have been
sent by SMTP, and reply sender matching was case-sensitive against un-normalised stored
addresses.

What is deliberately not tested: the reasoning steps (scoring, drafting, reply intent),
which live outside this codebase by design, and the live SAM.gov / ACEC / SMTP / IMAP
integrations, which were verified by hand against the real services.

## What's real vs. what's still a gap

Everything below has been live-tested against real data this session, not just written
and assumed to work -- see ARCHITECTURE.md §11 for the full list of real bugs found and
fixed in the process (mislabeled SAM.gov contacts, a fake-success enrichment fallback, an
enum/string bug, and a Spamhaus-blocklist validation bug that would have silently
mislabeled valid leads as invalid).

- **Sourcing** (SAM.gov + ACEC Texas): working, 324 real leads in the DB.
- **Enrichment**: working for ae_firm leads (real site text + discovered emails).
  gov_contractor leads have no company website in SAM.gov's data at all -- drafting uses
  real award details (title/amount/agency) for that segment instead.
- **Validation**: working, but this sandbox's outbound IP is Spamhaus-blocklisted, so
  Microsoft-365-hosted domains (a large share of real businesses) can't get a real verdict
  from here -- correctly left as `unchecked` rather than guessed. Worth re-testing from
  your own machine.
- **Scoring + Drafting**: working end-to-end, real reasoning demonstrated on 2 real leads
  (one per segment) -- see ARCHITECTURE.md §11 for what was actually written and why.
- **Known gap**: gov_contractor leads with no resolvable website have no way to discover a
  contact email yet -- not built.
- **Rate Limiter**: fully live-tested against the real DB -- warmup-cap math, kill switch,
  suppression list, and daily cap all confirmed working exactly as designed.
- **Email Outreach**: the send mechanism is built and gated correctly, but hasn't actually
  sent an email yet -- needs real SMTP credentials (a Gmail App Password) that only you
  can provide, and sending to the two real leads already drafted needs your explicit
  go-ahead separately from having built this. See ARCHITECTURE.md §12.
- **Reply/Response Agent**: unsubscribe detection tested against 12 real-world phrasings
  (all correct); the classify context/save round trip tested with a simulated real reply
  end-to-end, including confirming it flows through to the API correctly. `check_replies` is covered by
  tests against a fake IMAP server and was also run once against the real inbox. See
  ARCHITECTURE.md §13.
- **Monitoring API** (`scout/api.py`): all 5 endpoints live-tested via real HTTP requests
  against the real DB, including confirming a `POST /killswitch` is visible through the
  separate `GET /budget` endpoint (real persistence, not per-request state).
- **UI frontend**: not built -- that's yours to build against this API, per the original
  n8n-vs-custom decision.

## Notes

- Postgres runs on **5433**, not 5432 -- another project (`naeemic-postgres`) already
  owns 5432 on this machine, so scout was given its own port rather than touching it.
- `agent_events` (the timeline/audit table) is written to by every agent call already --
  worth a `SELECT * FROM agent_events ORDER BY ts DESC LIMIT 20` to see it in action.

## License

MIT
