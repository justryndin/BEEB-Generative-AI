"""Резервные копии базы: раз в сутки снимок SQLite в data/backups, хранятся последние KEEP.

Снимок делается штатным backup() SQLite — без остановки сайта. Владелец может скачать
последнюю копию на странице «Управление» и хранить её вне сервера.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

KEEP = 14


def folder(db_path: str) -> Path | None:
    if not db_path or db_path == ":memory:":
        return None
    return Path(db_path).resolve().parent / "backups"


def make(db_path: str, now: int, keep: int = KEEP) -> Path | None:
    """Снимок за сегодня (UTC), если его ещё нет. Возвращает путь к файлу или None."""
    target_dir = folder(db_path)
    if target_dir is None or not os.path.exists(db_path):
        return None
    target_dir.mkdir(parents=True, exist_ok=True)
    day = datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%d")
    target = target_dir / f"bot-{day}.db"
    if target.exists():
        return target
    tmp = target.with_suffix(".tmp")
    src = sqlite3.connect(db_path)
    try:
        dst = sqlite3.connect(tmp)
        with dst:
            src.backup(dst)
        dst.close()
    finally:
        src.close()
    tmp.replace(target)
    for old in sorted(target_dir.glob("bot-*.db"))[:-keep]:
        old.unlink(missing_ok=True)
    return target


def latest(db_path: str) -> Path | None:
    target_dir = folder(db_path)
    if target_dir is None or not target_dir.exists():
        return None
    files = sorted(target_dir.glob("bot-*.db"))
    return files[-1] if files else None


def listing(db_path: str) -> list[tuple[str, int]]:
    """[(имя файла, размер в байтах)] — новые сверху."""
    target_dir = folder(db_path)
    if target_dir is None or not target_dir.exists():
        return []
    return [(p.name, p.stat().st_size) for p in sorted(target_dir.glob("bot-*.db"), reverse=True)]
