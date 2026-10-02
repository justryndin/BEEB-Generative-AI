"""Тексты сообщений и клавиатуры."""

from __future__ import annotations

from html import escape

from aiogram.filters.callback_data import CallbackData
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from . import gamedata
from .logic import SLOT_BIG, SLOT_URGENT, SLOT_WAIT, STATUS_NEED, STATUS_TARGET, buffs_needed, timer_status
from .service import KIND_ACC, KIND_EMOJI, KIND_NAME, KINDS, Assignment, BuffResult, Service
from .timeparse import format_duration

BTN_BUILD = "🏗 Моя стройка"
BTN_RESEARCH = "🔬 Моё исследование"
BTN_BUFF_BUILD = "🎁 Баф на стройку"
BTN_BUFF_RESEARCH = "🎁 Баф на исследование"
BTN_QUEUE = "📋 Очередь"
BTN_ME = "👤 Профиль"
BTN_HELP = "❓ Помощь"
MENU_BUTTONS = {BTN_BUILD, BTN_RESEARCH, BTN_BUFF_BUILD, BTN_BUFF_RESEARCH, BTN_QUEUE, BTN_ME, BTN_HELP}

SLOT_REASON = {
    SLOT_BIG: "у него самый большой остаток",
    SLOT_WAIT: "он дольше всех ждёт помощи",
    SLOT_URGENT: "🔥 срочно (отметил админ)",
    "M": "записано админом",
}


class DonationCb(CallbackData, prefix="d"):
    action: str
    id: int


class TimerCb(CallbackData, prefix="t"):
    action: str
    kind: str


class QueueCb(CallbackData, prefix="q"):
    kind: str


class ItemCb(CallbackData, prefix="i"):
    code: str


class SkipCb(CallbackData, prefix="s"):
    step: str


def h(value) -> str:
    return escape(str(value))


def main_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_BUILD), KeyboardButton(text=BTN_RESEARCH)],
            [KeyboardButton(text=BTN_BUFF_BUILD), KeyboardButton(text=BTN_BUFF_RESEARCH)],
            [KeyboardButton(text=BTN_QUEUE), KeyboardButton(text=BTN_ME), KeyboardButton(text=BTN_HELP)],
        ],
        resize_keyboard=True,
    )


def donation_kb(donation_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Отдал", callback_data=DonationCb(action="ok", id=donation_id).pack())],
            [
                InlineKeyboardButton(text="🔁 Другой игрок", callback_data=DonationCb(action="other", id=donation_id).pack()),
                InlineKeyboardButton(text="❌ Отмена", callback_data=DonationCb(action="no", id=donation_id).pack()),
            ],
        ]
    )


def timer_kb(kind: str) -> InlineKeyboardMarkup:
    word = "стройка" if kind == "build" else "исследование"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"✏️ Новое {word}" if kind == "research" else f"✏️ Новая {word}",
                                  callback_data=TimerCb(action="new", kind=kind).pack())],
            [InlineKeyboardButton(text="🔧 Поправить остаток", callback_data=TimerCb(action="fix", kind=kind).pack())],
            [InlineKeyboardButton(text="🏁 Завершено / бафы не нужны", callback_data=TimerCb(action="close", kind=kind).pack())],
        ]
    )


def item_kb(kind: str) -> InlineKeyboardMarkup:
    items = gamedata.items_for(kind)
    rows = []
    for i in range(0, len(items), 2):
        rows.append([
            InlineKeyboardButton(text=it.ru, callback_data=ItemCb(code=it.code).pack())
            for it in items[i : i + 2]
        ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def skip_kb(step: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⏭ Пропустить", callback_data=SkipCb(step=step).pack())]]
    )


def item_prompt(kind: str) -> str:
    if kind == "build":
        return "🏗 Что строишь? Выбери здание (если строишь несколько — самое долгое):"
    return "🔬 Какая ветка исследований? Выбери (если изучаешь несколько — самое долгое):"


def level_prompt(it: gamedata.Item) -> str:
    top = f" (1–{it.max_level})" if it.max_level else ""
    return f"<b>{h(it.ru)}</b> — на какой уровень улучшаешь{top}? Напиши число, например <code>24</code>"


def note_prompt(it: gamedata.Item) -> str:
    if it.kind == "build":
        return "Какое здание и на какой уровень? Напиши коротко, например <code>Склад 18</code>"
    return (
        f"<b>{h(it.ru)}</b> — какое исследование и уровень? Напиши коротко, например "
        "<code>Скорость строительства 7</code>, или нажми «Пропустить»."
    )


def reference_text(svc: Service, it: gamedata.Item | None, level: int | None) -> str:
    """Справочное время для выбранного здания/уровня, если оно известно."""
    if it is None or level is None:
        return ""
    lines = []
    ref = it.time_for(level)
    if ref:
        approx = " ≈ (данные неточные)" if it.approximate else ""
        lines.append(f"📚 Справочно {h(it.ru)} → {level}: <b>{format_duration(ref)}</b> без бонусов{approx}")
    if level in it.requires:
        lines.append(f"Требования: {h(it.requires[level])}")
    for code, lvl, median, count in svc.observed_times(it.code):
        if lvl == level:
            lines.append(f"👥 По союзу: обычно заявляют ≈ {format_duration(median)} ({count} записей)")
    return "\n".join(lines)


def queue_kb(kind: str) -> InlineKeyboardMarkup:
    other = "research" if kind == "build" else "build"
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(
            text=f"{KIND_EMOJI[other]} Показать: {KIND_NAME[other]}",
            callback_data=QueueCb(kind=other).pack(),
        )]]
    )


def target_text(svc: Service, kind: str) -> str:
    lo, hi = svc.setting_float(f"{kind}_min"), svc.setting_float(f"{kind}_max")
    return f"{hi:g} дн." if lo == hi else f"{lo:g}–{hi:g} дн."


def timer_prompt(kind: str, fix: bool, reference: str = "") -> str:
    what = "самой долгой стройки" if kind == "build" else "самого долгого исследования"
    head = "Сколько сейчас осталось" if fix else "Сколько осталось"
    ref = f"{reference}\n\n" if reference else ""
    return (
        f"{ref}⏳ {head} до конца {what}? Посмотри таймер в игре.\n"
        "Напиши, например: <code>21д 5ч</code>, <code>100д</code> или <code>20d 13:45:12</code>\n\n"
        "Если запущено несколько — укажи самое долгое: баф действует сразу на все."
    )


def timer_card(svc: Service, player_id: int, kind: str, now: int) -> str:
    c = svc.timer_candidate(player_id, kind, now)
    if c is None or c.remaining <= 0:
        return f"{KIND_EMOJI[kind]} {KIND_NAME[kind].capitalize()}: не записано."
    rules = svc.rules(kind)
    status = timer_status(c, rules)
    timer = svc.active_timer(player_id, kind)
    what = gamedata.label(timer["item"], timer["level"], timer["note"]) if timer else ""
    lines = [
        f"{KIND_EMOJI[kind]} <b>{KIND_NAME[kind].capitalize()}</b>" + (f": {h(what)}" if what else ""),
        f"Осталось: <b>{format_duration(c.remaining)}</b> (заявлено {format_duration(c.base)})",
        f"Получено бафов: {c.received}",
    ]
    if c.urgent:
        lines.append("🔥 Отмечено как срочное")
    if status == STATUS_NEED:
        lines.append(f"Ещё нужно ≈ <b>{buffs_needed(c.remaining, c.base, rules)}</b> бафов (цель — остаток {target_text(svc, kind)})")
    else:
        lines.append(f"🎯 Цель достигнута — бафы больше не назначаются (цель — остаток {target_text(svc, kind)})")
    return "\n".join(lines)


def assignment_text(a: Assignment, confirm_minutes: float) -> str:
    lines = []
    if a.reused:
        lines.append("ℹ️ У тебя уже есть бронь на этот баф:\n")
    lines += [
        f"🎁 Баф на <b>{KIND_ACC[a.kind]}</b>",
        f"Отдай его игроку: <b>{h(a.recipient_nick)}</b>",
        *([f"Что у него: {h(a.recipient_label)}"] if a.recipient_label else []),
        f"У него осталось: {format_duration(a.remaining)} → после бафа ≈ {format_duration(a.remaining - a.reduction)}",
        f"Почему он: {SLOT_REASON.get(a.slot, a.slot)}",
    ]
    if a.waiting:
        lines.append(f"Без помощи уже: {format_duration(a.waiting)}")
    if a.donor_ready_in:
        lines.append(f"\n⚠️ По моим записям твой баф будет готов только через {format_duration(a.donor_ready_in)}.")
    lines.append(f"\nКак отдашь в игре — нажми «✅ Отдал». Бронь держится {confirm_minutes:g} мин.")
    return "\n".join(lines)


def donor_done_text(r: BuffResult, by_admin: bool = False) -> str:
    who = h(r.donor_nick) if by_admin else "ты"
    lines = [
        f"✅ Записал: {who} отдал баф на {KIND_ACC[r.kind]} игроку <b>{h(r.recipient_nick)}</b> "
        f"(−{format_duration(r.reduction)}).",
    ]
    if r.recipient_left is not None:
        lines.append(f"У него осталось ≈ {format_duration(r.recipient_left)}.")
    if by_admin:
        if r.donor_left is not None:
            lines.append(f"Таймер {h(r.donor_nick)} тоже сократился: осталось ≈ {format_duration(r.donor_left)}.")
        lines.append(f"Следующий баф у {h(r.donor_nick)} — через {r.cooldown_hours:g}ч.")
    else:
        lines[0] += " Спасибо! 🙌"
        if r.donor_left is not None:
            lines.append(f"Твой таймер тоже сократился: осталось ≈ {format_duration(r.donor_left)}.")
        lines.append(f"Следующий баф будет готов через {r.cooldown_hours:g}ч — я напомню.")
    return "\n".join(lines)


def recipient_text(r: BuffResult, svc: Service) -> str:
    lines = [f"🎁 <b>{h(r.donor_nick)}</b> отдал тебе баф на {KIND_ACC[r.kind]} (−{format_duration(r.reduction)})."]
    if r.recipient_left is not None:
        lines.append(f"Осталось ≈ {format_duration(r.recipient_left)}.")
    if r.recipient_reached_target:
        lines.append(f"🎯 Цель достигнута (остаток {target_text(svc, r.kind)}) — дальше бафы получат другие.")
    return "\n".join(lines)


def queue_text(svc: Service, kind: str, now: int, limit: int | None = None) -> str:
    view = svc.queue_view(kind, now)
    need = [r for r in view.rows if r.status == STATUS_NEED]
    target = [r for r in view.rows if r.status == STATUS_TARGET]
    lines = [f"📋 <b>Очередь — {KIND_NAME[kind]}</b> (цель: остаток {target_text(svc, kind)})"]
    if view.next_pick is not None:
        lines.append(f"Следующий баф получит: <b>{h(view.next_pick.nick)}</b> — {SLOT_REASON.get(view.next_slot, '')}")
    lines.append("")
    if need:
        lines.append(f"Нуждаются в бафах ({len(need)}):")
        shown = need if limit is None else need[:limit]
        for i, r in enumerate(shown, 1):
            c = r.candidate
            marks = ("🔥 " if c.urgent else "") + ("⏳ " if r.pending else "")
            offline = "" if c.has_tg else " 📵"
            what = f" · {h(r.label)}" if r.label else ""
            lines.append(
                f"{i}. {marks}{h(c.nick)}{offline} — {format_duration(c.remaining)}{what} · ещё ≈{r.needed} · получил {c.received}"
            )
        if len(shown) < len(need):
            lines.append(f"… и ещё {len(need) - len(shown)}")
    else:
        lines.append("Сейчас никому не нужны бафы 👍")
    if target:
        names = ", ".join(f"{h(r.candidate.nick)} ({format_duration(r.candidate.remaining)})" for r in target)
        lines.append(f"\n🎯 Цель достигнута: {names}")
    lines.append("\n🔥 срочно · ⏳ ждём подтверждения · 📵 нет в боте")
    return "\n".join(lines)


def profile_text(svc: Service, player, now: int) -> str:
    given, received = svc.stats(player["id"])
    lines = [f"👤 <b>{h(player['nick'])}</b>" + (f" (@{h(player['tg_username'])})" if player["tg_username"] else "")]
    if player["is_admin"]:
        lines.append("⭐ Админ")
    lines.append("")
    for kind in KINDS:
        lines.append(timer_card(svc, player["id"], kind, now))
        cd = svc.cooldown(player["id"], kind)
        if cd is None:
            lines.append(f"Твой баф на {KIND_ACC[kind]}: бот пока не знает, когда он готов")
        elif cd["ready_at"] <= now:
            lines.append(f"Твой баф на {KIND_ACC[kind]}: ✅ готов")
        else:
            lines.append(f"Твой баф на {KIND_ACC[kind]}: через {format_duration(cd['ready_at'] - now)}")
        lines.append("")
    lines.append(f"Отдано бафов: {given} · получено: {received}")
    return "\n".join(lines)


def digest_text(svc: Service, now: int, limit: int = 10) -> str:
    parts = ["☀️ <b>Сводка по бафам</b>\n"]
    for kind in KINDS:
        parts.append(queue_text(svc, kind, now, limit=limit).replace("\n🔥 срочно · ⏳ ждём подтверждения · 📵 нет в боте", ""))
        ready = [p for p in svc.ready_donors(kind, now) if p["tg_id"] is not None]
        if ready:
            who = ", ".join(f"@{h(p['tg_username'])}" if p["tg_username"] else h(p["nick"]) for p in ready[:20])
            parts.append(f"🎁 Баф на {KIND_ACC[kind]} уже должен быть готов у: {who}")
        parts.append("")
    parts.append("Отдаёшь баф — сначала спроси бота, кому 👉 кнопка «🎁 Баф…»")
    return "\n".join(parts)


def catalog_text(svc: Service) -> str:
    pp = gamedata.ITEMS["pp"]
    lines = ["📚 <b>Справочник</b>", "", f"🏗 <b>{pp.title}</b> — время улучшения без бонусов:"]
    lines.append(" · ".join(f"{lvl}: {format_duration(pp.time_for(lvl))}" for lvl in range(11, 31)))
    for code in ("bar1", "bar23"):
        it = gamedata.ITEMS[code]
        lines.append(f"\n🏗 <b>{it.title}</b> (≈, данные неточные):")
        lines.append(" · ".join(f"{lvl}: {format_duration(it.time_for(lvl))}" for lvl in range(20, 31)))
    others = [it.ru for it in gamedata.BUILDINGS if not it.times and it.en]
    lines.append(f"\nПо остальным зданиям публичных данных нет: {', '.join(others)}.")
    observed = svc.observed_times()
    if observed:
        lines.append("\n👥 <b>Заявлено игроками союза</b> (медиана):")
        for code, lvl, median, count in observed[:40]:
            it = gamedata.item(code)
            lines.append(f"{h(it.ru if it else code)} → {lvl}: ≈ {format_duration(median)} ({count})")
    lines.append("\n🔬 <b>Ветки исследований:</b> " + ", ".join(it.title for it in gamedata.RESEARCH if it.en))
    lines.append(f"\nИсточник справочных времён: {gamedata.SOURCE}. Твои бонусы к скорости сокращают реальное время.")
    return "\n".join(lines)


HELP = """<b>Как это работает</b>

1️⃣ Запустил стройку или исследование → нажми «🏗 Моя стройка» / «🔬 Моё исследование» и напиши, сколько осталось (самое долгое). Бот поставит тебя в очередь.

2️⃣ Готов твой баф → нажми «🎁 Баф на стройку» / «🎁 Баф на исследование». Бот скажет, кому его отдать. Отдай в игре и нажми «✅ Отдал».
Если игрока не получается найти — «🔁 Другой игрок».

3️⃣ Получил баф — бот пришлёт уведомление и сам пересчитает остаток.

<b>Правила очереди</b>
• Один баф срезает {pct}% от заявленного времени.
• Бафаем до остатка: стройка — {build}, исследование — {research}. Лишних бафов не даём.
• Ротация «{pattern}»: 2 бафа тем, у кого больше всего осталось, 1 — тому, кто дольше всех ждёт. Так помощь получают все.
• Одному игроку — не больше {streak} бафов подряд.
• Админы могут отметить срочное 🔥 — оно идёт вне очереди.

Ускорился сам → «Моя стройка» → «🔧 Поправить остаток».
Закончил или бафы не нужны → «🏁 Завершено».
Сменить ник: <code>/nick Новый ник</code>
Справочник построек и исследований: /catalog"""


def help_text(svc: Service) -> str:
    return HELP.format(
        pct=f"{svc.setting_float('pct'):g}",
        build=target_text(svc, "build"),
        research=target_text(svc, "research"),
        pattern=h(svc.setting("pattern")),
        streak=int(svc.setting_float("max_streak")),
    )


ADMIN_HELP = """<b>Команды админа</b>
Тип: <code>стройка</code> или <code>исследование</code>. Время: <code>21д 5ч</code>.

<b>Таймеры игроков</b> (можно и тех, кого нет в боте — бот создаст игрока; когда человек зарегистрируется с этим ником, таймер привяжется)
/add Ник стройка 21д — новая стройка
/fix Ник стройка 12д 3ч — поправить остаток
/close Ник стройка — закрыть
/urgent Ник стройка — включить/выключить 🔥 срочно

<b>Бафы</b>
/assign Ник стройка — кому отдать баф игроку Ник (если его нет в боте)
/gave Вася &gt; Мура стройка — записать уже отданный баф

<b>Игроки</b>
/players — список
/delplayer Ник — удалить игрока
/admins — список админов
/admin_add @username — сделать админом (только владелец)
/admin_del @username — снять админа (только владелец)

<b>Прочее</b>
/settings — настройки · /set ключ значение
/digest — отправить сводку в группу сейчас
/myid — твой Telegram ID"""
