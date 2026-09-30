"""Rate Limiter / Budget Service (ARCHITECTURE.md §3.7) -- one shared component every
outreach agent calls through before acting, not scattered per-agent logic. Tracks the
warmup ramp, today's send count vs. cap, the suppression list, and a global kill switch.

Warmup ramp numbers are the same ones cited in ARCHITECTURE.md's original research pass
(cold-email deliverability sources): 5-10/day weeks 1-2, scaling to 30-40/day once fully
warmed. This is deliberately conservative given ARCHITECTURE.md §3.6's real point: there's
no dedicated sending domain here, so a bounce/complaint spike hits the same personal inbox
used for everything else -- worth erring low, not matching the aggressive end of published
benchmarks.
"""

from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from scout.agents.events import log_event
from scout.db.models import Channel, RateBudget, SuppressionListEntry, SystemFlag

KILLSWITCH_KEY = "global_killswitch"

# (days_since_warmup_start, cap) -- first matching threshold wins, checked in order.
_WARMUP_RAMP = [
    (7, 5),
    (14, 10),
    (21, 20),
    (28, 30),
]
_WARMUP_STEADY_STATE_CAP = 40


def warmup_cap_for_day(warmup_start: date, today: date | None = None) -> int:
    today = today or date.today()
    days_in = (today - warmup_start).days
    if days_in < 0:
        days_in = 0
    for threshold, cap in _WARMUP_RAMP:
        if days_in < threshold:
            return cap
    return _WARMUP_STEADY_STATE_CAP


def get_or_create_budget(session: Session, channel: Channel | str, warmup_start: date) -> RateBudget:
    today = date.today()
    row = session.get(RateBudget, {"channel": channel, "date": today})
    if row is None:
        row = RateBudget(
            channel=channel, date=today, sent_count=0,
            cap=warmup_cap_for_day(warmup_start, today),
        )
        session.add(row)
        session.flush()
    return row


def is_killswitch_engaged(session: Session) -> tuple[bool, str | None]:
    flag = session.get(SystemFlag, KILLSWITCH_KEY)
    if flag is None or flag.value != "engaged":
        return False, None
    return True, flag.value


def set_killswitch(session: Session, engaged: bool, reason: str = "") -> None:
    flag = session.get(SystemFlag, KILLSWITCH_KEY)
    value = "engaged" if engaged else "clear"
    if flag is None:
        flag = SystemFlag(key=KILLSWITCH_KEY, value=value)
        session.add(flag)
    else:
        flag.value = value
        flag.updated_at = datetime.utcnow()
    log_event(
        session, actor="ratelimit.killswitch", action="set",
        input_data={"engaged": engaged, "reason": reason},
    )


def is_suppressed(session: Session, person_id: int) -> bool:
    return (
        session.query(SuppressionListEntry).filter_by(person_id=person_id).one_or_none()
        is not None
    )


def suppress(session: Session, person_id: int, reason: str) -> None:
    existing = session.query(SuppressionListEntry).filter_by(person_id=person_id).one_or_none()
    if existing:
        return
    session.add(SuppressionListEntry(person_id=person_id, reason=reason))
    log_event(
        session, actor="ratelimit.suppression", action="add",
        entity_type="person", entity_id=person_id, input_data={"reason": reason},
    )


def can_send(session: Session, channel: Channel | str, warmup_start: date, person_id: int) -> tuple[bool, str]:
    """The single gate every outreach agent must pass through before sending anything.
    Returns (allowed, reason) -- reason explains a False result, useful both for logs and
    for surfacing back to whoever's driving the pipeline.
    """
    engaged, _ = is_killswitch_engaged(session)
    if engaged:
        return False, "killswitch_engaged"

    if is_suppressed(session, person_id):
        return False, "person_suppressed"

    budget = get_or_create_budget(session, channel, warmup_start)
    if budget.sent_count >= budget.cap:
        return False, f"daily_cap_reached ({budget.sent_count}/{budget.cap})"

    return True, "ok"


def record_send(session: Session, channel: Channel | str, warmup_start: date) -> None:
    budget = get_or_create_budget(session, channel, warmup_start)
    budget.sent_count += 1
