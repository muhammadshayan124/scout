"""Sourcing Agent: ACEC Texas member firms, via the national ACEC directory filtered to
Texas -- ACEC Texas's own site has no public firm directory (verified: no "Find a Firm"
nav link, and the direct search URL 404s). The national directory at netforum.acec.org
does, confirmed live and unauthenticated (no login wall), with real firm data: name,
address, phone, website, primary contact.

Directory entry point:
  https://netforum.acec.org/EWEB/DynamicPage.aspx?Site=ACEC&WebKey=e3d6141d-69bb-428f-8ae7-cb13f2823557

Submitting the state filter (Texas) posts back to a results page whose pagination URLs
carry a per-search `filekey` token, so this has to be driven as a real browser session
(select state -> click Go -> scrape -> follow letter/page links), not reconstructed from
static URLs. See ARCHITECTURE.md §3.1 and §10.
"""

import re

from playwright.sync_api import sync_playwright, Page
from sqlalchemy.orm import Session

from scout.agents.events import log_event
from scout.db.models import Company, Person, Segment

# Matches a "City, ST 12345" line regardless of how many lines precede it -- some
# addresses have an extra building-name line before the street (verified live: "AECOM,
# One Galleria Tower / 13355 Noel RD Ste 400 / Dallas, TX 75240-6825" is 3 lines, not the
# usual 2), so a fixed line-index assumption silently extracted the street as the city.
_CITY_STATE_RE = re.compile(r"^(.+?),\s*([A-Z]{2})\s+\d{5}")


def _extract_city(address: str) -> str | None:
    for line in (address or "").splitlines():
        m = _CITY_STATE_RE.match(line.strip())
        if m:
            return m.group(1).strip()
    return None

SEARCH_URL = (
    "https://netforum.acec.org/EWEB/DynamicPage.aspx"
    "?Site=ACEC&WebKey=e3d6141d-69bb-428f-8ae7-cb13f2823557"
)
GO_BUTTON_ID = "C_3_1_ButtonFindGo"


def _scrape_current_page(page: Page) -> list[dict]:
    """Extract firm records from the results page's label/value table blocks."""
    return page.evaluate(
        """
        () => {
          const labels = ['Name', 'Address', 'Phone', 'Website', 'Primary Contact'];
          const nameCells = Array.from(document.querySelectorAll('td'))
            .filter(td => td.querySelector('b') && td.querySelector('b').textContent.trim() === 'Name:');
          return nameCells.map(nameLabelTd => {
            const row = nameLabelTd.closest('tr');
            const table = row.closest('table');
            const record = {};
            table.querySelectorAll('tr').forEach(tr => {
              const labelEl = tr.querySelector('td b');
              if (!labelEl) return;
              const label = labelEl.textContent.replace(':', '').trim();
              if (!labels.includes(label)) return;
              const valueTd = tr.querySelectorAll('td')[1];
              record[label] = valueTd ? valueTd.innerText.trim() : null;
            });
            return record;
          }).filter(r => r.Name);
        }
        """
    )


def _has_next_page(page: Page) -> bool:
    """Note: the pagination link's href is relative (e.g. 'DynamicPage.aspx?...') and
    resolves against the current page's actual path (which includes an '/eweb/' segment
    not present in the entry URL). Reconstructing that URL manually and navigating to it
    breaks the session's filekey and silently lands on an empty results page -- verified
    by testing (page 2 returned 0 records). Clicking the real link element instead lets
    the browser resolve it correctly, which is what fetch_firms below does.
    """
    return page.evaluate(
        """
        () => !!Array.from(document.querySelectorAll('a'))
          .find(a => a.textContent.trim() === 'Next >>' || a.textContent.trim() === 'Next')
        """
    )


def _click_next_page(page: Page) -> None:
    page.locator("a", has_text="Next").first.click()


def fetch_firms(state: str = "TX", max_pages: int = 500) -> list[dict]:
    """Drive a real browser session against the national ACEC directory, filtered to
    `state`, and return every firm record found across all paginated results.
    """
    records: list[dict] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(SEARCH_URL, wait_until="load", timeout=45000)

        # Select the state in the "State/Territory:" dropdown -- targeted via XPath
        # (a <select> containing an <option value="TX">) rather than a specific id/name,
        # since ASP.NET WebForms control IDs are liable to change between deploys.
        state_select = page.locator(f'xpath=//select[option[@value="{state}"]]').first
        state_select.select_option(state)
        page.click(f"#{GO_BUTTON_ID}")
        page.wait_for_load_state("load", timeout=45000)

        page_count = 0
        while page_count < max_pages:
            records.extend(_scrape_current_page(page))
            if not _has_next_page(page):
                break
            _click_next_page(page)
            page.wait_for_load_state("load", timeout=45000)
            page_count += 1

        browser.close()

    return records


def ingest_firms(session: Session, records: list[dict], state: str = "TX") -> list[Company]:
    """Turn raw ACEC firm records into Company/Person rows. Idempotent on
    (name, source, state) via the DB unique constraint.
    """
    event = log_event(
        session,
        actor="sourcing.acec_tx",
        action="ingest_firms",
        input_data={"record_count": len(records), "state": state},
    )

    companies: list[Company] = []
    for record in records:
        name = record.get("Name")
        if not name:
            continue

        city = _extract_city(record.get("Address", ""))

        # city included in the dedup lookup deliberately -- large firms like AECOM
        # register each regional office separately under the same name (verified live:
        # AECOM has 5 distinct TX offices in this directory). Matching on name+state
        # alone would treat 4 of those 5 real, distinct leads as duplicates.
        existing = (
            session.query(Company)
            .filter_by(name=name, source="acec_tx", state=state, city=city)
            .one_or_none()
        )
        if existing:
            continue

        company = Company(
            name=name,
            source="acec_tx",
            segment=Segment.ae_firm,
            source_url=None,
            domain=record.get("Website"),
            city=city,
            state=state,
            country="United States",
            geography="North America",
            phone=record.get("Phone"),
            raw_jsonb=record,
        )
        session.add(company)
        session.flush()

        contact = record.get("Primary Contact")
        if contact:
            person = Person(company_id=company.id, name=contact, source="acec_tx")
            session.add(person)

        companies.append(company)

    event.output_jsonb = {"companies_created": len(companies)}
    return companies
