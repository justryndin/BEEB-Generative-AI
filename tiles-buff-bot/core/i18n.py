"""Языки сайта: русский (исходный), английский, испанский, португальский.

Тексты пишутся по-русски и служат ключами. Перевод — в core/locales/<язык>.json
({"русский текст": "перевод"}). Нет перевода на испанский или португальский —
берём английский, нет и его — показываем русский.

Текущий язык хранится в contextvar: его ставит middleware для каждого запроса
(и фоновые задачи — для каждого игрока), поэтому t() и _() в шаблонах
не требуют передавать язык руками.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache
from pathlib import Path

LANGS = {"ru": "Русский", "en": "English", "es": "Español", "pt": "Português"}
SHORT = {"ru": "RU", "en": "EN", "es": "ES", "pt": "PT"}
DEFAULT = "ru"
# Какие языки включены. Сейчас сайт ведём только на русском (переводы лежат в locales/,
# но не обновляются). Вернуть языки: ENABLED = tuple(LANGS).
ENABLED: tuple[str, ...] = ("ru",)
_LOCALES = Path(__file__).parent / "locales"
_current: ContextVar[str] = ContextVar("lang", default=DEFAULT)


def get_lang() -> str:
    return _current.get()


def set_lang(code: str | None):
    return _current.set(code if code in ENABLED else DEFAULT)


@contextmanager
def using(code: str | None):
    token = set_lang(code)
    try:
        yield
    finally:
        _current.reset(token)


@lru_cache(maxsize=None)
def catalog(code: str) -> dict[str, str]:
    path = _LOCALES / f"{code}.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def translate(text: str, lang: str | None = None) -> str:
    lang = lang or get_lang()
    if lang == "ru" or not text:
        return text
    found = catalog(lang).get(text)
    if found is None and lang != "en":
        found = catalog("en").get(text)
    return found if found is not None else text


def t(text: str, **kw) -> str:
    """Перевод строки на текущий язык; {имя} в тексте заменяется значениями kw."""
    out = translate(text)
    return out.format(**kw) if kw else out


def pick_lang(cookie: str | None, accept: str | None) -> str:
    """Язык из cookie, иначе из настроек браузера, иначе русский."""
    if cookie in ENABLED:
        return cookie
    for part in (accept or "").split(","):
        code = part.split(";")[0].strip().lower()[:2]
        if code in ENABLED:
            return code
    return DEFAULT


def reload() -> None:
    catalog.cache_clear()
