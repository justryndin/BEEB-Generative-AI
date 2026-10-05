from zoneinfo import ZoneInfo

from core import polls, todo

from .test_service import T0, make, reg


def test_today_tasks_appear_and_disappear():
    svc = make()
    a, b = reg(svc, 1, "Аня"), reg(svc, 2, "Борис")
    svc.db.run("UPDATE players SET agreed_at = ? WHERE id IN (?, ?)", T0, a["id"], b["id"])
    svc.set_timer(b["id"], "build", 20 * 86400, T0, item="pp", level=25)
    me = svc.player(a["id"])
    t = todo.build(svc, me, T0 + 60, ZoneInfo("Europe/Moscow"), has_push=False)
    keys = [x.key for x in t.todo]
    assert "give:build" in keys and "pp" in keys and "push" in keys
    give = next(x for x in t.todo if x.key == "give:build")
    assert "Борис" in give.title and give.url == "/give?kind=build"
    assert any(g.key == "pp-now" for g in t.game)

    pid = polls.create(svc, "generic", "Время сбора?", "", ["18:00", "20:00"], False, None, b["id"], T0)
    assert any(x.key == f"poll:{pid}" for x in todo.build(svc, me, T0 + 60, ZoneInfo("UTC"), True).todo)
    polls.vote(svc, pid, a["id"], [0], "", T0 + 120)
    svc.db.run("UPDATE players SET pp_level = 22 WHERE id = ?", a["id"])
    t = todo.build(svc, svc.player(a["id"]), T0 + 180, ZoneInfo("UTC"), True)
    keys = [x.key for x in t.todo]
    assert f"poll:{pid}" not in keys and "pp" not in keys and "push" not in keys
