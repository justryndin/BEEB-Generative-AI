"""Точка входа: python -m bot.main"""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, BotCommandScopeAllGroupChats, BotCommandScopeAllPrivateChats

from . import handlers_admin, handlers_group, handlers_panel, handlers_private, scheduler
from .config import load_config
from .db import Database
from .service import Service


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = load_config()
    svc = Service(Database(cfg.db_path))

    bot = Bot(cfg.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    dp["svc"] = svc
    dp["cfg"] = cfg
    dp.include_routers(handlers_admin.router, handlers_panel.router, handlers_group.router, handlers_private.router)

    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Начать / главное меню"),
            BotCommand(command="queue", description="Очередь на бафы"),
            BotCommand(command="me", description="Мой профиль"),
            BotCommand(command="help", description="Как это работает"),
            BotCommand(command="catalog", description="Справочник построек и исследований"),
            BotCommand(command="admin", description="Управление (для админов)"),
        ],
        scope=BotCommandScopeAllPrivateChats(),
    )
    await bot.set_my_commands(
        [
            BotCommand(command="queue", description="Сводка по бафам"),
            BotCommand(command="catalog", description="Справочник построек"),
        ],
        scope=BotCommandScopeAllGroupChats(),
    )

    task = asyncio.create_task(scheduler.run(bot, svc, cfg))
    try:
        await dp.start_polling(bot)
    finally:
        task.cancel()


if __name__ == "__main__":
    asyncio.run(main())
