"""Справочник построек и веток исследований Tiles Survive.

Время улучшения — базовое, без бонусов к скорости, по данным tilessurvive.net
(октябрь 2026). Для остальных зданий и для исследований публичных данных нет:
бот собирает реальные заявленные времена от игроков союза (см. Service.observed_times).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import i18n
from .timeparse import parse_duration

SOURCE = "tilessurvive.net"


@dataclass(frozen=True)
class Item:
    code: str
    kind: str  # build | research
    ru: str
    en: str
    max_level: int = 0
    times: dict[int, str] = field(default_factory=dict)  # уровень → время «на этот уровень»
    requires: dict[int, str] = field(default_factory=dict)
    approximate: bool = False
    costs: dict[int, tuple[str, str, str, str]] = field(default_factory=dict)  # еда, дерево, металл, топливо
    source: str = SOURCE

    @property
    def title(self) -> str:
        return f"{self.ru} ({self.en})" if self.en and self.en != self.ru else self.ru

    @property
    def name(self) -> str:
        """Название на языке игрока: русское, иначе из каталога, иначе английское из игры."""
        lang = i18n.get_lang()
        if lang == "ru":
            return self.ru
        return i18n.catalog(lang).get(self.ru) or self.en or i18n.translate(self.ru)

    def requires_list(self, level: int) -> list[tuple[str, int]]:
        """«Лаборатория 1 — 29, Казарма 1 — 29» → [("Лаборатория 1", 29), ("Казарма 1", 29)]."""
        out = []
        for part in self.requires.get(level, "").split(","):
            name, _, lvl = part.rpartition("—")
            if name.strip() and lvl.strip().isdigit():
                out.append((name.strip(), int(lvl)))
        return out

    def requires_text(self, level: int) -> str:
        return ", ".join(f"{i18n.translate(n)} — {lvl}" for n, lvl in self.requires_list(level)) or "—"

    def time_for(self, level: int | None) -> int | None:
        if level is None or level not in self.times:
            return None
        return parse_duration(self.times[level])


# Электростанция: время, ресурсы и требования — по таблице Скальда (русское сообщество Tiles Survive).
# Время — как в игре без бонусов к скорости; уровни 2–5 в таблице указаны в минутах.
_PP_SOURCE = "таблицы Скальда (русское сообщество Tiles Survive)"
_PP_TIMES = {
    2: "3m", 3: "4m", 4: "5m", 5: "3m", 6: "20m", 7: "30m", 8: "2:15:00", 9: "4:20:00", 10: "6:00:00",
    11: "6:36:50", 12: "8:37:48", 13: "10:34:37", 14: "13:05:03", 15: "17:39:04",
    16: "1d 3:48:38", 17: "1d 11:12:57", 18: "2d 4:40:56", 19: "4d 6:02:27", 20: "6d 14:25:43",
    21: "8d 21:38:11", 22: "12d 9:54:01", 23: "17d 9:03:38", 24: "18d 7:48:41", 25: "20d 18:36:40",
    26: "23d 4:30:00", 27: "32d 17:24:02", 28: "35d 10:00:00", 29: "38d 18:16:00", 30: "42d 0:47:23",
}
# уровень → (еда, древесина, металл, топливо)
_PP_COST = {
    2: ("150", "150", "—", "—"), 3: ("400", "300", "200", "—"), 4: ("2 400", "2 000", "500", "—"),
    5: ("6 400", "2 500", "2 000", "—"), 6: ("4 000", "6 000", "10 000", "—"), 7: ("3 600", "12 000", "4 800", "—"),
    8: ("—", "48 000", "12 000", "—"), 9: ("7 224", "241 224", "46 802", "—"), 10: ("351 000", "673 000", "64 405", "—"),
    11: ("787 500", "1,12 млн", "91 000", "—"), 12: ("1,80 млн", "1,98 млн", "114 400", "—"),
    13: ("3,15 млн", "3,15 млн", "300 000", "—"), 14: ("3,72 млн", "3,72 млн", "600 000", "—"),
    15: ("4,57 млн", "4,57 млн", "913 500", "—"), 16: ("6,73 млн", "6,73 млн", "1,35 млн", "336 400"),
    17: ("8,49 млн", "8,49 млн", "1,70 млн", "424 100"), 18: ("11,73 млн", "11,73 млн", "2,35 млн", "586 100"),
    19: ("16,32 млн", "16,32 млн", "3,27 млн", "815 600"), 20: ("22,66 млн", "22,66 млн", "4,54 млн", "1,14 млн"),
    21: ("31,57 млн", "31,57 млн", "6,32 млн", "1,58 млн"), 22: ("43,72 млн", "43,72 млн", "8,75 млн", "2,19 млн"),
    23: ("61,00 млн", "61,00 млн", "12,20 млн", "3,05 млн"), 24: ("87,46 млн", "87,46 млн", "17,50 млн", "4,38 млн"),
    25: ("119,86 млн", "119,86 млн", "23,98 млн", "6,0 млн"), 26: ("136,52 млн", "136,52 млн", "27,31 млн", "6,38 млн"),
    27: ("161,98 млн", "161,98 млн", "32,40 млн", "8,10 млн"), 28: ("184,14 млн", "184,14 млн", "36,83 млн", "9,21 млн"),
    29: ("220,30 млн", "220,30 млн", "44,06 млн", "11,02 млн"), 30: ("255,94 млн", "255,94 млн", "51,19 млн", "12,80 млн"),
}
_PP_REQUIRES = {
    2: "Дом 1 — 1", 3: "Лесопилка — 2", 4: "Теплица — 3, База героев — 1",
    5: "Плавильня — 3, Мастерская артефактов — 2", 6: "Лесопилка — 5, Мастерская артефактов — 3",
    7: "Теплица — 5, Пост разведки — 1", 8: "Казарма 1 — 1, Мастерская артефактов — 5", 9: "Угольная шахта — 1",
    10: "Госпиталь — 1", 11: "Лаборатория 1 — 5, Казарма 1 — 3", 12: "Станция связи — 5, Казарма 1 — 5",
    13: "Станция связи — 8, Казарма 1 — 6", 14: "Лаборатория — 8, Казарма 1 — 10",
    15: "Станция связи — 13, Казарма 1 — 14", 16: "Лаборатория 1 — 13, Казарма 1 — 15",
    17: "Станция связи — 16, Казарма 1 — 16", 18: "Лаборатория 1 — 17, Гарнизон — 13",
    19: "Станция связи — 18, Казарма 1 — 18", 20: "Лаборатория 1 — 19, Гарнизон — 16",
    21: "Станция связи — 20, Казарма 1 — 20", 22: "Лаборатория 1 — 21, Вестник войны — 14",
    23: "Станция связи — 22, Казарма 1 — 22", 24: "Лаборатория 1 — 23, Академия героев — 18",
    25: "Станция связи — 24, Казарма 1 — 24", 26: "Лаборатория 1 — 25, Золотая защита — 20",
    27: "Станция связи — 26, Казарма 1 — 26", 28: "Лаборатория 1 — 27, Хранитель жизни — 23",
    29: "Станция связи — 28, Казарма 1 — 28, Золотая защита — 24",
    30: "Лаборатория 1 — 29, Казарма 1 — 29, Вестник войны — 26",
}
_BARRACKS1_TIMES = {
    4: "2m", 5: "4m", 6: "8m", 7: "25m", 8: "51m", 9: "1h42m", 10: "1h47m", 11: "2h4m",
    12: "2h20m", 13: "2h21m", 14: "4h37m", 15: "7h57m", 16: "13h54m", 17: "16h27m",
    18: "16h46m", 19: "19h0m", 20: "20h55m", 21: "22h17m", 22: "1d0h30m", 23: "1d1h16m",
    24: "1d16h8m", 25: "1d13h33m", 26: "2d6h34m", 27: "2d5h41m", 28: "2d9h18m",
    29: "2d14h21m", 30: "3d2h33m",
}
_BARRACKS23_TIMES = {
    5: "4m", 6: "7m", 7: "15m", 8: "24m", 9: "36m", 10: "48m", 11: "1h11m", 12: "2h13m",
    13: "3h34m", 14: "4h10m", 15: "4h37m", 16: "5h59m", 17: "7h20m", 18: "5h26m",
    19: "5h37m", 20: "8h58m", 21: "9h52m", 22: "13h59m", 23: "1d3h10m", 24: "1d15h58m",
    25: "1d16h24m", 26: "2d6h34m", 27: "2d1h58m", 28: "2d6h36m", 29: "2d21h18m",
    30: "3d3h56m",
}

BUILDINGS: list[Item] = [
    Item("pp", "build", "Электростанция", "Power Plant", 30, _PP_TIMES, _PP_REQUIRES, costs=_PP_COST, source=_PP_SOURCE),
    Item("lab", "build", "Лаборатория", "Lab", 30),
    Item("bar1", "build", "Казарма 1", "Barracks 1", 30, _BARRACKS1_TIMES, approximate=True),
    Item("bar23", "build", "Казарма 2/3", "Barracks 2·3", 30, _BARRACKS23_TIMES, approximate=True),
    Item("comms", "build", "Пост связи", "Comms Outpost", 30),
    Item("hosp", "build", "Госпиталь", "Hospital", 30),
    Item("enlist", "build", "Призывной пункт", "Enlistment Office", 30),
    Item("garr", "build", "Гарнизон", "Garrison Station", 30),
    Item("academy", "build", "Академия героев", "Hero Academy", 30),
    Item("warb", "build", "Монумент «Вестник войны»", "Warbringer Monument", 30),
    Item("guard", "build", "Монумент «Хранитель жизни»", "Guardian of Life", 30),
    Item("aegis", "build", "Монумент «Золотая эгида»", "Golden Aegis", 30),
    Item("bless", "build", "Стела исцеления", "Blessed Healing Stele", 60),
    Item("curse", "build", "Стела проклятых ран", "Cursed Wound Stele", 60),
    Item("bother", "build", "Другое здание", ""),
]

RESEARCH: list[Item] = [
    Item("dev", "research", "Развитие", "Development"),
    Item("eco", "research", "Экономика", "Economy"),
    Item("hero", "research", "Герои", "Hero"),
    Item("troop", "research", "Отряды", "Troop"),
    Item("intel", "research", "Пост разведки", "Intel Outpost"),
    Item("duel", "research", "Дуэль альянсов", "Alliance Duel"),
    Item("sf", "research", "Спецназ", "Special Forces"),
    Item("hunt", "research", "Охота на заражённых", "Infected Hunt"),
    Item("doom", "research", "Экспресс Судного дня", "Doomsday Express"),
    Item("ocean", "research", "Мощь океана", "Might of the Ocean"),
    Item("warfare", "research", "Военное дело", "Warfare"),
    Item("rother", "research", "Другое исследование", ""),
]

ITEMS: dict[str, Item] = {i.code: i for i in BUILDINGS + RESEARCH}


def items_for(kind: str) -> list[Item]:
    return BUILDINGS if kind == "build" else RESEARCH


def item(code: str | None) -> Item | None:
    return ITEMS.get(code or "")


def label(code: str | None, level: int | None, note: str | None = None) -> str:
    """«Электростанция → 24», «Экономика: Скорость строительства 7» или пусто."""
    it = item(code)
    if it is None:
        return note or ""
    text = it.name
    if level:
        text += f" → {level}"
    if note:
        text += f": {note}"
    return text
