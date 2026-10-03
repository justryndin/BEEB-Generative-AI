"""Разбор и вывод длительностей: «21д 5ч», «100», «20d 13:45:12»."""

from __future__ import annotations

import re

DAY = 86400
HOUR = 3600
MINUTE = 60
MAX_SECONDS = 400 * DAY

_CLOCK = re.compile(r"(\d{1,3}):(\d{2})(?::(\d{2}))?")
_TOKEN = re.compile(r"(\d+(?:[.,]\d+)?)\s*([a-zа-яё]*)")
_GAP_CHARS = " ,;+и\t"


def _unit(word: str) -> int | None:
    if not word:
        return None
    first = word[0]
    if first in "дd":
        return DAY
    if first in "чh":
        return HOUR
    if first in "мm":
        return MINUTE
    if first in "сs":
        return 1
    return None


def parse_duration(text: str) -> int | None:
    """Возвращает количество секунд или None, если строку не удалось разобрать.

    Число без единицы измерения считается днями, но только если оно одно:
    «100» — это 100 дней, а «21 5» отклоняется как неоднозначное.
    """
    s = text.lower().replace("ё", "е").strip()
    if not s:
        return None
    total = 0.0
    found = False

    clock = _CLOCK.search(s)
    if clock:
        hours, minutes, seconds = int(clock.group(1)), int(clock.group(2)), int(clock.group(3) or 0)
        if minutes >= 60 or seconds >= 60:
            return None
        total += hours * HOUR + minutes * MINUTE + seconds
        s = s[: clock.start()] + " " + s[clock.end():]
        found = True

    tokens = []
    pos = 0
    for m in _TOKEN.finditer(s):
        if s[pos : m.start()].strip(_GAP_CHARS):
            return None
        tokens.append((float(m.group(1).replace(",", ".")), m.group(2)))
        pos = m.end()
    if s[pos:].strip(_GAP_CHARS + "."):
        return None

    for value, word in tokens:
        if word:
            unit = _unit(word)
            if unit is None:
                return None
        elif len(tokens) == 1:
            unit = DAY
        else:
            return None
        total += value * unit
        found = True

    if not found or total <= 0 or total > MAX_SECONDS:
        return None
    return int(round(total))


_UNITS = {"ru": ("д", "ч", "м")}


def format_duration(seconds: float) -> str:
    from .i18n import get_lang  # язык текущего запроса: «2д 5ч» по-русски, «2d 5h» на остальных

    d, h, m = _UNITS.get(get_lang(), ("d", "h", "m"))
    seconds = max(0, int(seconds))
    days, rest = divmod(seconds, DAY)
    hours, rest = divmod(rest, HOUR)
    minutes = rest // MINUTE
    if days:
        return f"{days}{d} {hours}{h}" if hours else f"{days}{d}"
    if hours:
        return f"{hours}{h} {minutes}{m}" if minutes else f"{hours}{h}"
    return f"{minutes}{m}"

