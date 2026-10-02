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
    assert owner.get("/queue").url.path == "/login"


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


def test_join_give_confirm_flow(site):
    svc, owner, player = site
    register(owner, "Иван")
    register(player, "Мура")
    r = player.get("/join/build?item=pp&level=24")
    assert "13д 2ч" in r.text and "Шаг 3 из 3" in r.text
    r = player.post("/join/build", data={"csrf": csrf(r.text), "item": "pp", "level": 24, "days": 18, "hours": 3})
    assert "Ты в очереди" in r.text
    mura = svc.player_by_nick("Мура")
    assert svc.active_timer(mura["id"], "build")["level"] == 24

    token = csrf(owner.get("/").text)
    r = owner.post("/give/build", data={"csrf": token})
    assert r.url.path.startswith("/give/d/") and "Мура" in r.text
    donation = r.url.path.rsplit("/", 1)[1]
    assert player.get(f"/give/d/{donation}").status_code == 403  # чужая бронь
    r = owner.post(f"/give/d/{donation}/ok", data={"csrf": token})
    assert "Записал" in r.text
    assert "отдал тебе баф" in player.get("/").text
    assert owner.post("/give/build", data={"csrf": "wrong"}).status_code == 400


def test_fix_keeps_item(site):
    svc, _, player = site
    register(player, "Мура")
    token = csrf(player.get("/").text)
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


def test_stats_page(site):
    _, owner, _ = site
    register(owner, "Иван")
    for period in (7, 30, 90):
        r = owner.get(f"/stats?period={period}")
        assert r.status_code == 200 and "<svg" in r.text


def test_rules_consent_and_calculator(site):
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

    home = owner.get("/")
    assert "Кому следующие бафы" in home.text and "Калькулятор" in home.text
    r = owner.get("/calc?kind=research&days=27&hours=5")
    assert r.status_code == 200 and "Таймер 27д 5ч" in r.text and "бафов от союза" in r.text
