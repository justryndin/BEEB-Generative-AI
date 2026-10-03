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
    svc.set_setting("queue_order", "cycle")
    svc.set_setting("hold_hours", "0")
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


def test_queue_order_respects_pause_and_fair_round():
    svc = make()
    big = reg(svc, 1, "Большой")
    mid = reg(svc, 2, "Средний")
    small = reg(svc, 3, "Малый")
    donor = reg(svc, 4, "Донор")
    svc.set_timer(big["id"], "build", 60 * DAY, T0)
    svc.set_timer(mid["id"], "build", 30 * DAY, T0)
    svc.set_timer(small["id"], "build", 12 * DAY, T0)
    order = [r.candidate.nick for r in svc.queue_order("build", T0 + HOUR) if r.need]
    assert order[0] == "Большой" and set(order) == {"Большой", "Средний", "Малый"}

    result, error = svc.record_gift("build", donor["id"], big["id"], donor["id"], T0 + HOUR)
    assert error is None and result.recipient_nick == "Большой"
    rows = svc.queue_order("build", T0 + 2 * HOUR)
    first = rows[0]
    assert first.candidate.nick != "Большой"  # у него пауза и он уже на круг впереди
    paused = next(r for r in rows if r.candidate.nick == "Большой")
    assert paused.paused_for > 0


def test_record_gift_guards():
    svc = make()
    a = reg(svc, 1, "А")
    b = reg(svc, 2, "Б")
    svc.set_timer(b["id"], "research", 30 * DAY, T0)
    assert svc.record_gift("research", a["id"], a["id"], a["id"], T0)[1] == "self"
    assert svc.record_gift("build", a["id"], b["id"], a["id"], T0)[1] == "not_in_queue"
    assert svc.record_gift("research", a["id"], b["id"], a["id"], T0)[1] is None
    assert svc.record_gift("research", a["id"], b["id"], a["id"], T0 + 60)[1] == "duplicate"
    assert svc.record_gift("research", a["id"], b["id"], a["id"], T0 + 11 * 60)[1] is None


def test_undo_restores_everything_exactly():
    svc = make()
    donor = reg(svc, 1, "Донор")
    rec = reg(svc, 2, "Получатель")
    svc.set_timer(donor["id"], "research", 40 * DAY, T0)
    svc.set_timer(rec["id"], "research", 30 * DAY, T0)
    before_rec = dict(svc.active_timer(rec["id"], "research"))
    before_donor = dict(svc.active_timer(donor["id"], "research"))
    assert svc.cooldown(donor["id"], "research") is None

    result, error = svc.record_gift("research", donor["id"], rec["id"], donor["id"], T0 + HOUR)
    assert error is None
    after = svc.active_timer(rec["id"], "research")
    assert after["buffs_received"] == 1 and after["end_at"] < before_rec["end_at"]
    assert svc.active_timer(donor["id"], "research")["end_at"] < before_donor["end_at"]

    d, err, exact = svc.undo_donation(result.donation_id, donor["id"], T0 + 2 * HOUR)
    assert err is None and exact and d["status"] == "undone" and d["undone_by"] == donor["id"]
    restored = svc.active_timer(rec["id"], "research")
    for key in ("end_at", "buffs_received", "last_buff_at", "urgent", "target_notified"):
        assert restored[key] == before_rec[key], key
    assert svc.active_timer(donor["id"], "research")["end_at"] == before_donor["end_at"]
    assert svc.cooldown(donor["id"], "research") is None
    # отменённый баф не считается в статистике и очереди
    assert svc.totals()["buffs"] == 0
    assert svc.undo_donation(result.donation_id, donor["id"], T0 + 3 * HOUR)[1] == "not_done"
    # после отмены можно сразу записать правильный баф (защита от дубля не мешает)
    assert svc.record_gift("research", donor["id"], rec["id"], donor["id"], T0 + 3 * HOUR)[1] is None


def test_undo_reactivates_timer_closed_by_the_buff():
    svc = make()
    donor = reg(svc, 1, "Донор")
    rec = reg(svc, 2, "Получатель")
    svc.set_timer(rec["id"], "build", 10 * DAY, T0)
    svc.set_timer(rec["id"], "build", 2 * HOUR, T0 + 10, keep_base=True)  # почти закончилась
    result, _ = svc.record_gift("build", donor["id"], rec["id"], donor["id"], T0 + 20)
    assert svc.active_timer(rec["id"], "build") is None  # баф закрыл таймер
    svc.undo_donation(result.donation_id, donor["id"], T0 + 30)
    t = svc.active_timer(rec["id"], "build")
    assert t is not None and t["end_at"] == T0 + 10 + 2 * HOUR


def test_undo_legacy_record_without_undo_info():
    svc = make()
    donor = reg(svc, 1, "Донор")
    rec = reg(svc, 2, "Получатель")
    svc.set_timer(rec["id"], "build", 30 * DAY, T0)
    result, _ = svc.record_gift("build", donor["id"], rec["id"], donor["id"], T0 + HOUR)
    svc.db.run("UPDATE donations SET undo = NULL WHERE id = ?", result.donation_id)
    end_after = svc.active_timer(rec["id"], "build")["end_at"]
    _, err, exact = svc.undo_donation(result.donation_id, donor["id"], T0 + 2 * HOUR)
    assert err is None and not exact
    assert svc.active_timer(rec["id"], "build")["end_at"] == end_after + result.reduction


# ---------- очередь «по доле», взаимность, приоритет, жизнь записи ----------

def test_share_order_reasons_priority_and_holding():
    svc = make()
    svc.set_setting("min_gap_hours", "0")
    a = reg(svc, 1, "Аня")
    b = reg(svc, 2, "Борис")
    pp = reg(svc, 3, "Электрик")
    svc.set_timer(a["id"], "build", 30 * DAY, T0)
    svc.set_timer(b["id"], "build", 30 * DAY, T0 + 1)
    svc.set_timer(pp["id"], "build", 30 * DAY, T0 + 2, item="pp", level=27)
    rows = svc.queue_order("build", T0 + 10)
    assert [r.candidate.nick for r in rows][:1] == ["Аня"]  # все по нулям — дольше всех ждёт Аня
    assert rows[0].why.startswith("ещё не получал")
    assert next(r for r in rows if r.candidate.nick == "Электрик").candidate.priority

    # Аня и Электрик получили по бафу. Доля Электрика считается вдвое меньше — он раньше Бориса? Нет:
    # Борис ещё не получал (0%), он первый; Электрик (1 из N, /2) раньше Ани (1 из N).
    svc.record_gift("build", b["id"], a["id"], b["id"], T0 + 100)
    svc.record_gift("build", a["id"], pp["id"], a["id"], T0 + 200)
    order = [r.candidate.nick for r in svc.queue_order("build", T0 + 300)]
    assert order == ["Борис", "Электрик", "Аня"]

    # Взаимность: прошло 3 суток. Электрик ни разу не отдал баф — пропускает ход.
    # Аня и Борис отдавали, их баф готов только сутки — это в пределах нормы.
    later = T0 + 3 * DAY
    hold = svc.holding_map(later)
    assert pp["id"] in hold and a["id"] not in hold and b["id"] not in hold
    rows = svc.queue_order("build", later)
    assert rows[-1].candidate.nick == "Электрик" and "держит готовый баф" in rows[-1].why


def test_time_check_after_buff_and_ok():
    svc = make()
    a = reg(svc, 1, "Аня")
    b = reg(svc, 2, "Борис")
    svc.set_timer(a["id"], "build", 30 * DAY, T0)
    assert svc.time_checks(a["id"], T0 + 10) == []
    svc.record_gift("build", b["id"], a["id"], b["id"], T0 + 100)
    checks = svc.time_checks(a["id"], T0 + 200)
    assert [c["why"] for c in checks] == ["buff"]
    svc.confirm_time(a["id"], "build", T0 + 300)
    assert svc.time_checks(a["id"], T0 + 400) == []
    assert [c["why"] for c in svc.time_checks(a["id"], T0 + 300 + 49 * 3600)] == ["stale"]


def test_finished_timer_suggests_next_level():
    svc = make()
    a = reg(svc, 1, "Аня")
    svc.set_timer(a["id"], "build", 2 * DAY, T0, item="pp", level=25)
    svc.deactivate_finished(T0 + 3 * DAY)
    [f] = svc.finished_timers(a["id"], T0 + 3 * DAY)
    assert f["next_level"] == 26 and f["next_reference"] > 0
    svc.dismiss_next(a["id"], "build")
    assert svc.finished_timers(a["id"], T0 + 3 * DAY) == []
    # Новая запись тоже снимает подсказку, а «Готово» её показывает.
    svc.set_timer(a["id"], "build", 5 * DAY, T0 + 4 * DAY, item="pp", level=26)
    svc.close_timer(a["id"], "build", T0 + 5 * DAY)
    [f] = svc.finished_timers(a["id"], T0 + 5 * DAY)
    assert f["next_level"] == 27
    # Через 3 дня подсказка исчезает сама.
    assert svc.finished_timers(a["id"], T0 + 9 * DAY) == []


def test_due_notices_ready_got_next_and_quiet():
    from zoneinfo import ZoneInfo
    from core.notify import due_notices
    utc = ZoneInfo("UTC")
    svc = make()
    svc.set_setting("min_gap_hours", "0")
    a = reg(svc, 1, "Аня")
    b = reg(svc, 2, "Борис")
    svc.set_timer(a["id"], "build", 30 * DAY, T0)
    svc.set_timer(b["id"], "build", 20 * DAY, T0)
    noon = T0 - T0 % DAY + 12 * HOUR + DAY
    assert due_notices(svc, utc, noon) == []  # никто не подписан
    svc.add_push(b["id"], "https://push.example/b", "k", "a", noon)
    keys = {n.key.split(":")[0] for n in due_notices(svc, utc, noon)}
    assert "ready" in keys  # Борис должен отдать баф Ане
    svc.record_gift("build", a["id"], b["id"], a["id"], noon)
    got = [n for n in due_notices(svc, utc, noon + 60) if n.key.startswith("got:")]
    assert got and "Аня" in got[0].title
    svc.mark_notice(b["id"], got[0].key, noon + 60)
    assert svc.notice_sent(b["id"], got[0].key)
    # Ночью — тишина, если игрок не выключил «тихие часы»
    night = noon - 9 * HOUR  # 03:00 UTC
    assert due_notices(svc, utc, night) == []
    svc.set_notify_prefs(b["id"], {"off": ["ready"], "quiet": False})
    keys = {n.key.split(":")[0] for n in due_notices(svc, utc, night)}
    assert "ready" not in keys and "got" in keys


def test_notices_for_important_posts_and_events():
    from zoneinfo import ZoneInfo
    from core import crm
    from core.notify import due_notices
    utc = ZoneInfo("UTC")
    svc = make()
    a = reg(svc, 1, "Аня")
    b = reg(svc, 2, "Борис")
    svc.add_push(b["id"], "https://push.example/b", "k", "a", T0)
    monday = (T0 // DAY - (T0 // DAY + 3) % 7) * DAY + 7 * DAY  # ближайший понедельник 00:00 UTC
    crm.save_event(svc, None, "Сбор", "0", 12 * 60, 60, "Щиты!", 60, True, True)
    crm.add_post(svc, a["id"], "Важно: сбор\nподробности", False, True, False, monday + 10 * HOUR)
    keys = {n.key: n for n in due_notices(svc, utc, monday + 11 * HOUR + 10 * 60)}
    post = next(n for k, n in keys.items() if k.startswith("post:"))
    assert post.body == "Важно: сбор" and post.url == "/board"
    ev = next(n for k, n in keys.items() if k.startswith("event:"))
    assert "Сбор" in ev.title and "12:00" in ev.title and ev.body == "Щиты!"
    # Раньше чем за час до начала — ещё не напоминаем
    assert not any(k.startswith("event:") for k in (n.key for n in due_notices(svc, utc, monday + 10 * HOUR + 30 * 60)))
