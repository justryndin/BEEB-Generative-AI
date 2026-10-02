"""Чистая логика распределения бафов, без Telegram и базы данных.

Правила:
* Один баф срезает `pct`% от заявленного времени (mode="declared")
  или от текущего остатка (mode="remaining").
* Баф назначается, только пока остаток больше `max_left` и после бафа
  не опустится ниже `min_left`. Так лишних бафов не бывает.
* У срочных (urgent) таймеров пороги не действуют: бафаем, пока не закроется.
* Ротация идёт по циклу `pattern`: B — самый большой остаток,
  W — тот, кто дольше всех ждёт помощи. Цикл BBW означает «2 большим, 1 ждущему».
* Один игрок не получает больше `max_streak` бафов подряд, если есть кому ещё отдать.
"""

from __future__ import annotations

from dataclasses import dataclass

SLOT_BIG = "B"
SLOT_WAIT = "W"
SLOT_URGENT = "U"

STATUS_NEED = "need"
STATUS_TARGET = "target"
STATUS_DONE = "done"


@dataclass(frozen=True)
class Rules:
    min_left: int
    max_left: int
    pct: float = 15.0
    mode: str = "declared"
    pattern: str = "BBW"
    max_streak: int = 2


@dataclass(frozen=True)
class Candidate:
    player_id: int
    nick: str
    remaining: int
    base: int
    reduction: int
    urgent: bool
    waiting_since: int
    received: int = 0
    has_tg: bool = True


def buff_reduction(base: int, remaining: int, rules: Rules) -> int:
    source = remaining if rules.mode == "remaining" else base
    return max(0, int(source * rules.pct / 100))


def is_eligible(remaining: int, reduction: int, urgent: bool, rules: Rules) -> bool:
    if remaining <= 0 or reduction <= 0:
        return False
    if urgent:
        return True
    return remaining > rules.max_left and remaining - reduction >= rules.min_left


def timer_status(c: Candidate, rules: Rules) -> str:
    if c.remaining <= 0:
        return STATUS_DONE
    if is_eligible(c.remaining, c.reduction, c.urgent, rules):
        return STATUS_NEED
    return STATUS_TARGET


def buffs_needed(remaining: int, base: int, rules: Rules) -> int:
    """Сколько бафов ещё можно дать, пока таймер не упрётся в порог."""
    count = 0
    while count < 200:
        reduction = buff_reduction(base, remaining, rules)
        if not is_eligible(remaining, reduction, False, rules):
            break
        remaining -= reduction
        count += 1
    return count


def slot_for(index: int, pattern: str) -> str:
    pattern = pattern or SLOT_BIG
    return pattern[index % len(pattern)]


def _blocked_by_streak(recent: list[int], max_streak: int) -> int | None:
    """recent — получатели последних бафов, от новых к старым."""
    if max_streak <= 0 or len(recent) < max_streak:
        return None
    head = recent[:max_streak]
    return head[0] if all(pid == head[0] for pid in head) else None


def _pick(pool: list[Candidate], slot: str) -> Candidate:
    if slot == SLOT_WAIT:
        return min(pool, key=lambda c: (c.waiting_since, -c.remaining, c.player_id))
    return max(pool, key=lambda c: (c.remaining, -c.waiting_since, -c.player_id))


def choose_recipient(
    candidates: list[Candidate],
    rules: Rules,
    slot_index: int,
    recent_recipients: list[int],
) -> tuple[Candidate | None, str]:
    eligible = [c for c in candidates if is_eligible(c.remaining, c.reduction, c.urgent, rules)]
    if not eligible:
        return None, ""

    urgent = [c for c in eligible if c.urgent]
    if urgent:
        return _pick(urgent, SLOT_WAIT), SLOT_URGENT

    blocked = _blocked_by_streak(recent_recipients, rules.max_streak)
    pool = [c for c in eligible if c.player_id != blocked] or eligible
    slot = slot_for(slot_index, rules.pattern)
    return _pick(pool, slot), slot
