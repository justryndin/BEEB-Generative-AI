import sqlite3

from core import gamedata
from core.db import Database
from core.service import Service
from core.timeparse import DAY, HOUR, MINUTE

T0 = 1_700_000_000


def test_power_plant_times_parse():
    pp = gamedata.item("pp")
    assert pp.time_for(24) == 13 * DAY + 2 * HOUR + 33 * MINUTE
    assert pp.time_for(30) == 40 * DAY + 4 * HOUR + 27 * MINUTE
    assert all(pp.time_for(level) for level in range(2, 31))
    assert pp.time_for(1) is None and pp.time_for(31) is None


def test_every_item_has_unique_code_and_time_strings_parse():
    codes = [i.code for i in gamedata.BUILDINGS + gamedata.RESEARCH]
    assert len(codes) == len(set(codes))
    for it in gamedata.BUILDINGS:
        for level in it.times:
            assert it.time_for(level), (it.code, level)


def test_label():
    assert gamedata.label("pp", 24) == "Электростанция → 24"
    assert gamedata.label("eco", None, "Скорость строительства 7") == "Экономика: Скорость строительства 7"
    assert gamedata.label(None, None) == ""


def test_timer_stores_item_and_fix_keeps_it():
    svc = Service(Database(":memory:"))
    p, _ = svc.register(1, None, "П", T0)
    svc.set_timer(p["id"], "build", 13 * DAY, T0, item="pp", level=24)
    svc.set_timer(p["id"], "build", 10 * DAY, T0, keep_base=True)
    t = svc.active_timer(p["id"], "build")
    assert (t["item"], t["level"], t["base_seconds"]) == ("pp", 24, 13 * DAY)
    rows = svc.queue_view("build", T0).rows
    assert rows[0].label == "Электростанция → 24"


def test_observed_times_median():
    svc = Service(Database(":memory:"))
    for i, days in enumerate((10, 12, 30), 1):
        p, _ = svc.register(i, None, f"p{i}", T0)
        svc.set_timer(p["id"], "build", days * DAY, T0, item="lab", level=24)
    assert svc.observed_times("lab") == [("lab", 24, 12 * DAY, 3)]


def test_migration_adds_columns_to_old_database(tmp_path):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE timers (id INTEGER PRIMARY KEY, player_id INTEGER NOT NULL, kind TEXT NOT NULL, "
        "base_seconds INTEGER NOT NULL, end_at INTEGER NOT NULL, urgent INTEGER NOT NULL DEFAULT 0, "
        "active INTEGER NOT NULL DEFAULT 1, target_notified INTEGER NOT NULL DEFAULT 0, "
        "buffs_received INTEGER NOT NULL DEFAULT 0, last_buff_at INTEGER, created_at INTEGER NOT NULL)"
    )
    conn.commit()
    conn.close()
    db = Database(str(path))
    columns = {r["name"] for r in db.all("PRAGMA table_info(timers)")}
    assert {"item", "level", "note"} <= columns
