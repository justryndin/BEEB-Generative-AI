"""Сквозные проверки сайта через TestClient."""

import re
import warnings
from zoneinfo import ZoneInfo

import pytest

warnings.filterwarnings("ignore", category=DeprecationWarning)
from fastapi.testclient import TestClient  # noqa: E402

from core.db import Database  # noqa: E402
from core.service import Service  # noqa: E402
from web.app import create_app  # noqa: E402
from web.config import Config  # noqa: E402


def csrf(html: str) -> str:
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


@pytest.fixture()
def site():
    svc = Service(Database(":memory:"))
    app = create_app(Config(":memory:", ZoneInfo("Europe/Moscow"), "Бафы союза", False), svc)
    with TestClient(app) as owner, TestClient(app) as player:
        yield svc, owner, player


def register(client, nick, pin="1234", code=""):
    return client.post("/register", data={"nick": nick, "pin": pin, "pin2": pin, "code": code, "agree": "1"})


def test_public_pages(site):
    _, owner, _ = site
    for path in ("/", "/login", "/register", "/tips", "/healthz", "/manifest.webmanifest"):
        assert owner.get(path).status_code == 200
    assert owner.get("/give").url.path == "/login"
    assert owner.get("/live").status_code == 401


def test_register_login_and_lockout(site):
    svc, owner, player = site
    assert register(owner, "Иван").url.path == "/"
    assert register(player, "иван").status_code == 400  # ник занят
    player.cookies.clear()
    for _ in range(4):
        assert player.post("/login", data={"nick": "Иван", "pin": "0000"}).status_code == 400
    r = player.post("/login", data={"nick": "Иван", "pin": "0000"})
    assert "Слишком много" in r.text
    assert "Слишком много" in player.post("/login", data={"nick": "Иван", "pin": "1234"}).text


def test_alliance_code_required(site):
    svc, owner, player = site
    svc.set_setting("alliance_code", "Метель")
    assert "Неверный код" in register(player, "Мура", code="нет").text
    assert register(player, "Мура", code="метель").url.path == "/"


def test_join_and_gave_flow(site):
    svc, owner, player = site
    register(owner, "Иван")
    register(player, "Мура")
    r = player.get("/join/build?item=pp&level=24")
    assert "13д 2ч" in r.text and "Шаг 3 из 3" in r.text
    r = player.post("/join/build", data={"csrf": csrf(r.text), "item": "pp", "level": 24, "days": 18, "hours": 3})
    assert "Ты в очереди" in r.text and "положено" in r.text
    mura = svc.player_by_nick("Мура")
    assert svc.active_timer(mura["id"], "build")["level"] == 24

    # главная и живой блок показывают очередь
    home = owner.get("/")
    assert "Мура" in home.text and "следующий" in home.text and "Как это работает" in home.text
    assert "Отдай сейчас" in home.text
    live = owner.get("/live")
    assert live.status_code == 200 and "Мура" in live.text and "в очереди" in live.text

    # «Я отдал баф»: список, выбор получателя
    page = owner.get("/give?kind=build")
    assert "Мура" in page.text and "следующий" in page.text
    token = csrf(page.text)
    r = owner.post("/gave", data={"csrf": token, "kind": "build", "recipient": mura["id"]})
    assert "Записано" in r.text
    assert svc.active_timer(mura["id"], "build")["buffs_received"] == 1
    assert "отдал тебе баф" in player.get("/").text
    # повторная запись в течение 10 минут — защита от двойного нажатия
    r = owner.post("/gave", data={"csrf": token, "kind": "build", "recipient": mura["id"]})
    assert "уже записан" in r.text
    assert svc.active_timer(mura["id"], "build")["buffs_received"] == 1
    assert owner.post("/gave", data={"csrf": "wrong", "kind": "build", "recipient": mura["id"]}).status_code == 400


def test_fix_keeps_item(site):
    svc, _, player = site
    register(player, "Мура")
    token = csrf(player.get("/me").text)
    player.post("/join/build", data={"csrf": token, "item": "lab", "level": 25, "days": 20})
    r = player.get("/join/build?fix=1")
    assert "Поправить время" in r.text
    player.post("/join/build", data={"csrf": token, "fix": 1, "days": 15})
    t = svc.active_timer(svc.player_by_nick("Мура")["id"], "build")
    assert t["item"] == "lab" and t["base_seconds"] == 20 * 86400


def test_admin_panel(site):
    svc, owner, player = site
    register(owner, "Иван")
    register(player, "Мура")
    assert player.get("/admin").status_code == 403
    svc.set_owner(svc.player_by_nick("Иван")["id"])
    token = csrf(owner.get("/admin").text)

    r = owner.post("/admin/add", data={"csrf": token, "nick": "Тёмный Страж"})
    strazh = svc.player_by_nick("Тёмный Страж")
    assert r.url.path == f"/admin/p/{strazh['id']}"
    r = owner.post("/join/build", data={"csrf": token, "item": "lab", "level": 25, "days": 20, "player": strazh["id"]})
    assert "Записал игрока" in r.text
    assert svc.active_timer(strazh["id"], "build") is not None

    owner.post(f"/admin/p/{strazh['id']}/urgent", data={"csrf": token, "kind": "build"})
    assert svc.active_timer(strazh["id"], "build")["urgent"] == 1

    mura = svc.player_by_nick("Мура")
    owner.post(f"/admin/p/{mura['id']}/make_admin", data={"csrf": token})
    assert svc.player(mura["id"])["is_admin"] == 1
    owner.post(f"/admin/p/{mura['id']}/resetpin", data={"csrf": token})
    assert svc.player(mura["id"])["pin_hash"] is None
    assert player.get("/").url.path == "/"  # сессии сброшены → снова лендинг
    owner.post(f"/admin/p/{mura['id']}/delete", data={"csrf": token})
    assert svc.player_by_nick("Мура") is None

    r = owner.post("/admin/settings", data={"csrf": token, "pattern": "BBBW", "build_min": "9"})
    assert svc.setting("pattern") == "BBBW" and "build_min" in r.text

    owner.post("/admin/settings", data={"csrf": token, "cycle_big": "1", "cycle_wait": "2", "cycle_first": "big"})
    assert svc.setting("pattern") == "BWW"
    owner.post("/admin/settings", data={"csrf": token, "cycle_big": "1", "cycle_wait": "2", "cycle_first": "wait"})
    assert svc.setting("pattern") == "WWB"
    page = owner.get("/admin").text
    assert "1 : 2 — сначала 2 меньше получившим, потом 1 большим" in page and 'data-big="1" data-wait="2"' in page


def test_stats_page(site):
    _, owner, _ = site
    register(owner, "Иван")
    for period in (7, 30, 90):
        r = owner.get(f"/stats?period={period}")
        assert r.status_code == 200 and "<svg" in r.text


def test_rules_consent(site):
    svc, owner, player = site
    register(owner, "Иван")
    # регистрация без согласия не проходит
    r = player.post("/register", data={"nick": "Мура", "pin": "1234", "pin2": "1234"})
    assert "согласиться с условиями" in r.text
    # старый игрок без согласия не может встать в очередь
    svc.db.run("UPDATE players SET agreed_at = NULL WHERE nick = 'Иван'")
    assert owner.get("/join/build").url.path == "/rules"
    token = csrf(owner.get("/rules").text)
    owner.post("/rules/agree", data={"csrf": token})
    assert owner.get("/join/build").url.path == "/join/build"

    assert owner.get("/calc").status_code == 404


def test_admin_marks_gift_for_offline_player(site):
    svc, owner, player = site
    register(owner, "Иван")
    register(player, "Мура")
    svc.set_owner(svc.player_by_nick("Иван")["id"])
    token = csrf(owner.get("/admin").text)
    owner.post("/admin/add", data={"csrf": token, "nick": "Офлайн"})
    offline = svc.player_by_nick("Офлайн")
    mura = svc.player_by_nick("Мура")
    svc.set_timer(mura["id"], "research", 30 * 86400, 1_700_000_000)
    page = owner.get(f"/give?kind=research&donor={offline['id']}")
    assert "Отмечаешь баф за игрока" in page.text
    r = owner.post("/gave", data={"csrf": token, "kind": "research", "recipient": mura["id"], "donor": offline["id"]})
    assert "Офлайн → Мура" in r.text
    # обычный игрок не может отмечать за других
    ptoken = csrf(player.get("/me").text)
    assert player.post("/gave", data={"csrf": ptoken, "kind": "research", "recipient": mura["id"], "donor": offline["id"]}).status_code == 403


def test_undo_permissions(site):
    svc, owner, player = site
    register(owner, "Иван")
    register(player, "Мура")
    svc.set_owner(svc.player_by_nick("Иван")["id"])
    ivan, mura = svc.player_by_nick("Иван"), svc.player_by_nick("Мура")
    svc.set_timer(ivan["id"], "build", 30 * 86400, int(__import__("time").time()))
    ptoken = csrf(player.get("/me").text)

    # Мура отметила баф Ивану и видит «Ошибся? Отменить»
    player.post("/gave", data={"csrf": ptoken, "kind": "build", "recipient": ivan["id"]})
    home = player.get("/")
    assert "Ошибся?" in home.text
    d = svc.journal(1)[0]
    r = player.post(f"/undo/{d['id']}", data={"csrf": ptoken, "next": "/"})
    assert "Отменено" in r.text and svc.donation(d["id"])["status"] == "undone"

    # старую запись (больше 15 минут) игрок сам отменить не может, а владелец/R4 — может
    player.post("/gave", data={"csrf": ptoken, "kind": "build", "recipient": ivan["id"]})
    d = svc.journal(1)[0]
    svc.db.run("UPDATE donations SET resolved_at = resolved_at - 3600 WHERE id = ?", d["id"])
    assert player.post(f"/undo/{d['id']}", data={"csrf": ptoken}).status_code == 403
    otoken = csrf(owner.get("/admin/log").text)
    assert "Мура" in owner.get("/admin/log").text
    r = owner.post(f"/undo/{d['id']}", data={"csrf": otoken, "next": "/admin/log"})
    assert r.url.path == "/admin/log" and "Отменено" in r.text
    assert player.get("/admin/log").status_code == 403


def test_time_check_and_next_cards(site):
    svc, owner, player = site
    register(owner, "Иван")
    register(player, "Мура")
    ivan, mura = svc.player_by_nick("Иван"), svc.player_by_nick("Мура")
    t = int(__import__("time").time())
    svc.set_timer(ivan["id"], "build", 30 * 86400, t - 60)
    ptoken = csrf(player.get("/me").text)
    player.post("/gave", data={"csrf": ptoken, "kind": "build", "recipient": ivan["id"]})

    home = owner.get("/")
    assert "Сверь время" in home.text and "Да, совпадает" in home.text
    assert "Чья полоска короче" in home.text and "получил 1 из" in home.text
    token = csrf(home.text)
    # «Нет, в игре другое» — поправка прямо с главной
    r = owner.post("/join/build", data={"csrf": token, "fix": "1", "days": "20", "hours": "0", "minutes": "0"})
    assert "Остаток обновлён" in r.text and "Сверь время" not in r.text

    # Стройка закончилась → «Встать со следующим»
    svc.set_timer(mura["id"], "build", 86400, t - 2 * 86400, item="pp", level=24)
    svc.deactivate_finished(t)
    home = player.get("/")
    assert "Встать со следующим" in home.text and "item=pp&amp;level=25" in home.text
    r = player.post("/timer/build/skip-next", data={"csrf": ptoken})
    assert "Встать со следующим" not in r.text


def test_push_subscribe_and_settings(site):
    svc, owner, _ = site
    register(owner, "Иван")
    me = owner.get("/me")
    assert "Уведомления на телефон" in me.text and owner.get("/sw.js").status_code == 200
    token = csrf(me.text)
    sub = {"csrf": token, "endpoint": "https://push.example/1", "keys": {"p256dh": "x", "auth": "y"}}
    assert owner.post("/push/subscribe", json=sub).json() == {"ok": True}
    ivan = svc.player_by_nick("Иван")
    assert len(svc.push_subs(ivan["id"])) == 1
    assert owner.post("/push/subscribe", json={**sub, "csrf": "bad"}).status_code == 400
    owner.post("/me/notify", data={"csrf": token, "on_got": "1", "quiet": "1"})
    from core.notify import prefs
    assert set(prefs(svc.player(ivan["id"]))["off"]) == {"ready", "next", "done", "news"}
    owner.post("/push/unsubscribe", json={"csrf": token, "endpoint": "https://push.example/1"})
    assert svc.push_subs(ivan["id"]) == []


def test_admin_analytics_and_benefit(site):
    svc, owner, player = site
    register(owner, "Иван")
    register(player, "Мура")
    svc.set_owner(svc.player_by_nick("Иван")["id"])
    ivan = svc.player_by_nick("Иван")
    svc.set_timer(ivan["id"], "build", 30 * 86400, int(__import__("time").time()) - 60)
    ptoken = csrf(player.get("/me").text)
    player.post("/gave", data={"csrf": ptoken, "kind": "build", "recipient": ivan["id"]})
    page = owner.get("/admin/stats?period=14")
    assert page.status_code == 200 and "бафов союза использовано" in page.text and "Мура" in page.text
    assert player.get("/admin/stats").status_code == 403
    me = owner.get("/me").text
    assert "Моя выгода" in me and "получил <b>1 из" in me
