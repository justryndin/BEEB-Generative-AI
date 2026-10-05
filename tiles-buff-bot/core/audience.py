"""Кому адресовано объявление или опрос: всем, только R4, по метке CRM, выбранным игрокам (или одному).

Хранится строкой: "" или "all" — всем; "r4"; "tag:актив"; "ids:3,15,42".
Получатель — персонаж (у твинка своя метка и свой ответ); права R4 — у аккаунта.
"""

from __future__ import annotations

from .service import Service

ALL, R4, TAG, IDS = "all", "r4", "tag", "ids"


def make(kind: str, tag: str = "", ids: list[int] | None = None) -> str:
    if kind == R4:
        return R4
    if kind == TAG and tag.strip():
        return f"{TAG}:{tag.strip().lower()[:40]}"
    if kind == IDS and ids:
        return f"{IDS}:" + ",".join(str(i) for i in sorted(set(ids)))
    return ALL


def parse(text: str | None) -> tuple[str, str]:
    text = (text or "").strip()
    if not text or text == ALL:
        return ALL, ""
    if text == R4:
        return R4, ""
    kind, _, value = text.partition(":")
    return (kind, value) if kind in (TAG, IDS) else (ALL, "")


def ids_of(text: str | None) -> set[int]:
    kind, value = parse(text)
    return {int(x) for x in value.split(",") if x.strip().isdigit()} if kind == IDS else set()


def _tags(player) -> set[str]:
    return {t.strip().lower() for t in (player["crm_tags"] or "").split(",") if t.strip()}


def _is_r4(svc: Service, player) -> bool:
    if svc.is_admin_player(player):
        return True
    owner = player["owner_id"] if "owner_id" in player.keys() else None
    return bool(owner and svc.is_admin_player(svc.player(owner)))


def includes(svc: Service, text: str | None, player) -> bool:
    """Видит ли этот персонаж объявление/опрос."""
    kind, value = parse(text)
    if kind == ALL:
        return True
    if kind == R4:
        return _is_r4(svc, player)
    if kind == TAG:
        return value in _tags(player)
    return player["id"] in ids_of(text)


def members(svc: Service, text: str | None) -> list:
    """Все персонажи союза, кому адресовано (для аналитики: охват, кто прочитал, кто молчит)."""
    return [p for p in svc.players() if includes(svc, text, p)]


def describe(svc: Service, text: str | None) -> tuple[str, dict]:
    """Подпись для R4: (русский ключ для _(), подстановки)."""
    kind, value = parse(text)
    if kind == R4:
        return "Только R4", {}
    if kind == TAG:
        return "Метка «{tag}»", {"tag": value}
    if kind == IDS:
        ids = ids_of(text)
        if len(ids) == 1:
            p = svc.player(next(iter(ids)))
            return "Лично: {nick}", {"nick": p["nick"] if p else "?"}
        return "Выбранным: {n}", {"n": len(ids)}
    return "Всем", {}


def all_tags(svc: Service) -> list[str]:
    tags: set[str] = set()
    for p in svc.players():
        tags |= _tags(p)
    return sorted(tags)
