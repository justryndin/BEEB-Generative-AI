"""«Мой путь к Электростанции 30»: что строить дальше, сколько ресурсов и времени.

Данные — таблица Скальда из gamedata (время без бонусов к скорости).
Тексты советов — русские ключи, переводятся в шаблоне через _().
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import gamedata, i18n

RESOURCES = ("Еда", "Древесина", "Металл", "Топливо")
GOAL = 30


def parse_amount(text: str) -> int:
    """«1,12 млн» → 1 120 000, «351 000» → 351 000, «—» → 0."""
    text = text.replace(" ", " ").strip()
    if not text or text == "—":
        return 0
    mult = 1
    if text.endswith("млн"):
        mult, text = 1_000_000, text[:-3]
    return round(float(text.replace(" ", "").replace(",", ".")) * mult)


def fmt_amount(n: int) -> str:
    """Коротко и на языке игрока: «12,3 млн» / «12.3M» / «12,3 M»."""
    lang = i18n.get_lang()
    dec, sep = (".", ",") if lang == "en" else (",", " " if lang == "ru" else ".")
    if n >= 1_000_000_000 and lang in ("ru", "en"):
        value, unit = n / 1_000_000_000, " млрд" if lang == "ru" else "B"
    elif n >= 1_000_000:
        value, unit = n / 1_000_000, {"ru": " млн", "en": "M"}.get(lang, " M")
    elif n >= 10_000:
        value, unit = n / 1000, {"ru": " тыс.", "en": "K"}.get(lang, " mil")
    else:
        return f"{n:,}".replace(",", sep)
    digits = 0 if value >= 100 else (1 if value >= 10 else 2)
    text = f"{value:,.{digits}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text.replace(",", "\0").replace(".", dec).replace("\0", sep) + unit


@dataclass
class Step:
    level: int  # на какой уровень строим
    seconds: int
    cost: tuple[int, int, int, int]
    requires: list[tuple[str, int]]

    @property
    def cost_text(self) -> list[tuple[str, str]]:
        return [(name, fmt_amount(v)) for name, v in zip(RESOURCES, self.cost) if v]

    @property
    def requires_text(self) -> str:
        return ", ".join(f"{i18n.translate(n)} → {lvl}" for n, lvl in self.requires)


@dataclass
class Path:
    current: int
    goal: int
    steps: list[Step]
    upgrade: list[tuple[str, int]] = field(default_factory=list)  # что подтянуть по дороге, максимум уровня

    @property
    def next(self) -> Step | None:
        return self.steps[0] if self.steps else None

    @property
    def seconds(self) -> int:
        return sum(s.seconds for s in self.steps)

    @property
    def cost(self) -> tuple[int, ...]:
        return tuple(sum(s.cost[i] for s in self.steps) for i in range(4))

    @property
    def cost_text(self) -> list[tuple[str, str]]:
        return [(name, fmt_amount(v)) for name, v in zip(RESOURCES, self.cost) if v]

    @property
    def upgrade_text(self) -> str:
        return ", ".join(f"{i18n.translate(n)} → {lvl}" for n, lvl in self.upgrade)


def pp_path(current: int | None, goal: int = GOAL) -> Path | None:
    """Путь от текущего уровня Электростанции до цели; None — уровень неизвестен."""
    if current is None:
        return None
    pp = gamedata.ITEMS["pp"]
    steps = []
    for lvl in range(max(current, 1) + 1, goal + 1):
        cost = pp.costs.get(lvl, ("—",) * 4)
        steps.append(Step(lvl, pp.time_for(lvl) or 0, tuple(parse_amount(c) for c in cost), pp.requires_list(lvl)))
    need: dict[str, int] = {}
    for s in steps[1:]:
        for name, lvl in s.requires:
            need[name] = max(need.get(name, 0), lvl)
    upgrade = sorted(need.items(), key=lambda x: -x[1])
    return Path(current, goal, steps, upgrade)


def advice(current: int | None, pct: float) -> list[tuple[str, dict]]:
    """Советы под текущий уровень: (русский ключ для _(), подстановки)."""
    if current is None:
        return [("Укажи уровень Электростанции — сайт посчитает, что строить дальше и сколько ресурсов нужно до 30.", {})]
    out = []
    if current < 10:
        out.append("До 10 ур. стройки идут минуты и часы — не трать на них ускорения, сразу ставь следующую.")
    if current < 16:
        out.append("Возьми второго строителя, если его ещё нет: две стройки одновременно — самое полезное вложение.")
        out.append("В Лаборатории качай скорость строительства — она окупается на каждом следующем уровне.")
    if 12 <= current < 17:
        out.append("С 16 ур. стройке нужно топливо: заранее подними Угольную шахту и собирай топливо с плиток.")
    out.append("Ресурсные здания ради стройки не качай — с плиток ресурсов намного больше.")
    if current >= 18:
        out.append("Стройки идут днями и неделями: сразу после запуска вставай в очередь на бафы союза — "
                   "каждый баф срезает {pct}% времени стройки.")
    out.append("Ускорения стройки копи на вторник — день стройки в Дуэли союза. Лучшее окно: "
               "03:00–07:00 МСК (00:00–04:00 UTC), когда совпадает с «Игрой по-крупному».")
    if current >= 22:
        out.append("Требования к 26–30 — монументы и Золотая защита: качай их заранее, они тоже долгие.")
    out.append("Подарок за готовую стройку не забирай сразу — оставь до дня стройки в Дуэли.")
    return [(a, {"pct": f"{pct:g}"} if "{pct}" in a else {}) for a in out]
