"""Сайт очереди бафов союза (FastAPI + серверные шаблоны, почти без JavaScript)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, unquote

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from core import gamedata
from core.db import Database
from core.forecast import VIRTUAL_ID
from core.logic import STATUS_NEED, buff_reduction, buffs_needed, timer_status
from core.service import KIND_ACC, KIND_EMOJI, KIND_NAME, KINDS, SETTINGS, Service, clean_nick, valid_pin
from core.timeparse import DAY, HOUR, MINUTE, format_duration

from . import charts
from .config import Config, load_config

log = logging.getLogger(__name__)
BASE = Path(__file__).parent

SLOT_REASON = {
    "B": "у него самый большой остаток",
    "W": "он дольше всех ждёт помощи",
    "U": "🔥 срочная помощь (отметил админ)",
    "M": "записано админом",
}


def now() -> int:
    return int(time.time())


def create_app(cfg: Config | None = None, svc: Service | None = None) -> FastAPI:
    cfg = cfg or load_config()
    svc = svc or Service(Database(cfg.db_path))
    max_age = cfg.session_days * DAY

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        async def housekeeping():
            while True:
                try:
                    t = now()
                    svc.expire_pending(t)
                    svc.deactivate_finished(t)
                except Exception:
                    log.exception("Ошибка фоновой задачи")
                await asyncio.sleep(60)

        task = asyncio.create_task(housekeeping())
        yield
        task.cancel()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
    templates = Jinja2Templates(directory=BASE / "templates")
    app.state.svc, app.state.cfg = svc, cfg

    # ---------- помощники шаблонов ----------

    def local(ts: int | None, fmt: str = "%d.%m %H:%M") -> str:
        return datetime.fromtimestamp(ts, cfg.tz).strftime(fmt) if ts else "—"

    def target_text(kind: str) -> str:
        lo, hi = svc.setting_float(f"{kind}_min"), svc.setting_float(f"{kind}_max")
        return f"{hi:g} дн." if lo == hi else f"{lo:g}–{hi:g} дн."

    def pattern_text(pattern: str) -> str:
        big, wait = pattern.count("B"), pattern.count("W")
        return f"{big} : {wait}"

    templates.env.globals.update(
        dur=format_duration,
        local=local,
        KIND_NAME=KIND_NAME,
        KIND_ACC=KIND_ACC,
        KIND_EMOJI=KIND_EMOJI,
        KINDS=KINDS,
        SLOT_REASON=SLOT_REASON,
        target_text=target_text,
        pattern_text=pattern_text,
        site_name=cfg.site_name,
        gamedata=gamedata,
    )

    def current(request: Request):
        return svc.session(request.cookies.get("sid"), now(), max_age)

    def render(request: Request, name: str, me=None, status_code: int = 200, **ctx) -> HTMLResponse:
        flash = unquote(request.cookies.get("flash", ""))
        resp = templates.TemplateResponse(
            request,
            name,
            {
                "me": me,
                "is_admin": svc.is_admin_player(me),
                "csrf": me["csrf"] if me else "",
                "flash": flash,
                "path": request.url.path,
                "refresh_seconds": int(svc.setting_float("refresh_minutes") * 60),
                "updated_at": local(now(), "%H:%M"),
                **ctx,
            },
            status_code=status_code,
        )
        if flash:
            resp.delete_cookie("flash")
        return resp

    def go(url: str, flash: str | None = None) -> RedirectResponse:
        resp = RedirectResponse(url, status_code=303)
        if flash:
            resp.set_cookie("flash", quote(flash), max_age=60, httponly=True, samesite="lax", secure=cfg.secure_cookies)
        return resp

    def need_login(request: Request):
        me = current(request)
        if me is None:
            raise HTTPException(status_code=303, headers={"Location": "/login"})
        return me

    def need_admin(request: Request):
        me = need_login(request)
        if not svc.is_admin_player(me):
            raise HTTPException(status_code=403, detail="Только для админов")
        return me

    def check_csrf(me, token: str) -> None:
        if not me or token != me["csrf"]:
            raise HTTPException(status_code=400, detail="Страница устарела — обнови её и попробуй ещё раз")

    def parse_time(days: str, hours: str, minutes: str) -> int | None:
        try:
            d, h, m = (int(x or 0) for x in (days, hours, minutes))
        except ValueError:
            return None
        if min(d, h, m) < 0 or h > 23 or m > 59:
            return None
        total = d * DAY + h * HOUR + m * MINUTE
        return total if 0 < total <= 400 * DAY else None

    def my_timers(player_id: int, t: int) -> dict:
        out = {}
        for kind in KINDS:
            timer = svc.active_timer(player_id, kind)
            c = svc.timer_candidate(player_id, kind, t)
            if timer is None or c is None or c.remaining <= 0:
                out[kind] = None
                continue
            rules = svc.rules(kind)
            out[kind] = {
                "c": c,
                "label": gamedata.label(timer["item"], timer["level"], timer["note"]),
                "status": timer_status(c, rules),
                "needed": buffs_needed(c.remaining, c.base, rules),
                "position": queue_position(kind, player_id, t),
                "paused_for": max(0, c.paused_until(rules) - t),
            }
        return out

    def queue_position(kind: str, player_id: int, t: int) -> int | None:
        view = svc.queue_view(kind, t)
        need = [r for r in view.rows if r.status == STATUS_NEED]
        for i, r in enumerate(need, 1):
            if r.candidate.player_id == player_id:
                return i
        return None

    def buff_state(player_id: int, kind: str, t: int) -> tuple[str, int]:
        cd = svc.cooldown(player_id, kind)
        if cd is None:
            return "unknown", 0
        if cd["ready_at"] <= t:
            return "ready", 0
        return "wait", cd["ready_at"] - t

    # ---------- вход и регистрация ----------

    @app.get("/login", response_class=HTMLResponse)
    def login_page(request: Request):
        if current(request):
            return go("/")
        return render(request, "login.html")

    @app.post("/login")
    def login(request: Request, nick: str = Form(""), pin: str = Form("")):
        player, error = svc.login(clean_nick(nick), pin.strip(), now())
        if error:
            messages = {
                "unknown": "Такого ника нет. Проверь написание или зарегистрируйся.",
                "nopin": "Этот ник ещё без PIN-кода — пройди регистрацию с этим ником.",
                "locked": "Слишком много неверных попыток. Подожди 15 минут или попроси админа сбросить PIN.",
                "wrong": "Неверный PIN-код.",
            }
            return render(request, "login.html", error=messages[error], nick=nick, status_code=400)
        token, _ = svc.create_session(player["id"], now())
        resp = go("/")
        resp.set_cookie("sid", token, max_age=max_age, httponly=True, samesite="lax", secure=cfg.secure_cookies)
        return resp

    @app.get("/register", response_class=HTMLResponse)
    def register_page(request: Request):
        if current(request):
            return go("/")
        return render(request, "register.html", need_code=bool(svc.setting("alliance_code")))

    @app.post("/register")
    def register(request: Request, nick: str = Form(""), pin: str = Form(""), pin2: str = Form(""),
                 code: str = Form(""), agree: str = Form("")):
        nick, pin = clean_nick(nick), pin.strip()
        need_code = bool(svc.setting("alliance_code"))
        error = None
        if not 2 <= len(nick) <= 32:
            error = "Ник должен быть от 2 до 32 символов."
        elif not valid_pin(pin):
            error = "PIN-код — ровно 4 цифры."
        elif pin != pin2.strip():
            error = "PIN-коды не совпадают."
        elif need_code and code.strip().casefold() != svc.setting("alliance_code").strip().casefold():
            error = "Неверный код союза. Спроси его у руководства союза."
        elif not agree:
            error = "Нужно согласиться с условиями очереди."
        player = None
        if error is None:
            player, reg_error = svc.register_web(nick, pin, now())
            if reg_error == "taken":
                error = "Этот ник уже зарегистрирован. Если это твой ник — попроси админа сбросить PIN."
        if error:
            return render(request, "register.html", error=error, nick=nick, need_code=need_code, status_code=400)
        svc.agree(player["id"], now())
        token, _ = svc.create_session(player["id"], now())
        resp = go("/", f"Добро пожаловать, {player['nick']}! ✅")
        resp.set_cookie("sid", token, max_age=max_age, httponly=True, samesite="lax", secure=cfg.secure_cookies)
        return resp

    @app.post("/logout")
    def logout(request: Request, csrf: str = Form("")):
        me = current(request)
        if me:
            check_csrf(me, csrf)
            svc.drop_session(request.cookies.get("sid"))
        resp = go("/login", "Ты вышел. До встречи! 👋")
        resp.delete_cookie("sid")
        return resp

    # ---------- главная ----------

    def eta(seconds) -> str:
        if seconds is None:
            return "больше 4 мес."
        if seconds < 10 * MINUTE:
            return "сейчас"
        return "через " + format_duration(seconds)

    def eta_short(seconds) -> str:
        if seconds is None:
            return "—"
        if seconds < 10 * MINUTE:
            return "сейчас"
        return "~" + format_duration(seconds)

    templates.env.globals.update(eta=eta, eta_short=eta_short)

    def board(kind: str, t: int) -> dict:
        """Очередь + прогноз: кто следующий получит бафы и когда все получат своё."""
        fc = svc.forecast(kind, t)
        view = svc.queue_view(kind, t)
        rows = []
        for r in view.rows:
            st = fc.timers.get(r.candidate.player_id)
            rows.append({
                "r": r,
                "will_get": len(st.gets) if st else 0,
                "done_in": int(st.done_at - t) if st and st.done_at is not None else None,
                "left": st.left_at_done if st else None,
            })
        nxt = [
            {"n": i + 1, "nick": e.nick, "pid": e.pid, "in": int(e.at - t), "at": int(e.at)}
            for i, e in enumerate(fc.events[:30])
        ]
        return {
            "fc": fc,
            "next": nxt,
            "rows": rows,
            "need": sum(1 for r in view.rows if r.status == STATUS_NEED),
            "per_day": fc.buffs_per_day,
        }

    def my_forecast(fc, pid: int, t: int) -> dict | None:
        st = fc.timers.get(pid)
        if st is None:
            return None
        return {
            "remaining": int(st.start_remaining),
            "gets": [(int(at - t), cut) for at, cut in st.gets],
            "self_n": len(st.self_cuts),
            "self_sum": sum(c for _, c in st.self_cuts),
            "done_in": int(st.done_at - t) if st.done_at is not None else None,
            "left": int(st.left_at_done) if st.left_at_done is not None else None,
        }

    def calc(kind: str, days: float, t: int, donates: bool = True, donors: int | None = None,
             replace_pid: int | None = None) -> dict:
        seconds = int(days * DAY)
        rules = svc.rules(kind)
        fc = svc.forecast(kind, t, virtual_seconds=seconds, virtual_donates=donates,
                          replace_pid=replace_pid, donors=donors)
        mine = my_forecast(fc, VIRTUAL_ID, t)
        return {
            "days": days,
            "ideal": buffs_needed(seconds, seconds, rules),
            "per_buff": buff_reduction(seconds, seconds, rules),
            "per_day": fc.buffs_per_day,
            **mine,
        }

    def need_agreed(me):
        if not me["agreed_at"]:
            raise HTTPException(status_code=303, headers={"Location": "/rules?need=1"})

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request):
        me = current(request)
        t = now()
        if me is None:
            return render(request, "landing.html", totals=svc.totals(), queue_counts={
                k: sum(1 for r in svc.queue_view(k, t).rows if r.status == STATUS_NEED) for k in KINDS
            })
        received = svc.received_since(me["id"], me["last_seen_at"] or t)
        svc.touch_seen(me["id"], t)
        boards = {k: board(k, t) for k in KINDS}
        return render(
            request,
            "home.html",
            me,
            boards=boards,
            timers=my_timers(me["id"], t),
            mine={k: my_forecast(boards[k]["fc"], me["id"], t) for k in KINDS},
            buffs={k: buff_state(me["id"], k, t) for k in KINDS},
            received=received,
            incoming=svc.incoming_pending(me["id"]),
            pending=svc.pending_by_requester(me["id"]),
            presets={k: [calc(k, d, t, replace_pid=me["id"]) for d in (10, 20, 30, 40)] for k in KINDS},
            donors=svc.donor_count(),
            cooldown=svc.setting_float("cooldown_hours"),
        )

    # ---------- условия и калькулятор ----------

    @app.get("/rules", response_class=HTMLResponse)
    def rules_page(request: Request, need: int = 0):
        me = current(request)
        return render(
            request, "rules.html", me,
            need=need,
            pct=svc.setting_float("pct"),
            pattern=svc.setting("pattern"),
            streak=int(svc.setting_float("max_streak")),
            cooldown=svc.setting_float("cooldown_hours"),
            confirm=svc.setting_float("confirm_minutes"),
            gap=svc.setting_float("min_gap_hours"),
            fair=svc.setting_float("fair_round") > 0,
        )

    @app.post("/rules/agree")
    def rules_agree(request: Request, csrf: str = Form("")):
        me = need_login(request)
        check_csrf(me, csrf)
        svc.agree(me["id"], now())
        return go("/", "✅ Спасибо! Условия приняты — теперь можно вставать в очередь и отдавать бафы.")

    @app.get("/calc", response_class=HTMLResponse)
    def calculator(request: Request, days: float = 0, hours: int = 0, kind: str = "research",
                   donates: int = 1, donors: int = 0):
        me = need_login(request)
        t = now()
        kind = kind if kind in KINDS else "research"
        total = svc.donor_count()
        donors = donors if 0 < donors <= 500 else total
        days_total = max(0.0, min(400.0, days + hours / 24))
        result = calc(kind, days_total, t, bool(donates), donors, me["id"]) if days_total > 0 else None
        return render(
            request, "calc.html", me,
            kind=kind, days=days, hours=hours, donates=donates, donors=donors, total_donors=total,
            result=result,
            presets={k: [calc(k, d, t, bool(donates), donors, me["id"]) for d in (10, 20, 30, 40)] for k in KINDS},
            cooldown=svc.setting_float("cooldown_hours"),
        )

    # ---------- очередь ----------

    @app.get("/queue", response_class=HTMLResponse)
    def queue(request: Request, kind: str = "build"):
        me = need_login(request)
        if kind not in KINDS:
            kind = "build"
        view = svc.queue_view(kind, now())
        return render(
            request,
            "queue.html",
            me,
            kind=kind,
            view=view,
            need=[r for r in view.rows if r.status == STATUS_NEED],
            reached=[r for r in view.rows if r.status != STATUS_NEED],
        )

    # ---------- встать в очередь: здание → уровень → время ----------

    def join_target(request: Request, me, for_id: int | None):
        """Админ может записывать таймер за другого игрока."""
        if for_id and for_id != me["id"]:
            if not svc.is_admin_player(me):
                raise HTTPException(status_code=403)
            target = svc.player(for_id)
            if target is None:
                raise HTTPException(status_code=404, detail="Игрок не найден")
            return target
        return me

    @app.get("/join/{kind}", response_class=HTMLResponse)
    def join(request: Request, kind: str, item: str = "", level: int = 0, fix: int = 0, player: int = 0):
        me = need_login(request)
        need_agreed(me)
        if kind not in KINDS:
            raise HTTPException(status_code=404)
        target = join_target(request, me, player)
        it = gamedata.item(item)
        if fix:
            timer = svc.active_timer(target["id"], kind)
            if timer is None:
                return go(f"/join/{kind}" + (f"?player={player}" if player else ""))
            it = gamedata.item(timer["item"])
            level = timer["level"] or 0
            step = "time"
        elif it is None or it.kind != kind:
            step = "item"
        elif it.kind == "build" and it.max_level and not level:
            step = "level"
        else:
            step = "time"
        return render(
            request,
            "join.html",
            me,
            kind=kind,
            step=step,
            it=it,
            level=level,
            fix=fix,
            target=target,
            for_other=target["id"] != me["id"],
            reference=(it.time_for(level) if it and level else None),
            requires=(it.requires.get(level) if it and level else None),
            observed=[o for o in svc.observed_times(it.code) if o[1] == level] if it and level else [],
            total_steps=3 if kind == "build" else 2,
        )

    @app.post("/join/{kind}")
    def join_submit(request: Request, kind: str, csrf: str = Form(""), item: str = Form(""), level: int = Form(0),
                    fix: int = Form(0), player: int = Form(0), days: str = Form("0"), hours: str = Form("0"),
                    minutes: str = Form("0")):
        me = need_login(request)
        check_csrf(me, csrf)
        need_agreed(me)
        if kind not in KINDS:
            raise HTTPException(status_code=404)
        target = join_target(request, me, player)
        seconds = parse_time(days, hours, minutes)
        back = f"/join/{kind}?item={item}&level={level}&fix={fix}" + (f"&player={player}" if player else "")
        if seconds is None:
            return go(back, "⚠️ Укажи время: сколько дней, часов и минут осталось по таймеру в игре.")
        it = gamedata.item(item)
        svc.set_timer(
            target["id"], kind, seconds, now(),
            keep_base=bool(fix),
            item=None if fix else (it.code if it else None),
            level=None if fix else (level or None),
        )
        ref = it.time_for(level) if it and level else None
        warn = f" ⚠️ Это намного больше справочного времени ({format_duration(ref)}) — проверь таймер." if ref and seconds > ref * 1.5 else ""
        if target["id"] != me["id"]:
            return go(f"/admin/p/{target['id']}", f"✅ Записал игрока {target['nick']}: осталось {format_duration(seconds)}.{warn}")
        text = "✅ Остаток обновлён." if fix else "✅ Ты в очереди! Когда тебе отдадут баф — увидишь это на главной."
        return go("/", text + warn)

    @app.post("/timer/{kind}/close")
    def timer_close(request: Request, kind: str, csrf: str = Form("")):
        me = need_login(request)
        check_csrf(me, csrf)
        svc.close_timer(me["id"], kind)
        return go("/", f"🏁 Готово — ты убран из очереди ({KIND_NAME.get(kind, '')}).")

    # ---------- отдать баф ----------

    @app.post("/give/{kind}")
    def give(request: Request, kind: str, csrf: str = Form("")):
        me = need_login(request)
        check_csrf(me, csrf)
        need_agreed(me)
        if kind not in KINDS:
            raise HTTPException(status_code=404)
        a = svc.assign(kind, me["id"], me["id"], now())
        if a is None:
            return go(f"/queue?kind={kind}", f"👍 Сейчас никому не нужен баф на {KIND_ACC[kind]}. Придержи его и загляни позже.")
        return go(f"/give/d/{a.donation_id}")

    def own_donation(me, donation_id: int):
        d = svc.donation(donation_id)
        if d is None:
            raise HTTPException(status_code=404, detail="Бронь не найдена")
        if d["requested_by"] != me["id"] and not svc.is_admin_player(me):
            raise HTTPException(status_code=403, detail="Это не твоя бронь")
        return d

    @app.get("/give/d/{donation_id}", response_class=HTMLResponse)
    def give_page(request: Request, donation_id: int):
        me = need_login(request)
        d = own_donation(me, donation_id)
        a = svc._assignment(d, now(), reused=False) if d["status"] == "pending" else None
        donor = svc.player(d["donor_id"])
        return render(
            request,
            "give.html",
            me,
            d=d,
            a=a,
            donor=donor,
            for_other=d["donor_id"] != me["id"],
            confirm_minutes=svc.setting_float("confirm_minutes"),
            expires_at=d["created_at"] + int(svc.setting_float("confirm_minutes") * MINUTE),
        )

    @app.post("/give/d/{donation_id}/{action}")
    def give_action(request: Request, donation_id: int, action: str, csrf: str = Form("")):
        me = need_login(request)
        check_csrf(me, csrf)
        d = own_donation(me, donation_id)
        if d["status"] != "pending":
            return go("/", "Эта бронь уже закрыта.")
        if action == "ok":
            r = svc.confirm(donation_id, now())
            if r is None:
                return go("/", "Эта бронь уже закрыта.")
            who = "Ты" if r.donor_id == me["id"] else r.donor_nick
            return go("/", f"✅ Записал: {who} отдал баф игроку {r.recipient_nick} (−{format_duration(r.reduction)}). Спасибо! 🙌")
        if action == "other":
            a = svc.reassign(donation_id, me["id"], now())
            if a is None:
                return go(f"/queue?kind={d['kind']}", "Больше некому отдать этот баф 🤷 Придержи его и загляни позже.")
            return go(f"/give/d/{a.donation_id}")
        svc.cancel(donation_id, now())
        return go("/", "Отменено. Баф остаётся у тебя 👌")

    # ---------- профиль ----------

    @app.get("/me", response_class=HTMLResponse)
    def profile(request: Request):
        me = need_login(request)
        t = now()
        given, received = svc.stats(me["id"])
        saved = svc.db.one(
            "SELECT COALESCE(SUM(reduction), 0) AS s FROM donations WHERE donor_id = ? AND status = 'done'", me["id"]
        )["s"]
        return render(
            request, "me.html", me,
            timers=my_timers(me["id"], t),
            buffs={k: buff_state(me["id"], k, t) for k in KINDS},
            given=given, received=received, saved=saved,
        )

    @app.post("/me/nick")
    def change_nick(request: Request, csrf: str = Form(""), nick: str = Form("")):
        me = need_login(request)
        check_csrf(me, csrf)
        nick = clean_nick(nick)
        if not 2 <= len(nick) <= 32:
            return go("/me", "⚠️ Ник должен быть от 2 до 32 символов.")
        if svc.rename(me["id"], nick) == "taken":
            return go("/me", "⚠️ Этот ник уже занят другим игроком.")
        return go("/me", f"✅ Ник изменён на {nick}.")

    @app.post("/me/pin")
    def change_pin(request: Request, csrf: str = Form(""), old: str = Form(""), pin: str = Form(""),
                   pin2: str = Form("")):
        me = need_login(request)
        check_csrf(me, csrf)
        _, error = svc.login(me["nick"], old.strip(), now())
        if error:
            return go("/me", "⚠️ Текущий PIN-код неверный.")
        if not valid_pin(pin.strip()) or pin.strip() != pin2.strip():
            return go("/me", "⚠️ Новый PIN — 4 цифры, и оба раза одинаково.")
        svc.set_pin(me["id"], pin.strip())
        return go("/me", "✅ PIN-код изменён.")

    @app.post("/me/delete")
    def delete_me(request: Request, csrf: str = Form("")):
        me = need_login(request)
        check_csrf(me, csrf)
        svc.delete_player(me["id"], now())
        resp = go("/login", "Ты удалён с сайта. Вернуться можно в любой момент — просто зарегистрируйся снова.")
        resp.delete_cookie("sid")
        return resp

    # ---------- статистика и советы ----------

    @app.get("/stats", response_class=HTMLResponse)
    def stats(request: Request, period: int = 30):
        me = need_login(request)
        period = period if period in (7, 30, 90) else 30
        t = now()
        since = t - period * DAY
        offset = int(datetime.now(cfg.tz).utcoffset().total_seconds())
        days = svc.daily_counts(t, period, offset)
        step = 1 if period <= 7 else (5 if period <= 30 else 15)
        points = [
            (local(day, "%d.%m") if i % step == 0 or i == len(days) - 1 else "", n,
             f"{local(day, '%d.%m')}: {n} бафов")
            for i, (day, n) in enumerate(days)
        ]
        donors = svc.top_players("donor", since)
        recipients = svc.top_players("recipient", since)
        return render(
            request, "stats.html", me,
            period=period,
            all_time=svc.totals(),
            totals=svc.totals(since),
            chart=charts.column_chart(points, f"Бафы по дням за {period} дней"),
            days=days,
            donors=charts.bar_list([(r["nick"], r["n"], f"{r['n']} · −{format_duration(r['saved'])}") for r in donors], me["nick"]),
            recipients=charts.bar_list([(r["nick"], r["n"], f"{r['n']} · −{format_duration(r['saved'])}") for r in recipients], me["nick"]),
            recent=svc.recent_donations(15),
            queue_counts={k: sum(1 for r in svc.queue_view(k, t).rows if r.status == STATUS_NEED) for k in KINDS},
            players=len(svc.players()),
        )

    @app.get("/tips", response_class=HTMLResponse)
    def tips(request: Request):
        me = current(request)
        return render(
            request, "tips.html", me,
            pct=svc.setting_float("pct"),
            pattern=svc.setting("pattern"),
            streak=int(svc.setting_float("max_streak")),
            cooldown=svc.setting_float("cooldown_hours"),
            confirm=svc.setting_float("confirm_minutes"),
            observed=svc.observed_times(),
        )

    # ---------- управление ----------

    @app.get("/admin", response_class=HTMLResponse)
    def admin(request: Request):
        me = need_admin(request)
        t = now()
        rows = []
        for p in svc.players():
            rows.append({
                "p": p,
                "timers": {k: svc.timer_candidate(p["id"], k, t) for k in KINDS},
            })
        return render(
            request, "admin.html", me,
            rows=rows,
            settings={k: svc.setting(k) for k in SETTINGS},
        )

    @app.post("/admin/add")
    def admin_add(request: Request, csrf: str = Form(""), nick: str = Form("")):
        me = need_admin(request)
        check_csrf(me, csrf)
        nick = clean_nick(nick)
        if not 2 <= len(nick) <= 32:
            return go("/admin", "⚠️ Ник должен быть от 2 до 32 символов.")
        player = svc.player_by_nick(nick) or svc.create_offline_player(nick, now())
        return go(f"/admin/p/{player['id']}", f"✅ Игрок {player['nick']} добавлен. Он может зарегистрироваться под этим ником и задать PIN.")

    @app.post("/admin/settings")
    async def admin_settings(request: Request):
        me = need_admin(request)
        form = await request.form()
        check_csrf(me, form.get("csrf", ""))
        errors = []
        for key in SETTINGS:
            if key in form and str(form[key]).strip() != svc.setting(key):
                error = svc.set_setting(key, str(form[key]))
                if error:
                    errors.append(error)
        return go("/admin#settings", "⚠️ " + "; ".join(errors) if errors else "✅ Настройки сохранены.")

    @app.get("/admin/p/{pid}", response_class=HTMLResponse)
    def admin_player(request: Request, pid: int):
        me = need_admin(request)
        p = svc.player(pid)
        if p is None:
            return go("/admin", "Игрок не найден — возможно, уже удалён.")
        t = now()
        given, received = svc.stats(pid)
        return render(
            request, "admin_player.html", me,
            p=p,
            timers=my_timers(pid, t),
            urgent={k: bool((svc.active_timer(pid, k) or {"urgent": 0})["urgent"]) for k in KINDS},
            buffs={k: buff_state(pid, k, t) for k in KINDS},
            given=given, received=received,
            is_owner=bool(me["is_owner"]),
        )

    @app.post("/admin/p/{pid}/{action}")
    def admin_player_action(request: Request, pid: int, action: str, csrf: str = Form(""), kind: str = Form("")):
        me = need_admin(request)
        check_csrf(me, csrf)
        p = svc.player(pid)
        if p is None:
            return go("/admin", "Игрок не найден.")
        back = f"/admin/p/{pid}"
        if action == "urgent" and kind in KINDS:
            flag = svc.toggle_urgent(pid, kind)
            return go(back, "🔥 Срочно включено" if flag else "Срочно выключено")
        if action == "close" and kind in KINDS:
            svc.close_timer(pid, kind)
            return go(back, f"🏁 Убран из очереди: {KIND_NAME[kind]}")
        if action == "assign" and kind in KINDS:
            a = svc.assign(kind, pid, me["id"], now())
            if a is None:
                return go(back, f"Сейчас никому не нужен баф на {KIND_ACC[kind]}.")
            return go(f"/give/d/{a.donation_id}")
        if action == "resetpin":
            svc.set_pin(pid, None)
            return go(back, f"🔑 PIN сброшен. {p['nick']} может заново зарегистрироваться под своим ником и задать новый PIN.")
        if action in ("make_admin", "drop_admin"):
            if not me["is_owner"] or p["is_owner"]:
                raise HTTPException(status_code=403, detail="Назначать админов может только владелец")
            svc.set_admin(pid, action == "make_admin")
            return go(back, "⭐ Теперь админ" if action == "make_admin" else "Админ снят")
        if action == "delete":
            if p["is_owner"]:
                return go(back, "⚠️ Владельца удалить нельзя.")
            svc.delete_player(pid, now())
            return go("/admin", f"🗑 {p['nick']} удалён с сайта. История его бафов сохранена в статистике.")
        raise HTTPException(status_code=400)

    # ---------- служебное ----------

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    @app.get("/manifest.webmanifest")
    def manifest():
        body = (
            '{"name": "%s", "short_name": "Бафы", "start_url": "/", "display": "standalone", '
            '"background_color": "#0d0a14", "theme_color": "#0d0a14", '
            '"icons": [{"src": "/static/icon.svg", "sizes": "any", "type": "image/svg+xml"}]}' % cfg.site_name
        )
        return Response(body, media_type="application/manifest+json")

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        if exc.status_code == 303:
            return RedirectResponse(exc.headers["Location"], status_code=303)
        me = current(request)
        return render(request, "error.html", me, status_code=exc.status_code, message=exc.detail)

    return app


app = None


def get_app() -> FastAPI:
    global app
    if app is None:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
        app = create_app()
    return app
