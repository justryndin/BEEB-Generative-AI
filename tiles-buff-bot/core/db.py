"""SQLite: схема и простые помощники для запросов."""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager

SCHEMA = """
CREATE TABLE IF NOT EXISTS players (
    id          INTEGER PRIMARY KEY,
    tg_id       INTEGER UNIQUE,
    tg_username TEXT,
    nick        TEXT NOT NULL,
    nick_key    TEXT NOT NULL UNIQUE,
    is_admin    INTEGER NOT NULL DEFAULT 0,
    created_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS timers (
    id              INTEGER PRIMARY KEY,
    player_id       INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    kind            TEXT NOT NULL,
    base_seconds    INTEGER NOT NULL,
    end_at          INTEGER NOT NULL,
    urgent          INTEGER NOT NULL DEFAULT 0,
    active          INTEGER NOT NULL DEFAULT 1,
    target_notified INTEGER NOT NULL DEFAULT 0,
    buffs_received  INTEGER NOT NULL DEFAULT 0,
    last_buff_at    INTEGER,
    created_at      INTEGER NOT NULL,
    item            TEXT,
    level           INTEGER,
    note            TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS timers_one_active ON timers(player_id, kind) WHERE active = 1;

CREATE TABLE IF NOT EXISTS donations (
    id           INTEGER PRIMARY KEY,
    kind         TEXT NOT NULL,
    donor_id     INTEGER NOT NULL,
    recipient_id INTEGER NOT NULL,
    slot         TEXT NOT NULL,
    status       TEXT NOT NULL,
    excluded     TEXT NOT NULL DEFAULT '',
    requested_by INTEGER,
    chat_id      INTEGER,
    message_id   INTEGER,
    reduction    INTEGER,
    created_at   INTEGER NOT NULL,
    resolved_at  INTEGER
);
CREATE INDEX IF NOT EXISTS donations_kind_status ON donations(kind, status);

CREATE TABLE IF NOT EXISTS cooldowns (
    player_id INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    kind      TEXT NOT NULL,
    ready_at  INTEGER NOT NULL,
    reminded  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (player_id, kind)
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    token      TEXT PRIMARY KEY,
    player_id  INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    csrf       TEXT NOT NULL,
    created_at INTEGER NOT NULL
);

-- Подписки на уведомления (Web Push): одна строка — одно устройство.
CREATE TABLE IF NOT EXISTS push_subs (
    id         INTEGER PRIMARY KEY,
    player_id  INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    endpoint   TEXT NOT NULL UNIQUE,
    p256dh     TEXT NOT NULL,
    auth       TEXT NOT NULL,
    created_at INTEGER NOT NULL
);

-- Доска объявлений союза.
CREATE TABLE IF NOT EXISTS posts (
    id         INTEGER PRIMARY KEY,
    author_id  INTEGER REFERENCES players(id) ON DELETE SET NULL,
    text       TEXT NOT NULL,
    pinned     INTEGER NOT NULL DEFAULT 0,
    important  INTEGER NOT NULL DEFAULT 0,  -- прислать всем на телефон
    rsvp       INTEGER NOT NULL DEFAULT 0,  -- спросить «Буду / Не смогу»
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS post_reads (
    post_id   INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    player_id INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    PRIMARY KEY (post_id, player_id)
);

-- Ответы «Буду / Не смогу»: ref — «post:12» или «event:3:<начало>».
CREATE TABLE IF NOT EXISTS answers (
    ref       TEXT NOT NULL,
    player_id INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    answer    TEXT NOT NULL,
    at        INTEGER NOT NULL,
    PRIMARY KEY (ref, player_id)
);

-- Недельные события союза. Время — серверное (UTC), как в игре.
CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY,
    title      TEXT NOT NULL,
    days       TEXT NOT NULL,             -- дни недели UTC: 0 — пн … 6 — вс, например «1234»
    start_min  INTEGER NOT NULL,          -- начало, минут от 00:00 UTC
    duration   INTEGER NOT NULL,          -- длительность, минут
    prepare    TEXT NOT NULL DEFAULT '',  -- что делать союзу
    remind_min INTEGER NOT NULL DEFAULT 60,  -- напомнить за столько минут (-1 — не напоминать)
    checked    INTEGER NOT NULL DEFAULT 0,   -- R4 сверили время с игрой
    rsvp       INTEGER NOT NULL DEFAULT 0
);

-- Что уже отправлено, чтобы не присылать одно и то же дважды.
CREATE TABLE IF NOT EXISTS notify_log (
    player_id INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    key       TEXT NOT NULL,
    sent_at   INTEGER NOT NULL,
    PRIMARY KEY (player_id, key)
);
"""

# Колонки, добавленные после первой версии: таблица → [(колонка, тип)].
MIGRATIONS = {
    "timers": [
        ("item", "TEXT"),
        ("level", "INTEGER"),
        ("note", "TEXT"),
        ("checked_at", "INTEGER"),  # когда игрок последний раз сверил время с игрой
        ("closed_at", "INTEGER"),  # когда таймер закрылся (закончился, «Готово», заменён)
        ("next_dismissed", "INTEGER NOT NULL DEFAULT 0"),  # «Не сейчас» на «Встать со следующей»
    ],
    "players": [
        ("pin_hash", "TEXT"),
        ("failed_logins", "INTEGER NOT NULL DEFAULT 0"),
        ("locked_until", "INTEGER"),
        ("is_owner", "INTEGER NOT NULL DEFAULT 0"),
        ("last_seen_at", "INTEGER"),
        ("agreed_at", "INTEGER"),
        ("notify_prefs", "TEXT"),  # JSON: какие уведомления выключены
        ("crm_note", "TEXT"),  # заметка R4 об игроке
        ("crm_tags", "TEXT"),  # метки через запятую: «актив, новичок»
    ],
    "donations": [("undo", "TEXT"), ("undone_by", "INTEGER"), ("undone_at", "INTEGER")],
}


class Database:
    def __init__(self, path: str):
        if path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        if path != ":memory:":
            self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.executescript(SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        for table, wanted in MIGRATIONS.items():
            columns = {r["name"] for r in self.conn.execute(f"PRAGMA table_info({table})")}
            for name, sql_type in wanted:
                if name not in columns:
                    self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}")

    def all(self, sql: str, *args) -> list[sqlite3.Row]:
        return self.conn.execute(sql, args).fetchall()

    def one(self, sql: str, *args) -> sqlite3.Row | None:
        return self.conn.execute(sql, args).fetchone()

    def run(self, sql: str, *args) -> sqlite3.Cursor:
        return self.conn.execute(sql, args)

    @contextmanager
    def tx(self):
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise
        self.conn.execute("COMMIT")
