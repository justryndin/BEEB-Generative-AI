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
    created_at      INTEGER NOT NULL
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
"""


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
