# Портал союза Tiles Survive (tiles-buff-bot)

Сайт союза: очередь сезонных бафов, «VS сегодня», «Игра по-крупному», события, опросы R4, гайды, «Вопросы и ответы».
Обновление на сервере: `cd ~/BEEB-Generative-AI/tiles-buff-bot && git pull && docker compose up -d --build` (VPN на сервере не трогать).

## Как устроено
- Python 3.12, FastAPI + Jinja2 (рендер на сервере), SQLite (`core/db.py`: SCHEMA, MIGRATIONS через ALTER TABLE, VIEW timers_live), чистый JS (`web/static/app.js`), Docker Compose.
- `core/service.py` — данные и очередь; `core/logic.py` — порядок очереди (по доле, «горит», пауза, рулетка);
  `core/notify.py` — Web Push; `core/polls.py` — опросы; `core/audience.py` — «кому» (все/R4/метка/выбранные/один);
  `core/crm.py` — объявления и события; `core/vs.py` + `core/powerplay.py` — Дуэль и календарь «Игры по-крупному»;
  `core/planner.py` — путь к Электростанции 30; `core/faq.py` — вопросы и поиск; `core/tips.py` — советы; `core/analytics.py` — аналитика R4.
- `web/app.py` — все маршруты; `web/templates/` — шаблоны (`base.html` — каркас с боковым меню, `_icons.html` — SVG-спрайт);
  `web/static/style.css` — дизайн-система (токены цвета на :root, тёмная тема по умолчанию).
- Персонажи: аккаунт (вход по нику и PIN) + твинки (`players.owner_id`), активный персонаж в `sessions.char_id`;
  права R4 и настройки — у аккаунта (`acc_id(me)`, `acc_admin` в строке сессии).

## Правила
- **Только русский.** `core/i18n.py`: `ENABLED = ("ru",)`. Тексты пишем по-русски в `_("…")`; переводы в `core/locales/` не обновляем.
- Время всегда в двух видах: МСК и UTC. Игровой день — 00:00 UTC = 03:00 МСК.
- Объявления, опросы, события, настройки — только R4 (`need_admin`); назначать R4 — только владелец.
- Тесты: `python -m pytest -q` (должны проходить все). Коммиты в ветку `claude/dazzling-maxwell-uf3pkt`.
- Экономия лимита: перед крупной задачей — оценка и согласование; без скриншотов без нужды; без параллельных агентов-переводчиков.
