"""Сливает переводы частями в core/locales/<язык>.json.

python tools/merge_i18n.py en part1.json part2.json …
Существующие переводы сохраняются, новые дописываются; ключи, которых больше нет
в keys.json, выбрасываются.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main(lang: str, parts: list[str]) -> None:
    keys = json.loads((ROOT / "core/locales/keys.json").read_text())
    out_path = ROOT / f"core/locales/{lang}.json"
    merged = json.loads(out_path.read_text()) if out_path.exists() else {}
    for part in parts:
        merged.update(json.loads(Path(part).read_text()))
    final = {k: merged[k] for k in keys if k in merged and merged[k] != k}
    out_path.write_text(json.dumps(final, ensure_ascii=False, indent=1, sort_keys=True) + "\n")
    missing = [k for k in keys if k not in merged]
    print(f"{lang}: {len(final)} переводов, без перевода {len(missing)}")
    for k in missing[:20]:
        print("  нет:", k[:80])


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2:])
