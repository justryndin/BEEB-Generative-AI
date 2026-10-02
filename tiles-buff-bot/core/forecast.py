"""Прогноз очереди: моделируем, кто и когда получит бафы, если все соблюдают порядок.

Модель — те же правила, что и у настоящей очереди (logic.choose_recipient):
* каждый игрок-донор отдаёт один баф каждого типа раз в cooldown часов;
  если бот знает, когда баф будет готов (cooldowns), берём это время,
  иначе считаем, что бафы доноров готовы равномерно в течение ближайшего окна;
* баф получает тот, кого выбрала бы очередь (цикл B/W, лимит подряд, пороги цели);
* баф сокращает и таймер самого донора, если у него идёт таймер этого типа;
* все таймеры идут в реальном времени; игроки сами не ускоряются.
Как только никому больше не нужны бафы, прогноз заканчивается.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field

from .logic import SLOT_URGENT, STATUS_NEED, Candidate, Rules, buff_reduction, choose_recipient, timer_status
from .timeparse import DAY, HOUR

VIRTUAL_ID = 0  # «игрок» для калькулятора


@dataclass
class SimTimer:
    pid: int
    nick: str
    remaining: float
    base: int
    urgent: bool = False
    waiting_since: float = 0
    received: int = 0
    label: str = ""
    gets: list[tuple[float, int]] = field(default_factory=list)  # (когда, сколько срезал чужой баф)
    self_cuts: list[tuple[float, int]] = field(default_factory=list)  # (когда, сколько срезал свой баф)
    last_got: float | None = None
    done_at: float | None = None
    left_at_done: float | None = None
    start_remaining: float = 0

    def candidate(self, rules: Rules) -> Candidate:
        rem = int(self.remaining)
        return Candidate(
            self.pid, self.nick, rem, self.base, buff_reduction(self.base, rem, rules),
            self.urgent, int(self.waiting_since), self.received,
            last_got=int(self.last_got) if self.last_got else None,
        )


@dataclass
class Event:
    at: float
    pid: int
    nick: str
    reduction: int
    slot: str
    donor: int


@dataclass
class Forecast:
    kind: str
    start: int
    donors: int
    cooldown: float
    events: list[Event]
    timers: dict[int, SimTimer]
    finished: bool  # True — к концу прогноза бафы больше никому не нужны

    @property
    def buffs_per_day(self) -> float:
        return self.donors * DAY / self.cooldown if self.cooldown else 0.0

    def mine(self, pid: int) -> SimTimer | None:
        return self.timers.get(pid)


def simulate(
    timers: list[SimTimer],
    donors: list[tuple[int, float]],
    rules: Rules,
    cooldown: float,
    start: int,
    slot_index: int = 0,
    recent: list[int] | None = None,
    kind: str = "",
    horizon: float = 120 * DAY,
) -> Forecast:
    """donors: [(id игрока, когда его баф будет готов в первый раз)]."""
    by_id = {t.pid: t for t in timers}
    for t in timers:
        t.start_remaining = t.remaining
    recent = list(recent or [])
    queue = [(max(ready, start), i, pid) for i, (pid, ready) in enumerate(donors)]
    heapq.heapify(queue)
    events: list[Event] = []
    now = float(start)

    def mark_done(t: SimTimer, at: float) -> None:
        if t.done_at is None and (t.remaining <= 0 or timer_status(t.candidate(rules), rules) != STATUS_NEED):
            t.done_at = at
            t.left_at_done = max(0.0, t.remaining)

    for t in timers:
        mark_done(t, now)

    finished = False
    while queue:
        if all(t.done_at is not None for t in timers):
            finished = True
            break
        at, order, donor = heapq.heappop(queue)
        if at > start + horizon:
            break
        dt = at - now
        now = at
        for t in timers:
            if t.remaining > 0:
                t.remaining -= dt
            mark_done(t, now)

        candidates = [t.candidate(rules) for t in timers if t.pid != donor and t.done_at is None]
        pick, slot = choose_recipient(candidates, rules, slot_index, recent, int(now))
        if pick is None:
            # этот донор сейчас никому не нужен — попробует позже
            heapq.heappush(queue, (at + 2 * HOUR, order, donor))
            continue
        target = by_id[pick.player_id]
        cut = min(pick.reduction, int(target.remaining))
        target.remaining -= cut
        target.received += 1
        target.waiting_since = now
        target.last_got = now
        target.gets.append((now, cut))
        recent.insert(0, target.pid)
        if slot != SLOT_URGENT:
            slot_index += 1
        events.append(Event(now, target.pid, target.nick, cut, slot, donor))
        mark_done(target, now)

        own = by_id.get(donor)
        if own is not None and own.remaining > 0:
            self_cut = min(buff_reduction(own.base, int(own.remaining), rules), int(own.remaining))
            own.remaining -= self_cut
            own.self_cuts.append((now, self_cut))
            mark_done(own, now)
        heapq.heappush(queue, (at + cooldown, order, donor))

    return Forecast(kind, start, len(donors), cooldown, events, by_id, finished)
