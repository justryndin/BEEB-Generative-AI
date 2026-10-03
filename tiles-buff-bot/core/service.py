"""Бизнес-логика очереди бафов поверх SQLite. Не зависит от сайта."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
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
    "pattern": ("BBBWWW", "Цикл ротации: B — самому большому остатку, W — кому досталось меньше всех и кто дольше ждёт"),
    "max_streak": ("2", "Не больше стольких бафов подряд одному игроку"),
    "min_gap_hours": ("12", "Пауза после полученного бафа, часов: пока она идёт, бафы получают другие"),
    "fair_round": ("1", "Справедливый круг: 1 — сначала все по одному бафу, потом по второму…; 0 — выключить"),
    "cooldown_hours": ("48", "Через сколько часов у игрока снова готов баф"),
    "confirm_minutes": ("30", "Сколько минут держится бронь на назначенный баф"),
    "alliance_code": ("", "Код союза для регистрации на сайте (пусто — регистрация открыта всем)"),
}
_INTERNAL_SETTINGS: set[str] = set()
_TEXT_SETTINGS = {"mode", "pattern", "alliance_code"}

MAX_FAILED_LOGINS = 5
LOCK_SECONDS = 15 * 60
_PIN_ITERATIONS = 120_000


def hash_pin(pin: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(8)
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode(), salt.encode(), _PIN_ITERATIONS).hex()
    return f"{salt}${digest}"


def check_pin(pin: str, stored: str | None) -> bool:
    if not stored or "$" not in stored:
        return False
    salt, _ = stored.split("$", 1)
    return hmac.compare_digest(hash_pin(pin, salt), stored)


def valid_pin(pin: str) -> bool:
    return pin.isdigit() and len(pin) == 4


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
    paused_for: int = 0  # сколько секунд ещё длится пауза после полученного бафа


@dataclass
class OrderRow:
    position: int | None
    candidate: Candidate
    label: str
    needed: int
    paused_for: int
    joined_at: int
    slot: str
    need: bool


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
        elif key == "alliance_code":
            if len(value) > 32:
                return "Код союза — не длиннее 32 символов"
        elif key in SETTINGS:
            try:
                number = float(value.replace(",", "."))
            except ValueError:
                return f"{key}: нужно число"
            value = f"{number:g}"
            if key == "pct" and not 0 < number < 100:
                return "pct: от 0 до 100"
            if key in ("cooldown_hours", "confirm_minutes") and number <= 0:
                return f"{key}: должно быть больше 0"
            if number < 0:
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
            min_gap=int(self.setting_float("min_gap_hours") * HOUR),
            max_ahead=1 if self.setting_float("fair_round") > 0 else 0,
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
            last_got=row["last_buff_at"],
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
            candidates, rules, self._slot_index(kind), self._recent_recipients(kind, rules.max_streak), now
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
                max(0, c.paused_until(rules) - now),
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
        """Срезает таймер игрока. Возвращает (срезано, остаток, цель достигнута впервые, сведения для отмены)."""
        rules = self.rules(kind)
        timer = self.active_timer(player_id, kind)
        if timer is None or timer["end_at"] <= now:
            return 0, None, False, None
        remaining = timer["end_at"] - now
        reduction = min(remaining, buff_reduction(timer["base_seconds"], remaining, rules))
        left = remaining - reduction
        reached = False
        undo = {
            "timer_id": timer["id"],
            "reduction": reduction,
            "last_buff_at": timer["last_buff_at"],
            "urgent": timer["urgent"],
            "target_notified": timer["target_notified"],
            "deactivated": left <= 0,
        }
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
        return reduction, left, reached, undo

    def _complete(self, donation, now: int) -> BuffResult:
        kind = donation["kind"]
        reduction, recipient_left, reached, recipient_undo = self._apply_reduction(
            donation["recipient_id"], kind, now, as_recipient=True
        )
        # В игре баф действует и на того, кто его отдал.
        _, donor_left, _, donor_undo = self._apply_reduction(donation["donor_id"], kind, now, as_recipient=False)
        cooldown_hours = self.setting_float("cooldown_hours")
        prev_cd = self.cooldown(donation["donor_id"], kind)
        undo = {
            "recipient": recipient_undo,
            "donor": donor_undo,
            "cooldown": {"ready_at": prev_cd["ready_at"], "reminded": prev_cd["reminded"]} if prev_cd else None,
        }
        self.db.run(
            "INSERT INTO cooldowns(player_id, kind, ready_at, reminded) VALUES(?, ?, ?, 0) "
            "ON CONFLICT(player_id, kind) DO UPDATE SET ready_at = excluded.ready_at, reminded = 0",
            donation["donor_id"],
            kind,
            now + int(cooldown_hours * HOUR),
        )
        self.db.run(
            "UPDATE donations SET status = 'done', resolved_at = ?, reduction = ?, undo = ? WHERE id = ?",
            now,
            reduction,
            json.dumps(undo),
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

    # ---------- вход на сайт ----------

    def is_admin_player(self, player) -> bool:
        return bool(player and (player["is_admin"] or player["is_owner"]))

    def register_web(self, nick: str, pin: str, now: int):
        """Регистрация по нику и PIN. Возвращает (игрок, ошибка).

        Если ник уже завёл админ (или он остался от Telegram-бота) и PIN ещё не задан,
        игрок «забирает» его себе вместе с очередью и историей.
        """
        nick = clean_nick(nick)
        if not valid_pin(pin):
            return None, "pin"
        existing = self.player_by_nick(nick)
        if existing is not None:
            if existing["pin_hash"]:
                return None, "taken"
            self.db.run("UPDATE players SET pin_hash = ? WHERE id = ?", hash_pin(pin), existing["id"])
            return self.player(existing["id"]), None
        cur = self.db.run(
            "INSERT INTO players(nick, nick_key, pin_hash, created_at) VALUES(?, ?, ?, ?)",
            nick,
            nick_key(nick),
            hash_pin(pin),
            now,
        )
        return self.player(cur.lastrowid), None

    def login(self, nick: str, pin: str, now: int):
        """Возвращает (игрок, ошибка): ошибка — "unknown", "nopin", "locked" или "wrong"."""
        player = self.player_by_nick(nick)
        if player is None:
            return None, "unknown"
        if not player["pin_hash"]:
            return None, "nopin"
        if player["locked_until"] and player["locked_until"] > now:
            return None, "locked"
        if not check_pin(pin, player["pin_hash"]):
            failed = player["failed_logins"] + 1
            locked = now + LOCK_SECONDS if failed >= MAX_FAILED_LOGINS else None
            self.db.run(
                "UPDATE players SET failed_logins = ?, locked_until = ? WHERE id = ?",
                0 if locked else failed,
                locked,
                player["id"],
            )
            return None, "locked" if locked else "wrong"
        self.db.run("UPDATE players SET failed_logins = 0, locked_until = NULL WHERE id = ?", player["id"])
        return self.player(player["id"]), None

    def set_pin(self, player_id: int, pin: str | None) -> None:
        """PIN = None сбрасывает его: игрок сможет заново зарегистрироваться под своим ником."""
        self.db.run(
            "UPDATE players SET pin_hash = ?, failed_logins = 0, locked_until = NULL WHERE id = ?",
            hash_pin(pin) if pin else None,
            player_id,
        )
        if pin is None:
            self.db.run("DELETE FROM sessions WHERE player_id = ?", player_id)

    def agree(self, player_id: int, now: int) -> None:
        self.db.run("UPDATE players SET agreed_at = ? WHERE id = ?", now, player_id)

    def incoming_pending(self, player_id: int):
        """Брони, где этому игроку сейчас несут баф."""
        return self.db.all(
            "SELECT d.*, p.nick AS donor_nick FROM donations d LEFT JOIN players p ON p.id = d.donor_id "
            "WHERE d.recipient_id = ? AND d.status = 'pending' ORDER BY d.id",
            player_id,
        )

    def set_owner(self, player_id: int) -> None:
        self.db.run("UPDATE players SET is_owner = 1, is_admin = 1 WHERE id = ?", player_id)

    def create_session(self, player_id: int, now: int) -> tuple[str, str]:
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(16)
        self.db.run(
            "INSERT INTO sessions(token, player_id, csrf, created_at) VALUES(?, ?, ?, ?)",
            token,
            player_id,
            csrf,
            now,
        )
        return token, csrf

    def session(self, token: str | None, now: int, max_age: int):
        if not token:
            return None
        return self.db.one(
            "SELECT s.csrf, p.* FROM sessions s JOIN players p ON p.id = s.player_id "
            "WHERE s.token = ? AND s.created_at > ?",
            token,
            now - max_age,
        )

    def drop_session(self, token: str) -> None:
        self.db.run("DELETE FROM sessions WHERE token = ?", token)

    def touch_seen(self, player_id: int, now: int) -> None:
        self.db.run("UPDATE players SET last_seen_at = ? WHERE id = ?", now, player_id)

    # ---------- лента и статистика ----------

    def received_since(self, player_id: int, since: int):
        return self.db.all(
            "SELECT d.*, p.nick AS donor_nick FROM donations d LEFT JOIN players p ON p.id = d.donor_id "
            "WHERE d.recipient_id = ? AND d.status = 'done' AND d.resolved_at >= ? ORDER BY d.resolved_at DESC",
            player_id,
            since,
        )

    def pending_by_requester(self, requester_id: int):
        return self.db.all(
            "SELECT * FROM donations WHERE requested_by = ? AND status = 'pending' ORDER BY id DESC",
            requester_id,
        )

    def recent_donations(self, limit: int = 20):
        return self.db.all(
            "SELECT d.*, a.nick AS donor_nick, b.nick AS recipient_nick FROM donations d "
            "LEFT JOIN players a ON a.id = d.donor_id LEFT JOIN players b ON b.id = d.recipient_id "
            "WHERE d.status = 'done' ORDER BY d.resolved_at DESC LIMIT ?",
            limit,
        )

    def totals(self, since: int = 0) -> dict:
        row = self.db.one(
            "SELECT COUNT(*) AS buffs, COALESCE(SUM(reduction), 0) AS saved, "
            "COUNT(DISTINCT donor_id) AS donors, COUNT(DISTINCT recipient_id) AS recipients "
            "FROM donations WHERE status = 'done' AND resolved_at >= ?",
            since,
        )
        by_kind = {
            r["kind"]: r["n"]
            for r in self.db.all(
                "SELECT kind, COUNT(*) AS n FROM donations WHERE status = 'done' AND resolved_at >= ? GROUP BY kind",
                since,
            )
        }
        return {**dict(row), "build": by_kind.get("build", 0), "research": by_kind.get("research", 0)}

    def daily_counts(self, now: int, days: int, tz_offset: int = 0) -> list[tuple[int, int]]:
        """[(начало дня, число бафов)] за последние days дней, по местному времени."""
        today = (now + tz_offset) // DAY * DAY - tz_offset
        start = today - (days - 1) * DAY
        counts = {
            r["day"]: r["n"]
            for r in self.db.all(
                "SELECT ((resolved_at + ?) / 86400) * 86400 - ? AS day, COUNT(*) AS n FROM donations "
                "WHERE status = 'done' AND resolved_at >= ? GROUP BY day",
                tz_offset,
                tz_offset,
                start,
            )
        }
        return [(start + i * DAY, counts.get(start + i * DAY, 0)) for i in range(days)]

    def top_players(self, role: str, since: int = 0, limit: int = 10):
        column = "donor_id" if role == "donor" else "recipient_id"
        return self.db.all(
            f"SELECT p.id, p.nick, COUNT(*) AS n, COALESCE(SUM(d.reduction), 0) AS saved FROM donations d "
            f"JOIN players p ON p.id = d.{column} WHERE d.status = 'done' AND d.resolved_at >= ? "
            "GROUP BY p.id ORDER BY n DESC, saved DESC LIMIT ?",
            since,
            limit,
        )

    # ---------- очередь по порядку и «я отдал баф» ----------

    def queue_order(self, kind: str, now: int) -> list[OrderRow]:
        """Очередь в том порядке, в каком бафы положено отдавать по правилам
        (пауза после бафа, справедливый круг, цикл ротации, «срочно»).
        Первый в списке — «следующий». В конце — те, кто уже дошёл до цели."""
        rules = self.rules(kind)
        rows = {}
        for row in self._timer_rows(kind):
            c = self.candidate_from_row(row, rules, now)
            if c.remaining > 0:
                rows[c.player_id] = (c, row)
        pool = [c for c, _ in rows.values() if timer_status(c, rules) == STATUS_NEED]
        slot_index = self._slot_index(kind)
        recent = self._recent_recipients(kind, rules.max_streak)
        ordered: list[tuple[Candidate, str]] = []
        while pool:
            pick, slot = choose_recipient(pool, rules, slot_index, recent, now)
            if pick is None:
                break
            ordered.append((pick, slot))
            pool = [c for c in pool if c.player_id != pick.player_id]
            recent.insert(0, pick.player_id)
            if slot != SLOT_URGENT:
                slot_index += 1
        ordered += [(c, "") for c in sorted(pool, key=lambda c: -c.remaining)]
        reached = sorted(
            (c for c, _ in rows.values() if timer_status(c, rules) != STATUS_NEED), key=lambda c: c.remaining
        )

        def make(c: Candidate, slot: str, pos: int | None, need: bool) -> OrderRow:
            row = rows[c.player_id][1]
            return OrderRow(
                position=pos,
                candidate=c,
                label=gamedata.label(row["item"], row["level"], row["note"]),
                needed=buffs_needed(c.remaining, c.base, rules) if need else 0,
                paused_for=max(0, c.paused_until(rules) - now),
                joined_at=row["created_at"],
                slot=slot,
                need=need,
            )

        return [make(c, slot, i, True) for i, (c, slot) in enumerate(ordered, 1)] + [
            make(c, "", None, False) for c in reached
        ]

    def given_since(self, kind: str, since: int) -> int:
        return self.db.one(
            "SELECT COUNT(*) AS n FROM donations WHERE kind = ? AND status = 'done' AND resolved_at >= ?",
            kind,
            since,
        )["n"]

    def record_gift(self, kind: str, donor_id: int, recipient_id: int, by_id: int, now: int):
        """Игрок отметил: «я отдал баф вот ему». Возвращает (результат, ошибка)."""
        if donor_id == recipient_id:
            return None, "self"
        if self.active_timer(recipient_id, kind) is None:
            return None, "not_in_queue"
        recent = self.db.one(
            "SELECT id FROM donations WHERE kind = ? AND donor_id = ? AND status = 'done' AND resolved_at > ?",
            kind,
            donor_id,
            now - 10 * MINUTE,
        )
        if recent is not None:
            return None, "duplicate"
        return self.record_manual(donor_id, recipient_id, kind, by_id, now), None

    # ---------- отмена записанного бафа ----------

    def _restore_timer(self, info: dict | None, player_id: int, kind: str, recipient: bool, donation) -> bool:
        """Возвращает срезанное время таймеру. False — если точных сведений нет."""
        if info:
            timer = self.db.one("SELECT * FROM timers WHERE id = ?", info["timer_id"])
            if timer is None:
                return True  # таймер уже удалён вместе с игроком — возвращать некому
            if recipient:
                self.db.run(
                    "UPDATE timers SET end_at = end_at + ?, buffs_received = MAX(buffs_received - 1, 0), "
                    "last_buff_at = ?, urgent = ?, target_notified = ? WHERE id = ?",
                    info["reduction"], info["last_buff_at"], info["urgent"], info["target_notified"], timer["id"],
                )
            else:
                self.db.run("UPDATE timers SET end_at = end_at + ? WHERE id = ?", info["reduction"], timer["id"])
            if info["deactivated"] and not timer["active"] and self.active_timer(player_id, kind) is None:
                self.db.run("UPDATE timers SET active = 1 WHERE id = ?", timer["id"])
            return True
        if not recipient:
            return False
        # Старая запись (до появления отмены): возвращаем по сумме из журнала.
        timer = self.active_timer(player_id, kind)
        if timer is not None and donation["reduction"]:
            prev = self.db.one(
                "SELECT MAX(resolved_at) AS t FROM donations WHERE recipient_id = ? AND kind = ? "
                "AND status = 'done' AND id != ?",
                player_id, kind, donation["id"],
            )["t"]
            self.db.run(
                "UPDATE timers SET end_at = end_at + ?, buffs_received = MAX(buffs_received - 1, 0), "
                "last_buff_at = ? WHERE id = ?",
                donation["reduction"], prev, timer["id"],
            )
        return True

    def undo_donation(self, donation_id: int, by_id: int, now: int):
        """Отменяет записанный баф. Возвращает (запись, ошибка, полностью ли откатили таймер донора)."""
        with self.db.tx():
            d = self.donation(donation_id)
            if d is None:
                return None, "not_found", True
            if d["status"] != "done":
                return d, "not_done", True
            info = json.loads(d["undo"]) if d["undo"] else {}
            self._restore_timer(info.get("recipient"), d["recipient_id"], d["kind"], True, d)
            self._restore_timer(info.get("donor"), d["donor_id"], d["kind"], False, d)
            if "cooldown" in info:
                prev = info["cooldown"]
                if prev is None:
                    self.db.run("DELETE FROM cooldowns WHERE player_id = ? AND kind = ?", d["donor_id"], d["kind"])
                else:
                    self.db.run(
                        "UPDATE cooldowns SET ready_at = ?, reminded = ? WHERE player_id = ? AND kind = ?",
                        prev["ready_at"], prev["reminded"], d["donor_id"], d["kind"],
                    )
            self.db.run(
                "UPDATE donations SET status = 'undone', undone_by = ?, undone_at = ? WHERE id = ?",
                by_id, now, d["id"],
            )
            # Для старых записей (без сведений для отмены) таймер донора не откатить точно.
            return self.donation(d["id"]), None, bool(info)

    def journal(self, limit: int = 100, player_id: int | None = None):
        """Журнал бафов (записанные и отменённые), новые сверху."""
        sql = (
            "SELECT d.*, a.nick AS donor_nick, b.nick AS recipient_nick, r.nick AS by_nick, u.nick AS undone_nick "
            "FROM donations d LEFT JOIN players a ON a.id = d.donor_id LEFT JOIN players b ON b.id = d.recipient_id "
            "LEFT JOIN players r ON r.id = d.requested_by LEFT JOIN players u ON u.id = d.undone_by "
            "WHERE d.status IN ('done', 'undone')"
        )
        args: list = []
        if player_id is not None:
            sql += " AND (d.donor_id = ? OR d.recipient_id = ?)"
            args += [player_id, player_id]
        sql += " ORDER BY d.resolved_at DESC, d.id DESC LIMIT ?"
        args.append(limit)
        return self.db.all(sql, *args)

    def last_own_gift(self, player_id: int, since: int):
        """Последний баф, который игрок сам отметил недавно (его можно отменить самому)."""
        return self.db.one(
            "SELECT d.*, b.nick AS recipient_nick FROM donations d LEFT JOIN players b ON b.id = d.recipient_id "
            "WHERE d.requested_by = ? AND d.status = 'done' AND d.resolved_at >= ? ORDER BY d.resolved_at DESC LIMIT 1",
            player_id, since,
        )
