"""Фоновые задачи раз в минуту: истёкшие брони, напоминания, сводка в группу."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from .config import Config
from .handlers_private import notify
from .service import KIND_ACC, Service
from .texts import BTN_BUFF_BUILD, BTN_BUFF_RESEARCH, digest_text

log = logging.getLogger(__name__)


async def tick(bot: Bot, svc: Service, cfg: Config) -> None:
    now = int(time.time())

    for d in svc.expire_pending(now):
        text = (
            f"⌛ Бронь на баф ({KIND_ACC[d['kind']]}) истекла — баф не записан.\n"
            "Если он всё ещё у тебя, нажми «🎁 Баф…» ещё раз."
        )
        try:
            if d["chat_id"] and d["message_id"]:
                await bot.edit_message_text(text, chat_id=d["chat_id"], message_id=d["message_id"])
            else:
                await notify(bot, d["requested_by"], text)
        except TelegramAPIError:
            await notify(bot, d["requested_by"], text)

    svc.deactivate_finished(now)

    for r in svc.due_reminders(now):
        button = BTN_BUFF_BUILD if r["kind"] == "build" else BTN_BUFF_RESEARCH
        await notify(bot, r["tg_id"], f"🎁 Твой баф на {KIND_ACC[r['kind']]} снова готов! Нажми «{button}», и я скажу, кому его отдать.")
        svc.mark_reminded(r["player_id"], r["kind"])

    hour = int(svc.setting_float("digest_hour"))
    if cfg.alliance_chat_id is not None and hour >= 0:
        local = datetime.now(cfg.tz)
        today = local.date().isoformat()
        if local.hour == hour and svc.setting("last_digest") != today:
            svc.set_setting("last_digest", today)
            try:
                await bot.send_message(cfg.alliance_chat_id, digest_text(svc, now))
            except TelegramAPIError as e:
                log.warning("Не удалось отправить сводку в группу: %s", e)


async def run(bot: Bot, svc: Service, cfg: Config) -> None:
    while True:
        try:
            await tick(bot, svc, cfg)
        except Exception:
            log.exception("Ошибка в фоновой задаче")
        await asyncio.sleep(60)
