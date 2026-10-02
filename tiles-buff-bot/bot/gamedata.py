"""Справочник построек и веток исследований Tiles Survive.

Время улучшения — базовое, без бонусов к скорости, по данным tilessurvive.net
(октябрь 2026). Для остальных зданий и для исследований публичных данных нет:
бот собирает реальные заявленные времена от игроков союза (см. Service.observed_times).
"""

from __future__ import annotations

from dataclasses import dataclass, field

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

    @property
    def title(self) -> str:
        return f"{self.ru} ({self.en})" if self.en and self.en != self.ru else self.ru

    def time_for(self, level: int | None) -> int | None:
        if level is None or level not in self.times:
            return None
        return parse_duration(self.times[level])


_PP_TIMES = {
    2: "1m", 3: "1m", 4: "3m", 5: "10m", 6: "30m", 7: "1h", 8: "2h30m", 9: "4h30m", 10: "6h",
    11: "7h30m", 12: "9h", 13: "11h", 14: "14h", 15: "18h", 16: "1d6h28m", 17: "1d12h34m",
    18: "1d19h53m", 19: "2d17h50m", 20: "3d10h18m", 21: "4d11h59m", 22: "6d16h29m",
    23: "9d8h40m", 24: "13d2h33m", 25: "18d8h22m", 26: "21d2h26m", 27: "25d7h43m",
    28: "29d2h52m", 29: "33d11h2m", 30: "40d4h27m",
}
_PP_REQUIRES = {
    15: "Пост связи 13, Казарма 1 — 14",
    16: "Казарма 1 — 15, Лаборатория 1 — 13",
    17: "Пост связи 16, Казарма 1 — 16",
    18: "Лаборатория 1 — 17, Гарнизон 13",
    19: "Пост связи 18, Казарма 1 — 18",
    20: "Лаборатория 1 — 19, Гарнизон 16",
    21: "Пост связи 20, Казарма 1 — 20",
    22: "Лаборатория 1 — 21, Вестник войны 14",
    23: "Пост связи 22, Казарма 1 — 22",
    24: "Лаборатория 1 — 23, Академия героев 18",
    25: "Пост связи 24, Казарма 1 — 24",
    26: "Лаборатория 1 — 25, Эгида 20",
    27: "Пост связи 26, Казарма 1 — 26",
    28: "Лаборатория 1 — 27, Хранитель жизни 23",
    29: "Пост связи 28, Казарма 1 — 28, Эгида 24",
    30: "Лаборатория 1 — 29, Казарма 1 — 29, Вестник войны 26",
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
    Item("pp", "build", "Электростанция", "Power Plant", 30, _PP_TIMES, _PP_REQUIRES),
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
    Item("troop", "research", "Войска", "Troop"),
    Item("intel", "research", "Пост разведки", "Intel Outpost"),
    Item("duel", "research", "Дуэль альянсов", "Alliance Duel"),
    Item("sf", "research", "Спецназ", "Special Forces"),
    Item("hunt", "research", "Охота на заражённых", "Infected Hunt"),
    Item("doom", "research", "Экспресс Судного дня", "Doomsday Express"),
    Item("ocean", "research", "Мощь океана", "Might of the Ocean"),
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
    text = it.ru
    if level:
        text += f" → {level}"
    if note:
        text += f": {note}"
    return text
