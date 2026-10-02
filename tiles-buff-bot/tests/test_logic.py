from core.logic import (
    SLOT_BIG,
    SLOT_URGENT,
    SLOT_WAIT,
    Candidate,
    Rules,
    buff_reduction,
    buffs_needed,
    choose_recipient,
    is_eligible,
)
from core.timeparse import DAY, HOUR

BUILD = Rules(min_left=5 * DAY, max_left=7 * DAY, pattern="BBW")
RESEARCH = Rules(min_left=7 * DAY, max_left=8 * DAY, pattern="BBW")


def cand(pid, days, waiting_since=0, urgent=False, rules=BUILD, base_days=None, received=0, last_got=None):
    remaining = int(days * DAY)
    base = int((base_days or days) * DAY)
    return Candidate(pid, f"p{pid}", remaining, base, buff_reduction(base, remaining, rules), urgent, waiting_since,
                     received=received, last_got=last_got)


def test_declared_mode_cuts_from_declared_time():
    assert buff_reduction(100 * DAY, 50 * DAY, BUILD) == 15 * DAY


def test_remaining_mode():
    rules = Rules(min_left=5 * DAY, max_left=7 * DAY, mode="remaining")
    assert buff_reduction(100 * DAY, 50 * DAY, rules) == int(7.5 * DAY)


def test_build_21_days_needs_5_buffs():
    # 21 → 17.85 → 14.7 → 11.55 → 8.4 → 5.25 (в коридоре 5–7)
    assert buffs_needed(21 * DAY, 21 * DAY, BUILD) == 5


def test_research_100_days_stops_without_overshoot():
    # 100 → 85 → … → 10; ещё один баф (−15) ушёл бы ниже 7, поэтому стоп
    assert buffs_needed(100 * DAY, 100 * DAY, RESEARCH) == 6


def test_not_eligible_near_target():
    assert not is_eligible(6 * DAY, DAY, False, BUILD)
    assert not is_eligible(8 * DAY, 4 * DAY, False, BUILD)
    assert is_eligible(6 * DAY, DAY, True, BUILD)


def test_cycle_big_big_wait():
    big = cand(1, 60, waiting_since=500)
    small = cand(2, 15, waiting_since=100)
    assert choose_recipient([big, small], BUILD, 0, []) == (big, SLOT_BIG)
    assert choose_recipient([big, small], BUILD, 1, []) == (big, SLOT_BIG)
    assert choose_recipient([big, small], BUILD, 2, []) == (small, SLOT_WAIT)


def test_streak_limit_passes_to_next():
    big = cand(1, 60)
    other = cand(2, 30)
    pick, _ = choose_recipient([big, other], BUILD, 0, [1, 1])
    assert pick == other
    # если больше некому — всё равно отдаём
    pick, _ = choose_recipient([big], BUILD, 0, [1, 1])
    assert pick == big


def test_urgent_goes_first():
    big = cand(1, 60)
    urgent = cand(2, 6, urgent=True)
    assert choose_recipient([big, urgent], BUILD, 0, []) == (urgent, SLOT_URGENT)


def test_nobody_needs():
    assert choose_recipient([cand(1, 6)], BUILD, 0, []) == (None, "")


def test_pause_after_buff_lets_others_go_first():
    rules = Rules(min_left=5 * DAY, max_left=7 * DAY, pattern="B", min_gap=12 * HOUR)
    now = 100 * DAY
    big = cand(1, 60, last_got=now - 2 * HOUR, received=1)
    small = cand(2, 15)
    assert choose_recipient([big, small], rules, 0, [], now)[0] == small
    # пауза прошла — снова большой
    assert choose_recipient([big, small], rules, 0, [], now + 11 * HOUR)[0] == big


def test_pause_applies_to_urgent_too():
    rules = Rules(min_left=5 * DAY, max_left=7 * DAY, min_gap=12 * HOUR)
    now = 100 * DAY
    urgent = cand(1, 30, urgent=True, last_got=now - HOUR, received=3)
    other = cand(2, 20)
    assert choose_recipient([urgent, other], rules, 0, [], now)[0] == other


def test_everyone_paused_buff_is_not_wasted():
    rules = Rules(min_left=5 * DAY, max_left=7 * DAY, min_gap=12 * HOUR)
    now = 100 * DAY
    a = cand(1, 30, last_got=now - 5 * HOUR, received=1)
    b = cand(2, 20, last_got=now - 9 * HOUR, received=1)
    assert choose_recipient([a, b], rules, 0, [], now)[0] == b


def test_fair_round_nobody_runs_ahead():
    rules = Rules(min_left=5 * DAY, max_left=7 * DAY, pattern="B", max_ahead=1)
    big = cand(1, 60, received=1)
    small = cand(2, 12, received=0)
    # большой уже получил один — пока маленький не получит первый, большой ждёт
    assert choose_recipient([big, small], rules, 0, [])[0] == small


def test_wait_slot_prefers_who_got_least():
    rules = Rules(min_left=5 * DAY, max_left=7 * DAY, pattern="W")
    veteran = cand(1, 40, waiting_since=0, received=3)
    newbie = cand(2, 20, waiting_since=500, received=0)
    assert choose_recipient([veteran, newbie], rules, 0, [])[0] == newbie
