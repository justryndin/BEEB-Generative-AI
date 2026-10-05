"""Лёгкая CRM для R4: доска объявлений, недельные события союза, заметки об игроках.

Время событий хранится серверное (UTC) — как в игре; показываем по местному времени сайта.
"""

from __future__ import annotations

from dataclasses import dataclass

from .service import Service
from .timeparse import DAY, MINUTE

WEEKDAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
WEEKDAYS_FULL = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]

# Стартовый набор событий. Найдено по открытым гайдам (октябрь 2026); источники
# расходятся в деталях, поэтому все помечены «проверить» — R4 сверяют с игрой и правят.
SEED_EVENTS = [
    ("Дуэль союза · день 1: радар и снаряжение героев", "0", 0, 1440,
     "Трать выносливость и разведмиссии радара, улучшай снаряжение героев (обломки). Ускорения пока копим.", -1),
    ("Дуэль союза · день 2: стройка", "1", 0, 1440,
     "Ускорения стройки (лучше 03:00–07:00 МСК = 00:00–04:00 UTC), грузовики A-класса и S-задания, задания на питомцев (2.6.200).", 300),
    ("Дуэль союза · день 3: исследования", "2", 0, 1440,
     "Ускорения исследований, медали состязания, клетки и сыворотки титана.", 300),
    ("Дуэль союза · день 4: герои", "3", 0, 1440, "Карты призыва, фрагменты героев, книги навыков (лучше 15:00–19:00 МСК = 12:00–16:00 UTC).", 300),
    ("Дуэль союза · день 5: войска", "4", 0, 1440, "Обучение и улучшение войск. Освободи гарнизон и запасись лечением к субботе.", 300),
    ("Дуэль союза · день 6: бои", "5", 0, 1440, "Побеждай вражеские войска по плану R4, лечи раненых — очки и за потери союзников.", 300),
    ("Осада заражённых", "1234", 15 * 60, 30,
     "Запускают R4/R5 и выбирают сложность. Нужно 20+ участников онлайн: элитные волны (7, 14, 17) бьют только тех, кто в сети.", 60),
    ("Завоевание Аркадии", "5", 12 * 60, 180,
     "Война за Аркадию: держать её дольше соперников (от 90 мин). За 1 ч до начала поселения в зоне Аркадии телепортирует.", 60),
]


def utc_weekday(ts: int) -> int:
    return (ts // DAY + 3) % 7  # 01.01.1970 — четверг


# Прежние тексты подготовки к Дуэли → новые (обновляем, только если R4 их не меняли).
_PREPARE_V2 = {
    "Задания радара, выносливость, улучшение снаряжения героев. Ускорения пока копим.":
        "Трать выносливость и разведмиссии радара, улучшай снаряжение героев (обломки). Ускорения пока копим.",
    "Тратим ускорения стройки и запускаем улучшения зданий именно сегодня.":
        "Ускорения стройки (лучше 03:00–07:00 МСК = 00:00–04:00 UTC), грузовики A-класса и S-задания, задания на питомцев (2.6.200).",
    "Тратим ускорения исследований, сдаём медали союза.":
        "Ускорения исследований, медали состязания, клетки и сыворотки титана.",
    "Призыв и прокачка героев.":
        "Карты призыва, фрагменты героев, книги навыков (лучше 15:00–19:00 МСК = 12:00–16:00 UTC).",
    "Обучение и улучшение войск, ускорения обучения.":
        "Обучение и улучшение войск. Освободи гарнизон и запасись лечением к субботе.",
    "Бои и уничтожение войск — самые дорогие очки недели.":
        "Побеждай вражеские войска по плану R4, лечи раненых — очки и за потери союзников.",
}


def seed_events(svc: Service) -> None:
    if svc.setting("events_seeded"):
        if not svc.setting("events_v2"):
            with svc.db.tx():
                for old, new in _PREPARE_V2.items():
                    svc.db.run("UPDATE events SET prepare = ? WHERE prepare = ?", new, old)
            svc.set_setting("events_v2", "1")
        return
    with svc.db.tx():
        for title, days, start, duration, prepare, remind in SEED_EVENTS:
            svc.db.run(
                "INSERT INTO events(title, days, start_min, duration, prepare, remind_min, rsvp) VALUES(?, ?, ?, ?, ?, ?, ?)",
                title, days, start, duration, prepare, remind, int("Дуэль" not in title),
            )
    svc.set_setting("events_seeded", "1")
    svc.set_setting("events_v2", "1")


def events(svc: Service):
    return svc.db.all("SELECT * FROM events ORDER BY start_min, id")


def event(svc: Service, event_id: int):
    return svc.db.one("SELECT * FROM events WHERE id = ?", event_id)


@dataclass
class Occurrence:
    event: object
    start: int
    end: int

    @property
    def ref(self) -> str:
        return f"event:{self.event['id']}:{self.start}"

    def ongoing(self, now: int) -> bool:
        return self.start <= now < self.end


def occurrences(svc: Service, now: int, days: int = 7) -> list[Occurrence]:
    """Ближайшие события: идущие сейчас и начинающиеся в ближайшие days дней."""
    today = now // DAY * DAY
    out = []
    for e in events(svc):
        for d in range(-1, days + 1):
            day = today + d * DAY
            if str(utc_weekday(day)) not in e["days"]:
                continue
            start = day + e["start_min"] * MINUTE
            end = start + e["duration"] * MINUTE
            if end > now and start < now + days * DAY:
                out.append(Occurrence(e, start, end))
    out.sort(key=lambda o: (o.start, o.event["id"]))
    return out


def save_event(svc: Service, event_id: int | None, title: str, days: str, start_min: int, duration: int,
               prepare: str, remind_min: int, checked: bool, rsvp: bool) -> str | None:
    title = title.strip()[:80]
    days = "".join(sorted({d for d in days if d in "0123456"}))
    if not title:
        return "Нужно название"
    if not days:
        return "Отметь хотя бы один день недели"
    if not 0 <= start_min < 1440 or not 1 <= duration <= 7 * 1440:
        return "Неверное время или длительность"
    args = (title, days, start_min, duration, prepare.strip()[:400], remind_min, int(checked), int(rsvp))
    if event_id:
        svc.db.run("UPDATE events SET title = ?, days = ?, start_min = ?, duration = ?, prepare = ?, remind_min = ?, "
                   "checked = ?, rsvp = ? WHERE id = ?", *args, event_id)
    else:
        svc.db.run("INSERT INTO events(title, days, start_min, duration, prepare, remind_min, checked, rsvp) "
                   "VALUES(?, ?, ?, ?, ?, ?, ?, ?)", *args)
    return None


def delete_event(svc: Service, event_id: int) -> None:
    svc.db.run("DELETE FROM events WHERE id = ?", event_id)
    svc.db.run("DELETE FROM answers WHERE ref LIKE ?", f"event:{event_id}:%")


# ---------- доска объявлений ----------

def add_post(svc: Service, author_id: int, text: str, pinned: bool, important: bool, rsvp: bool, now: int) -> int | None:
    text = text.strip()[:2000]
    if not text:
        return None
    cur = svc.db.run(
        "INSERT INTO posts(author_id, text, pinned, important, rsvp, created_at) VALUES(?, ?, ?, ?, ?, ?)",
        author_id, text, int(pinned), int(important), int(rsvp), now,
    )
    svc.db.run("INSERT OR IGNORE INTO post_reads(post_id, player_id) VALUES(?, ?)", cur.lastrowid, author_id)
    return cur.lastrowid


def posts(svc: Service, player_id: int, limit: int = 50):
    return svc.db.all(
        "SELECT p.*, a.nick AS author, "
        "(SELECT COUNT(*) FROM post_reads r WHERE r.post_id = p.id) AS reads, "
        "EXISTS(SELECT 1 FROM post_reads r WHERE r.post_id = p.id AND r.player_id = ?) AS seen "
        "FROM posts p LEFT JOIN players a ON a.id = p.author_id ORDER BY p.pinned DESC, p.created_at DESC LIMIT ?",
        player_id, limit,
    )


def pinned_posts(svc: Service, limit: int = 2):
    return svc.db.all(
        "SELECT p.*, a.nick AS author FROM posts p LEFT JOIN players a ON a.id = p.author_id "
        "WHERE p.pinned = 1 ORDER BY p.created_at DESC LIMIT ?", limit,
    )


def unread(svc: Service, player_id: int) -> int:
    return svc.db.one(
        "SELECT COUNT(*) AS n FROM posts p WHERE NOT EXISTS("
        "SELECT 1 FROM post_reads r WHERE r.post_id = p.id AND r.player_id = ?)", player_id,
    )["n"]


def mark_read(svc: Service, player_id: int) -> None:
    svc.db.run("INSERT OR IGNORE INTO post_reads(post_id, player_id) SELECT id, ? FROM posts", player_id)


def set_pin(svc: Service, post_id: int, pinned: bool) -> None:
    svc.db.run("UPDATE posts SET pinned = ? WHERE id = ?", int(pinned), post_id)


def delete_post(svc: Service, post_id: int) -> None:
    svc.db.run("DELETE FROM posts WHERE id = ?", post_id)
    svc.db.run("DELETE FROM answers WHERE ref = ?", f"post:{post_id}")


# ---------- «Буду / Не смогу» ----------

def answer(svc: Service, ref: str, player_id: int, value: str, now: int) -> None:
    if value not in ("yes", "no"):
        svc.db.run("DELETE FROM answers WHERE ref = ? AND player_id = ?", ref, player_id)
        return
    svc.db.run(
        "INSERT INTO answers(ref, player_id, answer, at) VALUES(?, ?, ?, ?) "
        "ON CONFLICT(ref, player_id) DO UPDATE SET answer = excluded.answer, at = excluded.at",
        ref, player_id, value, now,
    )


def answers(svc: Service, refs: list[str]) -> dict[str, dict]:
    """{ref: {"yes": [ники], "no": [ники], "mine": {игрок: ответ}}}"""
    out = {r: {"yes": [], "no": [], "by": {}} for r in refs}
    if not refs:
        return out
    marks = ",".join("?" * len(refs))
    for row in svc.db.all(
        f"SELECT a.ref, a.answer, a.player_id, p.nick FROM answers a JOIN players p ON p.id = a.player_id "
        f"WHERE a.ref IN ({marks}) ORDER BY p.nick", *refs,
    ):
        out[row["ref"]][row["answer"]].append(row["nick"])
        out[row["ref"]]["by"][row["player_id"]] = row["answer"]
    return out


# ---------- заметки об игроках ----------

def save_note(svc: Service, player_id: int, note: str, tags: str) -> None:
    clean = ", ".join(t.strip()[:24] for t in tags.split(",") if t.strip())[:200]
    svc.db.run("UPDATE players SET crm_note = ?, crm_tags = ? WHERE id = ?", note.strip()[:1000], clean, player_id)


def tags(player) -> list[str]:
    return [t.strip() for t in (player["crm_tags"] or "").split(",") if t.strip()]
