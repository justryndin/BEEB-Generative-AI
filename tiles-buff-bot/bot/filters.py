"""Общие фильтры."""

from __future__ import annotations

from aiogram.filters import BaseFilter
from aiogram.types import CallbackQuery, Message

from .config import Config
from .service import Service


class IsAdmin(BaseFilter):
    async def __call__(self, event: Message | CallbackQuery, svc: Service, cfg: Config) -> bool:
        return svc.is_admin(event.from_user.id, cfg.owner_ids)
