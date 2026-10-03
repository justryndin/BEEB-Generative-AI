"""Отправка уведомлений на телефон (Web Push): ключи VAPID, отправка, фоновый проход.

Ключи создаются сами при первом запуске и хранятся в базе (настройки vapid_*),
поэтому ничего настраивать на сервере не нужно. Без библиотеки pywebpush сайт
работает как раньше, просто без уведомлений.
"""

from __future__ import annotations

import base64
import json
import logging
import os

from core.notify import Notice, due_notices
from core.service import Service

log = logging.getLogger(__name__)

try:  # pragma: no cover - зависит от установленных пакетов
    from cryptography.hazmat.primitives import serialization
    from py_vapid import Vapid01
    from pywebpush import WebPushException, webpush

    AVAILABLE = True
except ImportError:  # pragma: no cover
    AVAILABLE = False


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def ensure_keys(svc: Service) -> str:
    """Публичный ключ для браузера (applicationServerKey). Пусто — уведомления недоступны."""
    if not AVAILABLE:
        return ""
    public = svc.setting("vapid_public")
    if public and svc.setting("vapid_private"):
        return public
    vapid = Vapid01()
    vapid.generate_keys()
    private_pem = vapid.private_pem().decode()
    raw = vapid.public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    svc.set_setting("vapid_private", private_pem)
    svc.set_setting("vapid_public", _b64(raw))
    return svc.setting("vapid_public")


def _claims() -> dict:
    domain = os.environ.get("SITE_DOMAIN", "").strip()
    return {"sub": f"https://{domain}" if domain else "mailto:admin@example.com"}


def send(svc: Service, sub, notice: Notice) -> bool | None:
    """True — доставлено, False — устройство отписалось (удаляем), None — временная ошибка."""
    if not AVAILABLE:
        return None
    payload = json.dumps({"title": notice.title, "body": notice.body, "url": notice.url, "tag": notice.key})
    try:
        webpush(
            subscription_info={"endpoint": sub["endpoint"], "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]}},
            data=payload,
            vapid_private_key=Vapid01.from_pem(svc.setting("vapid_private").encode()),
            vapid_claims=_claims(),
            ttl=12 * 3600,
            timeout=10,
        )
        return True
    except WebPushException as e:  # pragma: no cover - сеть
        code = getattr(e.response, "status_code", None)
        if code in (404, 410):
            svc.drop_push(sub["endpoint"])
            return False
        log.warning("Уведомление не ушло (%s): %s", code, e)
        return None
    except Exception as e:  # pragma: no cover - сеть
        log.warning("Уведомление не ушло: %s", e)
        return None


def deliver(svc: Service, notice: Notice, now: int) -> int:
    """Отправить на все устройства игрока. Возвращает, на сколько доставлено."""
    delivered, gone, total = 0, 0, 0
    for sub in svc.push_subs(notice.player_id):
        total += 1
        result = send(svc, sub, notice)
        delivered += result is True
        gone += result is False
    if delivered or (total and gone == total):
        svc.mark_notice(notice.player_id, notice.key, now)
    return delivered


def run_once(svc: Service, tz, now: int) -> int:
    """Один проход: найти, что пора прислать, и отправить. Вызывается раз в минуту."""
    if not AVAILABLE or not svc.setting("vapid_private"):
        return 0
    sent = 0
    for notice in due_notices(svc, tz, now):
        if not svc.notice_sent(notice.player_id, notice.key):
            sent += deliver(svc, notice, now) > 0
    svc.db.run("DELETE FROM notify_log WHERE sent_at < ?", now - 30 * 86400)
    return sent
