"""Панель управления для владельца и админов (R4) — всё кнопками.

Игроки: список → карточка → срочно / закрыть таймер / записать таймер /
подобрать получателя для его бафа / сделать админом / удалить.
Плюс правила очереди, сводка в группу и оповещение, когда игрок вышел из группы союза.
"""

from __future__ import annotations

import logging
import time

from aiogram import Bot, F, Router
from aiogram.enums import ChatMemberStatus
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, ChatMemberUpdated, InlineKeyboardMarkup, Message, ReplyKeyboardRemove

from .config import Config
from .filters import IsAdmin
from .handlers_private import FREE_TEXT, notify, start_timer_flow, valid_nick
from .service import KIND_ACC, KIND_EMOJI, KIND_NAME, KINDS, Service
from .texts import (
    ADMIN_HELP,
    BTN_ADMIN,
    PanelCb,
    assignment_text,
    btn,
    cancel_kb,
    digest_text,
    donation_kb,
    h,
    pattern_text,
    target_text,
)
from . import gamedata
from .timeparse import format_duration

log = logging.getLogger(__name__)

router = Router(name="panel")
router.message.filter(F.chat.type == "private", IsAdmin())
router.callback_query.filter(F.message.chat.type == "private", IsAdmin())

PAGE_SIZE = 10
LEFT_STATUSES = (ChatMemberStatus.LEFT, ChatMemberStatus.KICKED)


class AdminInput(StatesGroup):
    nick = State()


def now() -> int:
    return int(time.time())


def home_back() -> list:
    return [btn("🏠 Панель", PanelCb(action="home"))]


# ---------- главная панель ----------

def panel_text(svc: Service) -> str:
    players = svc.players()
    in_bot = sum(1 for p in players if p["tg_id"])
    return (
        "⚙️ <b>Управление</b>\n\n"
        f"Игроков: {len(players)} (в боте {in_bot}, без Telegram {len(players) - in_bot})\n"
        f"Цикл: {pattern_text(svc.setting('pattern'))}\n"
        f"Цель: стройка {target_text(svc, 'build')}, исследование {target_text(svc, 'research')}"
    )


def panel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [btn("👥 Игроки", PanelCb(action="list"))],
        [btn("➕ Добавить игрока без Telegram", PanelCb(action="add"))],
        [btn("⚙️ Правила очереди", PanelCb(action="rules"))],
        [btn("📣 Сводка в группу сейчас", PanelCb(action="digest"))],
        [btn("📖 Команды текстом", PanelCb(action="cmds"))],
    ])


@router.message(F.text == BTN_ADMIN)
@router.message(Command("admin", "panel"))
async def open_panel(message: Message, state: FSMContext, svc: Service):
    await state.clear()
    await message.answer(panel_text(svc), reply_markup=panel_kb())


# ---------- список игроков и карточка ----------

def short_timer(svc: Service, player_id: int, kind: str, t: int) -> str:
    c = svc.timer_candidate(player_id, kind, t)
    if c is None or c.remaining <= 0:
        return ""
    return f"{KIND_EMOJI[kind]}{c.remaining // 86400}д" + ("🔥" if c.urgent else "")


def players_kb(svc: Service, page: int, owners: frozenset[int] = frozenset()) -> tuple[str, InlineKeyboardMarkup]:
    players = svc.players()
    pages = max(1, (len(players) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = min(max(page, 0), pages - 1)
    t = now()
    rows = []
    for p in players[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]:
        marks = ("👑" if p["tg_id"] in owners else "⭐" if p["is_admin"] else "") + ("" if p["tg_id"] else "📵")
        timers = " ".join(x for x in (short_timer(svc, p["id"], k, t) for k in KINDS) if x)
        rows.append([btn(f"{marks}{p['nick']} {timers}".strip(), PanelCb(action="card", pid=p["id"], page=page))])
    nav = []
    if page > 0:
        nav.append(btn("◀️", PanelCb(action="list", page=page - 1)))
    if page < pages - 1:
        nav.append(btn("▶️", PanelCb(action="list", page=page + 1)))
    if nav:
        rows.append(nav)
    rows.append(home_back())
    text = (
        f"👥 <b>Игроки</b> — {len(players)} (стр. {page + 1}/{pages})\n"
        "Нажми на игрока, чтобы управлять им.\n👑 владелец · ⭐ админ · 📵 нет в боте · 🔥 срочно"
    )
    if not players:
        text = "👥 Пока никого нет. Игроки появятся, когда нажмут /start в боте."
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def card_text(svc: Service, player, t: int) -> str:
    given, received = svc.stats(player["id"])
    if player["tg_id"]:
        tg = f"@{h(player['tg_username'])}" if player["tg_username"] else "в боте (без @username)"
    else:
        tg = "📵 нет в боте (добавлен админом)"
    lines = [f"👤 <b>{h(player['nick'])}</b>", f"Telegram: {tg}"]
    if player["is_admin"]:
        lines.append("⭐ Админ")
    for kind in KINDS:
        timer = svc.active_timer(player["id"], kind)
        c = svc.timer_candidate(player["id"], kind, t)
        if timer is None or c is None or c.remaining <= 0:
            lines.append(f"\n{KIND_EMOJI[kind]} {KIND_NAME[kind].capitalize()}: нет в очереди")
            continue
        what = gamedata.label(timer["item"], timer["level"], timer["note"])
        lines.append(
            f"\n{KIND_EMOJI[kind]} {KIND_NAME[kind].capitalize()}{': ' + h(what) if what else ''}\n"
            f"Осталось {format_duration(c.remaining)} · получил бафов: {c.received}"
            + (" · 🔥 срочно" if c.urgent else "")
        )
        cd = svc.cooldown(player["id"], kind)
        if cd and cd["ready_at"] > t:
            lines.append(f"Его баф на {KIND_ACC[kind]} — через {format_duration(cd['ready_at'] - t)}")
    lines.append(f"\nОтдал бафов: {given} · получил: {received}")
    return "\n".join(lines)


def card_kb(svc: Service, player, is_owner: bool, page: int) -> InlineKeyboardMarkup:
    pid = player["id"]
    rows = []
    for kind in KINDS:
        timer = svc.active_timer(pid, kind)
        e = KIND_EMOJI[kind]
        if timer is not None:
            urgent = "🔥 Срочно: ВКЛ" if timer["urgent"] else "🔥 Срочно: выкл"
            rows.append([
                btn(f"{e} {urgent}", PanelCb(action="urgent", pid=pid, kind=kind, page=page)),
                btn(f"{e} 🏁 Убрать из очереди", PanelCb(action="close", pid=pid, kind=kind, page=page)),
            ])
        word = "стройку" if kind == "build" else "исследование"
        rows.append([btn(f"{e} ✏️ Записать {word}", PanelCb(action="timer", pid=pid, kind=kind, page=page))])
    rows.append([
        btn("🎁 Его баф 🏗 → кому?", PanelCb(action="assign", pid=pid, kind="build", page=page)),
        btn("🎁 Его баф 🔬 → кому?", PanelCb(action="assign", pid=pid, kind="research", page=page)),
    ])
    if is_owner and player["tg_id"]:
        if player["is_admin"]:
            rows.append([btn("✖️ Снять админа", PanelCb(action="admin", pid=pid, value="0", page=page))])
        else:
            rows.append([btn("⭐ Сделать админом", PanelCb(action="admin", pid=pid, value="1", page=page))])
    rows.append([btn("🗑 Удалить из бота", PanelCb(action="del", pid=pid, page=page))])
    rows.append([btn("◀️ К списку", PanelCb(action="list", page=page)), *home_back()])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def show_card(cb_or_msg, svc: Service, cfg: Config, pid: int, page: int = 0, edit: bool = True):
    player = svc.player(pid)
    user_id = cb_or_msg.from_user.id
    message = cb_or_msg.message if isinstance(cb_or_msg, CallbackQuery) else cb_or_msg
    if player is None:
        text, kb = players_kb(svc, page, cfg.owner_ids)
        text = "Игрок уже удалён.\n\n" + text
    else:
        text, kb = card_text(svc, player, now()), card_kb(svc, player, user_id in cfg.owner_ids, page)
    if edit:
        await message.edit_text(text, reply_markup=kb)
    else:
        await message.answer(text, reply_markup=kb)


# ---------- правила ----------

def rules_text(svc: Service) -> str:
    return (
        "⚙️ <b>Правила очереди</b>\n\n"
        f"Цикл: <b>{pattern_text(svc.setting('pattern'))}</b>\n"
        f"Одному игроку подряд: не больше <b>{int(svc.setting_float('max_streak'))}</b>\n"
        f"Цель: стройка <b>{target_text(svc, 'build')}</b>, исследование <b>{target_text(svc, 'research')}</b>\n"
        f"Баф срезает <b>{svc.setting_float('pct'):g}%</b> от заявленного времени\n"
        f"Баф восстанавливается за <b>{svc.setting_float('cooldown_hours'):g} ч</b>, "
        f"бронь держится <b>{svc.setting_float('confirm_minutes'):g} мин</b>\n"
        "Сводка в группу: "
        + (f"<b>каждый день в {int(svc.setting_float('digest_hour'))}:00</b>"
           if svc.setting_float("digest_hour") >= 0 else "<b>выключена</b>")
        + "\n\nОстальные настройки — командой /settings"
    )


def rules_kb(svc: Service) -> InlineKeyboardMarkup:
    pattern = svc.setting("pattern")
    streak = int(svc.setting_float("max_streak"))
    digest_on = svc.setting_float("digest_hour") >= 0

    def mark(on: bool, text: str) -> str:
        return f"✅ {text}" if on else text

    return InlineKeyboardMarkup(inline_keyboard=[
        [btn("Цикл (большим : ждущим):", PanelCb(action="noop"))],
        [
            btn(mark(pattern == "BW", "1 : 1"), PanelCb(action="set", value="pattern=BW")),
            btn(mark(pattern == "BBW", "2 : 1"), PanelCb(action="set", value="pattern=BBW")),
            btn(mark(pattern == "BBBW", "3 : 1"), PanelCb(action="set", value="pattern=BBBW")),
        ],
        [btn("Бафов подряд одному:", PanelCb(action="noop"))],
        [btn(mark(streak == n, str(n)), PanelCb(action="set", value=f"max_streak={n}")) for n in (1, 2, 3)],
        [btn(
            "📣 Сводка в группу: ВКЛ" if digest_on else "📣 Сводка в группу: выкл",
            PanelCb(action="set", value="digest_hour=-1" if digest_on else "digest_hour=10"),
        )],
        home_back(),
    ])


# ---------- обработка кнопок ----------

@router.callback_query(PanelCb.filter())
async def panel_action(cb: CallbackQuery, callback_data: PanelCb, state: FSMContext, svc: Service,
                       cfg: Config, bot: Bot):
    a = callback_data.action
    pid, page, kind = callback_data.pid, callback_data.page, callback_data.kind
    is_owner = cb.from_user.id in cfg.owner_ids

    if a == "home":
        await state.clear()
        await cb.message.edit_text(panel_text(svc), reply_markup=panel_kb())
    elif a == "list":
        text, kb = players_kb(svc, page, cfg.owner_ids)
        await cb.message.edit_text(text, reply_markup=kb)
    elif a == "card":
        await show_card(cb, svc, cfg, pid, page)
    elif a == "urgent":
        flag = svc.toggle_urgent(pid, kind)
        await cb.answer("🔥 Срочно включено" if flag else "Срочно выключено")
        await show_card(cb, svc, cfg, pid, page)
        return
    elif a == "close":
        svc.close_timer(pid, kind)
        await cb.answer(f"Убран из очереди: {KIND_NAME[kind]}")
        await show_card(cb, svc, cfg, pid, page)
        return
    elif a == "timer":
        player = svc.player(pid)
        if player is not None:
            await start_timer_flow(cb.message, state, kind, for_pid=pid, for_nick=player["nick"])
    elif a == "assign":
        player = svc.player(pid)
        if player is None:
            await cb.answer("Игрок не найден", show_alert=True)
            return
        assignment = svc.assign(kind, pid, cb.from_user.id, now())
        if assignment is None:
            await cb.answer(f"Сейчас никому не нужен баф на {KIND_ACC[kind]}", show_alert=True)
            return
        sent = await cb.message.answer(
            f"Баф игрока <b>{h(player['nick'])}</b>:\n\n"
            + assignment_text(assignment, svc.setting_float("confirm_minutes")),
            reply_markup=donation_kb(assignment.donation_id),
        )
        svc.set_donation_message(assignment.donation_id, sent.chat.id, sent.message_id)
    elif a == "admin":
        player = svc.player(pid)
        if not is_owner or player is None or player["tg_id"] is None:
            await cb.answer("Назначать админов может только владелец", show_alert=True)
            return
        flag = callback_data.value == "1"
        svc.set_admin(pid, flag)
        if flag:
            await notify(bot, player["tg_id"], "⭐ Тебя назначили админом бота бафов. Нажми /start — появится кнопка «⚙️ Управление».")
        await cb.answer("⭐ Теперь админ" if flag else "Админ снят")
        await show_card(cb, svc, cfg, pid, page)
        return
    elif a == "del":
        player = svc.player(pid)
        if player is None:
            await cb.answer("Игрок уже удалён")
            return
        await cb.message.edit_text(
            f"🗑 Удалить <b>{h(player['nick'])}</b> из бота?\n\n"
            "Он пропадёт из очередей. История отданных бафов сохранится в статистике. "
            "Если он вернётся — сможет снова зарегистрироваться через /start.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [btn(f"🗑 Да, удалить {player['nick']}", PanelCb(action="del_yes", pid=pid, page=page))],
                [btn("↩️ Нет, назад", PanelCb(action="card", pid=pid, page=page))],
            ]),
        )
    elif a == "del_yes":
        player = svc.player(pid)
        if player is not None:
            svc.delete_player(pid, now())
            await notify(
                bot, player["tg_id"],
                "Тебя убрали из бота бафов союза. Если это ошибка — напиши админу союза.",
                reply_markup=ReplyKeyboardRemove(),
            )
            await cb.answer(f"Удалён: {player['nick']}")
        text, kb = players_kb(svc, page, cfg.owner_ids)
        await cb.message.edit_text(text, reply_markup=kb)
        return
    elif a == "keep":
        await cb.message.edit_reply_markup(reply_markup=None)
        await cb.answer("Оставили")
        return
    elif a == "add":
        await state.set_state(AdminInput.nick)
        await cb.message.answer(
            "✍️ Напиши <b>игровой ник</b> игрока, которого нет в Telegram-боте.\n"
            "Если он потом зарегистрируется с этим же ником — всё привяжется к нему.",
            reply_markup=cancel_kb(),
        )
    elif a == "rules":
        await cb.message.edit_text(rules_text(svc), reply_markup=rules_kb(svc))
    elif a == "set":
        key, _, value = callback_data.value.partition("=")
        error = svc.set_setting(key, value)
        if error:
            await cb.answer(error, show_alert=True)
            return
        await cb.message.edit_text(rules_text(svc), reply_markup=rules_kb(svc))
    elif a == "digest":
        if cfg.alliance_chat_id is None:
            await cb.message.answer("Группа союза не подключена (ALLIANCE_CHAT_ID). Вот сводка:\n\n" + digest_text(svc, now()))
        else:
            await bot.send_message(cfg.alliance_chat_id, digest_text(svc, now()))
            await cb.answer("✅ Сводка отправлена в группу")
            return
    elif a == "cmds":
        await cb.message.answer(ADMIN_HELP)
    elif a == "noop":
        await cb.answer("Выбери вариант ниже 👇")
        return
    await cb.answer()


@router.message(AdminInput.nick, *FREE_TEXT)
async def add_offline_player(message: Message, state: FSMContext, svc: Service, cfg: Config):
    nick = valid_nick(message.text)
    if nick is None:
        await message.answer("Ник должен быть от 2 до 32 символов. Напиши ник:", reply_markup=cancel_kb())
        return
    await state.clear()
    player = svc.player_by_nick(nick)
    if player is None:
        player = svc.create_offline_player(nick, now())
        await message.answer(f"✅ Добавлен игрок <b>{h(nick)}</b>. Теперь запиши ему стройку или исследование 👇")
    else:
        await message.answer(f"Игрок <b>{h(player['nick'])}</b> уже есть 👇")
    await show_card(message, svc, cfg, player["id"], edit=False)


# ---------- игрок вышел из группы союза ----------

@router.chat_member()
async def member_left(event: ChatMemberUpdated, svc: Service, cfg: Config, bot: Bot):
    if cfg.alliance_chat_id is None or event.chat.id != cfg.alliance_chat_id:
        return
    if event.new_chat_member.status not in LEFT_STATUSES or event.old_chat_member.status in LEFT_STATUSES:
        return
    player = svc.player_by_tg(event.new_chat_member.user.id)
    if player is None:
        return
    text = (
        f"🚪 <b>{h(player['nick'])}</b> вышел из группы союза в Telegram.\n"
        "Удалить его из бота бафов?"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        btn("🗑 Удалить", PanelCb(action="del", pid=player["id"])),
        btn("Оставить", PanelCb(action="keep", pid=player["id"])),
    ]])
    recipients = set(cfg.owner_ids) | {p["tg_id"] for p in svc.admins() if p["tg_id"]}
    for tg_id in recipients:
        await notify(bot, tg_id, text, reply_markup=kb)
