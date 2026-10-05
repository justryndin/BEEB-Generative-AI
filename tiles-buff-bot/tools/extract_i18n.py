"""Собирает все русские строки, которые сайт переводит, в core/locales/keys.json.

Запуск: python tools/extract_i18n.py — затем переводчики дополняют en/es/pt.json
(ключ — русский текст, значение — перевод). Отсутствующий перевод = русский текст.
"""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
CYR = re.compile(r"[А-Яа-яЁё]")
# Строковые литералы в шаблонах: "…" и '…' (без переносов строк).
TPL_STR = re.compile(r'"((?:[^"\\\n]|\\.)*)"|\'((?:[^\'\\\n]|\\.)*)\'')
TPL_EXPR = re.compile(r"{{.*?}}|{%.*?%}", re.S)
# Python: что не показываем игроку.
SKIP_PY = {"core/timeparse.py", "core/logic.py", "core/db.py", "core/analytics.py", "web/manage.py"}
SKIP_ASSIGN = {"SETTINGS", "_KIND_WORDS", "_PP_TIMES", "_PP_COST", "_PP_SOURCE", "_PP_REQUIRES", "SOURCE", "_PREPARE_V2"}
SKIP_CALLS = {"exception", "warning", "info", "error", "debug"}


def from_templates() -> set[str]:
    keys = set()
    for path in (ROOT / "web/templates").rglob("*.html"):
        if path.name.count(".") > 1:
            continue  # переведённые целиком страницы (*.en.html …)
        if (path.with_name(path.stem + ".en.html")).exists():
            continue  # у страницы есть целиком переведённые версии
        text = path.read_text()
        for expr in TPL_EXPR.findall(text):
            for m in TPL_STR.finditer(expr):
                s = m.group(1) if m.group(1) is not None else m.group(2)
                s = s.replace('\\"', '"').replace("\\'", "'")
                if CYR.search(s):
                    keys.add(s)
    return keys


class _Py(ast.NodeVisitor):
    def __init__(self):
        self.keys: set[str] = set()
        self.fstrings: list[str] = []
        self.skip_depth = 0

    def visit_Assign(self, node):
        targets = getattr(node, "targets", None) or [node.target]
        names = {t.id for t in targets if isinstance(t, ast.Name)}
        if names & SKIP_ASSIGN:
            return
        self.generic_visit(node)

    visit_AnnAssign = visit_Assign

    def visit_Call(self, node):
        f = node.func
        if isinstance(f, ast.Attribute) and f.attr in SKIP_CALLS:
            return
        self.generic_visit(node)

    def visit_Expr(self, node):
        if isinstance(node.value, ast.Constant):
            return  # докстринги
        self.generic_visit(node)

    def visit_Constant(self, node):
        if isinstance(node.value, str) and CYR.search(node.value):
            self.keys.add(node.value)

    def visit_JoinedStr(self, node):
        lits = "".join(v.value for v in node.values if isinstance(v, ast.Constant))
        if CYR.search(lits):
            self.fstrings.append(ast.unparse(node))
        # литералы внутри f-строки не ключи


def from_python() -> tuple[set[str], list[str]]:
    v = _Py()
    for path in list((ROOT / "core").glob("*.py")) + list((ROOT / "web").glob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if rel in SKIP_PY:
            continue
        v.visit(ast.parse(path.read_text()))
    return v.keys, v.fstrings


def from_data() -> set[str]:
    from core import gamedata
    from web.guides import GUIDES

    keys = {label for g in GUIDES for label, _ in g.sources}
    for it in gamedata.ITEMS.values():
        keys.add(it.ru)
        for lvl in it.requires:
            keys.update(name for name, _ in it.requires_list(lvl))
    return keys


def main() -> None:
    py, fstrings = from_python()
    keys = sorted(from_templates() | py | from_data())
    out = ROOT / "core/locales/keys.json"
    out.write_text(json.dumps(keys, ensure_ascii=False, indent=0) + "\n")
    print(f"{len(keys)} ключей → {out.relative_to(ROOT)}")
    for f in fstrings:
        print("f-строка с русским текстом (не переводится):", f[:120])


if __name__ == "__main__":
    main()
