"""Аналитика: насколько хорошо работает раздача бафов (для R4) и что она даёт игроку.

Главные числа эффективности — из модели союза: больше всего помощи теряется,
когда готовый баф лежит без дела. Поэтому смотрим скорость отдачи и долю
использованных бафов, а не только «сколько отдали».
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass

from .logic import STATUS_NEED
from .service import KINDS, Service
from .timeparse import DAY, HOUR


def _median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def _done(svc: Service, since: int, until: int):
    return svc.db.all(
        "SELECT d.*, json_extract(d.undo, '$.donor.reduction') AS donor_cut FROM donations d "
        "WHERE d.status = 'done' AND d.resolved_at >= ? AND d.resolved_at < ? ORDER BY d.resolved_at",
        since, until,
    )


def give_delays(svc: Service, since: int, until: int) -> dict[int, list[int]]:
    """Сколько секунд готовый баф лежал до отдачи: {игрок: [задержки]}.
    Считаем по соседним отметкам одного игрока и типа: готов = прошлая отдача + перезарядка."""
    cooldown = int(svc.setting_float("cooldown_hours") * HOUR)
    rows = svc.db.all(
        "SELECT donor_id, kind, resolved_at FROM donations WHERE status = 'done' AND resolved_at < ? "
        "ORDER BY donor_id, kind, resolved_at", until,
    )
    out: dict[int, list[int]] = {}
    prev: dict[tuple[int, str], int] = {}
    for r in rows:
        key = (r["donor_id"], r["kind"])
        if key in prev and r["resolved_at"] >= since:
            out.setdefault(r["donor_id"], []).append(max(0, r["resolved_at"] - (prev[key] + cooldown)))
        prev[key] = r["resolved_at"]
    return out


def first_waits(svc: Service, since: int, now: int) -> tuple[list[int], int]:
    """Сколько ждали первого бафа записи, созданные за период: ([секунды], сколько ещё ждут)."""
    waits, waiting = [], 0
    for t in svc.db.all("SELECT * FROM timers WHERE created_at >= ?", since):
        first = svc.db.one(
            "SELECT MIN(resolved_at) AS t FROM donations WHERE recipient_id = ? AND kind = ? AND status = 'done' "
            "AND resolved_at >= ?", t["player_id"], t["kind"], t["created_at"],
        )["t"]
        if first is not None and (t["closed_at"] is None or first <= t["closed_at"]):
            waits.append(first - t["created_at"])
        elif t["active"] and t["buffs_received"] == 0:
            waiting += 1
    return waits, waiting


@dataclass
class PlayerLine:
    id: int
    nick: str
    given: int
    received: int
    delay: float | None  # медиана, сек
    last_seen: int | None
    push: bool
    holding: int  # сколько держит готовый баф, сек (0 — нет)
    in_queue: bool


def r4_report(svc: Service, now: int, period: int = 7) -> dict:
    since, prev_since = now - period * DAY, now - 2 * period * DAY
    done = _done(svc, since, now)
    prev = _done(svc, prev_since, since)
    delays = give_delays(svc, since, now)
    all_delays = [d for ds in delays.values() for d in ds]
    cooldown_h = svc.setting_float("cooldown_hours")

    players = svc.players()
    waits, still_waiting = first_waits(svc, since, now)
    holding = svc.holding_map(now)
    subs = {r["player_id"] for r in svc.db.all("SELECT DISTINCT player_id FROM push_subs")}
    in_queue = {r["player_id"] for r in svc.db.all("SELECT DISTINCT player_id FROM timers WHERE active = 1")}
    # Активные — кто заходил, отдавал или стоит в очереди: у каждого есть баф, который можно отдать.
    donors = {d["donor_id"] for d in done}
    active = [p for p in players if (p["last_seen_at"] or 0) >= since or p["id"] in donors or p["id"] in in_queue]
    potential = len(active) * len(KINDS) * period * 24 / cooldown_h if cooldown_h else 0

    queues = {}
    for kind in KINDS:
        rows = svc.queue_order(kind, now)
        need = [r for r in rows if r.need]
        queues[kind] = {
            "need": len(need),
            "reached": len(rows) - len(need),
            "share": statistics.mean(r.share for r in need) if need else None,
            "owed": sum(r.needed for r in need),
        }

    given_by = {}
    got_by = {}
    for d in done:
        given_by[d["donor_id"]] = given_by.get(d["donor_id"], 0) + 1
        got_by[d["recipient_id"]] = got_by.get(d["recipient_id"], 0) + 1
    lines = [
        PlayerLine(
            id=p["id"], nick=p["nick"], given=given_by.get(p["id"], 0), received=got_by.get(p["id"], 0),
            delay=_median(delays.get(p["id"], [])), last_seen=p["last_seen_at"], push=p["id"] in subs,
            holding=holding.get(p["id"], ("", 0))[1], in_queue=p["id"] in in_queue,
        )
        for p in players
    ]
    lines.sort(key=lambda x: (-x.given, x.nick.lower()))
    owed_total = sum(q["owed"] for q in queues.values())
    per_day = len(done) / period
    return {
        "period": period,
        "given": len(done),
        "given_prev": len(prev),
        "per_day": per_day,
        "saved_recipients": sum(d["reduction"] or 0 for d in done),
        "saved_donors": sum(d["donor_cut"] or 0 for d in done),
        "delay_median": _median(all_delays),
        "on_time": (sum(1 for d in all_delays if d <= 6 * HOUR) / len(all_delays)) if all_delays else None,
        "utilization": min(1.0, len(done) / potential) if potential else None,
        "potential": potential,
        "active": len(active),
        "first_wait": _median(waits),
        "still_waiting": still_waiting,
        "queues": queues,
        "owed_total": owed_total,
        "days_to_clear": owed_total / per_day if per_day else None,
        "holders": sorted(((svc.player(pid)["nick"], kind, held) for pid, (kind, held) in holding.items()
                           if svc.player(pid)), key=lambda x: -x[2]),
        "silent": [p for p in players if p["id"] in in_queue and (p["last_seen_at"] or 0) < now - 3 * DAY],
        "push_share": len(subs & in_queue) / len(in_queue) if in_queue else None,
        "push_players": len(subs),
        "lines": lines,
    }


def benefit(svc: Service, player_id: int, now: int) -> dict:
    """Что система дала игроку: сколько дней ему срезали бафы — полученные и свои отданные."""
    got = svc.db.one(
        "SELECT COUNT(*) AS n, COALESCE(SUM(reduction), 0) AS s FROM donations "
        "WHERE recipient_id = ? AND status = 'done'", player_id,
    )
    gave = svc.db.one(
        "SELECT COUNT(*) AS n, COALESCE(SUM(json_extract(undo, '$.donor.reduction')), 0) AS s FROM donations "
        "WHERE donor_id = ? AND status = 'done'", player_id,
    )
    week = now - 7 * DAY
    alliance = svc.db.one(
        "SELECT COUNT(*) AS n, COALESCE(SUM(reduction), 0) + COALESCE(SUM(json_extract(undo, '$.donor.reduction')), 0) AS s "
        "FROM donations WHERE status = 'done' AND resolved_at >= ?", week,
    )
    shares = {}
    for kind in KINDS:
        row = next((r for r in svc.queue_order(kind, now) if r.candidate.player_id == player_id), None)
        if row is not None:
            shares[kind] = row
    return {
        "got_n": got["n"], "got_s": got["s"],
        "gave_n": gave["n"], "gave_s": gave["s"],
        "total_s": got["s"] + gave["s"],
        "alliance_n": alliance["n"], "alliance_s": alliance["s"],
        "shares": shares,
        "STATUS_NEED": STATUS_NEED,
    }
