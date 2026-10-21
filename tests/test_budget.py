from datetime import date, timedelta

from scout.agents.ratelimit.budget import (
    can_send,
    get_or_create_budget,
    record_send,
    set_killswitch,
    suppress,
    warmup_cap_for_day,
)
from scout.db.models import AgentEvent, Channel

from tests.factories import make_person

START = date(2026, 1, 1)


def _cap_on_day(n):
    return warmup_cap_for_day(START, START + timedelta(days=n))


def test_warmup_ramp_boundaries():
    assert [_cap_on_day(0), _cap_on_day(6)] == [5, 5]
    assert [_cap_on_day(7), _cap_on_day(13)] == [10, 10]
    assert [_cap_on_day(14), _cap_on_day(20)] == [20, 20]
    assert [_cap_on_day(21), _cap_on_day(27)] == [30, 30]
    assert [_cap_on_day(28), _cap_on_day(365)] == [40, 40]


def test_a_start_date_in_the_future_is_treated_as_day_zero():
    assert warmup_cap_for_day(START, START - timedelta(days=5)) == 5


def test_a_fresh_person_can_send(session):
    p = make_person(session)
    assert can_send(session, Channel.email, date.today(), p.id) == (True, "ok")


def test_killswitch_blocks_everything_and_can_be_cleared(session):
    p = make_person(session)
    set_killswitch(session, True, "testing")
    assert can_send(session, Channel.email, date.today(), p.id) == (False, "killswitch_engaged")
    set_killswitch(session, False)
    assert can_send(session, Channel.email, date.today(), p.id)[0] is True


def test_killswitch_changes_are_audited(session):
    set_killswitch(session, True, "because")
    event = session.query(AgentEvent).filter_by(actor="ratelimit.killswitch").one()
    assert event.input_jsonb == {"engaged": True, "reason": "because"}


def test_suppressed_people_are_blocked_and_suppression_is_idempotent(session):
    p = make_person(session)
    suppress(session, p.id, "unsubscribe")
    suppress(session, p.id, "unsubscribe again")
    session.flush()
    assert can_send(session, Channel.email, date.today(), p.id) == (False, "person_suppressed")
    assert session.query(AgentEvent).filter_by(actor="ratelimit.suppression").count() == 1


def test_daily_cap_is_enforced_at_exactly_the_cap(session):
    p = make_person(session)
    start = date.today()  # day 0 of warmup -> cap 5
    for _ in range(5):
        assert can_send(session, Channel.email, start, p.id)[0] is True
        record_send(session, Channel.email, start)
    allowed, reason = can_send(session, Channel.email, start, p.id)
    assert allowed is False and reason == "daily_cap_reached (5/5)"


def test_budget_row_is_created_once_per_day_and_channel(session):
    a = get_or_create_budget(session, Channel.email, date.today())
    b = get_or_create_budget(session, Channel.email, date.today())
    assert a is b and a.cap == 5
