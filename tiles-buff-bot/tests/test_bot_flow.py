"""Сквозной сценарий через aiogram с поддельным Telegram: все действия — кнопками."""

import asyncio
import datetime
import itertools
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import AnswerCallbackQuery, EditMessageText, GetChatMember, SendMessage
from aiogram.types import (
    CallbackQuery,
    Chat,
    ChatMemberLeft,
    ChatMemberMember,
    ChatMemberUpdated,
    Message,
    Update,
    User,
)

from bot import handlers_admin, handlers_group, handlers_panel, handlers_private
from bot.config import Config
from bot.db import Database
from bot.service import Service

OWNER, PLAYER = 1, 2
GROUP = -100


class FakeSession(BaseSession):
    def __init__(self, out):
        super().__init__()
        self.out = out
        self.ids = itertools.count(1000)

    async def make_request(self, bot, method, timeout=None):
        if isinstance(method, SendMessage):
            self.out.append((method.chat_id, method.text, method.reply_markup))
            return Message(message_id=next(self.ids), date=datetime.datetime.now(),
                           chat=Chat(id=method.chat_id, type="private"), text=method.text)
        if isinstance(method, EditMessageText):
            self.out.append((method.chat_id, method.text, method.reply_markup))
            return True
        if isinstance(method, GetChatMember):
            return ChatMemberMember(user=User(id=method.user_id, is_bot=False, first_name="x"))
        if isinstance(method, AnswerCallbackQuery):
            return True
        return True

    async def stream_content(self, *args, **kwargs):
        yield b""

    async def close(self):
        pass


class Harness:
    def __init__(self):
        self.out = []
        self.svc = Service(Database(":memory:"))
        self.cfg = Config("1:x", frozenset({OWNER}), GROUP, ":memory:", ZoneInfo("Europe/Moscow"))
        self.bot = Bot("123:abc", session=FakeSession(self.out), default=DefaultBotProperties(parse_mode="HTML"))
        self.dp = Dispatcher(storage=MemoryStorage())
        self.dp["svc"], self.dp["cfg"] = self.svc, self.cfg
        self.dp.include_routers(handlers_admin.router, handlers_panel.router, handlers_group.router, handlers_private.router)
        self.updates = itertools.count(1)
        self.last = []

    @staticmethod
    def user(tg):
        return User(id=tg, is_bot=False, first_name=f"u{tg}", username=f"user{tg}")

    async def say(self, tg, text):
        self.out.clear()
        msg = Message(message_id=1, date=datetime.datetime.now(), chat=Chat(id=tg, type="private"),
                      from_user=self.user(tg), text=text)
        await self.dp.feed_update(self.bot, Update(update_id=next(self.updates), message=msg))
        self.last = list(self.out)
        return "\n".join(t for _, t, _ in self.out)

    async def click(self, tg, label):
        data = next(
            b.callback_data
            for _, _, kb in reversed(self.last) if kb is not None and hasattr(kb, "inline_keyboard")
            for row in kb.inline_keyboard for b in row if b.text == label
        )
        self.out.clear()
        msg = Message(message_id=5, date=datetime.datetime.now(), chat=Chat(id=tg, type="private"),
                      from_user=self.user(tg), text="x")
        cb = CallbackQuery(id="c", from_user=self.user(tg), chat_instance="c", message=msg, data=data)
        await self.dp.feed_update(self.bot, Update(update_id=next(self.updates), callback_query=cb))
        self.last = [x for x in self.out if x[2] is not None] or self.last
        return "\n".join(t for _, t, _ in self.out)


def test_full_button_flow():
    async def scenario():
        hs = Harness()
        await hs.say(OWNER, "/start")
        await hs.say(OWNER, "Иван")
        await hs.say(PLAYER, "/start")
        assert "Готово" in await hs.say(PLAYER, "Мура")

        # Встать в очередь: здание → уровень → время
        await hs.say(PLAYER, "🏗 Моя стройка")
        assert "Шаг 2 из 3" in await hs.click(PLAYER, "Электростанция")
        assert "13д 2ч" in await hs.click(PLAYER, "24")
        assert "Электростанция → 24" in await hs.say(PLAYER, "18д 3ч")

        # Отдать баф
        assert "Мура" in await hs.say(OWNER, "🎁 Отдать баф: стройка")
        text = await hs.click(OWNER, "✅ Отдал")
        assert "Иван</b> отдал тебе баф" in text

        # Панель: добавить игрока без Telegram и записать ему стройку
        await hs.say(OWNER, "⚙️ Управление")
        await hs.click(OWNER, "➕ Добавить игрока без Telegram")
        assert "Добавлен игрок" in await hs.say(OWNER, "Тёмный Страж")
        await hs.click(OWNER, "🏗 ✏️ Записать стройку")
        await hs.click(OWNER, "Лаборатория")
        await hs.click(OWNER, "25")
        assert "Записал игрока <b>Тёмный Страж</b>" in await hs.say(OWNER, "20д")
        strazh = hs.svc.player_by_nick("Тёмный Страж")
        assert hs.svc.active_timer(strazh["id"], "build")["level"] == 25

        # Не-админ не видит панель
        assert "⚙️ Управление" not in str(hs.last)
        assert "Управление" not in await hs.say(PLAYER, "/admin")

        # Игрок вышел из группы → владельцу предлагают удалить
        hs.out.clear()
        event = ChatMemberUpdated(
            chat=Chat(id=GROUP, type="supergroup"), from_user=hs.user(PLAYER), date=datetime.datetime.now(),
            old_chat_member=ChatMemberMember(user=hs.user(PLAYER)), new_chat_member=ChatMemberLeft(user=hs.user(PLAYER)),
        )
        await hs.dp.feed_update(hs.bot, Update(update_id=next(hs.updates), chat_member=event))
        hs.last = list(hs.out)
        assert any(chat == OWNER and "вышел из группы" in t for chat, t, _ in hs.out)
        await hs.click(OWNER, "🗑 Удалить")
        await hs.click(OWNER, "🗑 Да, удалить Мура")
        assert hs.svc.player_by_tg(PLAYER) is None
        assert "/start" in await hs.say(PLAYER, "🏗 Моя стройка")

    asyncio.run(scenario())
