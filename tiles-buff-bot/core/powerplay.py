"""«Игра по-крупному» (Power Play): какая тема идёт сейчас и когда следующая.

Календарь переписан из игры (скриншоты 05.10.2026): 6 тем по 4 часа,
окна начинаются в 00, 04, 08, 12, 16, 20 UTC (03, 07, 11, 15, 19, 23 МСК),
неделя начинается в понедельник. Темы стройки в календаре больше нет.
Тексты — русские ключи для _().
"""

from __future__ import annotations

from dataclasses import dataclass

from .timeparse import DAY, HOUR

SLOT_HOURS = 4
MSK_OFFSET = 3


@dataclass(frozen=True)
class Theme:
    code: str
    emoji: str
    name: str
    spend: str  # что тратить в это окно


THEMES = {
    "hero": Theme("hero", "🦸", "Улучшение героя", "карты и жетоны призыва, фрагменты героев, руководства навыков"),
    "troops": Theme("troops", "🪖", "Улучшение войск", "обучение и улучшение войск, ускорения обучения"),
    "tech": Theme("tech", "🔬", "Исследование технологий", "ускорители исследования, завершение исследований"),
    "gear": Theme("gear", "🛡", "Усиление снаряжения", "улучшение и усиление снаряжения героев, молоты и изоленты перековки"),
    "titan": Theme("titan", "🧬", "Развитие титана", "клетки и сыворотки титана, биогенный белок"),
    "chef": Theme("chef", "📐", "Шеф Коллекция", "общие детали и точные чертежи"),
}

# День недели (0 — пн) → темы окон по порядку (00, 04, 08, 12, 16, 20 UTC).
CALENDAR = {
    0: ("hero", "troops", "tech", "gear", "titan", "chef"),
    1: ("troops", "tech", "gear", "titan", "chef", "hero"),
    2: ("tech", "gear", "titan", "chef", "hero", "troops"),
    3: ("gear", "titan", "chef", "hero", "troops", "tech"),
    4: ("titan", "chef", "hero", "troops", "tech", "gear"),
    5: ("chef", "hero", "troops", "tech", "gear", "titan"),
    6: ("hero", "troops", "tech", "gear", "titan", "chef"),
}

# Темы «Игры по-крупному», которые совпадают с днём Дуэли (день недели → коды тем).
DUEL_MATCH = {0: ("gear",), 2: ("tech", "titan"), 3: ("hero",), 4: ("troops",)}


@dataclass(frozen=True)
class Slot:
    theme: Theme
    start: int  # UTC timestamp
    end: int

    @property
    def msk(self) -> str:
        return _hours(self.start, MSK_OFFSET)

    @property
    def utc(self) -> str:
        return _hours(self.start, 0)


def _hours(start: int, offset: int) -> str:
    a = (start // HOUR + offset) % 24
    return f"{a:02d}:00–{(a + SLOT_HOURS) % 24:02d}:00"


def weekday(ts: int) -> int:
    return (ts // DAY + 3) % 7  # 01.01.1970 — четверг


def slot_at(ts: int) -> Slot:
    day_start = ts // DAY * DAY
    i = (ts - day_start) // (SLOT_HOURS * HOUR)
    start = day_start + i * SLOT_HOURS * HOUR
    return Slot(THEMES[CALENDAR[weekday(ts)][i]], start, start + SLOT_HOURS * HOUR)


def now_and_next(ts: int) -> tuple[Slot, Slot]:
    cur = slot_at(ts)
    return cur, slot_at(cur.end)


def day_slots(ts: int) -> list[Slot]:
    day_start = ts // DAY * DAY
    return [slot_at(day_start + i * SLOT_HOURS * HOUR) for i in range(24 // SLOT_HOURS)]


def duel_windows(ts: int) -> list[Slot]:
    """Окна этого игрового дня, где тема «Игры по-крупному» совпадает с Дуэлью."""
    codes = DUEL_MATCH.get(weekday(ts), ())
    return [s for s in day_slots(ts) if s.theme.code in codes]


def week_table() -> list[tuple[str, list[Theme]]]:
    """Для гайда: [(«03:00» МСК, [тема пн … вс])] по строкам-окнам."""
    rows = []
    for i in range(24 // SLOT_HOURS):
        msk = f"{(i * SLOT_HOURS + MSK_OFFSET) % 24:02d}:00"
        rows.append((msk, [THEMES[CALENDAR[d][i]] for d in range(7)]))
    return rows
