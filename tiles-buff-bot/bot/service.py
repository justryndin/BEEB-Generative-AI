"""Бизнес-логика бота поверх SQLite. Здесь нет ничего про Telegram."""

from __future__ import annotations

import re
from dataclasses import dataclass

from . import gamedata
from .db import Database
from .logic import (
    SLOT_URGENT,
    STATUS_NEED,
    Candidate,
    Rules,
    buff_reduction,
    buffs_needed,
    choose_recipient,
    timer_status,
)
from .timeparse import DAY, HOUR, MINUTE

KINDS = ("build", "research")
KIND_NAME = {"build": "стройка", "research": "исследование"}
KIND_ACC = {"build": "стройку", "research": "исследование"}
KIND_EMOJI = {"build": "🏗", "research": "🔬"}

_KIND_WORDS = {
    "build": {"стройка", "стройку", "стройки", "строй", "стр", "build"},
    "research": {"исследование", "исследования", "иссл", "наука", "research"},
}

# ключ: (значение по умолчанию, описание)
SETTINGS: dict[str, tuple[str, str]] = {
    "pct": ("15", "Сколько % срезает один баф"),
    "mode": ("declared", "От чего считать %: declared — от заявленного времени, remaining — от остатка"),
    "build_min": ("5", "Стройка: ниже этого остатка (дней) бафом не опускаем"),
    "build_max": ("7", "Стройка: при остатке не больше этого (дней) бафы уже не нужны"),
    "research_min": ("7", "Исследование: ниже этого остатка (дней) бафом не опускаем"),
    "research_max": ("8", "Исследование: при остатке не больше этого (дней) бафы уже не нужны"),
    "pattern": ("BBW", "Цикл ротации: B — самому большому остатку, W — кто дольше ждёт"),
    "max_streak": ("2", "Не больше стольких бафов подряд одному игроку"),
    "cooldown_hours": ("48", "Через сколько часов у игрока снова готов баф"),
    "confirm_minutes": ("30", "Сколько минут держится бронь на назначенный баф"),
    "digest_hour": ("10", "Во сколько (час, 0–23) постить сводку в группу; -1 — не постить"),
}
_INTERNAL_SETTINGS = {"last_digest"}


def parse_kind(word: str) -> str | None:
    w = word.lower().strip()
    for kind, words in _KIND_WORDS.items():
        if w in words:
            return kind
    return None


def nick_key(nick: str) -> str:
    return " ".join(nick.split()).casefold()


def clean_nick(nick: str) -> str:
    return " ".join(nick.split())


def split_nick_kind(args: str) -> tuple[str, str, str] | None:
    """«Мура Ли стройка 21д 5ч» → ("Мура Ли", "build", "21д 5ч")."""
    tokens = args.split()
    for i, token in enumerate(tokens):
        kind = parse_kind(token)
        if kind and i > 0:
            return " ".join(tokens[:i]), kind, " ".join(tokens[i + 1:])
    return None


@dataclass
class Assignment:
    donation_id: int
    kind: str
    slot: str
    donor_id: int
    recipient_id: int
    recipient_nick: str
    recipient_tg: int | None
    recipient_label: str
    remaining: int
    reduction: int
    waiting: int
    reused: bool
    donor_ready_in: int


@dataclass
class BuffResult:
    donation_id: int
    kind: str
    donor_id: int
    donor_nick: str
    donor_tg: int | None
    recipient_id: int
    recipient_nick: str
    recipient_tg: int | None
    reduction: int
    recipient_left: int | None
    recipient_reached_target: bool
    donor_left: int | None
    cooldown_hours: float


@dataclass
class QueueRow:
    candidate: Candidate
    status: str
    needed: int
    pending: bool
    label: str = ""


@dataclass
class QueueView:
    kind: str
    rows: list[QueueRow]
    next_pick: Candidate | None
    next_slot: str
    rules: Rules


class Service:
    def __init__(self, db: Database):
        self.db = db

    # ---------- настройки ----------

    def setting(self, key: str) -> str:
        row = self.db.one("SELECT value FROM settings WHERE key = ?", key)
        if row is not None:
            return row["value"]
        return SETTINGS[key][0] if key in SETTINGS else ""

    def setting_float(self, key: str) -> float:
        return float(self.setting(key))

    def set_setting(self, key: str, value: str) -> str | None:
        """Возвращает текст ошибки или None, если всё хорошо."""
        if key not in SETTINGS and key not in _INTERNAL_SETTINGS:
            return f"Нет такой настройки: {key}"
        value = value.strip()
        if key == "mode":
            if value not in ("declared", "remaining"):
                return "mode: только declared или remaining"
        elif key == "pattern":
            value = value.upper()
            if not re.fullmatch(r"[BW]{1,12}", value):
                return "pattern: только буквы B и W, например BBW"
        elif key in SETTINGS:
            try:
                number = float(value.replace(",", "."))
            except ValueError:
                return f"{key}: нужно число"
            value = f"{number:g}"
            if key == "pct" and not 0 < number < 100:
                return "pct: от 0 до 100"
            if key == "digest_hour" and not (number == -1 or 0 <= number <= 23):
                return "digest_hour: от 0 до 23 или -1"
            if key in ("cooldown_hours", "confirm_minutes") and number <= 0:
                return f"{key}: должно быть больше 0"
            if number < 0 and key != "digest_hour":
                return f"{key}: не может быть отрицательным"
            for kind in KINDS:
                lo, hi = f"{kind}_min", f"{kind}_max"
                if key in (lo, hi):
                    new_lo = number if key == lo else self.setting_float(lo)
                    new_hi = number if key == hi else self.setting_float(hi)
                    if new_lo > new_hi:
                        return f"{lo} не может быть больше {hi}"
        self.db.run(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            key,
            value,
        )
        return None

    def rules(self, kind: str) -> Rules:
        return Rules(
            min_left=int(self.setting_float(f"{kind}_min") * DAY),
            max_left=int(self.setting_float(f"{kind}_max") * DAY),
            pct=self.setting_float("pct"),
            mode=self.setting("mode"),
            pattern=self.setting("pattern"),
            max_streak=int(self.setting_float("max_streak")),
        )

    # ---------- игроки ----------

    def player(self, player_id: int):
        return self.db.one("SELECT * FROM players WHERE id = ?", player_id)

    def player_by_tg(self, tg_id: int):
        return self.db.one("SELECT * FROM players WHERE tg_id = ?", tg_id)

    def player_by_nick(self, nick: str):
        return self.db.one("SELECT * FROM players WHERE nick_key = ?", nick_key(nick))

    def find_player(self, ref: str):
        """@username, Telegram ID или игровой ник."""
        ref = ref.strip()
        if ref.startswith("@"):
            return self.db.one(
                "SELECT * FROM players WHERE lower(tg_username) = ?", ref[1:].lower()
            )
        if ref.isdigit():
            row = self.player_by_tg(int(ref))
            if row is not None:
                return row
        return self.player_by_nick(ref)

    def players(self):
        return self.db.all("SELECT * FROM players ORDER BY nick_key")

    def register(self, tg_id: int, username: str | None, nick: str, now: int):
        """Возвращает (игрок, ошибка). Если ник заранее завёл админ, привязывает его."""
        nick = clean_nick(nick)
        existing = self.player_by_nick(nick)
        if existing is not None:
            if existing["tg_id"] not in (None, tg_id):
                return None, "taken"
            self.db.run(
                "UPDATE players SET tg_id = ?, tg_username = ? WHERE id = ?",
                tg_id,
                username,
                existing["id"],
            )
            return self.player(existing["id"]), None
        cur = self.db.run(
            "INSERT INTO players(tg_id, tg_username, nick, nick_key, created_at) VALUES(?, ?, ?, ?, ?)",
            tg_id,
            username,
            nick,
            nick_key(nick),
            now,
        )
        return self.player(cur.lastrowid), None

    def create_offline_player(self, nick: str, now: int):
        nick = clean_nick(nick)
        cur = self.db.run(
            "INSERT INTO players(nick, nick_key, created_at) VALUES(?, ?, ?)",
            nick,
            nick_key(nick),
            now,
        )
        return self.player(cur.lastrowid)

    def rename(self, player_id: int, nick: str) -> str | None:
        nick = clean_nick(nick)
        other = self.player_by_nick(nick)
        if other is not None and other["id"] != player_id:
            return "taken"
        self.db.run(
            "UPDATE players SET nick = ?, nick_key = ? WHERE id = ?", nick, nick_key(nick), player_id
        )
        return None

    def touch_username(self, player_id: int, username: str | None) -> None:
        self.db.run("UPDATE players SET tg_username = ? WHERE id = ?", username, player_id)

    def delete_player(self, player_id: int, now: int) -> None:
        with self.db.tx():
            self.db.run(
                "UPDATE donations SET status = 'cancelled', resolved_at = ? "
                "WHERE status = 'pending' AND (donor_id = ? OR recipient_id = ?)",
                now,
                player_id,
                player_id,
            )
            self.db.run("DELETE FROM players WHERE id = ?", player_id)

    def is_admin(self, tg_id: int, owner_ids: frozenset[int]) -> bool:
        if tg_id in owner_ids:
            return True
        row = self.player_by_tg(tg_id)
        return bool(row and row["is_admin"])

    def set_admin(self, player_id: int, flag: bool) -> None:
        self.db.run("UPDATE players SET is_admin = ? WHERE id = ?", int(flag), player_id)

    def admins(self):
        return self.db.all("SELECT * FROM players WHERE is_admin = 1 ORDER BY nick_key")

    # ---------- таймеры ----------

    def active_timer(self, player_id: int, kind: str):
        return self.db.one(
            "SELECT * FROM timers WHERE player_id = ? AND kind = ? AND active = 1", player_id, kind
        )

    def set_timer(
        self,
        player_id: int,
        kind: str,
        seconds: int,
        now: int,
        keep_base: bool = False,
        item: str | None = None,
        level: int | None = None,
        note: str | None = None,
    ):
        """Новый таймер или поправка остатка.

        keep_base=False — новая стройка/исследование: заявленное время = seconds.
        keep_base=True — игрок поправил остаток (например, ускорился сам), а
        заявленное время, от которого считаются 15%, остаётся прежним
        (и то, что строится, тоже).
        item/level/note — что именно строится или изучается (справочник gamedata).
        """
        current = self.active_timer(player_id, kind)
        with self.db.tx():
            if current is not None and keep_base:
                self.db.run(
                    "UPDATE timers SET end_at = ?, target_notified = 0 WHERE id = ?",
                    now + seconds,
                    current["id"],
                )
                return self.db.one("SELECT * FROM timers WHERE id = ?", current["id"])
            if current is not None:
                self.db.run("UPDATE timers SET active = 0 WHERE id = ?", current["id"])
            cur = self.db.run(
                "INSERT INTO timers(player_id, kind, base_seconds, end_at, created_at, item, level, note) "
                "VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                player_id,
                kind,
                seconds,
                now + seconds,
                now,
                item,
                level,
                note,
            )
        return self.db.one("SELECT * FROM timers WHERE id = ?", cur.lastrowid)

    def close_timer(self, player_id: int, kind: str) -> bool:
        cur = self.db.run(
            "UPDATE timers SET active = 0 WHERE player_id = ? AND kind = ? AND active = 1",
            player_id,
            kind,
        )
        return cur.rowcount > 0

    def toggle_urgent(self, player_id: int, kind: str) -> bool | None:
        timer = self.active_timer(player_id, kind)
        if timer is None:
            return None
        flag = not timer["urgent"]
        self.db.run("UPDATE timers SET urgent = ? WHERE id = ?", int(flag), timer["id"])
        return flag

    def deactivate_finished(self, now: int) -> int:
        return self.db.run(
            "UPDATE timers SET active = 0 WHERE active = 1 AND end_at <= ?", now
        ).rowcount

    def candidate_from_row(self, row, rules: Rules, now: int) -> Candidate:
        remaining = max(0, row["end_at"] - now)
        return Candidate(
            player_id=row["player_id"],
            nick=row["nick"],
            remaining=remaining,
            base=row["base_seconds"],
            reduction=buff_reduction(row["base_seconds"], remaining, rules),
            urgent=bool(row["urgent"]),
            waiting_since=row["last_buff_at"] or row["created_at"],
            received=row["buffs_received"],
            has_tg=row["tg_id"] is not None,
        )

    def _timer_rows(self, kind: str):
        return self.db.all(
            "SELECT t.*, p.nick, p.tg_id FROM timers t JOIN players p ON p.id = t.player_id "
            "WHERE t.active = 1 AND t.kind = ?",
            kind,
        )

    def timer_candidate(self, player_id: int, kind: str, now: int) -> Candidate | None:
        row = self.db.one(
            "SELECT t.*, p.nick, p.tg_id FROM timers t JOIN players p ON p.id = t.player_id "
            "WHERE t.active = 1 AND t.kind = ? AND t.player_id = ?",
            kind,
            player_id,
        )
        return None if row is None else self.candidate_from_row(row, self.rules(kind), now)

    def _pending_recipients(self, kind: str) -> set[int]:
        rows = self.db.all(
            "SELECT recipient_id FROM donations WHERE kind = ? AND status = 'pending'", kind
        )
        return {r["recipient_id"] for r in rows}

    def _slot_index(self, kind: str) -> int:
        row = self.db.one(
            "SELECT COUNT(*) AS n FROM donations WHERE kind = ? AND status IN ('pending', 'done') AND slot != ?",
            kind,
            SLOT_URGENT,
        )
        return row["n"]

    def _recent_recipients(self, kind: str, limit: int) -> list[int]:
        rows = self.db.all(
            "SELECT recipient_id FROM donations WHERE kind = ? AND status = 'done' "
            "ORDER BY resolved_at DESC, id DESC LIMIT ?",
            kind,
            max(limit, 0),
        )
        return [r["recipient_id"] for r in rows]

    def _choose(self, kind: str, now: int, exclude: set[int]):
        rules = self.rules(kind)
        candidates = [
            self.candidate_from_row(row, rules, now)
            for row in self._timer_rows(kind)
            if row["player_id"] not in exclude
        ]
        pick, slot = choose_recipient(
            candidates, rules, self._slot_index(kind), self._recent_recipients(kind, rules.max_streak)
        )
        return pick, slot

    def queue_view(self, kind: str, now: int) -> QueueView:
        rules = self.rules(kind)
        pending = self._pending_recipients(kind)
        rows = []
        for row in self._timer_rows(kind):
            c = self.candidate_from_row(row, rules, now)
            status = timer_status(c, rules)
            rows.append(QueueRow(
                c,
                status,
                buffs_needed(c.remaining, c.base, rules),
                c.player_id in pending,
                gamedata.label(row["item"], row["level"], row["note"]),
            ))
        rows.sort(key=lambda r: (r.status != STATUS_NEED, not r.candidate.urgent, -r.candidate.remaining))
        pick, slot = self._choose(kind, now, pending)
        return QueueView(kind, rows, pick, slot, rules)

    # ---------- бафы ----------

    def donation(self, donation_id: int):
        return self.db.one("SELECT * FROM donations WHERE id = ?", donation_id)

    def pending_for_donor(self, donor_id: int, kind: str):
        return self.db.one(
            "SELECT * FROM donations WHERE donor_id = ? AND kind = ? AND status = 'pending'",
            donor_id,
            kind,
        )

    def donor_ready_in(self, donor_id: int, kind: str, now: int) -> int:
        row = self.db.one(
            "SELECT ready_at FROM cooldowns WHERE player_id = ? AND kind = ?", donor_id, kind
        )
        return max(0, row["ready_at"] - now) if row else 0

    def cooldown(self, player_id: int, kind: str):
        return self.db.one(
            "SELECT * FROM cooldowns WHERE player_id = ? AND kind = ?", player_id, kind
        )

    def _assignment(self, donation, now: int, reused: bool) -> Assignment:
        recipient = self.player(donation["recipient_id"])
        c = self.timer_candidate(donation["recipient_id"], donation["kind"], now)
        timer = self.active_timer(donation["recipient_id"], donation["kind"])
        return Assignment(
            donation_id=donation["id"],
            kind=donation["kind"],
            slot=donation["slot"],
            donor_id=donation["donor_id"],
            recipient_id=donation["recipient_id"],
            recipient_nick=recipient["nick"] if recipient else "?",
            recipient_tg=recipient["tg_id"] if recipient else None,
            recipient_label=gamedata.label(timer["item"], timer["level"], timer["note"]) if timer else "",
            remaining=c.remaining if c else 0,
            reduction=c.reduction if c else 0,
            waiting=max(0, now - c.waiting_since) if c else 0,
            reused=reused,
            donor_ready_in=self.donor_ready_in(donation["donor_id"], donation["kind"], now),
        )

    def assign(
        self, kind: str, donor_id: int, requested_by: int, now: int, excluded: tuple[int, ...] = ()
    ) -> Assignment | None:
        existing = self.pending_for_donor(donor_id, kind)
        if existing is not None:
            return self._assignment(existing, now, reused=True)
        exclude = set(excluded) | {donor_id} | self._pending_recipients(kind)
        pick, slot = self._choose(kind, now, exclude)
        if pick is None:
            return None
        cur = self.db.run(
            "INSERT INTO donations(kind, donor_id, recipient_id, slot, status, excluded, requested_by, created_at) "
            "VALUES(?, ?, ?, ?, 'pending', ?, ?, ?)",
            kind,
            donor_id,
            pick.player_id,
            slot,
            ",".join(str(x) for x in excluded),
            requested_by,
            now,
        )
        return self._assignment(self.donation(cur.lastrowid), now, reused=False)

    def set_donation_message(self, donation_id: int, chat_id: int, message_id: int) -> None:
        self.db.run(
            "UPDATE donations SET chat_id = ?, message_id = ? WHERE id = ?",
            chat_id,
            message_id,
            donation_id,
        )

    def cancel(self, donation_id: int, now: int, status: str = "cancelled") -> bool:
        cur = self.db.run(
            "UPDATE donations SET status = ?, resolved_at = ? WHERE id = ? AND status = 'pending'",
            status,
            now,
            donation_id,
        )
        return cur.rowcount > 0

    def reassign(self, donation_id: int, requested_by: int, now: int) -> Assignment | None:
        d = self.donation(donation_id)
        if d is None or not self.cancel(donation_id, now):
            return None
        excluded = tuple(int(x) for x in d["excluded"].split(",") if x) + (d["recipient_id"],)
        return self.assign(d["kind"], d["donor_id"], requested_by, now, excluded)

    def _apply_reduction(self, player_id: int, kind: str, now: int, as_recipient: bool):
        """Срезает таймер игрока. Возвращает (срезано, остаток, цель достигнута впервые)."""
        rules = self.rules(kind)
        timer = self.active_timer(player_id, kind)
        if timer is None or timer["end_at"] <= now:
            return 0, None, False
        remaining = timer["end_at"] - now
        reduction = min(remaining, buff_reduction(timer["base_seconds"], remaining, rules))
        left = remaining - reduction
        reached = False
        if as_recipient:
            c = Candidate(player_id, "", left, timer["base_seconds"],
                          buff_reduction(timer["base_seconds"], left, rules),
                          bool(timer["urgent"]), now)
            reached = left > 0 and not timer["target_notified"] and timer_status(c, rules) != STATUS_NEED
            self.db.run(
                "UPDATE timers SET end_at = end_at - ?, buffs_received = buffs_received + 1, "
                "last_buff_at = ?, target_notified = target_notified OR ?, "
                "urgent = CASE WHEN ? THEN 0 ELSE urgent END WHERE id = ?",
                reduction,
                now,
                int(reached),
                int(left <= 0),
                timer["id"],
            )
        else:
            self.db.run("UPDATE timers SET end_at = end_at - ? WHERE id = ?", reduction, timer["id"])
        if left <= 0:
            self.db.run("UPDATE timers SET active = 0 WHERE id = ?", timer["id"])
        return reduction, left, reached

    def _complete(self, donation, now: int) -> BuffResult:
        kind = donation["kind"]
        reduction, recipient_left, reached = self._apply_reduction(
            donation["recipient_id"], kind, now, as_recipient=True
        )
        # В игре баф действует и на того, кто его отдал.
        _, donor_left, _ = self._apply_reduction(donation["donor_id"], kind, now, as_recipient=False)
        cooldown_hours = self.setting_float("cooldown_hours")
        self.db.run(
            "INSERT INTO cooldowns(player_id, kind, ready_at, reminded) VALUES(?, ?, ?, 0) "
            "ON CONFLICT(player_id, kind) DO UPDATE SET ready_at = excluded.ready_at, reminded = 0",
            donation["donor_id"],
            kind,
            now + int(cooldown_hours * HOUR),
        )
        self.db.run(
            "UPDATE donations SET status = 'done', resolved_at = ?, reduction = ? WHERE id = ?",
            now,
            reduction,
            donation["id"],
        )
        donor = self.player(donation["donor_id"])
        recipient = self.player(donation["recipient_id"])
        return BuffResult(
            donation_id=donation["id"],
            kind=kind,
            donor_id=donation["donor_id"],
            donor_nick=donor["nick"] if donor else "?",
            donor_tg=donor["tg_id"] if donor else None,
            recipient_id=donation["recipient_id"],
            recipient_nick=recipient["nick"] if recipient else "?",
            recipient_tg=recipient["tg_id"] if recipient else None,
            reduction=reduction,
            recipient_left=recipient_left,
            recipient_reached_target=reached,
            donor_left=donor_left,
            cooldown_hours=cooldown_hours,
        )

    def confirm(self, donation_id: int, now: int) -> BuffResult | None:
        with self.db.tx():
            d = self.donation(donation_id)
            if d is None or d["status"] != "pending":
                return None
            return self._complete(d, now)

    def record_manual(self, donor_id: int, recipient_id: int, kind: str, requested_by: int, now: int) -> BuffResult:
        """Админ вручную записывает уже отданный баф."""
        with self.db.tx():
            cur = self.db.run(
                "INSERT INTO donations(kind, donor_id, recipient_id, slot, status, requested_by, created_at) "
                "VALUES(?, ?, ?, 'M', 'pending', ?, ?)",
                kind,
                donor_id,
                recipient_id,
                requested_by,
                now,
            )
            return self._complete(self.donation(cur.lastrowid), now)

    def expire_pending(self, now: int):
        limit = int(self.setting_float("confirm_minutes") * MINUTE)
        rows = self.db.all(
            "SELECT * FROM donations WHERE status = 'pending' AND created_at <= ?", now - limit
        )
        return [r for r in rows if self.cancel(r["id"], now, status="expired")]

    def due_reminders(self, now: int):
        return self.db.all(
            "SELECT c.*, p.tg_id, p.nick FROM cooldowns c JOIN players p ON p.id = c.player_id "
            "WHERE c.reminded = 0 AND c.ready_at <= ? AND p.tg_id IS NOT NULL",
            now,
        )

    def mark_reminded(self, player_id: int, kind: str) -> None:
        self.db.run(
            "UPDATE cooldowns SET reminded = 1 WHERE player_id = ? AND kind = ?", player_id, kind
        )

    def ready_donors(self, kind: str, now: int):
        """Игроки, у которых по записям бота баф уже снова готов."""
        return self.db.all(
            "SELECT p.* FROM cooldowns c JOIN players p ON p.id = c.player_id "
            "WHERE c.kind = ? AND c.ready_at <= ? ORDER BY c.ready_at",
            kind,
            now,
        )

    def stats(self, player_id: int) -> tuple[int, int]:
        given = self.db.one(
            "SELECT COUNT(*) AS n FROM donations WHERE donor_id = ? AND status = 'done'", player_id
        )["n"]
        received = self.db.one(
            "SELECT COUNT(*) AS n FROM donations WHERE recipient_id = ? AND status = 'done'", player_id
        )["n"]
        return given, received

    def observed_times(self, item: str | None = None) -> list[tuple[str, int, int, int]]:
        """Реальные заявленные игроками времена: (код, уровень, медиана секунд, сколько записей).

        Заявленное время — это остаток на момент записи, поэтому оценка грубая,
        но со временем по союзу набирается своя статистика.
        """
        sql = "SELECT item, level, base_seconds FROM timers WHERE item IS NOT NULL AND level IS NOT NULL"
        args: tuple = ()
        if item:
            sql += " AND item = ?"
            args = (item,)
        groups: dict[tuple[str, int], list[int]] = {}
        for r in self.db.all(sql, *args):
            groups.setdefault((r["item"], r["level"]), []).append(r["base_seconds"])
        out = []
        for (code, level), values in sorted(groups.items()):
            values.sort()
            out.append((code, level, values[len(values) // 2], len(values)))
        return out
