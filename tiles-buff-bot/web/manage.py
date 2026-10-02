"""Служебные команды на сервере.

  docker compose exec web python -m web.manage owner "Ник"     — сделать владельцем (после регистрации)
  docker compose exec web python -m web.manage reset-pin "Ник" — сбросить PIN
  docker compose exec web python -m web.manage players          — список игроков
"""

from __future__ import annotations

import sys

from core.db import Database
from core.service import Service

from .config import load_config


def main(argv: list[str]) -> int:
    svc = Service(Database(load_config().db_path))
    if len(argv) >= 2 and argv[0] in ("owner", "reset-pin"):
        nick = " ".join(argv[1:])
        player = svc.player_by_nick(nick)
        if player is None:
            print(f"Игрок «{nick}» не найден. Сначала зарегистрируйся на сайте с этим ником.")
            return 1
        if argv[0] == "owner":
            svc.set_owner(player["id"])
            print(f"✅ {player['nick']} теперь владелец. Обнови страницу — появится «⚙️ Управление».")
        else:
            svc.set_pin(player["id"], None)
            print(f"✅ PIN игрока {player['nick']} сброшен — он может заново зарегистрироваться под своим ником.")
        return 0
    if argv[:1] == ["players"]:
        for p in svc.players():
            role = "владелец" if p["is_owner"] else "админ" if p["is_admin"] else ""
            print(f"{p['nick']:<32} {'есть PIN' if p['pin_hash'] else 'без PIN':<10} {role}")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
