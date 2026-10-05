"""Что и кому прислать на телефон. Здесь только решения — отправка в web/push.py.

Каждое уведомление имеет ключ: одно и то же событие по одному ключу приходит один раз.
Ночью (тихие часы) ничего не шлём — события дождутся утра, если ещё актуальны.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from . import crm, gamedata, i18n
from .i18n import t as tr
from .tips import player_tip
from .service import KIND_ACC, KIND_NAME, KINDS, Service
from .timeparse import DAY, HOUR, format_duration

# Виды уведомлений: код → как называется в настройках игрока.
NOTICE_KINDS = {
    "ready": "Мой баф готов — кому отдать",
    "got": "Мне отдали баф — сверить время",
    "next": "Я следующий в очереди",
    "done": "Стройка или исследование закончились — встать со следующей",
    "check": "Сверить время с игрой (раз в день и после бафа)",
    "tips": "Совет дня — один полезный совет по игре в день",
    "news": "Важные объявления R4",
    "events": "События союза: напоминание перед началом",
}
GOT_WINDOW = DAY  # о полученном бафе напоминаем, если он был не раньше суток назад


@dataclass
class Notice:
    player_id: int
    key: str
    kind: str
    title: str
    body: str
    url: str = "/"


def prefs(player) -> dict:
    """{"off": [виды], "quiet": True}. По умолчанию всё включено и ночью тихо."""
    try:
        data = json.loads(player["notify_prefs"] or "{}")
    except (TypeError, ValueError):
        data = {}
    return {"off": list(data.get("off", [])), "quiet": bool(data.get("quiet", True))}


def is_quiet(svc: Service, tz: ZoneInfo, now: int) -> bool:
    start, end = int(svc.setting_float("quiet_from")), int(svc.setting_float("quiet_to"))
    hour = datetime.fromtimestamp(now, tz).hour
    if start == end:
        return False
    return start <= hour < end if start < end else hour >= start or hour < end


def due_notices(svc: Service, tz: ZoneInfo, now: int) -> list[Notice]:
    """Все уведомления, которые пора отправить подписанным игрокам."""
    subscribed = {r["player_id"] for r in svc.db.all("SELECT DISTINCT player_id FROM push_subs")}
    if not subscribed:
        return []
    quiet = is_quiet(svc, tz, now)
    orders = {k: svc.queue_order(k, now) for k in KINDS}
    first = {k: next((r for r in orders[k] if r.need), None) for k in KINDS}
    holding = svc.holding_map(now)
    hold_limit = svc.setting_float("hold_hours") * HOUR
    out: list[Notice] = []
    news = svc.db.all("SELECT * FROM posts WHERE important = 1 AND created_at >= ?", now - 3 * DAY)
    upcoming = [
        o for o in crm.occurrences(svc, now, 1)
        if o.event["remind_min"] >= 0 and now >= o.start - o.event["remind_min"] * 60
    ]

    for pid in subscribed:
        player = svc.player(pid)
        if player is None:
            continue
        p = prefs(player)
        if quiet and p["quiet"]:
            continue
        with i18n.using(player["lang"] or i18n.DEFAULT):  # каждому — на его языке
            mine: list[Notice] = []

            # 1. Баф готов — отдай первому в очереди (не себе).
            for kind in KINDS:
                target = next((r for r in orders[kind] if r.need and r.candidate.player_id != pid), None)
                spin = None
                if target is None and svc.roulette_on(kind):
                    spin = next(iter(svc.roulette_order(kind, now, exclude=pid)), None)
                if target is None and spin is None:
                    continue
                cd = svc.cooldown(pid, kind)
                if cd is not None and cd["ready_at"] > now:
                    continue
                stamp = cd["ready_at"] if cd is not None else f"d{now // DAY}"
                if cd is None and not svc.active_timer(pid, kind) and not svc.active_timer(pid, _other(kind)):
                    continue  # не знаем, играет ли он сейчас, — не тревожим каждый день
                nick = target.candidate.nick if target is not None else spin.nick
                body = (tr("Отдай его {nick} — он первый в очереди. Потом нажми «Я отдал».", nick=nick) if target is not None else
                        tr("Очередь пуста — по рулетке выпал {nick}. Отдай ему и нажми «Я отдал».", nick=nick))
                mine.append(Notice(pid, f"ready:{kind}:{stamp}", "ready", tr("🎁 Баф на {kind} готов", kind=tr(KIND_ACC[kind])), body,
                                   f"/give?kind={kind}"))
                if pid in holding and holding[pid][0] == kind and hold_limit > 0:
                    mine.append(Notice(pid, f"hold:{kind}:{stamp}", "ready",
                                       tr("⏳ Ты держишь готовый баф"),
                                       tr("Больше {time} — пока не отдашь, свой ход пропускаешь. Отдай {nick}.", time=format_duration(int(hold_limit)), nick=nick),
                                       f"/give?kind={kind}"))

            # 2. Мне отдали баф — сверь время.
            for d in svc.db.all(
                "SELECT d.*, p.nick AS donor_nick FROM donations d LEFT JOIN players p ON p.id = d.donor_id "
                "WHERE d.recipient_id = ? AND d.status = 'done' AND d.resolved_at >= ?", pid, now - GOT_WINDOW,
            ):
                mine.append(Notice(pid, f"got:{d['id']}", "got",
                                   tr("🎁 {donor} отдал тебе баф на {kind}", donor=d["donor_nick"] or tr("Кто-то"), kind=tr(KIND_ACC[d["kind"]])),
                                   (tr("−{time}. Сверь время с игрой — одна кнопка на сайте.", time=format_duration(d["reduction"]))
                                    if d["reduction"] else tr("🎲 По рулетке: очередь была пуста. Баф срежет время твоих строек.")),
                                   "/"))

            # 3. Я следующий.
            for kind in KINDS:
                r = first[kind]
                if r is not None and r.candidate.player_id == pid and not r.paused_for:
                    timer = svc.active_timer(pid, kind)
                    mine.append(Notice(pid, f"next:{kind}:{timer['id']}:{r.candidate.received}", "next",
                                       tr("👉 Ты следующий на баф ({kind})", kind=tr(KIND_NAME[kind])),
                                       tr("Держи стройку запущенной — баф придёт в ближайшее время.") if kind == "build"
                                       else tr("Держи исследование запущенным — баф придёт в ближайшее время."),
                                       "/"))

            # 4. Сверь время: в игре каждый день ускоряются — сайт этого не видит.
            for ch in svc.time_checks(pid, now):
                if ch["why"] == "stale":
                    t = ch["timer"]
                    mine.append(Notice(pid, f"check:{t['id']}:{t['checked_at'] or t['created_at']}", "check",
                                       tr("🔁 Сверь время: {kind}", kind=tr(KIND_NAME[ch["kind"]])),
                                       tr("На сайте осталось {time}. Ускорялся? Впиши, сколько в игре, — одна кнопка.", time=format_duration(ch["remaining"])),
                                       "/"))

            # 5. Закончилось — встань со следующей.
            for f in svc.finished_timers(pid, now):
                t = f["timer"]
                what = gamedata.label(t["item"], t["level"], t["note"]) or tr(KIND_NAME[f["kind"]]).capitalize()
                nxt = tr(" Следующая: {item} → {level}.", item=f["item"].name, level=f["next_level"]) if f["next_level"] else ""
                mine.append(Notice(pid, f"done:{t['id']}", "done", tr("🏁 {what} — закончилось", what=what),
                                   tr("Запустил следующее? Встань в очередь в одно нажатие.") + nxt, "/"))

            # 6. Совет дня — один раз в день, днём (11:00–20:00 по местному времени).
            local = datetime.fromtimestamp(now, tz)
            if 11 <= local.hour < 20:
                tip = player_tip(svc, player, now, tz)
                mine.append(Notice(pid, f"tip:{local:%Y-%m-%d}", "tips", tr("💡 Совет дня"), tr(tip.text), tip.link or "/"))

            # 7. Важные объявления R4.
            for post in news:
                if post["author_id"] != pid:
                    first_line = post["text"].strip().splitlines()[0][:120]
                    mine.append(Notice(pid, f"post:{post['id']}", "news", tr("📣 Объявление союза"), first_line, "/board"))

            # 8. События союза — напоминание перед началом.
            for o in upcoming:
                when = (datetime.fromtimestamp(o.start, tz).strftime("%H:%M") + " " + tr("МСК") + " · "
                        + datetime.fromtimestamp(o.start, timezone.utc).strftime("%H:%M") + " UTC")
                head = tr("идёт сейчас") if o.ongoing(now) else tr("начало в {when}", when=when)
                mine.append(Notice(pid, o.ref, "events", f"📅 {tr(o.event['title'])} — {head}",
                                   tr(o.event["prepare"])[:160] or tr("Подробности — в календаре союза."), "/events"))

            out += [n for n in mine if n.kind not in p["off"]]
    return out


def _other(kind: str) -> str:
    return "research" if kind == "build" else "build"
