"""Личный чат с ботом: регистрация, таймеры, бафы, очередь.

Вручную игрок вводит только ник и время по таймеру — всё остальное кнопками.
"""

from __future__ import annotations

import logging
import time

from aiogram import Bot, F, Router
from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message, ReplyKeyboardRemove

from . import gamedata
from .config import Config
from .logic import buffs_needed
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
    LevelCb,
    NavCb,
    PanelCb,
    QueueCb,
    TimerCb,
    assignment_text,
    btn,
    cancel_kb,
    catalog_text,
    confirm_close_kb,
    donation_kb,
    donor_done_text,
    h,
    help_text,
    item_kb,
    item_prompt,
    level_kb,
    level_prompt,
    main_kb,
    profile_text,
    queue_kb,
    queue_text,
    recipient_text,
    reference_text,
    target_text,
    timer_card,
    timer_kb,
    timer_prompt,
)
from .timeparse import format_duration, parse_duration

log = logging.getLogger(__name__)

router = Router(name="private")
router.message.filter(F.chat.type == "private")
router.callback_query.filter(F.message.chat.type == "private")


class Reg(StatesGroup):
    nick = State()
    rename = State()


class TimerInput(StatesGroup):
    value = State()


# Текст в шагах ввода, кроме кнопок меню и команд — они прерывают ввод.
FREE_TEXT = (F.text, ~F.text.in_(MENU_BUTTONS), ~F.text.startswith("/"))

WELCOME_STEPS = (
    "<b>Что дальше:</b>\n"
    "🏗 Запустил стройку → нажми «🏗 Моя стройка» и встань в очередь.\n"
    "🔬 Запустил исследование → «🔬 Моё исследование».\n"
    "🎁 Готов твой баф → «🎁 Отдать баф…» — я скажу, кому.\n\n"
    "Все кнопки — внизу экрана 👇 Подробнее — «❓ Как это работает»."
)


def now() -> int:
    return int(time.time())


def kb_for(svc: Service, cfg: Config, tg_id: int):
    return main_kb(svc.is_admin(tg_id, cfg.owner_ids))


async def notify(bot: Bot, tg_id: int | None, text: str, **kwargs) -> None:
    if not tg_id:
        return
    try:
        await bot.send_message(tg_id, text, **kwargs)
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


def valid_nick(text: str) -> str | None:
    nick = clean_nick(text)
    if nick.startswith("/") or nick in MENU_BUTTONS or not 2 <= len(nick) <= 32:
        return None
    return nick


def profile_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [btn("✏️ Сменить ник", NavCb(action="rename"))],
        [btn("🚪 Удалить меня из бота", NavCb(action="leave"))],
    ])


# ---------- регистрация ----------

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, svc: Service, cfg: Config, bot: Bot):
    await state.clear()
    player = svc.player_by_tg(message.from_user.id)
    if player is not None:
        svc.touch_username(player["id"], message.from_user.username)
        await message.answer(
            f"С возвращением, <b>{h(player['nick'])}</b>! 👋\n\n{WELCOME_STEPS}",
            reply_markup=kb_for(svc, cfg, message.from_user.id),
        )
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
        "✍️ Напиши свой <b>ник в игре</b> — точно как в игре, "
        "чтобы союзники могли найти тебя и отдать баф:",
        reply_markup=ReplyKeyboardRemove(),
    )


@router.message(Reg.nick, F.text)
async def reg_nick(message: Message, state: FSMContext, svc: Service, cfg: Config):
    nick = valid_nick(message.text)
    if nick is None:
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
        f"Готово, <b>{h(player['nick'])}</b>! ✅\n\n{WELCOME_STEPS}",
        reply_markup=kb_for(svc, cfg, message.from_user.id),
    )


@router.message(Reg.nick)
async def reg_nick_other(message: Message):
    await message.answer("Напиши свой <b>ник в игре</b> текстом:")


@router.message(Reg.rename, *FREE_TEXT)
async def rename_nick(message: Message, state: FSMContext, svc: Service, cfg: Config):
    player = await need_player(message, svc, state)
    if player is None:
        return
    nick = valid_nick(message.text)
    if nick is None:
        await message.answer("Ник должен быть от 2 до 32 символов. Напиши новый ник:", reply_markup=cancel_kb())
        return
    if svc.rename(player["id"], nick) == "taken":
        await message.answer("Этот ник уже занят другим игроком. Напиши другой:", reply_markup=cancel_kb())
        return
    await state.clear()
    await message.answer(f"Ник изменён на <b>{h(nick)}</b> ✅", reply_markup=kb_for(svc, cfg, message.from_user.id))


@router.message(Command("nick"))
async def cmd_nick(message: Message, command: CommandObject, state: FSMContext, svc: Service):
    player = await need_player(message, svc, state)
    if player is None:
        return
    nick = valid_nick(command.args or "")
    if nick is None:
        await message.answer("Напиши так: <code>/nick Новый ник</code>")
        return
    if svc.rename(player["id"], nick) == "taken":
        await message.answer("Этот ник уже занят другим игроком.")
        return
    await message.answer(f"Ник изменён на <b>{h(nick)}</b> ✅")


@router.message(Command("myid"))
async def cmd_myid(message: Message):
    await message.answer(f"Твой Telegram ID: <code>{message.from_user.id}</code>")


# ---------- кнопки навигации ----------

@router.callback_query(NavCb.filter())
async def nav(cb: CallbackQuery, callback_data: NavCb, state: FSMContext, svc: Service, cfg: Config):
    action = callback_data.action
    if action == "cancel":
        await state.clear()
        await cb.message.edit_text("Отменено 👌 Пользуйся кнопками внизу экрана.")
    elif action == "items":
        data = await state.get_data()
        for_nick = data.get("for_nick")
        await cb.message.edit_text(item_prompt(callback_data.kind, for_nick), reply_markup=item_kb(callback_data.kind))
    elif action == "rename":
        await state.set_state(Reg.rename)
        await cb.message.answer("✍️ Напиши новый ник — точно как в игре:", reply_markup=cancel_kb())
    elif action == "leave":
        await cb.message.answer(
            "Точно удалить тебя из бота? Ты пропадёшь из очереди, история бафов сохранится.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                btn("🗑 Да, удалить", NavCb(action="leave_yes")),
                btn("↩️ Нет", NavCb(action="cancel")),
            ]]),
        )
    elif action == "leave_yes":
        player = svc.player_by_tg(cb.from_user.id)
        if player is not None:
            svc.delete_player(player["id"], now())
        await state.clear()
        await cb.message.edit_text("Готово, ты удалён из бота. Вернуться можно в любой момент: /start")
    await cb.answer()


# ---------- таймеры ----------

async def start_timer_flow(message: Message, state: FSMContext, kind: str, for_pid: int | None = None,
                           for_nick: str | None = None, edit: bool = False) -> None:
    await state.clear()
    if for_pid:
        await state.set_data({"for_pid": for_pid, "for_nick": for_nick})
    text = item_prompt(kind, for_nick)
    if edit:
        await message.edit_text(text, reply_markup=item_kb(kind))
    else:
        await message.answer(text, reply_markup=item_kb(kind))


@router.message(F.text.in_({BTN_BUILD, BTN_RESEARCH}))
async def my_timer(message: Message, state: FSMContext, svc: Service):
    await state.clear()
    player = await need_player(message, svc, state)
    if player is None:
        return
    kind = "build" if message.text == BTN_BUILD else "research"
    c = svc.timer_candidate(player["id"], kind, now())
    if c is None or c.remaining <= 0:
        word = "стройку" if kind == "build" else "исследование"
        await message.answer(f"Тебя пока нет в очереди на {word}. Давай запишем 👇")
        await start_timer_flow(message, state, kind)
        return
    await message.answer(timer_card(svc, player["id"], kind, now()), reply_markup=timer_kb(kind))


@router.callback_query(TimerCb.filter())
async def timer_action(cb: CallbackQuery, callback_data: TimerCb, state: FSMContext, svc: Service):
    player = svc.player_by_tg(cb.from_user.id)
    if player is None:
        await cb.answer("Сначала /start", show_alert=True)
        return
    kind = callback_data.kind
    action = callback_data.action
    if action == "close":
        word = "стройки" if kind == "build" else "исследования"
        await cb.message.edit_text(
            f"Убрать тебя из очереди {word}? Бафы на неё тебе больше не будут назначаться.",
            reply_markup=confirm_close_kb(kind),
        )
    elif action == "close_yes":
        svc.close_timer(player["id"], kind)
        await cb.message.edit_text(f"🏁 Готово — ты убран из очереди ({KIND_NAME[kind]}).")
    elif action == "show":
        await cb.message.edit_text(timer_card(svc, player["id"], kind, now()), reply_markup=timer_kb(kind))
    elif action == "new":
        await start_timer_flow(cb.message, state, kind, edit=True)
    elif action == "fix":
        await state.set_state(TimerInput.value)
        await state.set_data({"kind": kind, "keep_base": True})
        await cb.message.answer(timer_prompt(kind, fix=True), reply_markup=cancel_kb())
    await cb.answer()


@router.callback_query(ItemCb.filter())
async def item_chosen(cb: CallbackQuery, callback_data: ItemCb, state: FSMContext, svc: Service):
    it = gamedata.item(callback_data.code)
    if it is None:
        await cb.answer()
        return
    data = await state.get_data()
    await state.set_data({
        "kind": it.kind, "keep_base": False, "item": it.code,
        "for_pid": data.get("for_pid"), "for_nick": data.get("for_nick"),
    })
    if it.kind == "build" and it.max_level:
        await cb.message.edit_text(level_prompt(it), reply_markup=level_kb(it))
    else:
        await state.set_state(TimerInput.value)
        await cb.message.edit_text(f"{KIND_EMOJI[it.kind]} {h(it.ru)} ✅")
        await cb.message.answer(timer_prompt(it.kind, fix=False), reply_markup=cancel_kb())
    await cb.answer()


@router.callback_query(LevelCb.filter())
async def level_chosen(cb: CallbackQuery, callback_data: LevelCb, state: FSMContext, svc: Service):
    data = await state.get_data()
    it = gamedata.item(data.get("item"))
    if it is None:
        await cb.answer("Начни заново через меню", show_alert=True)
        return
    level = callback_data.level
    await state.update_data(level=level)
    await state.set_state(TimerInput.value)
    await cb.message.edit_text(f"🏗 {h(it.ru)} → {level} ✅")
    await cb.message.answer(
        timer_prompt("build", fix=False, reference=reference_text(svc, it, level)), reply_markup=cancel_kb()
    )
    await cb.answer()


@router.message(TimerInput.value, *FREE_TEXT)
async def timer_value(message: Message, state: FSMContext, svc: Service, cfg: Config):
    me = await need_player(message, svc, state)
    if me is None:
        return
    seconds = parse_duration(message.text)
    if seconds is None:
        await message.answer(
            "Не понял время 🤔 Напиши как на таймере в игре, например: "
            "<code>21д 5ч</code>, <code>100д</code>, <code>21</code> или <code>20d 13:45:12</code>",
            reply_markup=cancel_kb(),
        )
        return
    data = await state.get_data()
    for_pid = data.get("for_pid")
    by_admin = bool(for_pid) and svc.is_admin(message.from_user.id, cfg.owner_ids)
    target = svc.player(for_pid) if by_admin else me
    if target is None:
        await state.clear()
        await message.answer("Игрок не найден — возможно, его уже удалили.")
        return
    kind = data.get("kind", "build")
    it = gamedata.item(data.get("item"))
    level = data.get("level")
    svc.set_timer(
        target["id"], kind, seconds, now(),
        keep_base=bool(data.get("keep_base")),
        item=data.get("item"), level=level,
    )
    await state.clear()

    warning = ""
    ref = it.time_for(level) if it else None
    if ref and seconds > ref * 1.5:
        warning = (
            f"\n\n⚠️ Это намного больше справочного времени ({format_duration(ref)} без бонусов). "
            "Проверь таймер — если ошибся, нажми «🔧 Поправить остаток времени»."
        )
    c = svc.timer_candidate(target["id"], kind, now())
    needed = buffs_needed(c.remaining, c.base, svc.rules(kind))
    what = gamedata.label(data.get("item"), level) if it else ""
    head = f"✅ Записал{' игрока <b>' + h(target['nick']) + '</b>' if by_admin else ''}: {KIND_EMOJI[kind]} "
    head += f"{h(what) if what else KIND_NAME[kind]} — осталось <b>{format_duration(c.remaining)}</b>."
    if needed:
        tail = (
            f"Чтобы дойти до остатка {target_text(svc, kind)}, нужно ≈ <b>{needed}</b> бафов.\n"
            + ("" if by_admin else "Ты в очереди 🙌 Когда тебе отдадут баф — придёт уведомление.")
        )
    else:
        tail = f"Бафы не нужны: остаток уже около цели ({target_text(svc, kind)})."
    if by_admin:
        await message.answer(
            f"{head}\n{tail.strip()}{warning}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[btn("◀️ К игроку", PanelCb(action="card", pid=target["id"]))]]),
        )
    else:
        await message.answer(f"{head}\n{tail}{warning}", reply_markup=timer_kb(kind))
        await message.answer("Меню — внизу 👇", reply_markup=kb_for(svc, cfg, message.from_user.id))


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
            "Придержи его и нажми кнопку ещё раз позже. Очередь — «📋 Очередь»."
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
        by_admin = d["donor_id"] != (svc.player_by_tg(cb.from_user.id) or {"id": None})["id"]
        await cb.message.edit_text(donor_done_text(result, by_admin=by_admin))
        await notify(bot, result.recipient_tg, recipient_text(result, svc))
        if by_admin and result.donor_tg:
            await notify(bot, result.donor_tg, donor_done_text(result))
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
    await message.answer(profile_text(svc, player, now()), reply_markup=profile_kb())


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
async def fallback(message: Message, state: FSMContext, svc: Service, cfg: Config):
    player = await need_player(message, svc, state)
    if player is None:
        return
    await message.answer(
        "Я понимаю только кнопки 🙂 Выбери действие внизу экрана 👇",
        reply_markup=kb_for(svc, cfg, message.from_user.id),
    )
