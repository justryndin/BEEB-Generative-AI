"""Настройки запуска из переменных окружения (.env)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class Config:
    bot_token: str
    owner_ids: frozenset[int]
    alliance_chat_id: int | None
    db_path: str
    tz: ZoneInfo


def _ids(raw: str) -> frozenset[int]:
    return frozenset(int(x) for x in raw.replace(";", ",").replace(" ", ",").split(",") if x.strip())


def load_config() -> Config:
    token = os.environ.get("BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit("BOT_TOKEN не задан. Впиши токен от @BotFather в файл .env")
    chat = os.environ.get("ALLIANCE_CHAT_ID", "").strip()
    return Config(
        bot_token=token,
        owner_ids=_ids(os.environ.get("OWNER_IDS", "")),
        alliance_chat_id=int(chat) if chat else None,
        db_path=os.environ.get("DB_PATH", "data/bot.db"),
        tz=ZoneInfo(os.environ.get("TZ", "Europe/Moscow") or "Europe/Moscow"),
    )
