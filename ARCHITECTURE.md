# scout — personal lead-gen / enrichment / outreach agent system

Working name only, rename freely. Architecture reference for a daily-run, custom-built,
$0-infra multi-agent pipeline: source leads → enrich → validate → score → personalize →
outreach (email + LinkedIn + other social) → track replies. Built to match your existing
stack (Python/FastAPI, agent systems like warden) rather than a low-code tool, per the
n8n-vs-custom call above.

---

## 1. The one big architectural decision: where does the "thinking" live?

Every LLM call costs money unless you own the inference. At $0 budget that's the real
constraint here, not the scraping or the DB. Two options:

**A. Pipeline calls an LLM API directly (OpenAI/Anthropic/etc.)** — simplest to reason
about, but every classification/scoring/drafting call is billed per token. Not $0.

**B. Pipeline exposes its capabilities as tools; Claude Code is the brain.** Build `scout`
as an MCP server (same pattern as `warden`) that exposes primitives — `search_companies`,
`enrich_lead`, `validate_email`, `draft_message`, `send_email`, `queue_linkedin_message`,
`check_rate_budget` — and let a scheduled Claude Code session (via `CronCreate` /
`scheduled-tasks`, which you already have access to) be the daily-running orchestrator that
calls those tools and does the reasoning. No separate LLM bill — it rides on the Claude Code
subscription you already pay for. This is exactly the pattern `gtm-mcp` uses ("zero LLM
calls inside the toolkit — all reasoning stays in Claude"), and it's exactly how `warden`
is already structured.

**Recommendation: B.** It's the only version of this that is actually free to run daily,
it reuses infrastructure you already have (MCP server pattern, cron scheduling), and it
keeps `scout` itself dumb/testable — pure Python functions and I/O, no prompt engineering
buried inside business logic. Your custom UI reads the same Postgres DB the MCP server
writes to, independent of whichever Claude session touched it last.

If you'd rather not depend on an interactive/scheduled Claude Code session being the
runtime brain (e.g. you want it running on a headless box with no Claude Code installed),
the fallback is a small local model (Ollama + a 7-8B instruct model) for the
low-stakes calls (classification, simple scoring) and reserving actual Claude API calls
(paid) only for message drafting, where quality matters most. That's a hybrid-cost model,
not $0 — flagging it so it's a conscious tradeoff, not a surprise.

---

## 2. High-level pipeline

```
                        ┌─────────────────────────────────────────┐
                        │        Orchestrator (Claude Code,        │
                        │   scheduled daily via CronCreate/        │
                        │   scheduled-tasks, calls scout's tools)  │
                        └───────────────┬───────────────────────────┘
                                        │ MCP tool calls
        ┌───────────────┬──────────────┼──────────────┬────────────────┐
        ▼               ▼              ▼              ▼                ▼
  ┌───────────┐   ┌───────────┐  ┌───────────┐  ┌───────────┐   ┌─────────────┐
  │ Sourcing  │   │Enrichment │  │Validation │  │ Drafting  │   │  Outreach   │
  │  Agent    │   │  Agent    │  │  Agent    │  │  Agent    │   │  Agents     │
  │(scrape/   │   │(Agent-    │  │(Reacher-  │  │(personal- │   │(email +     │
  │ discover) │   │ Reach +   │  │ style SMTP│  │ ized copy │   │ LinkedIn +  │
  │           │   │ site read)│  │ check)    │  │ per lead) │   │ social)     │
  └─────┬─────┘   └─────┬─────┘  └─────┬─────┘  └─────┬─────┘   └──────┬──────┘
        └───────────────┴──────────────┴──────────────┴────────────────┘
                                        │
                                        ▼
                        ┌─────────────────────────────┐
                        │   Postgres                    │
                        │   + Rate-Limit/Budget State   │
                        └───────────────┬───────────────┘
                                        │
                                        ▼
                        ┌─────────────────────────────┐
                        │   FastAPI read API            │
                        │   (for your custom UI)        │
                        └─────────────────────────────┘
                                        ▲
                                        │
                        ┌─────────────────────────────┐
                        │   Reply/Response Agent        │
                        │   (classifies inbound, flags  │
                        │    human-worthy replies)      │
                        └─────────────────────────────┘
```

Each "Agent" box is a Python module with a narrow, testable interface — not a persistent
process. The orchestrator (Claude Code, scheduled) invokes them in sequence once a day (or
on whatever cadence you set), each call is idempotent and resumable from DB state, so a
crash mid-run doesn't corrupt anything or double-send.

---

## 3. Component breakdown

### 3.1 Sourcing Agent

**The two ICP segments, as decided:**

| Segment | Your offer to them |
|---|---|
| Architecture / engineering firms | AI RFP analysis + proposal drafting |
| Government contractors | AI RFP monitoring + bid/no-bid analysis |

Geography allow-list (both segments): **North America, South America, Europe, Australia**.

This resolves the last open input — both segments turn out to have unusually good, free,
official, structured public data sources, which changes this agent's design for the
better: instead of "scrape company websites and hope," source directly from the
institutions each segment is already required to appear in.

**Government contractors** — public procurement portals list contract *awards*, meaning a
company appearing there isn't a guess at being a government contractor, it's a confirmed
one, with the exact contract context you need for a personalized opener:
- **US**: SAM.gov — has a genuine free API, but the key must come from SAM.gov's own
  Account Details page (Public API Key), not api.data.gov -- confirmed by testing;
  api.data.gov keys 404 on this endpoint despite looking like the same kind of key
- **Canada**: CanadaBuys — public tender + award data
- **EU** (27 member states + EEA): TED — contract notices with buyer info, value, CPV codes
- **UK**: Find a Tender / Contracts Finder
- **Australia**: AusTender — Commonwealth, NSW, QLD tenders and awards

Every one of these is built for public consumption — pulling from them isn't scraping in
the risky sense at all, it's using data these portals exist to publish. This is the
cleanest, lowest-risk sourcing path in the entire system, and it directly matches your
product angle for this segment (RFP monitoring) — the same portal client you build for
sourcing is close to a working proof-of-concept for the product itself.

**Architecture/engineering firms** — sourced from public professional-licensing and
association directories rather than general company scraping:
- State/provincial architecture licensing boards (NCARB-affiliated in the US) and
  engineering licensing boards (NCEES-affiliated) — most publish public licensed-firm
  directories
- AIA (American Institute of Architects) and ACEC (American Council of Engineering
  Companies) chapter directories — both public, organized by region

**Output**: raw `companies` + `people` rows, tagged with `segment` (`ae_firm` |
`gov_contractor`) so downstream scoring/drafting can branch per segment (see §3.5).

**Still true, worth keeping in view**: neither of these is Apollo-scale coverage, and each
source has its own page structure/access pattern to build and maintain — this is real
engineering work, just work with a much better risk/reliability profile than generic
web scraping would have given you.

### 3.2 Enrichment Agent
- Input: a `person`/`company` row from sourcing
- Does: calls Agent-Reach (or direct Playwright) to pull LinkedIn profile context (recent
  posts, job changes, headline), company site content, GitHub/Twitter if relevant to your
  ICP
- Output: an `enrichment` blob per lead — this is what makes drafted messages actually
  personalized instead of templated
- Read-only, lowest-risk component in the whole system — worth building first

### 3.3 Validation Agent
- Input: candidate email addresses (guessed via common patterns — `first.last@domain`,
  scraped, or found via enrichment)
- Does: SMTP-level verification (Reacher-style: syntax → MX lookup → SMTP handshake,
  no message actually sent) to filter dead/invalid addresses *before* they hurt your
  domain's sender reputation
- Output: `valid` / `invalid` / `risky` (catch-all domains, role addresses) status per email
- This step is not optional at $0 budget — every bounce above ~2% actively degrades your
  ability to send at all. Validation is what protects the email leg long-term, not the
  rate limiter.

### 3.4 Scoring Agent
- Input: enrichment blob + your ICP criteria
- Does: scores fit (e.g. 0-100) and produces a short rationale
- Output: `score`, `rationale`, `qualified: bool`
- This is a reasoning call — lives on the Claude Code side of the MCP boundary (see §1)

### 3.5 Drafting Agent
- Input: qualified lead + enrichment blob + a **per-segment** message template/voice guide
  — `ae_firm` leads get the RFP-analysis-and-proposal-drafting pitch, `gov_contractor`
  leads get the RFP-monitoring-and-bid/no-bid pitch. Two genuinely different value props,
  not one generic template with a find-and-replace industry name.
- Does: writes a personalized email opener and a separate, shorter LinkedIn message —
  different register per channel, referencing something real about the lead (for
  `gov_contractor` leads, this is often the specific contract/award pulled straight from
  the procurement portal in §3.1 — about as concrete as personalization gets)
- Output: draft text, stored with status `draft`, never sent automatically from here
- Also a reasoning call, same boundary note as above

### 3.6 Outreach Agents (one per channel, shared interface)
Each channel agent implements the same contract: `send(lead_id, draft_id) -> outreach_event`,
but internally is very different:

- **Email**: SMTP send via your existing personal mailbox (decided — no new domain budget).
  Gated by the Rate Limiter (§4) and the warmup schedule, same as any mailbox would be.
  **Real tradeoff worth naming plainly**: without a dedicated sending domain, your
  personal inbox's reputation is directly on the line — a bounce spike or spam-complaint
  bump affects the same address you use for everything else (job applications, banking,
  personal). This isn't a blocker, it's a reason to run the warmup ramp *more*
  conservatively than the 30-40/day ceiling mentioned earlier, and to treat the
  Validation Agent (§3.3) and suppression list (§3.7) as non-negotiable rather than
  nice-to-haves. Gmail's own SPF/DKIM are already configured for you on `gmail.com` —
  DMARC alignment is the one thing worth double-checking, since you don't control that
  domain's DMARC policy yourself.
- **LinkedIn**: Playwright driving your real logged-in session. **Default mode: `draft`
  only** — writes the message into a review queue, does not click send. A config flag
  (`LINKEDIN_MODE=draft|auto`) exists so you can consciously flip to `auto` later if you
  decide to accept the ban risk we discussed — but the default should stay `draft` and
  the flag should require an explicit, separate confirmation to flip, not just an env var
  someone forgets is set to `auto`.
- **Other social**: same `draft`-first pattern, same interface, added as needed. Design
  the channel agents as plugins against one interface from day one so adding a third/fourth
  channel is additive, not a rewrite.

### 3.7 Rate Limiter / Budget Service
Not scattered per-agent logic — one shared component every outreach agent calls through
before acting. Tracks, per channel, per day:
- Actions taken today vs. configured cap
- Current warmup week (for email: which rung of the 5→10→20→30-40/day ramp you're on)
- A suppression list (bounced, unsubscribed, explicitly asked not to be contacted — checked
  before every single send, no exceptions)
- A global kill switch (one flag that halts all outreach agents instantly if something
  looks wrong)

### 3.8 Reply/Response Agent
- Watches the mailbox (IMAP) and, if `LINKEDIN_MODE=auto` later, LinkedIn messages
- Classifies each reply: interested / not interested / out-of-office / unsubscribe-request
- Unsubscribe requests go straight into the suppression list, automatically, no LLM
  judgment call needed there
- Interested replies get flagged for *you* — this is the human-in-the-loop handoff point,
  and matches the "AI does the volume, human does the high-value conversation" pattern
  from the research

---

## 4. Data model (Postgres, per your call — see §7 for why this also sets up the timeline/memory piece)

```
companies(id, name, domain, source_url, industry, size_bucket, geography, scraped_at)
people(id, company_id, name, title, linkedin_url, source, scraped_at)
enrichment(id, person_id, blob_jsonb, fetched_at)
emails(id, person_id, address, validation_status, checked_at)
scores(id, person_id, score, rationale, qualified, scored_at)
drafts(id, person_id, channel, body, status, created_at)      -- status: draft/queued/sent
outreach_events(id, person_id, channel, draft_id, sent_at, result)
replies(id, person_id, channel, body, classification, received_at, needs_human)
suppression_list(id, person_id, reason, added_at)
rate_budget(channel, date, sent_count, cap)

-- the "save the whole timeline" table: append-only, one row per orchestrator action
agent_events(
  id, ts, session_id,           -- which Claude Code run this belongs to
  actor,                        -- 'orchestrator' | agent name (sourcing/enrichment/...)
  action,                       -- tool called, e.g. 'enrich_lead'
  entity_type, entity_id,       -- what it acted on (person/company/draft/...)
  input_jsonb, output_jsonb,    -- full call in/out, for replay and debugging
  reasoning_summary,            -- short note on *why*, when the actor is Claude Code
  created_at
)
```

`agent_events` is an audit log, not a graph — every row is flat and queryable with plain
SQL (`SELECT * FROM agent_events WHERE entity_id = ... ORDER BY ts`), which already gets
you "the whole timeline for this lead" without any graph technology at all. It's also the
raw material the graph layer in §8 would eventually build on top of, so building this table
properly now isn't wasted work even if you defer the graph piece.

Every table maps directly onto something your future UI will want to show (funnel counts,
today's send budget remaining, replies needing you, and now a full per-lead history view).

---

## 5. Tech stack

- **Python 3.12 + FastAPI** — read API for the UI, matches your production stack exactly
- **Postgres via SQLAlchemy** — per your call. One real infra cost: something has to run
  Postgres. Free options: a local install, or a free-tier hosted instance (Neon, Supabase,
  Railway all have $0 tiers sufficient for personal-scale daily volume) if you'd rather not
  manage the server yourself. Either is $0; local install is $0 and zero third-party
  dependency, hosted free-tier is $0 and zero ops burden — your call, not a big decision.
- **Playwright** — all browser automation (LinkedIn reading, scraping, and LinkedIn
  sending if you ever flip to `auto`)
- **MCP server** (`mcp` Python SDK, same shape as warden) exposing the tools listed in §1
- **APScheduler** (in-process) for the daily run trigger, *or* rely entirely on
  `CronCreate`/`scheduled-tasks` to wake a Claude Code session daily — pick one, don't run
  both (redundant triggers is how you get double-sends)
- **No Celery/Redis/Docker-compose sprawl.** This is a personal daily tool, not a SaaS —
  keep the infra as thin as the problem allows. Add complexity only when something
  concrete forces it.

---

## 6. Monitoring UI — data contract only (you build the frontend)

Expose a small FastAPI read layer so whichever frontend you build (a Next.js dashboard
would match your portfolio stack) has clean endpoints:

- `GET /funnel` — counts at each stage today/this week (sourced → enriched → validated →
  qualified → drafted → sent → replied)
- `GET /budget` — today's send count vs. cap, per channel, current warmup week
- `GET /activity` — recent events feed (last N outreach events, replies, errors)
- `GET /replies?needs_human=true` — the queue that actually needs you
- `POST /killswitch` — flip the global halt flag from the UI, not just a config file

---

## 7. Timeline / graph memory — the "Graphify" question

Worth clearing up first: **Graphify isn't the tool for this.** I checked — it's a real,
current (YC S26) project, but it builds a knowledge graph *of a codebase* (functions,
classes, call graphs) for coding assistants to query instead of grepping. Not related to
agent memory or event timelines at all. Easy name to confuse with what you actually want,
which is closer to what Zep (built on a temporal graph engine called Graphiti), Letta, or
Mem0 do — purpose-built memory layers for agents that track *when* something happened and
*when the agent learned about it* (bitemporal), not just static facts.

Two ways to get there, given you've already decided on Postgres:

**Option A — Apache AGE.** A Postgres extension (not a separate database) that adds a
graph query layer — nodes, edges, and Cypher queries — directly on top of the same
Postgres instance holding everything else. This is the version of "graph memory" that
actually matches "I want Postgres" rather than fighting it: no second database to run,
no data duplicated into Neo4j, your existing tables and a graph view of them coexist.
Self-hosts via a Docker image, genuinely $0.

**Option B — a dedicated agent-memory library (Zep/Graphiti, Mem0, Letta).** More
purpose-built for exactly "track an agent's timeline of decisions," with real bitemporal
semantics out of the box instead of you designing that schema yourself. The catch: the
self-hosted path for Zep specifically requires running your own Neo4j, and Neo4j's free
Community Edition is being phased out as the recommended self-host path — so this option
currently has a real operational cost (running and maintaining Neo4j, or paying for Zep
Cloud) that Option A doesn't. Mem0 is the exception here — it can run its graph layer on
Kuzu, an embedded graph database with no separate server to manage, which keeps it
genuinely $0 and low-ops if you want purpose-built memory semantics without the Neo4j
burden.

**Recommendation, matching your own "defer if hard" instinct:** don't build either yet.
The `agent_events` table in §4 already captures full per-lead, per-session history in
plain Postgres from day one — that's your timeline, today, for free, with zero new
technology. Once the pipeline has been running for a few weeks and you know what you
actually want to *ask* the graph ("show me every lead that came from a referral chain
through person X," "what pattern precedes a reply") — that's the point where Option A
(Apache AGE, since it's additive to what you already have) becomes a same-week addition
instead of upfront speculative infrastructure. Building the graph layer before you know
your own query patterns is exactly the kind of complexity worth avoiding until something
concrete forces it.

---

## 8. Build phases

Matching how we've worked before — I'll stop after each phase for you to test and sign off
rather than building all of this in one pass.

1. **Data model + Sourcing + Enrichment** — get real leads with real context into the DB.
   Nothing is sent yet. Lowest risk, immediately useful (you can eyeball lead quality).
2. **Validation + Scoring + Drafting** — the DB now contains qualified, personalized drafts
   sitting in `draft` status. Still nothing sent. You can review draft quality manually.
3. **Rate Limiter/Budget Service + Email Outreach (draft-review mode first, then live once
   your mailbox has been through the conservative warmup ramp)** — the first channel that
   actually sends anything.
4. **LinkedIn Outreach in `draft` mode** — messages queued for you to personally send in
   LinkedIn's own UI. No bot ever touches LinkedIn's send action at this phase.
5. **Reply/Response Agent + UI data endpoints** — closes the loop, gives you the dashboard
   backend.
6. *(Only if you explicitly decide to accept the risk later)* — flip `LINKEDIN_MODE=auto`.
7. *(Optional, later — see §7)* — add Apache AGE on top of the existing Postgres instance
   once you know what graph queries you actually want to run.

---

## 9. Decisions made this round

- **Orchestrator**: Claude Code, scheduled (§1, option B) — confirmed
- **Database**: Postgres (§4, §5) — confirmed
- **ICP**: two segments — architecture/engineering firms (RFP analysis + proposal drafting
  pitch) and government contractors (RFP monitoring + bid/no-bid pitch); geography =
  North America, South America, Europe, Australia (§3.1) — confirmed
- **Sourcing strategy**: public procurement portals (SAM.gov, CanadaBuys, TED, Find a
  Tender, AusTender) for gov contractors; state/provincial licensing boards + AIA/ACEC
  directories for A/E firms (§3.1) — confirmed
- **Sending mailbox**: your existing personal email, no dedicated domain — confirmed, risk
  noted in §3.6
- **Timeline/memory**: plain Postgres `agent_events` table now, Apache AGE layered on later
  if needed (§7) — confirmed

## 10. First-pass sourcing scope

- **Government contractors**: SAM.gov only (US), via its free API — confirmed
- **A/E firms**: ACEC Texas — confirmed. Reasoning: large state economy/infrastructure
  market suggests a large member pool; not California/New York means it's less likely to
  be the reflexive first target of every other sales-tooling go-to-market motion. The
  "less competition" half of this is a judgment call, not verifiable data — no dataset
  tracks outreach saturation by state. Low-stakes choice either way since the Sourcing
  Agent is built chapter-agnostic — swapping chapters later is a config change.

Both to widen once this first pass is proven end-to-end: more ACEC chapters + licensing
boards for the A/E segment, TED/AusTender/CanadaBuys for the gov-contractor segment.

---

## 11. Phase 2 status: Validation + Scoring + Drafting

Built and live-tested against real leads already in the DB (324 from Phase 1). Real bugs
found and fixed in the process, same as Phase 1 -- worth reading if you're extending this:

- **SAM.gov's `pointOfContact` is the government's contracting officer, not the awardee
  company's staff** (verified: every one was a `.mil`/`.gov` address). Phase 1's code was
  creating `Person` rows from this data as if they were outreach targets at the awardee
  company -- fixed in `sourcing/sam_gov.py`, and the ~373 wrongly-created rows were deleted
  from the DB. Real contacts for gov_contractor leads now come from company-site
  enrichment instead (when a site exists) or a placeholder until one's found (`ensure_person`
  in `agents/util.py`).
- **SAM.gov's award data has no company-website field at all** (checked the raw record
  directly). `enrich_company_site`'s fallback to `source_url` was silently fetching
  SAM.gov's own opportunity page instead and reporting fake success (~11 chars of junk).
  Fixed to only use `company.domain`. For gov_contractor leads without a site, drafting
  now uses the real award details (title/amount/agency) instead — see `award_summary` in
  `agents/util.py` — which is arguably better personalization material than generic site
  copy anyway, since it's the literal reason the company is a lead.
- **Segment/Channel/etc. columns come back as plain strings after a DB round-trip**, not
  the Python enum (the columns are plain `String`, not SQLAlchemy's `Enum` type) --
  `.value` calls broke on real reads. Fixed with a small `enum_value()` helper rather than
  a schema change.
- **SMTP validation: this environment's outbound IP is on Spamhaus's blocklist.**
  Confirmed by testing against Gmail directly (worked perfectly, gave a specific accurate
  "no such user" result) vs. Microsoft 365 (rejected the whole session citing Spamhaus,
  saying nothing about the actual recipient). The original code treated any non-2xx RCPT
  response as "invalid," which would have silently mislabeled real, valid leads as bad
  addresses. Fixed to distinguish a genuine non-existence rejection from an
  inconclusive/reputation-based one (`_smtp_check` in `validation/validate.py`) -- the
  latter now leaves the email as `unchecked` rather than guessing. On your own machine
  (assuming your IP isn't similarly blocklisted), this should classify Microsoft 365
  addresses correctly too, worth re-testing there.
- **Score/Draft need a person_id, but freshly-sourced companies may have none** (no named
  contact found yet, gov_contractor especially). Added `ensure_person()` -- get-or-create
  a placeholder, same pattern enrichment.py already used for a discovered-but-unnamed
  email address.

**Real output from this pass**: 2 leads scored and drafted end-to-end (one per segment) --
[Lead 1] (gov_contractor, scored 72/qualified, drafted referencing their real
$500K USACE tree-maintenance award) and [Lead 2] (ae_firm, scored 81/qualified,
drafted referencing their real SBA joint-venture structure). Both drafts are genuinely
personalized from real data, sitting in `draft` status -- nothing sent, per §3.6/§8.

**Known gap, not yet solved**: gov_contractor leads without a resolvable company website
have no way to discover a contact email at all right now (SAM.gov gives none, and there's
no name-to-website resolution step built). This is why [Lead 1]'s draft has
no verified send target yet -- a future small addition (e.g. a company-name web search
step) would close this, not yet built.

---

## 12. Phase 3 status: Rate Limiter + Email Outreach

Built: the Rate Limiter/Budget Service (§3.7) as one shared gate every send passes
through -- kill switch, suppression list, daily cap tied to a real warmup ramp (5/day
week 1 scaling to 40/day by week 4+, matching the deliverability research from earlier in
this doc) -- plus the actual SMTP send mechanism.

**Fully live-tested, no credentials needed**: the warmup-cap calculation (every threshold
checked against real dates) and all four gate conditions against the real Postgres
instance -- baseline allowed, suppressed person blocked, kill switch blocks everyone
regardless of suppression state, daily cap correctly blocks once hit. All passed exactly
as designed on the first real run.

**Deliberately not yet tested**: an actual SMTP send. That needs real Gmail App Password
credentials (§3.6 -- a Gmail App Password, not the account password; generate one at
myaccount.google.com/apppasswords with 2FA on), which only you can provide. `send_test_email`
exists specifically for this -- it sends a real email but isn't gated by rate
limits/suppression/drafts at all, so it's meant for sending to an address you control
(yourself), not a lead.

**Deliberately not sent, and won't be without asking first**: the two real drafts from
Phase 2 ([Lead 1], [Lead 2]) sitting in the DB. Sending a message to
a real third party is a different kind of action than anything built so far -- building
the capability and using it against a stranger aren't the same decision, and the second
one is yours to make explicitly, per-send, not implied by "build Phase 3." Practically
moot right now anyway: `send_outreach_email` refuses to send to anything short of a
confirmed `valid` email status, and both of those leads' emails are currently `unchecked`
(the Spamhaus/sandbox-IP issue from §11) -- so neither could send yet even with a yes.

---

## 13. Phase 4 status: Reply/Response Agent + UI data endpoints

Built, following the same split as Scoring/Drafting (§1): `imap_watch.py` is the
mechanical half (fetch unseen mail, match sender to a known lead, store a Reply row) plus
the one thing §3.8 explicitly says doesn't need reasoning -- unsubscribe detection, done
here with a deterministic phrase match that immediately suppresses the person, no
reasoning call in the loop. `classify.py` is the reasoning half (context-fetch + save,
same shape as scoring/drafting) for actual reply *intent* -- interested vs. not vs.
out-of-office isn't a keyword-matching problem the way "does this say unsubscribe" is.

Also built: the five monitoring endpoints from §6 (`scout/api.py`, FastAPI) -- this is the
backend half of "you build the frontend," giving whatever dashboard gets built later
something real to read from.

**Fully live-tested, no IMAP credentials needed for most of it:**
- All 5 API endpoints, hit as real HTTP requests against the real DB: `/funnel` (matched
  known real counts exactly -- 324 sourced, 2 qualified, 2 drafted), `/budget`,
  `/activity`, `/replies`, and `POST /killswitch` -- confirmed a flip via POST is visible
  through the separate GET /budget endpoint, i.e. real DB persistence, not per-request
  state.
- The unsubscribe-detection regex against 12 real-world phrasings, including the
  deliberately tricky ones ("Not interested right now but maybe later" and "Out of office
  until the 15th" correctly do NOT trigger it) -- all 12 correct.
- The full classify context/save round trip and its effect on the API: inserted a
  simulated real reply (a plausible "interested" response to the actual [Lead 2]
  draft from Phase 2), confirmed `get_reply_classification_context` correctly pulled the
  right person/company/original-draft-thread, classified it, saved it, then confirmed it
  showed up correctly in `GET /replies?needs_human=true` and that `/funnel`'s `replied`
  count incremented. Deleted the simulated row afterward -- it was a test fixture, not a
  real inbound message, so it doesn't belong in the DB going forward.

**Not yet tested**: `check_replies` itself (the actual IMAP fetch) -- same category of gap
as Phase 3's SMTP send, needs real credentials (IMAP_USERNAME/IMAP_PASSWORD in .env; the
same Gmail App Password used for SMTP works for IMAP too, no separate credential to
generate).
