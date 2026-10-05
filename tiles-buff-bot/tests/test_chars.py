import re

from .test_web import csrf, register, site  # noqa: F401  (фикстура)


def test_alt_characters_act_separately(site):  # noqa: F811
    svc, owner, player = site
    register(owner, "Boss")  # первый — владелец сайта
    register(player, "Main")
    me = player.get("/me").text
    assert "Мои персонажи" in me
    player.post("/me/chars/add", data={"csrf": csrf(me), "nick": "Twink"})
    alt = svc.player_by_nick("Twink")
    main = svc.player_by_nick("Main")
    assert alt["owner_id"] == main["id"] and alt["agreed_at"] == main["agreed_at"]

    # переключились на твинка — запись в очередь идёт от его имени
    r = player.post(f"/char/{alt['id']}", data={"csrf": csrf(me), "next": "/"}, follow_redirects=False)
    assert r.status_code == 303
    home = player.get("/").text
    assert 'class="who-nick">Twink<' in home
    player.post("/join/build", data={"csrf": csrf(home), "item": "pp", "level": "25", "days": "20", "hours": "0", "minutes": "0"})
    assert svc.active_timer(alt["id"], "build") is not None
    assert svc.active_timer(main["id"], "build") is None

    # нельзя переключиться на чужого персонажа и забрать чужой аккаунт
    boss = svc.player_by_nick("Boss")
    assert player.post(f"/char/{boss['id']}", data={"csrf": csrf(home)}, follow_redirects=False).headers["location"] == "/me"
    player.post("/me/chars/add", data={"csrf": csrf(home), "nick": "Boss"})
    assert svc.player_by_nick("Boss")["owner_id"] is None

    # права админа — у аккаунта: твинк владельца остаётся админом
    svc.set_owner(svc.player_by_nick("Boss")["id"])
    o = owner.get("/me").text
    owner.post("/me/chars/add", data={"csrf": csrf(o), "nick": "BossAlt"})
    owner.post(f"/char/{svc.player_by_nick('BossAlt')['id']}", data={"csrf": csrf(o)})
    assert owner.get("/admin").status_code == 200

    # отвязать твинка
    me = player.get("/me").text
    player.post(f"/me/chars/{alt['id']}/release", data={"csrf": csrf(me)})
    assert svc.player_by_nick("Twink")["owner_id"] is None
    assert 'class="who-nick">Main<' in player.get("/").text


def test_alt_notifications_go_to_account(site):  # noqa: F811
    from zoneinfo import ZoneInfo

    from core.notify import due_notices

    svc, _, player = site
    register(player, "Main")
    me = player.get("/me").text
    player.post("/me/chars/add", data={"csrf": csrf(me), "nick": "Twink"})
    main, alt = svc.player_by_nick("Main"), svc.player_by_nick("Twink")
    svc.add_push(main["id"], "https://push.example/1", "k", "a", 1)
    svc.set_notify_prefs(main["id"], {"off": [], "quiet": False})
    t = 4 * 86400 + 12 * 3600
    svc.db.run("INSERT INTO donations(kind, slot, donor_id, recipient_id, status, created_at, resolved_at, reduction) "
               "VALUES('build', 'M', ?, ?, 'done', ?, ?, 3600)", main["id"], alt["id"], t - 60, t - 60)
    got = [n for n in due_notices(svc, ZoneInfo("Europe/Moscow"), t) if n.kind == "got"]
    assert got and all(n.player_id == main["id"] for n in got)
    assert any(n.title.startswith("[Twink]") and n.key.startswith(f"c{alt['id']}:") for n in got)
    assert re.match(r"\[Twink\]", got[0].title)
