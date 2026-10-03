"""Чистая логика распределения бафов, без базы данных.

Правила:
* Один баф срезает `pct`% от заявленного времени (mode="declared")
  или от текущего остатка (mode="remaining").
* Баф назначается, только пока остаток больше `max_left` и после бафа
  не опустится ниже `min_left`. Так лишних бафов не бывает.
* У срочных (urgent) таймеров пороги не действуют: бафаем, пока не закроется.
* После полученного бафа у игрока пауза `min_gap` секунд: пока она идёт, бафы получают
  другие (в том числе и «срочные» ждут паузу). Если пауза у всех, кому нужен баф,
  баф получает тот, у кого пауза началась раньше всех, — чтобы баф не пропал.
* Один игрок не получает больше `max_streak` бафов подряд, если есть кому ещё отдать.

Порядок «по доле» (order="share", основной):
* У каждого есть доля: сколько он получил из положенного (получил / (получил + ещё положено)).
  Следующий — у кого доля меньше. Так все доходят до цели примерно одновременно:
  каждому помогаем в равной степени, а не «поровну штук».
* Приоритетные стройки (например, Электростанция до 30 ур.) считаются с весом
  `priority_weight`: их доля растёт медленнее, и они получают бафы чаще.
* «Горит»: если остаток сам опустится до цели раньше чем через `fire_window`,
  игрок идёт первым — иначе положенный баф пропадёт.
* Взаимность: кто держит готовый баф дольше нормы (holding > 0), пропускает ход —
  бафы получают остальные, пока он не отдаст свой.

Порядок «циклом» (order="cycle", запасной): справедливый круг `max_ahead` и цикл `pattern`:
B — самый большой остаток, W — кто получил меньше всех (при равенстве — кто дольше ждёт).
"""

from __future__ import annotations

from dataclasses import dataclass

SLOT_BIG = "B"
SLOT_WAIT = "W"
SLOT_URGENT = "U"
SLOT_FIRE = "F"
SLOT_SHARE = "S"

ORDER_SHARE = "share"
ORDER_CYCLE = "cycle"

STATUS_NEED = "need"
STATUS_TARGET = "target"
STATUS_DONE = "done"


@dataclass(frozen=True)
class Rules:
    min_left: int
    max_left: int
    pct: float = 15.0
    mode: str = "declared"
    pattern: str = "BBBWWW"
    max_streak: int = 2
    min_gap: int = 0
    max_ahead: int = 0  # 0 — без ограничения
    order: str = ORDER_CYCLE
    fire_window: int = 0  # «горит», если до цели сама дойдёт быстрее, сек. 0 — выключено
    priority_weight: float = 1.0


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
    last_got: int | None = None  # когда получил последний баф
    priority: bool = False  # приоритетная стройка (например, Электростанция до 30)
    holding: int = 0  # сколько секунд сверх нормы держит готовый баф (0 — не держит)

    def paused_until(self, rules: "Rules") -> int:
        if not self.last_got or rules.min_gap <= 0:
            return 0
        return self.last_got + rules.min_gap


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


CYCLE_MAX = 6  # сколько бафов подряд одной группе можно задать в цикле


def make_pattern(big: int, wait: int, wait_first: bool = False) -> str:
    """Цикл «big большим : wait меньше получившим». wait_first — начинать с меньше получивших."""
    big = max(0, min(CYCLE_MAX, big))
    wait = max(0, min(CYCLE_MAX, wait))
    if big + wait == 0:
        big = 1
    return SLOT_WAIT * wait + SLOT_BIG * big if wait_first else SLOT_BIG * big + SLOT_WAIT * wait


def parse_pattern(pattern: str) -> tuple[int, int, bool]:
    """Обратно к (большим, меньше получившим, начинать с меньше получивших)."""
    pattern = pattern or SLOT_BIG
    return pattern.count(SLOT_BIG), pattern.count(SLOT_WAIT), pattern[0] == SLOT_WAIT


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
        return min(pool, key=lambda c: (c.received, c.waiting_since, -c.remaining, c.player_id))
    return max(pool, key=lambda c: (c.remaining, -c.waiting_since, -c.player_id))


def owed(c: Candidate, rules: Rules) -> int:
    """Сколько бафов ещё положено до цели."""
    return buffs_needed(c.remaining, c.base, rules)


def share(c: Candidate, rules: Rules) -> float:
    """Доля полученного от положенного: 0 — ещё ничего, 1 — всё получил."""
    total = c.received + owed(c, rules)
    return c.received / total if total else 1.0


def share_key(c: Candidate, rules: Rules) -> tuple:
    weight = rules.priority_weight if c.priority and rules.priority_weight > 0 else 1.0
    return (round(share(c, rules) / weight, 6), c.waiting_since, -c.remaining, c.player_id)


def fire_left(c: Candidate, rules: Rules) -> int | None:
    """Через сколько секунд остаток сам дойдёт до цели, если это скоро («горит»), иначе None."""
    if rules.fire_window <= 0 or c.urgent:
        return None
    slack = c.remaining - rules.max_left
    return slack if 0 < slack <= rules.fire_window else None


def _choose_share(eligible: list[Candidate], rules: Rules, recent: list[int]) -> tuple[Candidate, str]:
    pool = [c for c in eligible if not c.holding] or eligible  # держит баф — пропускает ход
    urgent = [c for c in pool if c.urgent]
    if urgent:
        return min(urgent, key=lambda c: share_key(c, rules)), SLOT_URGENT
    blocked = _blocked_by_streak(recent, rules.max_streak)
    pool = [c for c in pool if c.player_id != blocked] or pool
    fire = [c for c in pool if fire_left(c, rules) is not None]
    if fire:
        return min(fire, key=lambda c: (fire_left(c, rules), share_key(c, rules))), SLOT_FIRE
    return min(pool, key=lambda c: share_key(c, rules)), SLOT_SHARE


def choose_recipient(
    candidates: list[Candidate],
    rules: Rules,
    slot_index: int,
    recent_recipients: list[int],
    now: int | None = None,
) -> tuple[Candidate | None, str]:
    eligible = [c for c in candidates if is_eligible(c.remaining, c.reduction, c.urgent, rules)]
    if not eligible:
        return None, ""

    if now is not None and rules.min_gap > 0:
        ready = [c for c in eligible if c.paused_until(rules) <= now]
        if not ready:
            # Пауза у всех — отдаём тому, кто получил баф раньше всех, чтобы баф не пропал.
            return min(eligible, key=lambda c: (c.last_got or 0, c.player_id)), SLOT_WAIT
        eligible = ready

    if rules.order == ORDER_SHARE:
        return _choose_share(eligible, rules, recent_recipients)

    urgent = [c for c in eligible if c.urgent]
    if urgent:
        return _pick(urgent, SLOT_WAIT), SLOT_URGENT

    if rules.max_ahead > 0:
        least = min(c.received for c in eligible)
        eligible = [c for c in eligible if c.received <= least + rules.max_ahead - 1] or eligible

    blocked = _blocked_by_streak(recent_recipients, rules.max_streak)
    pool = [c for c in eligible if c.player_id != blocked] or eligible
    slot = slot_for(slot_index, rules.pattern)
    return _pick(pool, slot), slot
