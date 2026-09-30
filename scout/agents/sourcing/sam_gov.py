"""Sourcing Agent: SAM.gov (US federal contract opportunities/awards).

Real, documented, free API -- see https://open.gsa.gov/api/get-opportunities-public-api/
Base URL: https://api.sam.gov/opportunities/v2/search
Auth: api_key query param. NOT an api.data.gov key (confirmed by testing -- those 404 on
this endpoint despite looking valid). Get it from SAM.gov itself: sign in/register at
sam.gov, then Account Details -> "Public API Key".

A company appearing here isn't a guess at being a "government contractor" -- it's a
confirmed one, with the exact contract to reference in outreach. See ARCHITECTURE.md §3.1.
"""

from datetime import date, timedelta

import httpx
from sqlalchemy.orm import Session

from scout.agents.events import log_event
from scout.config import settings
from scout.db.models import Company, Segment

BASE_URL = "https://api.sam.gov/opportunities/v2/search"

# Geography allow-list per ARCHITECTURE.md §3.1: North America, South America, Europe,
# Australia. SAM.gov's `state` filter covers US states/territories only (place of
# performance) -- non-US geography for gov contractors is out of scope for this source
# and would need TED/CanadaBuys/AusTender (see ARCHITECTURE.md §10, deferred).
US_STATES = [
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL", "IN",
    "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV",
    "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN",
    "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY", "DC",
]


class SamGovError(RuntimeError):
    pass


def fetch_awards(
    *,
    naics_code: str | None = None,
    states: list[str] | None = None,
    days_back: int = 90,
    limit: int = 100,
) -> list[dict]:
    """Fetch opportunities with award data from SAM.gov. Returns raw award records.

    days_back: SAM.gov requires a bounded postedFrom/postedTo range (max 1 year).
    """
    if not settings.sam_gov_api_key:
        raise SamGovError(
            "SAM_GOV_API_KEY not set. Get it from sam.gov itself (Account Details -> "
            "Public API Key) -- NOT api.data.gov, confirmed those 404 on this endpoint. "
            "Put it in .env"
        )

    today = date.today()
    posted_from = (today - timedelta(days=days_back)).strftime("%m/%d/%Y")
    posted_to = today.strftime("%m/%d/%Y")

    params = {
        "api_key": settings.sam_gov_api_key,
        "postedFrom": posted_from,
        "postedTo": posted_to,
        "limit": limit,
    }
    if naics_code:
        params["ncode"] = naics_code

    results: list[dict] = []
    target_states = states or US_STATES
    with httpx.Client(timeout=30) as client:
        for state in target_states:
            resp = client.get(BASE_URL, params={**params, "state": state})
            resp.raise_for_status()
            data = resp.json()
            for record in data.get("opportunitiesData", []):
                award = record.get("award") or {}
                if award.get("awardee"):
                    results.append(record)
    return results


def ingest_awards(session: Session, records: list[dict]) -> list[Company]:
    """Turn raw SAM.gov award records into Company rows (no Person rows -- see the note
    below on why pointOfContact isn't usable for that). Idempotent on (name, source,
    state, city) -- large contractors win awards from multiple regional offices (same
    pattern verified against the ACEC directory for AECOM; the same risk applies here),
    so city is included to avoid treating distinct offices as duplicates.
    """
    event = log_event(
        session,
        actor="sourcing.sam_gov",
        action="ingest_awards",
        input_data={"record_count": len(records)},
    )

    companies: list[Company] = []
    for record in records:
        awardee = record["award"]["awardee"]
        location = awardee.get("location", {})
        name = awardee.get("name")
        if not name:
            continue

        city = (location.get("city") or {}).get("name")
        state_code = (location.get("state") or {}).get("code")

        existing = (
            session.query(Company)
            .filter_by(name=name, source="sam_gov", state=state_code, city=city)
            .one_or_none()
        )
        if existing:
            continue

        company = Company(
            name=name,
            source="sam_gov",
            segment=Segment.gov_contractor,
            source_url=f"https://sam.gov/opp/{record.get('noticeId', '')}",
            city=city,
            state=state_code,
            country="United States",
            geography="North America",
            raw_jsonb=record,
        )
        session.add(company)
        session.flush()

        # Deliberately NOT creating Person rows from record["pointOfContact"] here.
        # Verified against real pulled data: every pointOfContact email is a .mil/.gov
        # address -- the GOVERNMENT's contracting officer for this award, not anyone at
        # the awardee company. Materializing those as outreach-target "people" would mean
        # pitching a commercial tool to a federal contracting officer, which is both
        # pointless (wrong persona) and inappropriate. Real contacts at the awardee
        # company come from the Enrichment Agent (company site scraping) instead -- see
        # scout/agents/enrichment/enrich.py, which now also extracts any real emails
        # found on the company's own site.
        companies.append(company)

    event.output_jsonb = {"companies_created": len(companies)}
    return companies
