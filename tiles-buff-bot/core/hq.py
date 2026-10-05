"""Штаб R4: заметки и решения руководства, обсуждение под ними и список «кто что делает».

Видят и пишут только R4. Это не чат: страница сама обновляется раз в ~10 секунд.
Заметки и задачи — от имени аккаунта (не твинка).
"""

from __future__ import annotations

from .service import Service

KINDS = {"note": "Заметка", "decision": "Решение"}
MAX_TEXT = 2000


def add_note(svc: Service, author_id: int, text: str, kind: str, now: int) -> int | None:
    text = text.strip()[:MAX_TEXT]
    if not text:
        return None
    kind = kind if kind in KINDS else "note"
    return svc.db.run("INSERT INTO hq_notes (author_id, kind, text, created_at) VALUES (?, ?, ?, ?)",
                      author_id, kind, text, now).lastrowid


def notes(svc: Service, limit: int = 40) -> list[dict]:
    rows = svc.db.all(
        "SELECT n.*, p.nick AS author FROM hq_notes n LEFT JOIN players p ON p.id = n.author_id "
        "ORDER BY n.pinned DESC, n.created_at DESC, n.id DESC LIMIT ?", limit,
    )
    out = {r["id"]: dict(r, comments=[]) for r in rows}
    if out:
        marks = ",".join("?" * len(out))
        for c in svc.db.all(
            f"SELECT c.*, p.nick AS author FROM hq_comments c LEFT JOIN players p ON p.id = c.author_id "
            f"WHERE c.note_id IN ({marks}) ORDER BY c.created_at, c.id", *out,
        ):
            out[c["note_id"]]["comments"].append(c)
    return list(out.values())


def add_comment(svc: Service, note_id: int, author_id: int, text: str, now: int) -> bool:
    text = text.strip()[:MAX_TEXT]
    if not text or not svc.db.one("SELECT 1 FROM hq_notes WHERE id = ?", note_id):
        return False
    svc.db.run("INSERT INTO hq_comments (note_id, author_id, text, created_at) VALUES (?, ?, ?, ?)",
               note_id, author_id, text, now)
    return True


def pin_note(svc: Service, note_id: int, on: bool) -> None:
    svc.db.run("UPDATE hq_notes SET pinned = ? WHERE id = ?", int(on), note_id)


def delete_note(svc: Service, note_id: int, by_id: int, is_owner: bool) -> bool:
    """Удалить может автор или владелец сайта."""
    row = svc.db.one("SELECT author_id FROM hq_notes WHERE id = ?", note_id)
    if row is None or not (is_owner or row["author_id"] == by_id):
        return False
    svc.db.run("DELETE FROM hq_notes WHERE id = ?", note_id)
    return True


def add_task(svc: Service, title: str, assignee_id: int | None, due_at: int | None, by_id: int, now: int) -> int | None:
    title = title.strip()[:300]
    if not title:
        return None
    return svc.db.run(
        "INSERT INTO hq_tasks (title, assignee_id, due_at, created_by, created_at) VALUES (?, ?, ?, ?, ?)",
        title, assignee_id, due_at, by_id, now,
    ).lastrowid


def tasks(svc: Service, done_days: int = 7, now: int = 0) -> tuple[list, list]:
    """(открытые — сначала со сроком, по сроку; сделанные за последние дни — новые сверху)."""
    base = ("SELECT t.*, a.nick AS assignee, b.nick AS author FROM hq_tasks t "
            "LEFT JOIN players a ON a.id = t.assignee_id LEFT JOIN players b ON b.id = t.created_by ")
    opened = svc.db.all(base + "WHERE t.done_at IS NULL ORDER BY t.due_at IS NULL, t.due_at, t.created_at")
    done = svc.db.all(base + "WHERE t.done_at >= ? ORDER BY t.done_at DESC LIMIT 20", now - done_days * 86400)
    return opened, done


def toggle_task(svc: Service, task_id: int, now: int) -> None:
    svc.db.run("UPDATE hq_tasks SET done_at = CASE WHEN done_at IS NULL THEN ? ELSE NULL END WHERE id = ?", now, task_id)


def delete_task(svc: Service, task_id: int) -> None:
    svc.db.run("DELETE FROM hq_tasks WHERE id = ?", task_id)


def fresh(svc: Service, account_id: int, seen_at: int | None) -> int:
    """Сколько новых заметок и комментариев от других R4 с прошлого визита (для значка в меню)."""
    since = seen_at or 0
    n = svc.db.one("SELECT COUNT(*) AS n FROM hq_notes WHERE created_at > ? AND COALESCE(author_id, 0) != ?",
                   since, account_id)["n"]
    c = svc.db.one("SELECT COUNT(*) AS n FROM hq_comments WHERE created_at > ? AND COALESCE(author_id, 0) != ?",
                   since, account_id)["n"]
    return n + c


def my_open(svc: Service, account_id: int) -> int:
    return svc.db.one("SELECT COUNT(*) AS n FROM hq_tasks WHERE done_at IS NULL AND assignee_id = ?", account_id)["n"]


def mark_seen(svc: Service, account_id: int, now: int) -> None:
    svc.db.run("UPDATE players SET hq_seen_at = ? WHERE id = ?", now, account_id)
