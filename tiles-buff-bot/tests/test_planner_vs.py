from core import i18n, planner, vs
from core.timeparse import DAY


def test_parse_and_format_amounts(monkeypatch):
    monkeypatch.setattr(i18n, "ENABLED", tuple(i18n.LANGS))
    assert planner.parse_amount("1,12 млн") == 1_120_000
    assert planner.parse_amount("351 000") == 351_000
    assert planner.parse_amount("—") == 0
    assert planner.fmt_amount(119_860_000) == "120 млн"
    assert planner.fmt_amount(53_500_000) == "53,5 млн"
    with i18n.using("en"):
        assert planner.fmt_amount(1_080_000_000) == "1.08B"
        assert planner.fmt_amount(351_000) == "351K"


def test_pp_path_to_30():
    assert planner.pp_path(None) is None
    way = planner.pp_path(24)
    assert way.next.level == 25 and len(way.steps) == 6
    assert ("Станция связи", 24) in way.next.requires
    assert way.seconds > 150 * DAY
    assert dict(way.upgrade)["Лаборатория 1"] == 29
    assert planner.pp_path(30).next is None


def test_advice_has_placeholders_only_where_needed():
    tips = planner.advice(24, 15)
    assert any(kw == {"pct": "15"} for _, kw in tips)
    assert all("{pct}" in text or not kw for text, kw in tips)


def test_vs_days_follow_utc():
    monday = 4 * DAY  # 05.01.1970 — понедельник
    assert vs.today(monday).day.num == 1
    assert vs.today(monday + 1 * DAY + 3600).day.theme == "Стройка"
    sunday = vs.today(monday + 6 * DAY)
    assert sunday.day is None and sunday.tomorrow.num == 1
    tue = vs.today(monday + DAY)
    assert tue.windows() == [] and tue.ends_at == monday + 2 * DAY  # темы стройки в «Игре по-крупному» нет
    wed = vs.today(monday + 2 * DAY)
    assert [(w.theme.code, w.msk, w.utc) for w in wed.windows()] == [
        ("tech", "03:00–07:00", "00:00–04:00"), ("titan", "11:00–15:00", "08:00–12:00")]


def test_power_play_calendar_matches_game():
    from core import powerplay
    monday = 4 * DAY
    cur, nxt = powerplay.now_and_next(monday + 8 * 3600 + 1800)  # пн 11:30 МСК — как на скриншоте
    assert cur.theme.code == "tech" and cur.msk == "11:00–15:00" and nxt.theme.code == "gear"
    assert powerplay.slot_at(monday + 6 * DAY + 20 * 3600).theme.code == "chef"  # вс 23:00 МСК
    assert all(len(set(day)) == 6 for day in powerplay.CALENDAR.values())
    rows = powerplay.week_table()
    assert rows[0][0] == "03:00" and rows[-1][0] == "23:00" and len(rows[0][1]) == 7
