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
from core import crm
from core.analytics import benefit, r4_report
from core.notify import NOTICE_KINDS, Notice, prefs as notify_prefs
from core.logic import CYCLE_MAX, STATUS_NEED, buffs_needed, make_pattern, parse_pattern, timer_status
from core.service import KIND_ACC, KIND_EMOJI, KIND_NAME, KINDS, SETTINGS, Service, clean_nick, valid_pin
from core.timeparse import DAY, HOUR, MINUTE, format_duration

from . import charts, push
from .guides import BY_SLUG as GUIDE_BY_SLUG, GUIDES
from .config import Config, load_config

log = logging.getLogger(__name__)
BASE = Path(__file__).parent
SELF_UNDO_SECONDS = 15 * 60  # сколько игрок может сам отменить свою запись

SLOT_REASON = {
    "B": "у него самый большой остаток",
    "W": "он дольше всех ждёт помощи",
    "U": "🔥 срочная помощь (отметил админ)",
    "M": "записано вручную",
    "R": "🎲 по рулетке — очередь была пуста",
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
                    await asyncio.to_thread(push.run_once, svc, cfg.tz, t)
                except Exception:
                    log.exception("Ошибка фоновой задачи")
                await asyncio.sleep(60)

        try:
            crm.seed_events(svc)
        except Exception:
            log.exception("Не удалось добавить события союза")
        try:
            push.ensure_keys(svc)
        except Exception:
            log.exception("Не удалось создать ключи для уведомлений")

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

    def priority_text() -> str:
        it = gamedata.item(svc.setting("priority_item"))
        if it is None:
            return ""
        return f"{it.ru} до {int(svc.setting_float('priority_below')) - 1} ур."

    def pattern_text(pattern: str) -> str:
        big, wait, wait_first = parse_pattern(pattern)
        if not wait:
            return "только большим таймерам"
        if not big:
            return "только тем, кому досталось меньше всех"
        if wait_first:
            return f"{big} : {wait} — сначала {wait} меньше получившим, потом {big} большим"
        return f"{big} : {wait} — сначала {big} большим, потом {wait} меньше получившим"

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
        NOTICE_KINDS=NOTICE_KINDS,
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
                "vapid_public": svc.setting("vapid_public") if me else "",
                "unread": crm.unread(svc, me["id"]) if me else 0,
                "pp_goal": int(svc.setting_float("priority_below")),
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

    def boards(me, t: int) -> dict:
        """Живые блоки очереди: кто в каком порядке, сколько получил, сколько осталось, сколько ждёт."""
        midnight = int(datetime.now(cfg.tz).replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
        out = {}
        for kind in KINDS:
            rows = svc.queue_order(kind, t)
            need = [r for r in rows if r.need]
            mine = next((r for r in rows if me and r.candidate.player_id == me["id"]), None)
            others = [r for r in need if not me or r.candidate.player_id != me["id"]]
            roulette = (svc.roulette_order(kind, t, exclude=me["id"] if me else None)
                        if not others and svc.roulette_on(kind) else [])
            out[kind] = {
                "roulette": roulette,
                "need": need,
                "reached": [r for r in rows if not r.need],
                "mine": mine,
                "today": svc.given_since(kind, midnight),
                "received_total": sum(r.candidate.received for r in rows),
            }
        return out

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
        return render(
            request, "home.html", me,
            boards=boards(me, t),
            received=received,
            own_gift=svc.last_own_gift(me["id"], t - SELF_UNDO_SECONDS),
            checks=svc.time_checks(me["id"], t),
            pinned=crm.pinned_posts(svc),
            soon=[o for o in crm.occurrences(svc, t, 1) if o.start - t < DAY][:2],
            order=svc.setting("queue_order"),
            pattern=svc.setting("pattern"),
            finished=svc.finished_timers(me["id"], t),
            updated=local(t, "%H:%M:%S"),
            now_ts=t,
            gap=svc.setting_float("min_gap_hours"),
        )

    @app.get("/live", response_class=HTMLResponse)
    def live(request: Request):
        """Кусок главной с блоками очереди — страница подтягивает его каждые несколько секунд."""
        me = current(request)
        if me is None:
            return HTMLResponse("", status_code=401)
        t = now()
        return templates.TemplateResponse(
            request, "_boards.html", {"me": me, "boards": boards(me, t), "updated": local(t, "%H:%M:%S"), "now_ts": t,
                                      "order": svc.setting("queue_order"), "pattern": svc.setting("pattern"),
                                      "pp_goal": int(svc.setting_float("priority_below"))},
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
            gap=svc.setting_float("min_gap_hours"),
            fair=svc.setting_float("fair_round") > 0,
            order=svc.setting("queue_order"),
            fire_hours=svc.setting_float("fire_hours"),
            hold_hours=svc.setting_float("hold_hours"),
            prio=priority_text(),
            prio_weight=svc.setting_float("priority_weight"),
            roulette_build=svc.roulette_on("build"),
            roulette_research=svc.roulette_on("research"),
        )

    @app.post("/rules/agree")
    def rules_agree(request: Request, csrf: str = Form("")):
        me = need_login(request)
        check_csrf(me, csrf)
        svc.agree(me["id"], now())
        return go("/", "✅ Спасибо! Условия приняты — теперь можно вставать в очередь и отдавать бафы.")

    @app.get("/queue")
    def queue_redirect():
        return RedirectResponse("/", status_code=303)

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

    @app.get("/join", response_class=HTMLResponse)
    def join_choose(request: Request):
        me = need_login(request)
        need_agreed(me)
        t = now()
        return render(request, "join_choose.html", me, timers={k: svc.timer_candidate(me["id"], k, t) for k in KINDS})

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
        c = svc.timer_candidate(target["id"], kind, now())
        owed = buffs_needed(c.remaining, c.base, svc.rules(kind)) if c else 0
        tail = f" Тебе положено {owed} баф. до цели." if owed else " Бафы не нужны — ты уже около цели."
        text = ("✅ Остаток обновлён." if fix else "✅ Ты в очереди!") + tail
        return go("/", text + warn)

    @app.post("/timer/{kind}/close")
    def timer_close(request: Request, kind: str, csrf: str = Form("")):
        me = need_login(request)
        check_csrf(me, csrf)
        svc.close_timer(me["id"], kind, now())
        return go("/", f"🏁 Готово — ты убран из очереди ({KIND_NAME.get(kind, '')}).")

    @app.post("/timer/{kind}/ok")
    def timer_ok(request: Request, kind: str, csrf: str = Form("")):
        """«Время совпадает с игрой» — сверка пройдена, напомним снова через check_hours."""
        me = need_login(request)
        check_csrf(me, csrf)
        svc.confirm_time(me["id"], kind, now())
        return go("/", "✅ Спасибо! Время сверено — очередь считает точно.")

    @app.post("/timer/{kind}/skip-next")
    def timer_skip_next(request: Request, kind: str, csrf: str = Form("")):
        me = need_login(request)
        check_csrf(me, csrf)
        svc.dismiss_next(me["id"], kind)
        return go("/", "Хорошо. Запустишь следующее — «➕ Встать в очередь».")

    # ---------- я отдал баф ----------

    def gift_donor(me, donor_id: int):
        """Админ может отметить баф за другого игрока (у кого нет доступа к сайту)."""
        if donor_id and donor_id != me["id"]:
            if not svc.is_admin_player(me):
                raise HTTPException(status_code=403)
            donor = svc.player(donor_id)
            if donor is None:
                raise HTTPException(status_code=404, detail="Игрок не найден")
            return donor
        return me

    @app.get("/give", response_class=HTMLResponse)
    def give_page(request: Request, kind: str = "research", donor: int = 0):
        me = need_login(request)
        need_agreed(me)
        kind = kind if kind in KINDS else "research"
        who = gift_donor(me, donor)
        t = now()
        return render(
            request, "give.html", me,
            kind=kind,
            donor=who,
            for_other=who["id"] != me["id"],
            rows=[r for r in svc.queue_order(kind, t) if r.need],
            roulette=(svc.roulette_order(kind, t, exclude=who["id"])
                      if svc.roulette_on(kind) and not [r for r in svc.queue_order(kind, t)
                                                         if r.need and r.candidate.player_id != who["id"]] else []),
            buff=buff_state(who["id"], kind, t),
            gap=svc.setting_float("min_gap_hours"),
        )

    @app.post("/gave")
    def gave(request: Request, csrf: str = Form(""), kind: str = Form(""), recipient: int = Form(0),
             donor: int = Form(0)):
        me = need_login(request)
        check_csrf(me, csrf)
        need_agreed(me)
        if kind not in KINDS:
            raise HTTPException(status_code=400)
        who = gift_donor(me, donor)
        back = f"/give?kind={kind}" + (f"&donor={who['id']}" if who["id"] != me["id"] else "")
        result, error = svc.record_gift(kind, who["id"], recipient, me["id"], now())
        if error:
            messages = {
                "self": "⚠️ Себе баф отдать нельзя.",
                "not_in_queue": "⚠️ Этого игрока уже нет в очереди — обнови страницу.",
                "duplicate": f"⚠️ Баф на {KIND_ACC[kind]} уже записан несколько минут назад. Если отдал ещё один — подожди 10 минут.",
            }
            return go(back, messages[error])
        who_text = "Ты" if who["id"] == me["id"] else who["nick"]
        cut = f" (−{format_duration(result.reduction)})" if result.reduction else " (🎲 по рулетке)"
        done = f"✅ Записано: {who_text} → {result.recipient_nick}, баф на {KIND_ACC[kind]}{cut}. Спасибо! 🙌"
        return go(f"/admin/p/{who['id']}" if who["id"] != me["id"] else "/", done)

    # ---------- отмена записанного бафа ----------

    @app.post("/undo/{donation_id}")
    def undo(request: Request, donation_id: int, csrf: str = Form(""), next: str = Form("/")):
        me = need_login(request)
        check_csrf(me, csrf)
        d = svc.donation(donation_id)
        if d is None:
            return go("/", "⚠️ Запись не найдена.")
        own = me["id"] in (d["requested_by"], d["donor_id"]) and (d["resolved_at"] or 0) >= now() - SELF_UNDO_SECONDS
        if not (svc.is_admin_player(me) or own):
            raise HTTPException(status_code=403, detail="Отменить эту запись может только руководство союза (R4)")
        result, error, exact = svc.undo_donation(donation_id, me["id"], now())
        back = next if next.startswith("/") and not next.startswith("//") else "/"
        if error == "not_done":
            return go(back, "⚠️ Эта запись уже отменена.")
        donor = svc.player(d["donor_id"])
        recipient = svc.player(d["recipient_id"])
        text = (f"↩️ Отменено: {donor['nick'] if donor else '?'} → {recipient['nick'] if recipient else '?'} "
                f"({KIND_NAME[d['kind']]}). Таймеры и очередь вернулись как были.")
        if not exact:
            text += " ⚠️ Запись старая — таймер того, кто отдавал, поправьте вручную, если нужно."
        return go(back, text)

    @app.get("/admin/log", response_class=HTMLResponse)
    def admin_log(request: Request):
        me = need_admin(request)
        return render(request, "admin_log.html", me, rows=svc.journal(200))

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
            notify=notify_prefs(me),
            gain=benefit(svc, me["id"], t),
            devices=len(svc.push_subs(me["id"])),
            quiet=(int(svc.setting_float("quiet_from")), int(svc.setting_float("quiet_to"))),
        )

    # ---------- союз: объявления и события ----------

    def offset_min() -> int:
        return int(datetime.now(cfg.tz).utcoffset().total_seconds() // 60)

    def to_utc(days_local: list[str], hhmm: str) -> tuple[str, int] | None:
        """Местные дни недели и время → дни и минуты по UTC (как в игре)."""
        try:
            hh, mm = (int(x) for x in hhmm.split(":"))
        except ValueError:
            return None
        if not (0 <= hh < 24 and 0 <= mm < 60):
            return None
        minute = hh * 60 + mm - offset_min()
        shift = -1 if minute < 0 else (1 if minute >= 1440 else 0)
        days = "".join(sorted({str((int(d) + shift) % 7) for d in days_local if d in "0123456"}))
        return days, minute % 1440

    def to_local(days_utc: str, start_min: int) -> tuple[set[str], str]:
        minute = start_min + offset_min()
        shift = -1 if minute < 0 else (1 if minute >= 1440 else 0)
        minute %= 1440
        return {str((int(d) + shift) % 7) for d in days_utc}, f"{minute // 60:02d}:{minute % 60:02d}"

    @app.get("/board", response_class=HTMLResponse)
    def board(request: Request):
        me = need_login(request)
        items = crm.posts(svc, me["id"])
        crm.mark_read(svc, me["id"])
        refs = [f"post:{p['id']}" for p in items if p["rsvp"]]
        return render(request, "board.html", me, items=items, answers=crm.answers(svc, refs),
                      players=len(svc.players()), unread=0)

    @app.post("/board")
    def board_add(request: Request, csrf: str = Form(""), text: str = Form(""), pinned: str = Form(""),
                  important: str = Form(""), rsvp: str = Form("")):
        me = need_admin(request)
        check_csrf(me, csrf)
        if crm.add_post(svc, me["id"], text, pinned == "1", important == "1", rsvp == "1", now()) is None:
            return go("/board", "⚠️ Напиши текст объявления.")
        tail = " Важное — придёт всем на телефон в течение минуты." if important == "1" else ""
        return go("/board", "✅ Опубликовано." + tail)

    @app.post("/board/{post_id}/{action}")
    def board_action(request: Request, post_id: int, action: str, csrf: str = Form("")):
        me = need_admin(request)
        check_csrf(me, csrf)
        if action == "pin":
            crm.set_pin(svc, post_id, True)
        elif action == "unpin":
            crm.set_pin(svc, post_id, False)
        elif action == "delete":
            crm.delete_post(svc, post_id)
        else:
            raise HTTPException(status_code=404)
        return go("/board", "✅ Готово.")

    @app.post("/answer")
    def answer(request: Request, csrf: str = Form(""), ref: str = Form(""), value: str = Form(""), next: str = Form("/")):
        me = need_login(request)
        check_csrf(me, csrf)
        if not (ref.startswith("post:") or ref.startswith("event:")):
            raise HTTPException(status_code=400)
        crm.answer(svc, ref, me["id"], value, now())
        return go(next if next.startswith("/") else "/", "✅ Ответ записан." if value in ("yes", "no") else "Ответ убран.")

    @app.get("/events", response_class=HTMLResponse)
    def events_page(request: Request):
        me = need_login(request)
        t = now()
        occ = crm.occurrences(svc, t, 7)
        days: list[dict] = []
        for o in occ:
            key = local(max(o.start, t) if o.ongoing(t) else o.start, "%Y-%m-%d")
            if not days or days[-1]["key"] != key:
                ts = max(o.start, t) if o.ongoing(t) else o.start
                wd = datetime.fromtimestamp(ts, cfg.tz).weekday()
                days.append({"key": key, "title": f"{crm.WEEKDAYS_FULL[wd]}, {local(ts, '%d.%m')}", "list": []})
            days[-1]["list"].append(o)
        refs = [o.ref for o in occ if o.event["rsvp"]]
        all_events = [
            {"e": e, "local": to_local(e["days"], e["start_min"])} for e in crm.events(svc)
        ]
        return render(request, "events.html", me, days=days, answers=crm.answers(svc, refs), now_ts=t,
                      all_events=all_events, WEEKDAYS=crm.WEEKDAYS, reset=to_local("0", 0)[1])

    @app.get("/events/edit", response_class=HTMLResponse)
    def event_edit(request: Request, id: int = 0):
        me = need_admin(request)
        e = crm.event(svc, id) if id else None
        days, hhmm = to_local(e["days"], e["start_min"]) if e else (set(), "18:00")
        return render(request, "event_edit.html", me, e=e, days=days, hhmm=hhmm, WEEKDAYS=crm.WEEKDAYS)

    @app.post("/events/save")
    async def event_save(request: Request):
        me = need_admin(request)
        form = await request.form()
        check_csrf(me, str(form.get("csrf", "")))
        conv = to_utc(form.getlist("days"), str(form.get("time", "")))
        if conv is None:
            return go("/events", "⚠️ Время в формате ЧЧ:ММ, например 18:00.")
        try:
            hours = float(str(form.get("hours", "1")).replace(",", "."))
            remind = int(str(form.get("remind", "60")))
            event_id = int(str(form.get("id", "0")) or 0)
        except ValueError:
            return go("/events", "⚠️ Проверь длительность и напоминание.")
        error = crm.save_event(svc, event_id or None, str(form.get("title", "")), conv[0], conv[1], int(hours * 60),
                               str(form.get("prepare", "")), remind, form.get("checked") == "1", form.get("rsvp") == "1")
        return go("/events", "⚠️ " + error if error else "✅ Событие сохранено.")

    @app.post("/events/{event_id}/delete")
    def event_delete(request: Request, event_id: int, csrf: str = Form("")):
        me = need_admin(request)
        check_csrf(me, csrf)
        crm.delete_event(svc, event_id)
        return go("/events", "🗑 Событие удалено.")

    @app.post("/admin/p/{pid}/crm")
    def player_note(request: Request, pid: int, csrf: str = Form(""), note: str = Form(""), tags: str = Form("")):
        me = need_admin(request)
        check_csrf(me, csrf)
        if svc.player(pid) is None:
            raise HTTPException(status_code=404)
        crm.save_note(svc, pid, note, tags)
        return go(f"/admin/p/{pid}", "✅ Заметка сохранена.")

    @app.post("/admin/p/{pid}/pp")
    def player_pp(request: Request, pid: int, csrf: str = Form(""), level: str = Form("")):
        me = need_admin(request)
        check_csrf(me, csrf)
        if svc.player(pid) is None:
            raise HTTPException(status_code=404)
        svc.set_pp_level(pid, int(level) if level.strip().isdigit() else None)
        return go(f"/admin/p/{pid}", "✅ Уровень Электростанции сохранён.")

    # ---------- уведомления на телефон ----------

    @app.get("/sw.js")
    def service_worker():
        body = (BASE / "static" / "sw.js").read_text(encoding="utf-8")
        return Response(body, media_type="application/javascript",
                        headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})

    @app.post("/push/subscribe")
    async def push_subscribe(request: Request):
        me = need_login(request)
        data = await request.json()
        check_csrf(me, str(data.get("csrf", "")))
        endpoint = str(data.get("endpoint", ""))
        keys = data.get("keys") or {}
        if not endpoint.startswith("https://") or not keys.get("p256dh") or not keys.get("auth"):
            raise HTTPException(status_code=400, detail="Неверная подписка")
        svc.add_push(me["id"], endpoint, str(keys["p256dh"]), str(keys["auth"]), now())
        return {"ok": True}

    @app.post("/push/unsubscribe")
    async def push_unsubscribe(request: Request):
        me = need_login(request)
        data = await request.json()
        check_csrf(me, str(data.get("csrf", "")))
        svc.drop_push(str(data.get("endpoint", "")), me["id"])
        return {"ok": True}

    @app.post("/push/test")
    def push_test(request: Request, csrf: str = Form("")):
        me = need_login(request)
        check_csrf(me, csrf)
        t = now()
        n = Notice(me["id"], f"test:{t}", "test", "🔔 Проверка", "Уведомления работают. Так придёт «баф готов — отдай X».", "/")
        if not svc.push_subs(me["id"]):
            return go("/me#notify", "⚠️ На этом аккаунте нет устройств — сначала нажми «Включить уведомления».")
        ok = push.deliver(svc, n, t)
        return go("/me#notify", "✅ Отправил — посмотри на телефон." if ok else "⚠️ Не дошло. Нажми «Включить уведомления» ещё раз.")

    @app.post("/me/notify")
    async def notify_settings(request: Request):
        me = need_login(request)
        form = await request.form()
        check_csrf(me, str(form.get("csrf", "")))
        off = [k for k in NOTICE_KINDS if form.get(f"on_{k}") != "1"]
        svc.set_notify_prefs(me["id"], {"off": off, "quiet": form.get("quiet") == "1"})
        return go("/me#notify", "✅ Настройки уведомлений сохранены.")

    @app.post("/me/pp")
    def me_pp(request: Request, csrf: str = Form(""), level: str = Form("")):
        me = need_login(request)
        check_csrf(me, csrf)
        svc.set_pp_level(me["id"], int(level) if level.strip().isdigit() else None)
        return go("/me", "✅ Уровень Электростанции сохранён.")

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

    @app.get("/help", response_class=HTMLResponse)
    def help_page(request: Request):
        """Справка: как устроено всё на сайте — для игроков и для R4. Числа берутся из настроек."""
        me = current(request)
        f = svc.setting_float
        return render(
            request, "help.html", me,
            pct=int(f("pct")),
            build_target=target_text("build"),
            research_target=target_text("research"),
            gap=int(f("min_gap_hours")),
            fire=int(f("fire_hours")),
            hold=int(f("hold_hours")),
            cooldown=int(f("cooldown_hours")),
            check=int(f("check_hours")),
            prio=priority_text(),
            prio_weight=f("priority_weight"),
            roulette_build=svc.roulette_on("build"),
            roulette_research=svc.roulette_on("research"),
            roulette_days=int(f("roulette_active_days")),
            quiet=(int(f("quiet_from")), int(f("quiet_to"))),
            order=svc.setting("queue_order"),
            self_undo=SELF_UNDO_SECONDS // 60,
            site_url=str(request.base_url).rstrip("/"),
        )

    @app.get("/tips")
    def tips():
        return RedirectResponse("/guides", status_code=303)

    @app.get("/guides", response_class=HTMLResponse)
    def guides_index(request: Request):
        return render(request, "guides/index.html", current(request), guides=GUIDES)

    @app.get("/guides/{slug}", response_class=HTMLResponse)
    def guide_page(request: Request, slug: str):
        guide = GUIDE_BY_SLUG.get(slug)
        if guide is None:
            raise HTTPException(status_code=404, detail="Такого гайда нет")
        return render(request, f"guides/{slug}.html", current(request), guide=guide, guides=GUIDES,
                      observed=svc.observed_times() if slug == "build" else [])

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
            cycle=parse_pattern(svc.setting("pattern")),
            cycle_max=CYCLE_MAX,
        )

    @app.get("/admin/stats", response_class=HTMLResponse)
    def admin_stats(request: Request, period: int = 7):
        me = need_admin(request)
        period = period if period in (7, 14, 30) else 7
        t = now()
        r = r4_report(svc, t, period)
        offset = int(datetime.now(cfg.tz).utcoffset().total_seconds())
        days = svc.daily_counts(t, period, offset)
        step = 1 if period <= 14 else 5
        points = [
            (local(day, "%d.%m") if i % step == 0 or i == len(days) - 1 else "", n, f"{local(day, '%d.%m')}: {n} бафов")
            for i, (day, n) in enumerate(days)
        ]
        return render(request, "admin_stats.html", me, r=r, settings_cd=svc.setting_float("cooldown_hours"),
                      chart=charts.column_chart(points, f"Бафы по дням за {period} дней"))

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
        if "cycle_big" in form and "cycle_wait" in form:
            try:
                big, wait = int(str(form["cycle_big"])), int(str(form["cycle_wait"]))
            except ValueError:
                big, wait = parse_pattern(svc.setting("pattern"))[:2]
            pattern = make_pattern(big, wait, str(form.get("cycle_first", "big")) == "wait")
            if pattern != svc.setting("pattern"):
                svc.set_setting("pattern", pattern)
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
            log=svc.journal(30, pid),
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
            svc.close_timer(pid, kind, now(), suggest_next=False)
            return go(back, f"🏁 Убран из очереди: {KIND_NAME[kind]}")
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
            '"icons": [{"src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png"}, '
            '{"src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png"}, '
            '{"src": "/static/icon.svg", "sizes": "any", "type": "image/svg+xml"}]}' % cfg.site_name
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
