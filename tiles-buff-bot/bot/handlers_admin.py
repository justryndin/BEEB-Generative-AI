"""Команды админов (R4) и владельца."""

from __future__ import annotations

import time

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from .config import Config
from .filters import IsAdmin
from .handlers_private import notify
from .service import KIND_ACC, KIND_NAME, KINDS, SETTINGS, Service, parse_kind, split_nick_kind
from .texts import (
    ADMIN_HELP,
    assignment_text,
    digest_text,
    donation_kb,
    donor_done_text,
    h,
    recipient_text,
)
from .timeparse import format_duration, parse_duration


router = Router(name="admin")
router.message.filter(F.chat.type == "private", IsAdmin())


def now() -> int:
    return int(time.time())


@router.message(Command("commands"))
async def cmd_commands(message: Message):
    await message.answer(ADMIN_HELP)


async def _player_kind(message: Message, command: CommandObject, svc: Service, usage: str):
    parsed = split_nick_kind(command.args or "")
    if parsed is None:
        await message.answer(f"Формат: <code>{h(usage)}</code>")
        return None
    nick, kind, rest = parsed
    return nick, kind, rest


@router.message(Command("add", "fix"))
async def cmd_add(message: Message, command: CommandObject, svc: Service):
    keep_base = command.command == "fix"
    parsed = await _player_kind(message, command, svc, f"/{command.command} Ник стройка 21д 5ч")
    if parsed is None:
        return
    nick, kind, rest = parsed
    seconds = parse_duration(rest)
    if seconds is None:
        await message.answer("Не понял время. Пример: <code>21д 5ч</code>")
        return
    player = svc.player_by_nick(nick)
    created = player is None
    if created:
        player = svc.create_offline_player(nick, now())
    svc.set_timer(player["id"], kind, seconds, now(), keep_base=keep_base)
    note = " (новый игрок, ещё не в боте 📵)" if created else ""
    await message.answer(
        f"✅ {h(player['nick'])}{note}: {KIND_NAME[kind]} — осталось {format_duration(seconds)}"
    )


@router.message(Command("close"))
async def cmd_close(message: Message, command: CommandObject, svc: Service):
    parsed = await _player_kind(message, command, svc, "/close Ник стройка")
    if parsed is None:
        return
    nick, kind, _ = parsed
    player = svc.player_by_nick(nick)
    if player is None or not svc.close_timer(player["id"], kind):
        await message.answer("Не нашёл такой активный таймер.")
        return
    await message.answer(f"🏁 {h(player['nick'])}: {KIND_NAME[kind]} закрыто.")


@router.message(Command("urgent"))
async def cmd_urgent(message: Message, command: CommandObject, svc: Service):
    parsed = await _player_kind(message, command, svc, "/urgent Ник стройка")
    if parsed is None:
        return
    nick, kind, _ = parsed
    player = svc.player_by_nick(nick)
    flag = None if player is None else svc.toggle_urgent(player["id"], kind)
    if flag is None:
        await message.answer("Не нашёл такой активный таймер.")
        return
    state = "🔥 срочно — получает бафы вне очереди" if flag else "обычная очередь"
    await message.answer(f"{h(player['nick'])}, {KIND_NAME[kind]}: {state}")


@router.message(Command("assign"))
async def cmd_assign(message: Message, command: CommandObject, svc: Service):
    parsed = await _player_kind(message, command, svc, "/assign Ник стройка")
    if parsed is None:
        return
    nick, kind, _ = parsed
    donor = svc.player_by_nick(nick)
    if donor is None:
        donor = svc.create_offline_player(nick, now())
    a = svc.assign(kind, donor["id"], message.from_user.id, now())
    if a is None:
        await message.answer(f"Сейчас никому не нужен баф на {KIND_ACC[kind]}.")
        return
    sent = await message.answer(
        f"Для игрока <b>{h(donor['nick'])}</b>:\n\n" + assignment_text(a, svc.setting_float("confirm_minutes")),
        reply_markup=donation_kb(a.donation_id),
    )
    svc.set_donation_message(a.donation_id, sent.chat.id, sent.message_id)


@router.message(Command("gave"))
async def cmd_gave(message: Message, command: CommandObject, svc: Service, bot: Bot):
    usage = "Формат: <code>/gave Вася &gt; Мура стройка</code>"
    args = command.args or ""
    if ">" not in args:
        await message.answer(usage)
        return
    donor_nick, rest = (part.strip() for part in args.split(">", 1))
    tokens = rest.split()
    kind = parse_kind(tokens[-1]) if tokens else None
    recipient_nick = " ".join(tokens[:-1])
    if not donor_nick or not recipient_nick or kind is None:
        await message.answer(usage)
        return
    donor = svc.player_by_nick(donor_nick) or svc.create_offline_player(donor_nick, now())
    recipient = svc.player_by_nick(recipient_nick)
    if recipient is None or svc.active_timer(recipient["id"], kind) is None:
        await message.answer(f"У игрока «{h(recipient_nick)}» нет записанного таймера: {KIND_NAME[kind]}.")
        return
    result = svc.record_manual(donor["id"], recipient["id"], kind, message.from_user.id, now())
    await message.answer(donor_done_text(result, by_admin=True))
    await notify(bot, result.recipient_tg, recipient_text(result, svc))


@router.message(Command("players"))
async def cmd_players(message: Message, svc: Service):
    rows = svc.players()
    if not rows:
        await message.answer("Пока никого нет.")
        return
    lines = [f"<b>Игроки ({len(rows)})</b>"]
    t = now()
    for p in rows:
        parts = []
        for kind in KINDS:
            c = svc.timer_candidate(p["id"], kind, t)
            if c and c.remaining > 0:
                parts.append(f"{'🏗' if kind == 'build' else '🔬'}{format_duration(c.remaining)}")
        tg = f"@{h(p['tg_username'])}" if p["tg_username"] else ("в боте" if p["tg_id"] else "📵 нет в боте")
        star = "⭐ " if p["is_admin"] else ""
        lines.append(f"{star}{h(p['nick'])} — {tg} {' '.join(parts)}")
    text = "\n".join(lines)
    for i in range(0, len(text), 4000):
        await message.answer(text[i : i + 4000])


@router.message(Command("delplayer"))
async def cmd_delplayer(message: Message, command: CommandObject, svc: Service):
    player = svc.player_by_nick(command.args or "")
    if player is None:
        await message.answer("Формат: <code>/delplayer Ник</code> — ник не найден.")
        return
    svc.delete_player(player["id"], now())
    await message.answer(f"🗑 Игрок {h(player['nick'])} удалён.")


@router.message(Command("admins"))
async def cmd_admins(message: Message, svc: Service, cfg: Config):
    rows = svc.admins()
    names = [f"⭐ {h(p['nick'])}" + (f" (@{h(p['tg_username'])})" if p["tg_username"] else "") for p in rows]
    owners = ", ".join(f"<code>{i}</code>" for i in sorted(cfg.owner_ids)) or "—"
    await message.answer(f"Владелец (ID): {owners}\n\n<b>Админы:</b>\n" + ("\n".join(names) or "пока нет"))


@router.message(Command("admin_add", "admin_del"))
async def cmd_admin_set(message: Message, command: CommandObject, svc: Service, cfg: Config, bot: Bot):
    if message.from_user.id not in cfg.owner_ids:
        await message.answer("Назначать админов может только владелец.")
        return
    player = svc.find_player(command.args or "")
    if player is None or player["tg_id"] is None:
        await message.answer(
            f"Формат: <code>/{command.command} @username</code> (или ник, или Telegram ID).\n"
            "Человек должен быть зарегистрирован в боте."
        )
        return
    flag = command.command == "admin_add"
    svc.set_admin(player["id"], flag)
    if flag:
        await message.answer(f"⭐ {h(player['nick'])} теперь админ.")
        await notify(bot, player["tg_id"], "⭐ Тебя назначили админом бота бафов. Команды — /admin")
    else:
        await message.answer(f"{h(player['nick'])} больше не админ.")


@router.message(Command("settings"))
async def cmd_settings(message: Message, svc: Service):
    lines = ["<b>Настройки</b> (менять: <code>/set ключ значение</code>)\n"]
    for key, (_, desc) in SETTINGS.items():
        lines.append(f"<code>{key}</code> = <b>{h(svc.setting(key))}</b>\n   {h(desc)}")
    await message.answer("\n".join(lines))


@router.message(Command("set"))
async def cmd_set(message: Message, command: CommandObject, svc: Service):
    parts = (command.args or "").split(maxsplit=1)
    if len(parts) != 2 or parts[0] not in SETTINGS:
        await message.answer("Формат: <code>/set ключ значение</code>. Список ключей — /settings")
        return
    error = svc.set_setting(parts[0], parts[1])
    if error:
        await message.answer(f"❌ {h(error)}")
        return
    await message.answer(f"✅ {parts[0]} = <b>{h(svc.setting(parts[0]))}</b>")


@router.message(Command("digest"))
async def cmd_digest(message: Message, svc: Service, cfg: Config, bot: Bot):
    text = digest_text(svc, now())
    if cfg.alliance_chat_id is None:
        await message.answer("Группа союза не настроена (ALLIANCE_CHAT_ID), показываю здесь:\n\n" + text)
        return
    await bot.send_message(cfg.alliance_chat_id, text)
    await message.answer("✅ Сводка отправлена в группу.")
