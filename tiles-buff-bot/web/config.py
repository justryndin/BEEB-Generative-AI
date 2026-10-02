"""Настройки сайта из переменных окружения (.env)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class Config:
    db_path: str
    tz: ZoneInfo
    site_name: str
    secure_cookies: bool
    session_days: int = 60


def load_config() -> Config:
    return Config(
        db_path=os.environ.get("DB_PATH", "data/bot.db"),
        tz=ZoneInfo(os.environ.get("TZ", "Europe/Moscow") or "Europe/Moscow"),
        site_name=os.environ.get("SITE_NAME", "Бафы союза") or "Бафы союза",
        secure_cookies=os.environ.get("SECURE_COOKIES", "1") != "0",
    )
