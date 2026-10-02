from core.db import Database
from core.service import Service, split_nick_kind
from core.timeparse import DAY, HOUR

T0 = 1_700_000_000


def make():
    return Service(Database(":memory:"))


def reg(svc, tg, nick):
    player, err = svc.register(tg, None, nick, T0)
    assert err is None
    return player


def test_register_links_offline_player_and_rejects_taken_nick():
    svc = make()
    offline = svc.create_offline_player("Мура", T0)
    svc.set_timer(offline["id"], "build", 21 * DAY, T0)
    player = reg(svc, 111, "  мура ")
    assert player["id"] == offline["id"] and player["tg_id"] == 111
    _, err = svc.register(222, None, "МУРА", T0)
    assert err == "taken"


def test_full_flow_with_rotation_and_donor_self_buff():
    svc = make()
    svc.set_setting("pattern", "BBW")
    svc.set_setting("min_gap_hours", "0")
    svc.set_setting("fair_round", "0")
    donor = reg(svc, 1, "Донор")
    big = reg(svc, 2, "Большой")
    small = reg(svc, 3, "Малый")
    svc.set_timer(donor["id"], "build", 30 * DAY, T0)
    svc.set_timer(big["id"], "build", 100 * DAY, T0)
    svc.set_timer(small["id"], "build", 15 * DAY, T0 + 10)

    picks = []
    t = T0 + HOUR
    for _ in range(3):
        a = svc.assign("build", donor["id"], 1, t)
        picks.append((a.recipient_nick, a.slot))
        r = svc.confirm(a.donation_id, t)
        assert r is not None
        t += HOUR
    # 2 большим (но не больше 2 подряд одному), 1 — дольше всех ждущему
    assert picks[0] == ("Большой", "B")
    assert picks[1][1] == "B"
    assert picks[2][1] == "W"

    # баф срезал и таймер донора (дважды из трёх раз — когда донор сам не получатель)
    donor_timer = svc.active_timer(donor["id"], "build")
    assert donor_timer["end_at"] < T0 + 30 * DAY
    # кулдаун 48ч
    assert svc.donor_ready_in(donor["id"], "build", t) > 40 * HOUR


def test_pending_reuse_cancel_and_expire():
    svc = make()
    donor = reg(svc, 1, "Донор")
    a1 = reg(svc, 2, "А")
    a2 = reg(svc, 3, "Б")
    svc.set_timer(a1["id"], "research", 50 * DAY, T0)
    svc.set_timer(a2["id"], "research", 40 * DAY, T0)

    first = svc.assign("research", donor["id"], 1, T0)
    again = svc.assign("research", donor["id"], 1, T0)
    assert again.reused and again.donation_id == first.donation_id

    other = svc.reassign(first.donation_id, 1, T0)
    assert other.recipient_id != first.recipient_id
    assert svc.reassign(other.donation_id, 1, T0) is None  # больше некому

    third = svc.assign("research", donor["id"], 1, T0)
    assert svc.expire_pending(T0 + 31 * 60)[0]["id"] == third.donation_id
    assert svc.donation(third.donation_id)["status"] == "expired"


def test_target_reached_notification_once():
    svc = make()
    donor = reg(svc, 1, "Донор")
    p = reg(svc, 2, "П")
    svc.set_timer(p["id"], "build", 10 * DAY, T0)  # 10 → 8.5 → 7 → стоп
    a = svc.assign("build", donor["id"], 1, T0)
    assert not svc.confirm(a.donation_id, T0).recipient_reached_target
    a = svc.assign("build", donor["id"], 1, T0)
    assert svc.confirm(a.donation_id, T0).recipient_reached_target
    assert svc.assign("build", donor["id"], 1, T0) is None


def test_fix_keeps_declared_base():
    svc = make()
    p = reg(svc, 1, "П")
    svc.set_timer(p["id"], "build", 20 * DAY, T0)
    svc.set_timer(p["id"], "build", 10 * DAY, T0, keep_base=True)
    t = svc.active_timer(p["id"], "build")
    assert t["base_seconds"] == 20 * DAY and t["end_at"] == T0 + 10 * DAY


def test_manual_record_and_reminders():
    svc = make()
    donor = svc.create_offline_player("Вася", T0)
    p = reg(svc, 2, "Мура")
    svc.set_timer(p["id"], "build", 30 * DAY, T0)
    r = svc.record_manual(donor["id"], p["id"], "build", 99, T0)
    assert r.reduction == int(4.5 * DAY)
    me = reg(svc, 5, "Я")
    svc.set_timer(me["id"], "research", 30 * DAY, T0)
    q = reg(svc, 6, "Кто-то")
    svc.set_timer(q["id"], "research", 30 * DAY, T0)
    a = svc.assign("research", me["id"], 5, T0)
    svc.confirm(a.donation_id, T0)
    assert svc.due_reminders(T0 + 47 * HOUR) == []
    due = svc.due_reminders(T0 + 48 * HOUR)
    assert [r["player_id"] for r in due] == [me["id"]]  # Вася не в боте — не напоминаем


def test_settings_validation():
    svc = make()
    assert svc.set_setting("pattern", "bbbw") is None and svc.setting("pattern") == "BBBW"
    assert svc.set_setting("pattern", "BXW")
    assert svc.set_setting("build_min", "9")  # больше build_max=7
    assert svc.set_setting("pct", "200")
    assert svc.set_setting("mode", "remaining") is None


def test_split_nick_kind():
    assert split_nick_kind("Тёмный Страж стройка 21д 5ч") == ("Тёмный Страж", "build", "21д 5ч")
    assert split_nick_kind("Мура иссл") == ("Мура", "research", "")
    assert split_nick_kind("стройка 5д") is None
