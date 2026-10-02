"""Личный чат с ботом: регистрация, таймеры, бафы, очередь."""

from __future__ import annotations

import logging
import time

from aiogram import Bot, F, Router
from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove

from . import gamedata
from .config import Config
from .service import KIND_ACC, KIND_EMOJI, KIND_NAME, Service, clean_nick
from .texts import (
    BTN_BUFF_BUILD,
    BTN_BUFF_RESEARCH,
    BTN_BUILD,
    BTN_HELP,
    BTN_ME,
    BTN_QUEUE,
    BTN_RESEARCH,
    MENU_BUTTONS,
    DonationCb,
    ItemCb,
    QueueCb,
    SkipCb,
    TimerCb,
    assignment_text,
    catalog_text,
    donation_kb,
    donor_done_text,
    h,
    help_text,
    item_kb,
    item_prompt,
    level_prompt,
    main_kb,
    note_prompt,
    profile_text,
    queue_kb,
    queue_text,
    recipient_text,
    reference_text,
    skip_kb,
    target_text,
    timer_card,
    timer_kb,
    timer_prompt,
)
from .logic import buffs_needed
from .timeparse import format_duration, parse_duration

log = logging.getLogger(__name__)

router = Router(name="private")
router.message.filter(F.chat.type == "private")
router.callback_query.filter(F.message.chat.type == "private")


class Reg(StatesGroup):
    nick = State()


class TimerInput(StatesGroup):
    level = State()
    note = State()
    value = State()


# Текст в шагах ввода, кроме кнопок меню и команд — они прерывают ввод.
_FREE_TEXT = (F.text, ~F.text.in_(MENU_BUTTONS), ~F.text.startswith("/"))


def now() -> int:
    return int(time.time())


async def notify(bot: Bot, tg_id: int | None, text: str) -> None:
    if not tg_id:
        return
    try:
        await bot.send_message(tg_id, text)
    except TelegramAPIError as e:
        log.warning("Не удалось написать %s: %s", tg_id, e)


async def is_alliance_member(bot: Bot, cfg: Config, svc: Service, tg_id: int) -> bool:
    if cfg.alliance_chat_id is None or svc.is_admin(tg_id, cfg.owner_ids):
        return True
    try:
        member = await bot.get_chat_member(cfg.alliance_chat_id, tg_id)
    except TelegramAPIError as e:
        log.warning("Не удалось проверить участника группы (бот добавлен в группу?): %s", e)
        return False
    if member.status in (ChatMemberStatus.CREATOR, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.MEMBER):
        return True
    return member.status == ChatMemberStatus.RESTRICTED and getattr(member, "is_member", False)


async def need_player(message: Message, svc: Service, state: FSMContext):
    """Все действия — только после регистрации с игровым ником."""
    player = svc.player_by_tg(message.from_user.id)
    if player is None:
        await state.clear()
        await message.answer(
            "Сначала нужно зарегистрироваться и указать ник в игре 👉 нажми /start",
            reply_markup=ReplyKeyboardRemove(),
        )
    return player


# ---------- регистрация ----------

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, svc: Service, cfg: Config, bot: Bot):
    await state.clear()
    player = svc.player_by_tg(message.from_user.id)
    if player is not None:
        svc.touch_username(player["id"], message.from_user.username)
        await message.answer(f"С возвращением, <b>{h(player['nick'])}</b>! 👋", reply_markup=main_kb())
        return
    if not await is_alliance_member(bot, cfg, svc, message.from_user.id):
        await message.answer(
            "Этот бот — только для участников союза.\n"
            "Вступи в Telegram-группу союза и нажми /start ещё раз."
        )
        return
    await state.set_state(Reg.nick)
    await message.answer(
        "Привет! Я помогаю союзу распределять сезонные бафы на стройку и исследования, "
        "чтобы ни один баф не пропал зря. ❄️\n\n"
        "✍️ Для начала напиши свой <b>ник в игре</b> — точно как в игре, "
        "чтобы союзники могли найти тебя и отдать баф:",
        reply_markup=ReplyKeyboardRemove(),
    )


@router.message(Reg.nick, F.text)
async def reg_nick(message: Message, state: FSMContext, svc: Service):
    nick = clean_nick(message.text)
    if nick.startswith("/") or nick in MENU_BUTTONS or not 2 <= len(nick) <= 32:
        await message.answer("Ник должен быть от 2 до 32 символов. Напиши свой <b>ник в игре</b>:")
        return
    player, error = svc.register(message.from_user.id, message.from_user.username, nick, now())
    if error == "taken":
        await message.answer(
            f"Ник <b>{h(nick)}</b> уже привязан к другому аккаунту Telegram.\n"
            "Проверь написание или напиши админу союза. Введи ник ещё раз:"
        )
        return
    await state.clear()
    await message.answer(
        f"Готово, <b>{h(player['nick'])}</b>! ✅\n\n"
        "Запустил стройку или исследование — нажми «🏗 Моя стройка» / «🔬 Моё исследование».\n"
        "Готов баф — нажми «🎁 Баф…», и я скажу, кому его отдать.\n\n"
        "Подробнее — «❓ Помощь».",
        reply_markup=main_kb(),
    )


@router.message(Reg.nick)
async def reg_nick_other(message: Message):
    await message.answer("Напиши свой <b>ник в игре</b> текстом:")


@router.message(Command("nick"))
async def cmd_nick(message: Message, command: CommandObject, state: FSMContext, svc: Service):
    player = await need_player(message, svc, state)
    if player is None:
        return
    nick = clean_nick(command.args or "")
    if not 2 <= len(nick) <= 32:
        await message.answer("Напиши так: <code>/nick Новый ник</code>")
        return
    if svc.rename(player["id"], nick) == "taken":
        await message.answer("Этот ник уже занят другим игроком.")
        return
    await message.answer(f"Ник изменён на <b>{h(nick)}</b> ✅")


@router.message(Command("myid"))
async def cmd_myid(message: Message):
    await message.answer(f"Твой Telegram ID: <code>{message.from_user.id}</code>")


# ---------- таймеры ----------

@router.message(F.text.in_({BTN_BUILD, BTN_RESEARCH}))
async def my_timer(message: Message, state: FSMContext, svc: Service):
    await state.clear()
    player = await need_player(message, svc, state)
    if player is None:
        return
    kind = "build" if message.text == BTN_BUILD else "research"
    c = svc.timer_candidate(player["id"], kind, now())
    if c is None or c.remaining <= 0:
        await message.answer(item_prompt(kind), reply_markup=item_kb(kind))
        return
    await message.answer(timer_card(svc, player["id"], kind, now()), reply_markup=timer_kb(kind))


@router.callback_query(TimerCb.filter())
async def timer_action(cb: CallbackQuery, callback_data: TimerCb, state: FSMContext, svc: Service):
    player = svc.player_by_tg(cb.from_user.id)
    if player is None:
        await cb.answer("Сначала /start", show_alert=True)
        return
    kind = callback_data.kind
    if callback_data.action == "close":
        svc.close_timer(player["id"], kind)
        await cb.message.edit_text(f"🏁 {KIND_NAME[kind].capitalize()} закрыто — ты убран из очереди.")
    elif callback_data.action == "new":
        await state.clear()
        await cb.message.answer(item_prompt(kind), reply_markup=item_kb(kind))
    else:
        await state.set_state(TimerInput.value)
        await state.set_data({"kind": kind, "keep_base": True})
        await cb.message.answer(timer_prompt(kind, fix=True))
    await cb.answer()


@router.callback_query(ItemCb.filter())
async def item_chosen(cb: CallbackQuery, callback_data: ItemCb, state: FSMContext):
    it = gamedata.item(callback_data.code)
    if it is None:
        await cb.answer()
        return
    await state.set_data({"kind": it.kind, "keep_base": False, "item": it.code})
    await cb.message.edit_text(f"{'🏗' if it.kind == 'build' else '🔬'} {h(it.ru)}")
    if it.kind == "build" and it.en:
        await state.set_state(TimerInput.level)
        await cb.message.answer(level_prompt(it))
    else:
        await state.set_state(TimerInput.note)
        await cb.message.answer(note_prompt(it), reply_markup=skip_kb("note"))
    await cb.answer()


@router.message(TimerInput.level, *_FREE_TEXT)
async def level_value(message: Message, state: FSMContext, svc: Service):
    data = await state.get_data()
    it = gamedata.item(data.get("item"))
    top = (it.max_level if it and it.max_level else 60)
    text = message.text.strip()
    if not text.isdigit() or not 1 <= int(text) <= top:
        await message.answer(f"Напиши уровень числом от 1 до {top}, например <code>24</code>")
        return
    level = int(text)
    await state.update_data(level=level)
    await state.set_state(TimerInput.value)
    await message.answer(timer_prompt(data["kind"], fix=False, reference=reference_text(svc, it, level)))


@router.message(TimerInput.note, *_FREE_TEXT)
async def note_value(message: Message, state: FSMContext):
    note = " ".join(message.text.split())[:60]
    await state.update_data(note=note)
    await state.set_state(TimerInput.value)
    data = await state.get_data()
    await message.answer(timer_prompt(data["kind"], fix=False))


@router.callback_query(SkipCb.filter())
async def skip_step(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if "kind" not in data:
        await cb.answer("Начни заново через меню")
        return
    await state.set_state(TimerInput.value)
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.message.answer(timer_prompt(data["kind"], fix=False))
    await cb.answer()


@router.message(TimerInput.value, *_FREE_TEXT)
async def timer_value(message: Message, state: FSMContext, svc: Service):
    player = await need_player(message, svc, state)
    if player is None:
        return
    seconds = parse_duration(message.text)
    if seconds is None:
        await message.answer(
            "Не понял время 🤔 Напиши, например: <code>21д 5ч</code>, <code>100д</code> или <code>20d 13:45:12</code>"
        )
        return
    data = await state.get_data()
    kind = data.get("kind", "build")
    it = gamedata.item(data.get("item"))
    level = data.get("level")
    svc.set_timer(
        player["id"], kind, seconds, now(),
        keep_base=bool(data.get("keep_base")),
        item=data.get("item"), level=level, note=data.get("note"),
    )
    await state.clear()
    warning = ""
    ref = it.time_for(level) if it else None
    if ref and seconds > ref * 1.5:
        warning = (
            f"\n\n⚠️ Это больше справочного времени ({format_duration(ref)} без бонусов). "
            "Проверь, правильно ли ввёл — если ошибся, поправь через «Моя стройка» → «🔧 Поправить остаток»."
        )
    c = svc.timer_candidate(player["id"], kind, now())
    needed = buffs_needed(c.remaining, c.base, svc.rules(kind))
    tail = (
        f"Чтобы дойти до остатка {target_text(svc, kind)}, нужно ≈ <b>{needed}</b> бафов. "
        "Я поставил тебя в очередь и сообщу, когда кто-то отдаст тебе баф. 🙌"
        if needed
        else f"Бафы не нужны: остаток уже около цели ({target_text(svc, kind)})."
    )
    await message.answer(
        f"✅ Записал: {KIND_EMOJI[kind]} {KIND_NAME[kind]}"
        + (f" ({h(gamedata.label(data.get('item'), level, data.get('note')))})" if it else "")
        + f" — осталось <b>{format_duration(c.remaining)}</b>.\n{tail}{warning}",
        reply_markup=main_kb(),
    )


# ---------- бафы ----------

@router.message(F.text.in_({BTN_BUFF_BUILD, BTN_BUFF_RESEARCH}))
async def give_buff(message: Message, state: FSMContext, svc: Service):
    await state.clear()
    player = await need_player(message, svc, state)
    if player is None:
        return
    kind = "build" if message.text == BTN_BUFF_BUILD else "research"
    a = svc.assign(kind, player["id"], message.from_user.id, now())
    if a is None:
        await message.answer(
            f"Сейчас никому не нужен баф на {KIND_ACC[kind]} 👍\n"
            "Придержи его и загляни позже — очередь можно посмотреть в «📋 Очередь»."
        )
        return
    sent = await message.answer(
        assignment_text(a, svc.setting_float("confirm_minutes")), reply_markup=donation_kb(a.donation_id)
    )
    svc.set_donation_message(a.donation_id, sent.chat.id, sent.message_id)


@router.callback_query(DonationCb.filter())
async def donation_action(cb: CallbackQuery, callback_data: DonationCb, svc: Service, cfg: Config, bot: Bot):
    d = svc.donation(callback_data.id)
    if d is None:
        await cb.answer("Бронь не найдена", show_alert=True)
        return
    if d["requested_by"] != cb.from_user.id and not svc.is_admin(cb.from_user.id, cfg.owner_ids):
        await cb.answer("Это не твоя бронь", show_alert=True)
        return
    if d["status"] != "pending":
        await cb.answer("Эта бронь уже закрыта")
        await cb.message.edit_reply_markup(reply_markup=None)
        return

    if callback_data.action == "ok":
        result = svc.confirm(d["id"], now())
        if result is None:
            await cb.answer("Эта бронь уже закрыта")
            return
        await cb.message.edit_text(donor_done_text(result))
        await notify(bot, result.recipient_tg, recipient_text(result, svc))
    elif callback_data.action == "other":
        a = svc.reassign(d["id"], cb.from_user.id, now())
        if a is None:
            await cb.message.edit_text(
                f"Больше некому отдать баф на {KIND_ACC[d['kind']]} 🤷 Придержи его и загляни позже."
            )
        else:
            await cb.message.edit_text(
                assignment_text(a, svc.setting_float("confirm_minutes")), reply_markup=donation_kb(a.donation_id)
            )
            svc.set_donation_message(a.donation_id, cb.message.chat.id, cb.message.message_id)
    else:
        svc.cancel(d["id"], now())
        await cb.message.edit_text("Ок, отменил. Баф остаётся у тебя 👌")
    await cb.answer()


# ---------- очередь, профиль, помощь ----------

@router.message(F.text == BTN_QUEUE)
@router.message(Command("queue"))
async def show_queue(message: Message, state: FSMContext, svc: Service):
    await state.clear()
    await message.answer(queue_text(svc, "build", now()), reply_markup=queue_kb("build"))


@router.callback_query(QueueCb.filter())
async def switch_queue(cb: CallbackQuery, callback_data: QueueCb, svc: Service):
    await cb.message.edit_text(queue_text(svc, callback_data.kind, now()), reply_markup=queue_kb(callback_data.kind))
    await cb.answer()


@router.message(F.text == BTN_ME)
@router.message(Command("me"))
async def show_profile(message: Message, state: FSMContext, svc: Service):
    await state.clear()
    player = await need_player(message, svc, state)
    if player is None:
        return
    await message.answer(profile_text(svc, player, now()), reply_markup=main_kb())


@router.message(Command("catalog"))
async def show_catalog(message: Message, state: FSMContext, svc: Service):
    await state.clear()
    await message.answer(catalog_text(svc))


@router.message(F.text == BTN_HELP)
@router.message(Command("help"))
async def show_help(message: Message, state: FSMContext, svc: Service):
    await state.clear()
    await message.answer(help_text(svc))


@router.message()
async def fallback(message: Message, state: FSMContext, svc: Service):
    player = await need_player(message, svc, state)
    if player is None:
        return
    await message.answer("Не понял 🙂 Пользуйся кнопками меню внизу.", reply_markup=main_kb())
