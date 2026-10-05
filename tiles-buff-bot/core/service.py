"""Бизнес-логика очереди бафов поверх SQLite. Не зависит от сайта."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import time
from dataclasses import dataclass

from . import gamedata
from .db import Database
from .logic import (
    ORDER_CYCLE,
    ORDER_SHARE,
    SLOT_URGENT,
    STATUS_NEED,
    Candidate,
    Rules,
    buff_reduction,
    buffs_needed,
    choose_recipient,
    fire_left,
    share,
    timer_status,
)
from .i18n import t
from .timeparse import DAY, HOUR, MINUTE, format_duration

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
    "build_min": ("3", "Стройка: ниже этого остатка (дней) бафом не опускаем"),
    "build_max": ("3", "Стройка: при остатке не больше этого (дней) бафы уже не нужны"),
    "research_min": ("7", "Исследование: ниже этого остатка (дней) бафом не опускаем"),
    "research_max": ("8", "Исследование: при остатке не больше этого (дней) бафы уже не нужны"),
    "queue_order": (ORDER_SHARE, "Порядок очереди: share — по доле от положенного; cycle — круг и цикл"),
    "fire_hours": ("24", "«Горит»: если остаток сам дойдёт до цели быстрее, чем за столько часов, — первым"),
    "hold_hours": ("24", "Держит готовый баф дольше стольких часов — пропускает ход (0 — выключить)"),
    "priority_item": ("pp", "Приоритетная стройка (код из справочника, пусто — без приоритета)"),
    "priority_below": ("30", "Приоритет — для уровней ниже этого"),
    "priority_weight": ("2", "Во сколько раз быстрее растёт помощь приоритетной стройке"),
    "roulette_build": ("1", "Очередь стройки пуста — баф по рулетке активному игроку (1 — да, 0 — нет)"),
    "roulette_research": ("0", "Очередь исследований пуста — баф по рулетке (1 — да, 0 — нет)"),
    "roulette_active_days": ("3", "Рулетка: только среди тех, кто заходил на сайт за столько дней"),
    "pattern": ("BBBWWW", "Цикл ротации: B — самому большому остатку, W — кому досталось меньше всех и кто дольше ждёт"),
    "max_streak": ("2", "Не больше стольких бафов подряд одному игроку"),
    "min_gap_hours": ("12", "Пауза после полученного бафа, часов: пока она идёт, бафы получают другие"),
    "fair_round": ("1", "Справедливый круг: 1 — сначала все по одному бафу, потом по второму…; 0 — выключить"),
    "check_hours": ("24", "Через сколько часов просить игрока сверить время с игрой (0 — не просить)"),
    "quiet_from": ("0", "Тихие часы: с какого часа не присылать уведомления"),
    "quiet_to": ("8", "Тихие часы: до какого часа"),
    "cooldown_hours": ("48", "Через сколько часов у игрока снова готов баф"),
    "confirm_minutes": ("30", "Сколько минут держится бронь на назначенный баф"),
    "alliance_code": ("", "Код союза для регистрации на сайте (пусто — регистрация открыта всем)"),
}
_INTERNAL_SETTINGS: set[str] = {"vapid_private", "vapid_public", "events_seeded"}
_TEXT_SETTINGS = {"mode", "pattern", "alliance_code", "queue_order", "priority_item"}

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
    share: float = 0.0  # доля полученного от положенного, 0…1
    total: int = 0  # всего положено на эту стройку (получил + ещё положено)
    fire_in: int | None = None  # «горит»: через сколько секунд сам дойдёт до цели
    why: str = ""  # почему он на этом месте — простыми словами


@dataclass
class RouletteRow:
    """Кандидат рулетки — когда в очереди никого нет, баф получает активный игрок союза."""
    player_id: int
    nick: str
    pp_level: int | None
    group: int  # 0 — приоритет (Электростанция ниже цели), 1 — уровень неизвестен, 2 — уже построил
    got_week: int  # сколько бафов этого типа получил по рулетке за 7 дней
    why: str = ""
    paused: int = 0  # пауза после недавнего бафа, сек: такие идут после остальных


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
            return t("Нет такой настройки: {key}", key=key)
        value = value.strip()
        if key == "mode":
            if value not in ("declared", "remaining"):
                return t("mode: только declared или remaining")
        elif key == "pattern":
            value = value.upper()
            if not re.fullmatch(r"[BW]{1,12}", value):
                return t("pattern: только буквы B и W, например BBW")
        elif key == "queue_order":
            if value not in (ORDER_SHARE, ORDER_CYCLE):
                return t("queue_order: только share или cycle")
        elif key == "priority_item":
            if value and gamedata.item(value) is None:
                return t("priority_item: нет такого здания в справочнике")
        elif key == "alliance_code":
            if len(value) > 32:
                return t("Код союза — не длиннее 32 символов")
        elif key in SETTINGS:
            try:
                number = float(value.replace(",", "."))
            except ValueError:
                return t("{key}: нужно число", key=key)
            value = f"{number:g}"
            if key == "pct" and not 0 < number < 100:
                return t("pct: от 0 до 100")
            if key in ("cooldown_hours", "confirm_minutes", "priority_weight") and number <= 0:
                return t("{key}: должно быть больше 0", key=key)
            if number < 0:
                return t("{key}: не может быть отрицательным", key=key)
            for kind in KINDS:
                lo, hi = f"{kind}_min", f"{kind}_max"
                if key in (lo, hi):
                    new_lo = number if key == lo else self.setting_float(lo)
                    new_hi = number if key == hi else self.setting_float(hi)
                    if new_lo > new_hi:
                        return t("{lo} не может быть больше {hi}", lo=lo, hi=hi)
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
            order=self.setting("queue_order"),
            fire_window=int(self.setting_float("fire_hours") * HOUR),
            priority_weight=self.setting_float("priority_weight"),
        )

    def is_priority(self, item: str | None, level: int | None) -> bool:
        code = self.setting("priority_item")
        if not code or item != code:
            return False
        return not level or level < self.setting_float("priority_below")

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
                    "UPDATE timers SET end_at = ?, target_notified = 0, checked_at = ? WHERE id = ?",
                    now + seconds,
                    now,
                    current["id"],
                )
                return self.db.one("SELECT * FROM timers WHERE id = ?", current["id"])
            if current is not None:
                self.db.run(
                    "UPDATE timers SET active = 0, closed_at = ?, next_dismissed = 1 WHERE id = ?", now, current["id"]
                )
            # Новая запись снимает подсказку «Встать со следующей» у прошлых таймеров этого типа.
            self.db.run(
                "UPDATE timers SET next_dismissed = 1 WHERE player_id = ? AND kind = ? AND active = 0", player_id, kind
            )
            self._note_pp(player_id, item, level, finished=False)
            cur = self.db.run(
                "INSERT INTO timers(player_id, kind, base_seconds, end_at, created_at, item, level, note, checked_at) "
                "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
                player_id,
                kind,
                seconds,
                now + seconds,
                now,
                item,
                level,
                note,
                now,
            )
        return self.db.one("SELECT * FROM timers WHERE id = ?", cur.lastrowid)

    def close_timer(self, player_id: int, kind: str, now: int | None = None, suggest_next: bool = True) -> bool:
        """suggest_next=False — закрыл админ: игроку не предлагаем «встать со следующей»."""
        timer = self.active_timer(player_id, kind)
        if timer is not None and suggest_next:
            self._note_pp(player_id, timer["item"], timer["level"], finished=True)
        cur = self.db.run(
            "UPDATE timers SET active = 0, closed_at = ?, next_dismissed = ? "
            "WHERE player_id = ? AND kind = ? AND active = 1",
            now or int(time.time()),
            int(not suggest_next),
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
        for t in self.db.all("SELECT * FROM timers WHERE active = 1 AND end_at <= ? AND item = 'pp'", now):
            self._note_pp(t["player_id"], t["item"], t["level"], finished=True)
        return self.db.run(
            "UPDATE timers SET active = 0, closed_at = end_at WHERE active = 1 AND end_at <= ?", now
        ).rowcount

    # ---------- жизнь записи: сверка времени и «встать со следующей» ----------

    NEXT_PROMPT_SECONDS = 3 * DAY  # сколько дней после окончания предлагать «встать со следующей»

    def time_checks(self, player_id: int, now: int) -> list:
        """Активные таймеры, время которых стоит сверить с игрой.

        Просим, если после последней сверки игрок получил баф (сайт срезал время по расчёту)
        или если сверки не было дольше `check_hours` (вдруг игрок ускорился сам).
        """
        hours = self.setting_float("check_hours")
        if hours <= 0:
            return []
        rows = self.db.all(
            "SELECT * FROM timers_live WHERE player_id = ? AND active = 1 AND end_at > ? ORDER BY kind", player_id, now
        )
        out = []
        for row in rows:
            checked = row["checked_at"] or row["created_at"]
            if row["got_at"] and row["got_at"] > checked:
                out.append({"timer": row, "kind": row["kind"], "remaining": row["end_at"] - now, "why": "buff"})
            elif now - checked >= hours * HOUR:
                out.append({"timer": row, "kind": row["kind"], "remaining": row["end_at"] - now, "why": "stale"})
        return out

    def confirm_time(self, player_id: int, kind: str, now: int) -> bool:
        cur = self.db.run(
            "UPDATE timers SET checked_at = ? WHERE player_id = ? AND kind = ? AND active = 1", now, player_id, kind
        )
        return cur.rowcount > 0

    def finished_timers(self, player_id: int, now: int) -> list:
        """Недавно закончившиеся стройки/исследования, после которых игрок ещё не встал снова."""
        out = []
        for kind in KINDS:
            if self.active_timer(player_id, kind) is not None:
                continue
            row = self.db.one(
                "SELECT * FROM timers WHERE player_id = ? AND kind = ? AND active = 0 AND next_dismissed = 0 "
                "AND closed_at IS NOT NULL AND closed_at <= ? AND closed_at > ? ORDER BY closed_at DESC, id DESC LIMIT 1",
                player_id, kind, now, now - self.NEXT_PROMPT_SECONDS,
            )
            if row is None:
                continue
            it = gamedata.item(row["item"])
            next_level = None
            if it is not None and it.kind == "build" and row["level"] and (not it.max_level or row["level"] < it.max_level):
                next_level = row["level"] + 1
            out.append({
                "timer": row,
                "kind": kind,
                "item": it,
                "next_level": next_level,
                "next_reference": it.time_for(next_level) if it is not None and next_level else None,
            })
        return out

    def dismiss_next(self, player_id: int, kind: str) -> None:
        self.db.run(
            "UPDATE timers SET next_dismissed = 1 WHERE player_id = ? AND kind = ? AND active = 0", player_id, kind
        )

    def candidate_from_row(self, row, rules: Rules, now: int, holding: int = 0) -> Candidate:
        remaining = max(0, row["end_at"] - now)
        return Candidate(
            player_id=row["player_id"],
            nick=row["nick"],
            remaining=remaining,
            base=row["base_seconds"],
            reduction=buff_reduction(row["base_seconds"], remaining, rules),
            urgent=bool(row["urgent"]),
            waiting_since=row["got_at"] or row["created_at"],
            received=row["got"],
            has_tg=row["tg_id"] is not None,
            last_got=row["got_at"],
            priority=self.is_priority(row["item"], row["level"]),
            holding=holding,
        )

    def holding_map(self, now: int) -> dict[int, tuple[str, int]]:
        """Кто держит готовый баф дольше нормы: {игрок: (тип бафа, сколько держит всего, сек)}.

        Баф считается готовым с момента окончания перезарядки. Кто ещё ни разу не отмечал
        баф этого типа — с момента, как встал в очередь (значит, играет и баф у него есть).
        Не считаем, если отдать было некому: в очереди этого типа нет никого, кроме него самого.
        """
        limit = self.setting_float("hold_hours") * HOUR
        if limit <= 0:
            return {}
        rules = {k: self.rules(k) for k in KINDS}
        needers = {
            k: {r["player_id"] for r in self._timer_rows(k)
                if timer_status(self.candidate_from_row(r, rules[k], now), rules[k]) == STATUS_NEED}
            for k in KINDS
        }
        joined = {
            r["player_id"]: r["t"]
            for r in self.db.all("SELECT player_id, MIN(created_at) AS t FROM timers WHERE active = 1 GROUP BY player_id")
        }
        cooldown = int(self.setting_float("cooldown_hours") * HOUR)
        cds = {
            (r["donor_id"], r["kind"]): r["t"] + cooldown
            for r in self.db.all(
                "SELECT donor_id, kind, MAX(resolved_at) AS t FROM donations WHERE status = 'done' GROUP BY donor_id, kind"
            )
        }
        out: dict[int, tuple[str, int]] = {}
        for pid, since in joined.items():
            for kind in KINDS:
                if not (needers[kind] - {pid}):
                    continue
                ready = max(cds.get((pid, kind), since), since)
                held = now - ready
                if held > limit and held > out.get(pid, ("", 0))[1]:
                    out[pid] = (kind, held)
        return out

    def _timer_rows(self, kind: str):
        return self.db.all(
            "SELECT t.*, p.nick, p.tg_id FROM timers_live t JOIN players p ON p.id = t.player_id "
            "WHERE t.active = 1 AND t.kind = ?",
            kind,
        )

    def timer_candidate(self, player_id: int, kind: str, now: int) -> Candidate | None:
        row = self.db.one(
            "SELECT t.*, p.nick, p.tg_id FROM timers_live t JOIN players p ON p.id = t.player_id "
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
        holding = self.holding_map(now)
        candidates = [
            self.candidate_from_row(row, rules, now, holding.get(row["player_id"], ("", 0))[1])
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
        cd = self.cooldown(donor_id, kind)
        return max(0, cd["ready_at"] - now) if cd else 0

    def cooldown(self, player_id: int, kind: str):
        """Когда у игрока снова готов баф: по последней записи в журнале (отменённые не считаются)."""
        row = self.db.one(
            "SELECT MAX(resolved_at) AS t FROM donations WHERE donor_id = ? AND kind = ? AND status = 'done'",
            player_id, kind,
        )
        if row is None or row["t"] is None:
            return None
        return {"ready_at": row["t"] + int(self.setting_float("cooldown_hours") * HOUR), "reminded": 0}

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
            self.db.run("UPDATE timers SET active = 0, closed_at = ? WHERE id = ?", now, timer["id"])
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

    def record_manual(self, donor_id: int, recipient_id: int, kind: str, requested_by: int, now: int,
                      slot: str = "M") -> BuffResult:
        """Записывает уже отданный баф. slot «R» — отдан по рулетке (очередь была пуста)."""
        with self.db.tx():
            cur = self.db.run(
                "INSERT INTO donations(kind, donor_id, recipient_id, slot, status, requested_by, created_at) "
                "VALUES(?, ?, ?, ?, 'pending', ?, ?)",
                kind,
                donor_id,
                recipient_id,
                slot,
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

    # ---------- уведомления ----------

    def add_push(self, player_id: int, endpoint: str, p256dh: str, auth: str, now: int) -> None:
        self.db.run(
            "INSERT INTO push_subs(player_id, endpoint, p256dh, auth, created_at) VALUES(?, ?, ?, ?, ?) "
            "ON CONFLICT(endpoint) DO UPDATE SET player_id = excluded.player_id, p256dh = excluded.p256dh, "
            "auth = excluded.auth",
            player_id, endpoint, p256dh, auth, now,
        )

    def drop_push(self, endpoint: str, player_id: int | None = None) -> None:
        if player_id is None:
            self.db.run("DELETE FROM push_subs WHERE endpoint = ?", endpoint)
        else:
            self.db.run("DELETE FROM push_subs WHERE endpoint = ? AND player_id = ?", endpoint, player_id)

    def push_subs(self, player_id: int):
        return self.db.all("SELECT * FROM push_subs WHERE player_id = ?", player_id)

    def push_count(self) -> int:
        return self.db.one("SELECT COUNT(DISTINCT player_id) AS n FROM push_subs")["n"]

    def notice_sent(self, player_id: int, key: str) -> bool:
        return self.db.one("SELECT 1 FROM notify_log WHERE player_id = ? AND key = ?", player_id, key) is not None

    def mark_notice(self, player_id: int, key: str, now: int) -> None:
        self.db.run("INSERT OR IGNORE INTO notify_log(player_id, key, sent_at) VALUES(?, ?, ?)", player_id, key, now)

    def set_notify_prefs(self, player_id: int, prefs: dict) -> None:
        self.db.run("UPDATE players SET notify_prefs = ? WHERE id = ?", json.dumps(prefs), player_id)

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

    # ---------- рулетка: очередь пуста — баф всё равно не пропадает ----------

    def roulette_on(self, kind: str) -> bool:
        return self.setting_float(f"roulette_{kind}") > 0

    def set_pp_level(self, player_id: int, level: int | None) -> None:
        if level is not None and not 1 <= level <= 99:
            level = None
        self.db.run("UPDATE players SET pp_level = ? WHERE id = ?", level, player_id)

    def _note_pp(self, player_id: int, item: str | None, level: int | None, finished: bool) -> None:
        """Запись стройки Электростанции подсказывает её уровень: строит до N — значит уже N−1."""
        if item != "pp" or not level:
            return
        known = level if finished else level - 1
        self.db.run(
            "UPDATE players SET pp_level = ? WHERE id = ? AND (pp_level IS NULL OR pp_level < ?)",
            known, player_id, known,
        )

    def roulette_order(self, kind: str, now: int, exclude: int | None = None) -> list[RouletteRow]:
        """Кому отдать баф по рулетке. Кто недавно получил баф (пауза) — в конце. Дальше приоритет —
        кто не построил Электростанцию до цели (настройка priority_below), потом — кто меньше всех
        получил по рулетке за неделю,
        при равенстве — жребий. Жребий меняется только после каждого бафа по рулетке,
        поэтому у всех на экране один и тот же «следующий»."""
        goal = int(self.setting_float("priority_below"))
        active_since = now - int(self.setting_float("roulette_active_days") * DAY)
        rows = self.db.all(
            "SELECT p.id, p.nick, p.pp_level, "
            "(SELECT COUNT(*) FROM donations d WHERE d.recipient_id = p.id AND d.kind = ? AND d.slot = 'R' "
            " AND d.status = 'done' AND d.resolved_at >= ?) AS got_week, "
            "(SELECT MAX(d.resolved_at) FROM donations d WHERE d.recipient_id = p.id AND d.kind = ? "
            " AND d.status = 'done') AS got_at "
            "FROM players p WHERE p.last_seen_at >= ? "
            "OR EXISTS(SELECT 1 FROM timers t WHERE t.player_id = p.id AND t.active = 1) "
            "OR EXISTS(SELECT 1 FROM donations d WHERE d.donor_id = p.id AND d.status = 'done' AND d.resolved_at >= ?)",
            kind, now - 7 * DAY, kind, active_since, active_since,
        )
        gap = int(self.setting_float("min_gap_hours") * HOUR)
        draw = self.db.one(
            "SELECT COUNT(*) AS n FROM donations WHERE kind = ? AND slot = 'R' AND status = 'done'", kind
        )["n"]
        out = []
        for r in rows:
            if r["id"] == exclude:
                continue
            level = r["pp_level"]
            group = 1 if level is None else (0 if level < goal else 2)
            if group == 0:
                why = t("⚡ Электростанция {level} — ещё не построил {goal}", level=level, goal=goal)
            elif group == 1:
                why = t("уровень Электростанции не указан")
            else:
                why = t("Электростанция {level} — уже построил", level=level)
            if r["got_week"]:
                why += t(" · по рулетке за неделю: {n}", n=r["got_week"])
            paused = max(0, (r["got_at"] or 0) + gap - now) if gap else 0
            if paused:
                why = t("⏸ пауза ещё {time} · ", time=format_duration(paused)) + why
            out.append(RouletteRow(r["id"], r["nick"], level, group, r["got_week"], why, paused))
        lot = lambda pid: hashlib.sha1(f"{kind}:{draw}:{pid}".encode()).hexdigest()  # noqa: E731
        out.sort(key=lambda x: (x.paused > 0, x.group, x.got_week, lot(x.player_id)))
        return out

    def give_target(self, kind: str, donor_id: int, now: int):
        """Кому этот игрок должен отдать баф прямо сейчас: (ник, id, это рулетка?) или None."""
        first = next((r for r in self.queue_order(kind, now) if r.need and r.candidate.player_id != donor_id), None)
        if first is not None:
            return first.candidate.nick, first.candidate.player_id, False
        if self.roulette_on(kind):
            pick = next(iter(self.roulette_order(kind, now, exclude=donor_id)), None)
            if pick is not None:
                return pick.nick, pick.player_id, True
        return None

    # ---------- очередь по порядку и «я отдал баф» ----------

    def queue_order(self, kind: str, now: int) -> list[OrderRow]:
        """Очередь в том порядке, в каком бафы положено отдавать по правилам.
        Первый в списке — «следующий». В конце — те, кто уже дошёл до цели.
        У каждого — понятная причина, почему он на этом месте."""
        rules = self.rules(kind)
        holding = self.holding_map(now)
        rows = {}
        for row in self._timer_rows(kind):
            c = self.candidate_from_row(row, rules, now, holding.get(row["player_id"], ("", 0))[1])
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
            needed = buffs_needed(c.remaining, c.base, rules) if need else 0
            paused = max(0, c.paused_until(rules) - now)
            fire = fire_left(c, rules) if need else None
            r = OrderRow(
                position=pos,
                candidate=c,
                label=gamedata.label(row["item"], row["level"], row["note"]),
                needed=needed,
                paused_for=paused,
                joined_at=row["created_at"],
                slot=slot,
                need=need,
                share=share(c, rules) if need else 1.0,
                total=c.received + needed,
                fire_in=fire,
            )
            r.why = self._why(r, rules, holding.get(c.player_id), now)
            return r

        return [make(c, slot, i, True) for i, (c, slot) in enumerate(ordered, 1)] + [
            make(c, "", None, False) for c in reached
        ]

    @staticmethod
    def _why(r: OrderRow, rules: Rules, held: tuple[str, int] | None, now: int) -> str:
        """Почему игрок на этом месте — одной строкой, без математики."""
        c = r.candidate
        if not r.need:
            return t("дошёл до цели — бафы больше не нужны")
        got = t("получил {n} из {total}", n=c.received, total=r.total)
        if c.urgent:
            return t("🔥 срочно (отметил R4) · {got}", got=got)
        if held:
            return t("⏳ держит готовый баф на {kind} {time} — пропускает ход, пока не отдаст",
                     kind=t(KIND_ACC[held[0]]), time=format_duration(held[1]))
        if r.paused_for:
            return t("⏸ пауза после бафа ещё {time} · {got}", time=format_duration(r.paused_for), got=got)
        if r.fire_in is not None:
            return t("⏰ горит: через {time} сам дойдёт до цели — баф нужен раньше · {got}",
                     time=format_duration(r.fire_in), got=got)
        prio = t("⚡ приоритет ×{w} · ", w=f"{rules.priority_weight:g}") if c.priority and rules.order == ORDER_SHARE else ""
        if rules.order == ORDER_SHARE:
            if c.received == 0:
                return prio + t("ещё не получал · ждёт {time}", time=format_duration(max(0, now - c.waiting_since)))
            return prio + t("{got} — это {pct}% от положенного", got=got, pct=round(r.share * 100))
        if r.slot == "B":
            return t("самый большой остаток · {got}", got=got)
        return t("меньше всех получил и дольше ждёт · {got}", got=got)

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
        need = [r.candidate.player_id for r in self.queue_order(kind, now) if r.need]
        # Рулетка: в очереди нет никого, кроме самого донора, — баф может получить любой игрок союза.
        roulette = recipient_id not in need and not [p for p in need if p != donor_id] and self.roulette_on(kind)
        if roulette and self.player(recipient_id) is None:
            return None, "not_in_queue"
        if not roulette and self.active_timer(recipient_id, kind) is None:
            return None, "not_in_queue"
        recent = self.db.one(
            "SELECT id FROM donations WHERE kind = ? AND donor_id = ? AND status = 'done' AND resolved_at > ?",
            kind,
            donor_id,
            now - 10 * MINUTE,
        )
        if recent is not None:
            return None, "duplicate"
        return self.record_manual(donor_id, recipient_id, kind, by_id, now, slot="R" if roulette else "M"), None

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
                self.db.run("UPDATE timers SET active = 1, closed_at = NULL WHERE id = ?", timer["id"])
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
