from core import i18n, planner, vs
from core.timeparse import DAY


def test_parse_and_format_amounts():
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
    assert tue.windows() == [("Ускорения стройки", 3, 7, 0, 4)]
    assert tue.ends_at == monday + 2 * DAY
