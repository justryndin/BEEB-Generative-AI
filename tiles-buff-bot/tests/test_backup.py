import sqlite3

from core import backup
from core.db import Database


def test_daily_backup_keeps_last(tmp_path):
    db = tmp_path / "bot.db"
    d = Database(str(db))
    d.run("INSERT INTO settings(key, value) VALUES('x', '1')")
    day = 86400
    for i in range(16):
        assert backup.make(str(db), 20000 * day + i * day, keep=14) is not None
    names = [n for n, _ in backup.listing(str(db))]
    assert len(names) == 14 and names[0] > names[-1]
    again = backup.make(str(db), 20000 * day + 15 * day + 3600, keep=14)  # тот же день — новый файл не нужен
    assert again.name == names[0]
    row = sqlite3.connect(backup.latest(str(db))).execute("SELECT value FROM settings WHERE key = 'x'").fetchone()
    assert row == ("1",)
    assert backup.make(":memory:", 0) is None
