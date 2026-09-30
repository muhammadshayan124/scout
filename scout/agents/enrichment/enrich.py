"""Enrichment Agent -- read-only, lowest-risk component in the system (ARCHITECTURE.md
§3.2). First pass: fetch the company's own site and pull readable text, which is what
makes drafted messages reference something real instead of being templated.

Also the source of real outreach-target contacts for gov_contractor leads: SAM.gov's
pointOfContact data is the *government's* contracting officer, not anyone at the awardee
company (verified against real pulled data -- every one was a .mil/.gov address), so
those are deliberately not stored as Person rows (see sourcing/sam_gov.py). A company's
own site is the honest source for a real contact instead.

Deliberately NOT wired to Agent-Reach yet: Agent-Reach is an external CLI tool (its own
install step, its own auth story per platform), and a working httpx-based site fetch
already delivers real personalization context with zero new dependencies. Swapping in
Agent-Reach for LinkedIn-specific context (recent posts, job changes) is a natural next
slice once this baseline is proven -- see ARCHITECTURE.md §3.2.
"""

import re

import httpx
from sqlalchemy.orm import Session

from scout.agents.events import log_event
from scout.agents.util import classify_contact_status
from scout.db.models import Company, EmailAddress, Enrichment, Person

_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")

# Filenames and tracking/CDN addresses that match the email regex but aren't real contact
# emails -- excluded rather than trusted blindly.
_JUNK_EMAIL_DOMAINS = (
    "sentry.io", "wixpress.com", "example.com", "godaddy.com", "schema.org",
    "w3.org", "yoursite.com", "domain.com",
)


def _extract_text(html: str, max_chars: int = 4000) -> str:
    text = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>", " ", html)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_chars]


def _extract_emails(html: str) -> list[str]:
    """Find real contact emails: mailto: links (highest confidence) plus a plain-text
    regex fallback over the page, both filtered against known junk/tracking domains and
    image-filename false positives (regex email patterns can match things like
    'photo@2x.png' style asset names).
    """
    mailtos = re.findall(r'mailto:([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})', html)
    text_matches = _EMAIL_RE.findall(re.sub(r"<[^>]+>", " ", html))

    seen: list[str] = []
    for addr in mailtos + text_matches:
        addr = addr.lower().strip()
        domain = addr.split("@")[-1]
        if domain in _JUNK_EMAIL_DOMAINS:
            continue
        if re.search(r"\.(png|jpg|jpeg|gif|svg|webp)$", addr):
            continue
        if addr not in seen:
            seen.append(addr)
    return seen


def enrich_company_site(session: Session, company: Company) -> dict | None:
    """Fetch and extract text from a company's own website, if we have a URL for it.

    Deliberately only looks at company.domain, never source_url as a fallback: source_url
    is "where this record came from" (e.g. a SAM.gov opportunity page), not the company's
    own site -- confirmed by testing, that fallback was silently "succeeding" against
    SAM.gov's page and returning ~11 chars of junk for every gov_contractor lead. SAM.gov's
    award data has no company-website field at all (checked the raw record directly), so
    gov_contractor leads currently have no site to enrich from -- see get_drafting_context,
    which uses the real award data (title/amount/agency) instead, which is honestly better
    personalization material for that segment anyway.
    """
    url = company.domain
    if not url:
        return None
    if not url.startswith("http"):
        url = f"https://{url}"

    try:
        with httpx.Client(timeout=15, follow_redirects=True) as client:
            resp = client.get(url, headers={"User-Agent": "Mozilla/5.0 (scout research bot)"})
            resp.raise_for_status()
    except httpx.HTTPError as exc:
        log_event(
            session,
            actor="enrichment.company_site",
            action="fetch_failed",
            entity_type="company",
            entity_id=company.id,
            output_data={"error": str(exc)},
        )
        return None

    blob = {"source": "company_site", "url": url, "text": _extract_text(resp.text)}
    found_emails = _extract_emails(resp.text)

    log_event(
        session,
        actor="enrichment.company_site",
        action="fetch_ok",
        entity_type="company",
        entity_id=company.id,
        output_data={"chars": len(blob["text"]), "emails_found": len(found_emails)},
    )

    # A named contact already exists (e.g. ACEC's "Primary Contact") -- attach discovered
    # emails there rather than inventing a second, redundant person for the same company.
    target_person = company.people[0] if company.people else None
    if target_person is None and found_emails:
        target_person = Person(
            company_id=company.id,
            name=None,
            title="General contact (site)",
            source="enrichment",
        )
        session.add(target_person)
        session.flush()
        company.people.append(target_person)

    if target_person is not None:
        session.add(Enrichment(person_id=target_person.id, blob_jsonb=blob))
        for addr in found_emails:
            exists = (
                session.query(EmailAddress)
                .filter_by(person_id=target_person.id, address=addr)
                .one_or_none()
            )
            if not exists:
                session.add(EmailAddress(person_id=target_person.id, address=addr))
        session.flush()
        target_person.contact_status = classify_contact_status(target_person)

    return blob
