"""Раздел «Гайды»: список страниц и их источники.

Тексты — пересказ своими словами (с ссылкой на оригинал), а не копия.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Guide:
    slug: str
    emoji: str
    title: str
    summary: str
    sources: list[tuple[str, str]] = field(default_factory=list)


DD = "dandangers.ru (союз [Dan]Dangers, штат 174)"
OFF = "tilessurvive.com — официальный сайт"

GUIDES = [
    Guide("news", "📰", "Что нового в игре",
          "Обновления 2.5.900–2.6.200 и планы разработчиков — коротко и по делу.",
          [(OFF + ": 2.6.200", "https://tilessurvive.com/ru/blog/1194"),
           (OFF + ": обратная связь 26 сентября", "https://tilessurvive.com/ru/blog/1187"),
           (OFF + ": 2.6.100", "https://tilessurvive.com/ru/blog/1179"),
           (OFF + ": обратная связь 11 сентября", "https://tilessurvive.com/ru/blog/1140"),
           (OFF + ": 2.6.0", "https://tilessurvive.com/ru/blog/1127"),
           (OFF + ": 2.5.900", "https://tilessurvive.com/ru/blog/1113")]),
    Guide("pets", "🐾", "Питомцы",
          "Новая система с 2.6.100: как получить питомцев, что они дают героям и как не распылить ресурсы.",
          [(OFF + ": 2.6.100", "https://tilessurvive.com/ru/blog/1179"),
           (OFF + ": обратная связь 11 сентября", "https://tilessurvive.com/ru/blog/1140"),
           (OFF + ": 2.6.200", "https://tilessurvive.com/ru/blog/1194"),
           ("Гайд игрока по питомцам (скриншоты, прислали в союзе)", "")]),
    Guide("season", "❄️", "Сезон IV: «Эра метели»",
          "Что нового в сезоне: Печь Судного дня, холод, бастионы и города, природный газ, Ледяной гигант, очки прогресса сезона.",
          [(OFF + ": обновление 2.5.900", "https://tilessurvive.com/ru/blog/1113"),
           (OFF + ": обратная связь 29 августа", "https://tilessurvive.com/ru/blog/1120"),
           (OFF + ": обновление 2.6.100", "https://tilessurvive.com/en/blog/1173"),
           ("App Store — описание игры", "https://apps.apple.com/ru/app/tiles-survive/id6738109752")]),
    Guide("boosts", "⚡", "Бафы и ускорения",
          "Бафы союза −15%, бафы базы на 24 часа, помощь союза, ускорители: что сокращает время каждый день и как это учесть.",
          [("Скриншоты из игры от игроков союза", ""), (DD, "https://dandangers.ru/guide-bigguide.html")]),
    Guide("week", "📅", "Неделя: когда что тратить",
          "В какие дни и часы (МСК) тратить ускорения стройки и исследований, героев, снаряжение — чтобы получить очки событий.",
          [(DD, "https://dandangers.ru/daily.html"), (DD, "https://dandangers.ru/guide-17sovetov.html")]),
    Guide("build", "🏗", "Развитие базы",
          "Что строить в первую очередь, зачем нужно каждое здание, время улучшения Электростанции и Казарм.",
          [(DD, "https://dandangers.ru/guide-bigguide.html"), (DD, "https://dandangers.ru/guide-17sovetov.html"),
           ("tilessurvive.net", "https://tilessurvive.net/en/buildings/")]),
    Guide("research", "🔬", "Лаборатория",
          "Какие ветки исследований качать первыми и почему.",
          [(DD, "https://dandangers.ru/guide-laboratoriya.html"), (DD, "https://dandangers.ru/guide-17sovetov.html"),
           (DD, "https://dandangers.ru/guide-bigguide.html")]),
    Guide("newbie", "🌱", "Новичку: с чего начать",
          "Самое важное за первый месяц: VIP, герои, ресурсы только под события, ежедневная рутина.",
          [(DD, "https://dandangers.ru/guide-fullguide.html"), (DD, "https://dandangers.ru/guide-bigguide.html")]),
    Guide("tips", "💡", "Советы опытных",
          "Короткая выжимка «17 советов»: копи и трать под события, радар, армия как ресурс, зомби, безопасность.",
          [(DD, "https://dandangers.ru/guide-17sovetov.html")]),
    Guide("arcadia", "🏰", "Завоевание Аркадии",
          "Суббота, 3 часа: как победить, как работают башни, переезд, очки и награды Губернатора.",
          [(OFF, "https://tilessurvive.com/en/blog/828"), (DD, "https://dandangers.ru/guide-bigguide.html")]),
    Guide("gear", "🛡", "Перековка снаряжения героев",
          "Какие характеристики выбирать танку, бойцу и лекарю.",
          [(OFF, "https://tilessurvive.com/en/blog/821")]),
]

BY_SLUG = {g.slug: g for g in GUIDES}
