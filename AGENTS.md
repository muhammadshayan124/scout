# scout — context handoff

Read this first, in a new session or a new chat. It's written to be self-contained: paste
it as your first message if you're not opening this repo as the working directory. For
full technical spec (data model, every design decision's reasoning, all phase writeups),
see [ARCHITECTURE.md](ARCHITECTURE.md) — this file is the "what happened and where things
stand" summary, not a replacement for it.

## What this is and why

A personal, custom-built (deliberately not n8n) multi-agent pipeline that finds companies
likely to want an AI RFP-analysis/monitoring product, researches them for real, and
prepares personalized outreach. Two customer segments, both decided early and still
current:

- **Architecture/engineering firms** — pitch: AI RFP analysis + proposal drafting
- **Government contractors** — pitch: AI RFP monitoring + bid/no-bid analysis

Key standing decisions (all made deliberately, reasoning in ARCHITECTURE.md if you need
it, don't re-litigate without a real reason to):

- **Custom Python, not n8n** — this is a stateful agent loop with a custom-monitoring-UI
  requirement, not a linear workflow; also plays to the user's actual skillset (production
  FastAPI/agent systems).
- **Claude Code is the reasoning engine, not a paid LLM API** — scout is an MCP server
  (same shape as the `warden` project) exposing mechanical primitives only. Scoring,
  drafting, and reply-classification are reasoning steps done by whoever's driving the
  MCP tools (a Claude Code session), never by scout itself. This is *why* the system is
  $0 to run daily. Don't add LLM API calls inside scout's own code — that breaks the cost
  model the whole design depends on.
  - Practically: `get_scoring_context(person_id)` → you read it and reason → `save_score(...)`.
    Same pattern for drafting (`get_drafting_context`/`save_draft`) and reply
    classification (`get_reply_classification_context`/`save_reply_classification`).
- **Postgres**, not SQLite — user's own call, and it doubles as the timeline/audit-trail
  store (`agent_events` table — every agent action, queryable, from day one).
- **LinkedIn as a channel was deliberately never built.** Early in this project a real
  question came up about LinkedIn automation ban-risk; the resolution was "email first,
  decide on LinkedIn later" — it's still later. Don't build LinkedIn outreach without
  raising this explicitly first.
- **No dedicated sending domain** — outreach runs through the user's real personal Gmail
  (`[email removed]`), no budget for a separate domain. This is why the warmup
  ramp and validation-before-send are treated as non-negotiable, not nice-to-haves — a
  bounce/complaint spike hits the same inbox used for everything else.

## Current real state (as of this handoff)

Live counts in the actual Postgres DB:

```
companies: 324  (13 ae_firm, 311 gov_contractor)
people: 27  (13 original + 14 from the 2026-08-13 backlog pass below)
emails: 5  (0 valid, 4 unchecked/inconclusive, 1 invalid)
scores: 16      drafts: 5 (all status='draft', unsent)
outreach_events: 0      replies: 0
contact_status: 1 found, 20 needs_finder, 6 unresolved
agent_events: 60  (full audit trail of everything below)
```

**2026-08-13 backlog pass**: worked the 311 never-processed gov_contractor companies (sourced
earlier but never got a Person/score/draft pass) since fresh SAM.gov sourcing was quota-blocked
(shared quota across opportunities+entity endpoints -- a single `fetch_awards` call with no
`states` filter loops every US state and can exhaust the whole day's quota in one call; scope
`states` narrowly if you don't want that). Picked 14 real candidates by award size/type, scored
honestly (not just to produce "qualified" leads) -- 3 qualified: [Lead 4] ($460.8K
janitorial, score 74), [Lead 5] ($458K janitorial, score 72), [Lead 6] ($371.6K
DoD health-physics consulting, score 70). All 3 drafted. **None have a sendable email** -- ran
them through the Email Finder Agent, found real named owners for 2 of 3 ([contact B]
at [Lead 5], [contact C] at [Lead 6]) but zero individual emails published anywhere
public, same dead-end pattern as the ae_firm leads. All 3 parked 'unresolved'.

**`is_role_address()` limitation (still open):** exact-localpart matching only, so
`supportindustio@...`-style addresses slip through as "deliberate" even though they read as
generic. Not fixed yet -- could be made substring-aware, but watch for false positives on
real personal-name localparts that happen to contain a role word (e.g. "hope" contains no
role word, but a real name like "Pat Support" would). Low priority: doesn't block
anything today since the one address this affected (person_id 1's gmail address) is already
SMTP-validated 'invalid'.

**Hunter.io email-finder -- real free lever, but capped at ~1 lookup total.** No account
needed for `hunter.io/find/{domain}/{Full%20Name}` (note: root `/find/` with no name/domain
redirects to a login wall -- the *direct* URL with both params is what works anonymously).
Confirmed 2026-08-13: found `[lead email removed]` for [contact A] (person_id 1) at 98%
confidence, 3 corroborating public sources -- genuinely better than anything web-search-only
research turned up. But it's gated by "demo credits": exactly one anonymous lookup, then it
demands a free account (user still needs to create that themselves -- can't be done by
Claude, account creation/login is a hard no regardless of who asks). If the user has since
created an account, more lookups are unblocked -- retry the same URL pattern for the 3
still-unresolved qualified leads ([contact B] @ [lead-domain-b], [contact C] @
[lead-domain-c]; [Lead 4] has no named contact to search).

**SMTP-blocklisted sandbox IP is still live and confirmed again** on this real find:
`[lead email removed]` validated to 'unchecked' (not 'valid') because Microsoft-365 rejected the
whole SMTP session on sender-IP reputation (`smtp_550:sender_ip_reputation`), same root cause
as the original validate.py fix -- this is an environment problem (this dev sandbox's
outbound IP), not evidence the address is bad. `send_outreach_email` requires status='valid'
specifically, so this real, well-corroborated email still can't pass the send gate from here.
Would validate cleanly from a non-blocklisted IP (e.g. actual production infra).

**Monitoring/control dashboard**: a Next.js frontend now lives at `scout/dashboard`
(dark charcoal + orange-red theme), talking to `scout/api.py` (FastAPI, port 8199).
Run via `.claude/launch.json` config `scout-dashboard` (port 3007) + `uvicorn
scout.api:app --port 8199`. Pages: Overview (funnel/budget/killswitch), Leads (table +
detail drawer, with a live Send-this-draft button wired to the real gated sender),
Companies, Activity (agent_events feed, now shows reasoning_summary not just output),
Replies. `dashboard/.env.local` sets `NEXT_PUBLIC_API_URL`.

**Email-discovery pipeline (three lists)**: `Person.contact_status` (found /
needs_finder / unresolved) tracks whether a deliberate (non-role) email exists for a
lead. `enrich_company_site` auto-classifies after saving discovered emails
(`agents/util.classify_contact_status`). A dedicated **Email Finder Agent**
(`agents/emailfinder/finder.py`, MCP tools `get_finder_context`/`save_finder_result`)
follows the same reasoning-on-the-Claude-Code-side pattern as scoring/drafting: it
gathers company/award/enrichment context, Claude Code does real web research
(WebSearch/WebFetch against public pages only — company sites, federal contractor
directories, SAM.gov entity data), then saves either a found email (promotes back to
'found') or a `note` explaining the dead end (parks as 'unresolved'). The dashboard's
Leads page has tabs for all three lists with live counts (`GET /contact-queue`).

All four build phases from ARCHITECTURE.md §8 are built:

1. **Sourcing + Enrichment** — SAM.gov (US federal contract awards) + ACEC Texas
   (national directory, filtered to TX). Both live-tested against real data.
2. **Validation + Scoring + Drafting** — SMTP-level email validation, and 2 real leads
   scored + drafted end-to-end (one per segment): [Lead 1] (gov_contractor,
   scored 72/qualified, drafted referencing their real $500K USACE award) and
   [Lead 2] (ae_firm, scored 81/qualified, drafted referencing their real SBA
   joint-venture structure).
3. **Rate Limiter + Email Outreach** — kill switch, suppression list, warmup-ramp daily
   cap, all live-tested against the real DB. SMTP send mechanism **confirmed working for
   real** — a real test email was sent and received.
4. **Reply/Response Agent + monitoring API** — IMAP mailbox watching **confirmed working
   for real** — connected to the real inbox, fetched 50 real unseen messages, correctly
   matched (zero, correctly, since nothing's been sent to a real lead yet). 5 FastAPI
   monitoring endpoints (`scout/api.py`) all live-tested via real HTTP requests.

SMTP + IMAP credentials are both in `.env` (gitignored) and confirmed working as of this
session — that gap is closed.

## Real bugs found and fixed this build (worth knowing before extending anything)

- SAM.gov's `pointOfContact` is the *government's* contracting officer, not the awardee
  company's staff (verified: every one was a `.mil`/`.gov` address) — don't reintroduce
  this by pulling contact data from SAM.gov's award JSON.
- SAM.gov's award data has no company-website field at all — `enrich_company_site` only
  uses `company.domain`, never a source-record URL as a fallback (that was a real bug,
  fixed).
- Segment/Channel/etc. DB columns come back as plain strings after a round-trip, not the
  Python enum — use `enum_value()` from `agents/util.py`, don't call `.value` directly on
  something read from the DB.
- SMTP validation: this dev sandbox's outbound IP was Spamhaus-blocklisted, which made
  Microsoft-365-hosted domains (a large share of real businesses) reject the whole
  session on IP reputation, not recipient validity. The validator correctly distinguishes
  "no such user" from "inconclusive/reputation" now — don't collapse those back into one
  "invalid" bucket if touching `validation/validate.py`.
- ACEC directory contact names are unreliable: checked live against two firms'
  real staff pages ([Lead 2], [Lead 3]) and in both cases the ACEC "Primary
  Contact" name didn't match anyone on the company's own official staff list ("[contact E]" → real CEO is [contact D]; "[contact F]" → not on staff at all).
  Worth treating ACEC-sourced person names as a starting guess, not verified fact.
- Both firms checked also confirm a broader pattern: professional engineering firms
  commonly publish zero individual staff emails, only a general role inbox
  (`contact@...`) — the Email Finder Agent will likely park a large share of ae_firm
  leads as 'unresolved' via public-web research alone; a paid data source may be the
  only way past this for that segment specifically.

## Open decisions / where to pick up

- **The two real drafts ([Lead 1], [Lead 2]) still have not been sent**
  — both are blocked on the send gate's validated-email requirement (see below), not on
  a policy decision anymore. Sending to a real third party needs the user's explicit
  go-ahead the first time in any new session regardless.
- **[Lead 1] (person_id 386): retry the SAM.gov Entity Management API
  lookup.** UEI is known (`[UEI removed]`). `settings.sam_gov_api_key` hit its daily quota
  on 2026-08-12 (resets 2026-08-13 00:00 UTC) — this was the correct next lever (the
  entity's own registered POC email, not the award's contracting officer) but untried
  past the quota wall. Query `https://api.sam.gov/entity-information/v3/entities?ueiSAM=[UEI removed]`,
  then run it through `get_finder_context`/`save_finder_result` (person_id 386) same as
  any other finder result.
- [Lead 2] (person_id 3) and [Lead 3] (person_id 4) are parked 'unresolved' —
  public-web research (official staff pages) found named leadership but no individual
  emails, only generic role inboxes. No further public-web lever identified; would need
  a paid contact-data source or a different channel to move past this.
- **Agent-Reach was evaluated and deliberately not installed.** Its zero-auth path
  (public LinkedIn page reads via Jina Reader) is already covered by plain WebFetch, no
  install needed. Everything else requires cookie-based login reusing a real browser
  session, and its own docs warn of account-ban risk — directly conflicts with this
  project's standing "no LinkedIn automation" decision above. Don't reopen this without
  the user explicitly accepting that risk on a real account.
- `EMAIL_WARMUP_START_DATE` in `.env` is still blank — defaults to "today" every run,
  which is fine for testing but will silently restart the warmup ramp if left unset once
  real sending begins. Pin it the day real sending actually starts.
- Graph-queryable memory (Apache AGE on top of the existing Postgres instance) was
  deliberately deferred until real query patterns are known — see ARCHITECTURE.md §7 if
  this comes up again. Don't build it speculatively.
- First-pass sourcing scope is intentionally narrow (SAM.gov US-only, ACEC Texas only).
  Widening to TED/AusTender/CanadaBuys and more ACEC chapters/licensing boards is the
  documented next expansion, not yet started.

## If migrating to a raw LLM API later

The MCP-server-as-substrate design (scout exposes tools, an external reasoner calls them)
isn't Claude-Code-specific — any agent loop that can call the tools in `scout/mcp_server.py`
works the same way. The cost model changes, though: `get_scoring_context`/`get_drafting_context`/
`get_reply_classification_context` calls would need a real LLM API call wrapped around
them instead of a Claude Code session doing the reasoning inline, which reintroduces
per-token cost that the current design deliberately avoids. Worth deciding consciously,
not defaulting into.
