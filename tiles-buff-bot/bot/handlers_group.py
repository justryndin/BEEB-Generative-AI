"""Команды в общей группе союза."""

from __future__ import annotations

import time

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message

from .service import Service
from .texts import catalog_text, digest_text

router = Router(name="group")
router.message.filter(F.chat.type.in_({"group", "supergroup"}))


@router.message(Command("queue"))
async def group_queue(message: Message, svc: Service):
    await message.answer(digest_text(svc, int(time.time())))


@router.message(Command("chatid"))
async def group_chat_id(message: Message):
    await message.answer(f"ID этой группы: <code>{message.chat.id}</code>\nВпиши его в ALLIANCE_CHAT_ID в файле .env")


@router.message(Command("catalog"))
async def group_catalog(message: Message, svc: Service):
    await message.answer(catalog_text(svc))
