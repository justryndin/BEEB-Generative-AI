"""«Мои задачи на сегодня»: что сделать этому персонажу прямо сейчас — собирается само.

Задача пропадает, как только сделана (отдал баф, ответил на опрос, сверил время…).
Тексты — по-русски; время — МСК и UTC.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from . import crm, polls, powerplay, vs
from .service import KIND_ACC, KIND_NAME, KINDS, Service
from .timeparse import DAY, HOUR, format_duration


@dataclass
class Todo:
    key: str
    icon: str  # имя иконки из спрайта
    title: str
    sub: str = ""
    url: str = ""
    action: str = ""  # подпись кнопки
    urgent: bool = False


@dataclass
class Today:
    todo: list[Todo]  # сделать
    game: list[Todo]  # что идёт в игре сейчас (подсказки, не отмечаются)
    done: list[str]  # что уже сделано сегодня


def _both(ts: int, tz) -> str:
    return (datetime.fromtimestamp(ts, tz).strftime("%H:%M") + " МСК · "
            + datetime.fromtimestamp(ts, timezone.utc).strftime("%H:%M") + " UTC")


def build(svc: Service, me, now: int, tz, has_push: bool) -> Today:
    pid = me["id"]
    todo: list[Todo] = []
    done: list[str] = []
    day_start = now // DAY * DAY

    if not me["agreed_at"]:
        todo.append(Todo("rules", "file", "Прими условия очереди", "Без этого нельзя вставать в очередь и отмечать бафы.",
                         "/rules", "Открыть", True))

    # Бафы: отдать готовый — первому в очереди (или по рулетке).
    for kind in KINDS:
        given = svc.db.one(
            "SELECT 1 FROM donations WHERE donor_id = ? AND kind = ? AND status = 'done' AND resolved_at >= ?",
            pid, kind, day_start,
        )
        if given:
            done.append(f"Отдал баф на {KIND_ACC[kind]}")
        cd = svc.cooldown(pid, kind)
        if cd is not None and cd["ready_at"] > now:
            continue
        target = svc.give_target(kind, pid, now)
        if target is None:
            continue
        nick, _, spin = target
        how = "по рулетке — очередь пуста" if spin else "первый в очереди"
        if cd is None:
            todo.append(Todo(f"give:{kind}", "gift", f"Баф на {KIND_ACC[kind]} готов? Отдай: {nick}",
                             f"{nick} — {how}. Отдал в игре — отметь на сайте.", f"/give?kind={kind}", "Отдал"))
        else:
            todo.append(Todo(f"give:{kind}", "gift", f"Отдай баф на {KIND_ACC[kind]}: {nick}",
                             f"Баф готов, {nick} — {how}. Отдал в игре — отметь на сайте.", f"/give?kind={kind}", "Отдал", True))

    # Сверить время и встать со следующей.
    for ch in svc.time_checks(pid, now):
        why = "тебе отдали баф" if ch["why"] == "buff" else "давно не сверял"
        todo.append(Todo(f"check:{ch['kind']}", "clock", f"Сверь время: {KIND_NAME[ch['kind']]}",
                         f"На сайте осталось {format_duration(ch['remaining'])} — {why}.", "/#checks", "Сверить"))
    for f in svc.finished_timers(pid, now):
        todo.append(Todo(f"next:{f['kind']}", "plus", f"{KIND_NAME[f['kind']].capitalize()} закончилось — запусти следующее",
                         "И встань в очередь, пока таймер длинный.", "/join/" + f["kind"], "Встать"))

    # Опросы и отметки «Буду / Не смогу».
    for p in polls.unanswered(svc, me, now):
        sub = f"до {_both(p['closes_at'], tz)}" if p["closes_at"] else "Ответ в один клик."
        todo.append(Todo(f"poll:{p['id']}", "poll", f"Ответь на опрос: {p['title']}", sub, f"/polls#p{p['id']}", "Ответить",
                         bool(p["closes_at"] and p["closes_at"] - now < DAY)))
    for post in crm.posts(svc, me, limit=20):
        if post["rsvp"] and post["created_at"] >= now - 7 * DAY and not svc.db.one(
            "SELECT 1 FROM answers WHERE ref = ? AND player_id = ?", f"post:{post['id']}", pid,
        ):
            first = post["text"].strip().splitlines()[0][:80]
            todo.append(Todo(f"rsvp:{post['id']}", "msg", "Отметься: будешь или нет", first, f"/board#post{post['id']}", "Отметиться"))
    if svc.db.one("SELECT 1 FROM poll_votes WHERE player_id = ? AND updated_at >= ?", pid, day_start):
        done.append("Ответил на опрос")

    # Профиль: то, без чего сайт помогает хуже.
    if me["pp_level"] is None:
        todo.append(Todo("pp", "zap", "Укажи уровень Электростанции", "Сайт посчитает путь до 30 и даст приоритет в очереди.",
                         "/me#pp", "Указать"))
    if not has_push:
        todo.append(Todo("push", "bell", "Включи уведомления", "Сайт сам скажет, когда твой баф готов и кому его отдать.",
                         "/me#notify", "Включить"))

    # Что идёт в игре сейчас — подсказки.
    game: list[Todo] = []
    today = vs.today(now)
    if today.day:
        game.append(Todo("vs", "swords", f"Дуэль, день {today.day.num}: {today.day.theme}", today.day.tasks[0].text, "/#vs"))
    cur, nxt = powerplay.now_and_next(now)
    match = cur.theme.code in powerplay.DUEL_MATCH.get(powerplay.weekday(now), ())
    game.append(Todo("pp-now", "dice", f"Игра по-крупному: {cur.theme.name}" + (" — совпадает с Дуэлью" if match else ""),
                     f"до {_both(cur.end, tz)} · трать {cur.theme.spend}", "/guides/week#pp", urgent=match))
    for o in crm.occurrences(svc, now, 1):
        if o.event["duration"] >= 1440:
            continue
        if o.ongoing(now):
            game.append(Todo(o.ref, "cal", f"Идёт: {o.event['title']}", o.event["prepare"][:100], "/events", urgent=True))
        elif o.start - now <= 3 * HOUR:
            game.append(Todo(o.ref, "cal", f"Через {format_duration(o.start - now)}: {o.event['title']}",
                             f"{_both(o.start, tz)} · {o.event['prepare'][:80]}", "/events"))
    todo.sort(key=lambda x: not x.urgent)
    return Today(todo, game, done)

