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
    make_pattern,
    parse_pattern,
    slot_for,
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


def test_cycle_patterns_both_ways():
    assert make_pattern(1, 2) == "BWW"
    assert make_pattern(2, 1) == "BBW"
    assert make_pattern(1, 2, wait_first=True) == "WWB"
    assert make_pattern(3, 0) == "BBB" and make_pattern(0, 2) == "WW"
    assert make_pattern(0, 0) == "B"  # пустой цикл не бывает
    assert make_pattern(9, 9) == "B" * 6 + "W" * 6  # не больше 6 подряд
    for big, wait, first in [(1, 1, False), (1, 2, True), (3, 3, False), (2, 3, True)]:
        assert parse_pattern(make_pattern(big, wait, first)) == (big, wait, first)
    # Цикл «1 : 2, сначала меньше получившим» повторяется: W W B W W B …
    assert "".join(slot_for(i, make_pattern(1, 2, True)) for i in range(6)) == "WWBWWB"


# ---------- очередь «по доле от положенного» ----------

SHARE = Rules(min_left=5 * DAY, max_left=7 * DAY, order="share", min_gap=12 * HOUR,
              fire_window=24 * HOUR, priority_weight=2)


def sc(pid, remaining, base=None, received=0, since=0, **kw):
    base = base or remaining
    from core.logic import buff_reduction as red
    return Candidate(pid, f"p{pid}", remaining, base, red(base, remaining, SHARE), False, since, received, **kw)


def test_share_lowest_fraction_goes_first():
    from core.logic import share
    a = sc(1, 30 * DAY, received=1, since=10)  # 1 из 6 ≈ 17%
    b = sc(2, 12 * DAY, received=0, since=20)  # 0 из 3 — ещё не получал
    c = sc(3, 20 * DAY, received=2, since=0)   # больше доля
    assert share(b, SHARE) == 0
    pick, slot = choose_recipient([a, b, c], SHARE, 0, [], now=10**6)
    assert pick.player_id == 2 and slot == "S"
    pick, _ = choose_recipient([a, c], SHARE, 0, [], now=10**6)
    assert pick.player_id == 1


def test_share_ties_go_to_longest_waiting():
    a = sc(1, 20 * DAY, since=500)
    b = sc(2, 20 * DAY, since=100)
    assert choose_recipient([a, b], SHARE, 0, [], now=10**6)[0].player_id == 2


def test_priority_weight_moves_power_plant_ahead():
    plain = sc(1, 20 * DAY, received=1, since=0)  # доля 1/(1+…) больше, чем у приоритетного /2
    prio = sc(2, 20 * DAY, received=2, since=0, priority=True)
    from core.logic import share_key
    assert share_key(prio, SHARE)[0] < share_key(plain, SHARE)[0]
    assert choose_recipient([plain, prio], SHARE, 0, [], now=10**6)[0].player_id == 2


def test_fire_goes_first():
    calm = sc(1, 30 * DAY, since=0)
    burning = sc(2, 7 * DAY + 10 * HOUR, base=10 * DAY, received=2, since=999)
    pick, slot = choose_recipient([calm, burning], SHARE, 0, [], now=10**6)
    assert pick.player_id == 2 and slot == "F"


def test_holder_skips_turn_but_still_gets_if_alone():
    holder = sc(1, 30 * DAY, since=0, holding=5 * HOUR)
    other = sc(2, 30 * DAY, received=3, since=0)
    assert choose_recipient([holder, other], SHARE, 0, [], now=10**6)[0].player_id == 2
    assert choose_recipient([holder], SHARE, 0, [], now=10**6)[0].player_id == 1


def test_share_respects_pause():
    now = 10**6
    fresh = sc(1, 30 * DAY, since=0, last_got=now - HOUR)
    waiting = sc(2, 30 * DAY, received=4, since=0)
    assert choose_recipient([fresh, waiting], SHARE, 0, [], now=now)[0].player_id == 2
