"""Опросы R4: обычные (свои варианты) и «Рейд на резервуар» (3 времени на выбор + основа/резерв).

Голосует персонаж (у твинка свой голос). Итоги видят все — союзу так проще договориться.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from . import audience
from .service import Service

ROLES = ("main", "reserve", "no")
ROLE_NAME = {"main": "Участвую", "reserve": "В резерв", "no": "Не смогу"}
RESERVOIR_MAIN, RESERVOIR_RESERVE, RESERVOIR_MIN_PP = 30, 10, 12


@dataclass
class OptionResult:
    index: int
    label: str  # текст варианта или время (UTC, секунды) для reservoir
    at: int | None = None
    votes: list = field(default_factory=list)  # строки игроков
    main: list = field(default_factory=list)
    reserve: list = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.main) if self.at is not None else len(self.votes)


@dataclass
class Poll:
    row: object
    options: list[OptionResult]
    voters: int
    my_choices: list[int]
    my_role: str
    no: list = field(default_factory=list)

    @property
    def id(self) -> int:
        return self.row["id"]

    @property
    def is_reservoir(self) -> bool:
        return self.row["kind"] == "reservoir"

    @property
    def voted(self) -> bool:
        return bool(self.my_choices or self.my_role)

    def is_open(self, now: int) -> bool:
        return not self.row["closed"] and (self.row["closes_at"] is None or self.row["closes_at"] > now)

    @property
    def best(self) -> OptionResult | None:
        """Вариант с наибольшим числом голосов (для воды — участников основы)."""
        top = max(self.options, key=lambda o: (o.count, len(o.reserve)), default=None)
        return top if top and top.count else None

    def reach(self, svc: Service) -> tuple[list, list]:
        """Аналитика для R4: (кому адресован, кто ещё не ответил)."""
        members = audience.members(svc, self.row["audience"])
        answered = {v["player_id"] for o in self.options for v in o.votes + o.main + o.reserve} | {v["player_id"] for v in self.no}
        return members, [m for m in members if m["id"] not in answered]

    def roster(self) -> tuple[list, list]:
        """Состав на лучшее время: основа (до 30) и резерв (до 10). Сначала — у кого выше Электростанция."""
        best = self.best
        if best is None:
            return [], []
        key = lambda p: -(p["pp_level"] or 0)  # noqa: E731
        main = sorted(best.main, key=key)
        reserve = sorted(best.reserve, key=key) + main[RESERVOIR_MAIN:]
        return main[:RESERVOIR_MAIN], reserve[:RESERVOIR_RESERVE]


def create(svc: Service, kind: str, title: str, note: str, options: list, multi: bool,
           closes_at: int | None, by: int, now: int, aud: str = "") -> int:
    cur = svc.db.run(
        "INSERT INTO polls(kind, title, note, options, multi, closes_at, created_by, created_at, audience) "
        "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
        kind, title.strip()[:200], note.strip()[:1000], json.dumps(options, ensure_ascii=False),
        int(multi or kind == "reservoir"), closes_at, by, now, aud,
    )
    return cur.lastrowid


def vote(svc: Service, poll_id: int, player_id: int, choices: list[int], role: str, now: int) -> str | None:
    row = svc.db.one("SELECT * FROM polls WHERE id = ?", poll_id)
    if row is None:
        return "missing"
    if row["closed"] or (row["closes_at"] and row["closes_at"] <= now):
        return "closed"
    n = len(json.loads(row["options"]))
    choices = sorted({c for c in choices if 0 <= c < n})
    if row["kind"] == "reservoir":
        if role not in ROLES:
            return "role"
        if role == "no":
            choices = []
        elif not choices:
            return "time"
    else:
        role = ""
        if not choices:
            return "empty"
        if not row["multi"]:
            choices = choices[:1]
    svc.db.run(
        "INSERT INTO poll_votes(poll_id, player_id, choices, role, updated_at) VALUES(?, ?, ?, ?, ?) "
        "ON CONFLICT(poll_id, player_id) DO UPDATE SET choices = excluded.choices, role = excluded.role, updated_at = excluded.updated_at",
        poll_id, player_id, json.dumps(choices), role, now,
    )
    return None


def close(svc: Service, poll_id: int) -> None:
    svc.db.run("UPDATE polls SET closed = 1 WHERE id = ?", poll_id)


def delete(svc: Service, poll_id: int) -> None:
    svc.db.run("DELETE FROM polls WHERE id = ?", poll_id)


def load(svc: Service, row, player_id: int | None) -> Poll:
    labels = json.loads(row["options"])
    reservoir = row["kind"] == "reservoir"
    opts = [OptionResult(i, str(v), int(v) if reservoir else None) for i, v in enumerate(labels)]
    votes = svc.db.all(
        "SELECT v.*, p.nick, p.pp_level FROM poll_votes v JOIN players p ON p.id = v.player_id "
        "WHERE v.poll_id = ? ORDER BY p.nick_key", row["id"],
    )
    mine, role, no = [], "", []
    for v in votes:
        chosen = json.loads(v["choices"])
        if v["player_id"] == player_id:
            mine, role = chosen, v["role"]
        if reservoir and v["role"] == "no":
            no.append(v)
        for c in chosen:
            if 0 <= c < len(opts):
                o = opts[c]
                if reservoir:
                    (o.main if v["role"] == "main" else o.reserve).append(v)
                else:
                    o.votes.append(v)
    return Poll(row, opts, len(votes), mine, role, no)


def visible(svc: Service, row, player, everything: bool = False) -> bool:
    return everything or audience.includes(svc, row["audience"], player)


def listing(svc: Service, player, now: int, limit: int = 30, everything: bool = False) -> tuple[list[Poll], list[Poll]]:
    """(открытые, недавно закрытые) — только те, что адресованы персонажу (R4 видят все)."""
    rows = [r for r in svc.db.all("SELECT * FROM polls ORDER BY id DESC LIMIT ?", limit) if visible(svc, r, player, everything)]
    polls = [load(svc, r, player["id"]) for r in rows]
    return [p for p in polls if p.is_open(now)], [p for p in polls if not p.is_open(now)]


def get(svc: Service, poll_id: int, player_id: int | None) -> Poll | None:
    row = svc.db.one("SELECT * FROM polls WHERE id = ?", poll_id)
    return load(svc, row, player_id) if row else None


def unanswered(svc: Service, player, now: int, since: int = 0):
    """Открытые опросы, адресованные персонажу, без его ответа (новые или с недавним «напомнить»)."""
    rows = svc.db.all(
        "SELECT * FROM polls p WHERE p.closed = 0 AND (p.closes_at IS NULL OR p.closes_at > ?) "
        "AND (p.created_at >= ? OR COALESCE(p.nudged_at, 0) >= ?) "
        "AND NOT EXISTS (SELECT 1 FROM poll_votes v WHERE v.poll_id = p.id AND v.player_id = ?)",
        now, since, since, player["id"],
    )
    return [r for r in rows if audience.includes(svc, r["audience"], player)]


def waiting_for(svc: Service, player, now: int) -> int:
    """Сколько открытых опросов ждут ответа этого персонажа."""
    return len(unanswered(svc, player, now))


def nudge(svc: Service, poll_id: int, now: int) -> None:
    svc.db.run("UPDATE polls SET nudged_at = ? WHERE id = ?", now, poll_id)
