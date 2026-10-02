"""Простые SVG-графики без внешних библиотек: столбики по дням и горизонтальные полосы.

Один ряд данных → без легенды (название графика говорит, что на нём), тонкие столбики
со скруглённым верхом, бледная сетка, подсказка по наведению (<title>) и таблица рядом.
"""

from __future__ import annotations

from html import escape


def _nice_max(value: int) -> int:
    if value <= 4:
        return 4
    for step in (5, 10, 20, 25, 50, 100, 200, 250, 500, 1000):
        top = -(-value // step) * step
        if top / step <= 5:
            return top
    return value


def column_chart(points: list[tuple[str, int, str]], title: str) -> str:
    """points: [(подпись оси, значение, текст подсказки)]."""
    width, height = 400, 200
    left, right, top, bottom = 28, 6, 10, 24
    plot_w, plot_h = width - left - right, height - top - bottom
    top_value = _nice_max(max((v for _, v, _ in points), default=0))
    n = max(len(points), 1)
    band = plot_w / n
    bar = min(18.0, band * 0.7)
    parts = [
        f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" aria-label="{escape(title)}">'
    ]
    for i in range(5):
        value = top_value * i // 4
        y = top + plot_h - plot_h * value / top_value
        parts.append(f'<line class="grid" x1="{left}" x2="{width - right}" y1="{y:.1f}" y2="{y:.1f}"/>')
        parts.append(f'<text class="tick" x="{left - 6}" y="{y + 4:.1f}" text-anchor="end">{value}</text>')
    for i, (label, value, tip) in enumerate(points):
        x = left + band * i + (band - bar) / 2
        h = plot_h * value / top_value
        y = top + plot_h - h
        parts.append(f'<g class="col"><title>{escape(tip)}</title>')
        parts.append(f'<rect class="hit" x="{left + band * i:.1f}" y="{top}" width="{band:.1f}" height="{plot_h}"/>')
        if value:
            r = min(4.0, bar / 2, h)
            parts.append(
                f'<path class="bar" d="M{x:.1f},{top + plot_h:.1f} V{y + r:.1f} '
                f'Q{x:.1f},{y:.1f} {x + r:.1f},{y:.1f} H{x + bar - r:.1f} '
                f'Q{x + bar:.1f},{y:.1f} {x + bar:.1f},{y + r:.1f} V{top + plot_h:.1f} Z"/>'
            )
        parts.append("</g>")
        if label:
            anchor, lx = ("end", x + bar) if i == n - 1 else ("middle", x + bar / 2)
            parts.append(
                f'<text class="tick" x="{lx:.1f}" y="{height - 8}" text-anchor="{anchor}">{escape(label)}</text>'
            )
    parts.append(f'<line class="axis" x1="{left}" x2="{width - right}" y1="{top + plot_h}" y2="{top + plot_h}"/>')
    parts.append("</svg>")
    return "".join(parts)


def bar_list(rows: list[tuple[str, int, str]], highlight: str | None = None) -> str:
    """Горизонтальные полосы: [(имя, значение, подпись справа)] — HTML, не SVG, чтобы текст не обрезался."""
    if not rows:
        return '<p class="muted">Пока пусто — данные появятся после первых бафов.</p>'
    top_value = max(v for _, v, _ in rows) or 1
    out = ['<ol class="barlist">']
    for name, value, note in rows:
        pct = max(2.0, 100 * value / top_value)
        me = " me" if highlight and name == highlight else ""
        out.append(
            f'<li class="barrow{me}"><span class="barname">{escape(name)}</span>'
            f'<span class="bartrack"><span class="barfill" style="width:{pct:.1f}%"></span></span>'
            f'<span class="barval">{escape(note)}</span></li>'
        )
    out.append("</ol>")
    return "".join(out)
