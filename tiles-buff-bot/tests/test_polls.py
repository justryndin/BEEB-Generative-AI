from .test_web import csrf, register, site  # noqa: F401  (фикстура)


def make_r4(svc, owner):
    register(owner, "Boss")
    svc.set_owner(svc.player_by_nick("Boss")["id"])


def test_reservoir_poll_flow(site):  # noqa: F811
    svc, owner, player = site
    make_r4(svc, owner)
    register(player, "Ana")
    svc.db.run("UPDATE players SET pp_level = 25 WHERE nick = 'Ana'")
    page = owner.get("/polls/new?kind=reservoir").text
    assert "Время 1" in page and "Кому" in page
    r = owner.post("/polls/create", data={"csrf": csrf(page), "kind": "reservoir", "title": "",
                                          "t1": "2026-10-07T19:00", "t2": "2026-10-08T12:00", "t3": "", "aud": "all"})
    assert r.status_code == 200
    polls_page = player.get("/polls").text
    assert "Рейд на резервуар: время и состав" in polls_page and "19:00 МСК" in polls_page and "16:00 UTC" in polls_page
    assert 'class="dotcount">1<' in player.get("/").text  # опрос ждёт ответа
    pid = svc.db.one("SELECT id FROM polls")["id"]
    player.post(f"/polls/{pid}/vote", data={"csrf": csrf(polls_page), "c": ["0", "1"], "role": "main"})
    poll = __import__("core.polls", fromlist=["get"]).get(svc, pid, None)
    assert [len(o.main) for o in poll.options] == [1, 1] and poll.best.index == 0
    # R4 видит охват и кто молчит
    r4 = owner.get("/polls").text
    assert "Охват" in r4 and "Ещё не ответили" in r4 and "Boss" in r4.split("Ещё не ответили")[1]
    # «не смогу» снимает выбранное время
    player.post(f"/polls/{pid}/vote", data={"csrf": csrf(polls_page), "c": ["0"], "role": "no"})
    poll = __import__("core.polls", fromlist=["get"]).get(svc, pid, None)
    assert poll.best is None and len(poll.no) == 1
    owner.post(f"/polls/{pid}/close", data={"csrf": csrf(r4)})
    assert "⚠️ Опрос уже закрыт" in player.post(f"/polls/{pid}/vote", data={"csrf": csrf(polls_page), "c": ["0"], "role": "main"}).text


def test_audience_limits_posts_and_polls(site):  # noqa: F811
    svc, owner, player = site
    make_r4(svc, owner)
    register(player, "Ana")
    ana = svc.player_by_nick("Ana")
    page = owner.get("/board").text
    owner.post("/board", data={"csrf": csrf(page), "text": "Только для R4", "aud": "r4"})
    owner.post("/board", data={"csrf": csrf(page), "text": "Лично Ане", "aud": "one", "aud_one": str(ana["id"])})
    r4_board = owner.get("/board").text
    assert "Только R4" in r4_board and "Лично: Ana" in r4_board and "Не прочитали" in r4_board
    board = player.get("/board").text
    assert "Только для R4" not in board and "Лично Ане" in board
    assert "Не прочитали" not in owner.get("/board").text  # Аня прочитала
    np = owner.get("/polls/new?kind=generic").text
    owner.post("/polls/create", data={"csrf": csrf(np), "kind": "generic", "title": "Секрет R4", "options": "Да\nНет", "aud": "r4"})
    assert "Секрет R4" not in player.get("/polls").text and "Секрет R4" in owner.get("/polls").text
    pid = svc.db.one("SELECT id FROM polls")["id"]
    assert player.post(f"/polls/{pid}/vote", data={"csrf": csrf(board), "c": ["0"]}).status_code == 404


def test_faq_search_and_dashboard(site):  # noqa: F811
    svc, owner, _ = site
    register(owner, "Ana")
    home = owner.get("/").text
    for part in ("VS сегодня", "Игра по-крупному", "Ближайшее событие", "Мои персонажи", "Частые вопросы"):
        assert part in home
    faq = owner.get("/faq").text
    assert 'id="water"' in faq and "Когда вода и как туда попасть?" in faq
    found = owner.get("/search?q=вода").text
    assert "/faq#water" in found and "/guides/reservoir" in found
    assert "Ничего не нашлось" in owner.get("/search?q=zzzqqq").text
    from core import faq as faq_mod
    assert all(x.slug in faq_mod.BY_SLUG for x in faq_mod.top(6))
