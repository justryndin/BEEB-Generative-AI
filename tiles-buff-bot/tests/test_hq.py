from .test_polls import make_r4
from .test_web import csrf, register, site  # noqa: F401  (фикстура)


def test_hq_is_r4_only_with_notes_comments_and_tasks(site):  # noqa: F811
    svc, owner, player = site
    make_r4(svc, owner)
    register(player, "Ana")
    assert player.get("/hq").status_code == 403
    assert "Штаб R4" not in player.get("/").text

    page = owner.get("/hq").text
    token = csrf(page)
    owner.post("/hq/note", data={"csrf": token, "text": "Воду ставим на 20:00 МСК", "kind": "decision"})
    nid = svc.db.one("SELECT id FROM hq_notes")["id"]
    owner.post(f"/hq/note/{nid}/comment", data={"csrf": token, "text": "Резерв собирает Скальд"})
    ana = svc.player_by_nick("Ana")["id"]
    boss = svc.player_by_nick("Boss")["id"]
    owner.post("/hq/task", data={"csrf": token, "title": "Собрать резерв", "assignee": boss, "due": "2026-10-07T19:00"})
    owner.post("/hq/task", data={"csrf": token, "title": "Не R4", "assignee": ana})  # игроку задачу не назначить
    hq = owner.get("/hq").text
    assert "Решение" in hq and "Воду ставим" in hq and "Резерв собирает Скальд" in hq and "Собрать резерв" in hq
    assert svc.db.one("SELECT assignee_id FROM hq_tasks WHERE title = 'Не R4'")["assignee_id"] is None
    assert 'class="dotcount">1<' in owner.get("/board").text  # моя открытая задача — значок у «Штаба R4»

    tid = svc.db.one("SELECT id FROM hq_tasks WHERE title = 'Собрать резерв'")["id"]
    owner.post(f"/hq/task/{tid}/toggle", data={"csrf": token})
    assert svc.db.one("SELECT done_at FROM hq_tasks WHERE id = ?", tid)["done_at"] is not None
    assert "Сделано за неделю (1)" in owner.get("/hq").text
    owner.post(f"/hq/note/{nid}/delete", data={"csrf": token})
    assert svc.db.one("SELECT COUNT(*) AS n FROM hq_comments")["n"] == 0


def test_hq_badge_counts_news_from_other_r4(site):  # noqa: F811
    svc, owner, player = site
    make_r4(svc, owner)
    register(player, "Kira")
    kira = svc.player_by_nick("Kira")["id"]
    svc.db.run("UPDATE players SET is_admin = 1 WHERE id = ?", kira)
    owner.get("/hq")  # Boss заходил — всё прочитано
    from core import hq
    svc.db.run("UPDATE players SET hq_seen_at = 1 WHERE nick = 'Boss'")
    hq.add_note(svc, kira, "Новая мысль", "note", 10)
    assert hq.fresh(svc, svc.player_by_nick("Boss")["id"], 1) == 1
    assert hq.fresh(svc, kira, 1) == 0  # своё — не новое
    page = owner.get("/hq").text
    assert "новое" in page and "Новая мысль" in page
    assert hq.fresh(svc, svc.player_by_nick("Boss")["id"], svc.player_by_nick("Boss")["hq_seen_at"]) == 0
