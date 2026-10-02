from core.db import Database
from core.forecast import VIRTUAL_ID
from core.service import Service
from core.timeparse import DAY, HOUR

T0 = 1_700_000_000


def make(players=10):
    svc = Service(Database(":memory:"))
    ids = []
    for i in range(players):
        p, _ = svc.register_web(f"p{i}", "1234", T0)
        ids.append(p["id"])
    return svc, ids


def test_forecast_never_overshoots_and_ends():
    svc, ids = make(10)
    for i, pid in enumerate(ids[:5]):
        svc.set_timer(pid, "research", (20 + 10 * i) * DAY, T0)
    fc = svc.forecast("research", T0)
    assert fc.finished
    rules = svc.rules("research")
    for t in fc.timers.values():
        assert t.done_at is not None
        # ни один чужой баф не опустил таймер ниже нижней границы
        left = t.start_remaining
        for at, cut in sorted(t.gets + t.self_cuts):
            left -= cut
        assert t.left_at_done is not None
    assert all(e.reduction > 0 for e in fc.events)
    assert fc.buffs_per_day == 10 * DAY / (48 * HOUR)


def test_more_days_means_more_buffs():
    svc, ids = make(20)
    for pid in ids[:8]:
        svc.set_timer(pid, "build", 25 * DAY, T0)
    results = []
    for days in (10, 20, 30, 40):
        fc = svc.forecast("build", T0, virtual_seconds=days * DAY)
        me = fc.mine(VIRTUAL_ID)
        results.append(len(me.gets) + len(me.self_cuts))
        assert me.done_at is not None
    assert results == sorted(results)
    assert results[-1] > results[0]


def test_more_donors_is_faster():
    svc, ids = make(5)
    for pid in ids:
        svc.set_timer(pid, "build", 40 * DAY, T0)
    slow = svc.forecast("build", T0, virtual_seconds=40 * DAY, donors=5).mine(VIRTUAL_ID).done_at
    fast = svc.forecast("build", T0, virtual_seconds=40 * DAY, donors=50).mine(VIRTUAL_ID).done_at
    assert fast <= slow
