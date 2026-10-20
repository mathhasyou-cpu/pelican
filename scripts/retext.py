"""Дописать сохранённым отчётам причины исключения и русские названия — без прогона `ask`.

    python scripts/retext.py --all                 # сухо: сколько пустых причин и латинских названий
    python scripts/retext.py --all --save          # причины + названия (модель), записать обратно
    python scripts/retext.py --all --save --no-model   # только причины, модель не нужна

Отчёт хранит тексты той версии конвейера, что его собрала. Исключённый без объяснения со
ссылкой получает причину из своих же признаков оценки (`ask.excluded_why`, BR-03) — это
детерминированно и модели не требует. Название без кириллицы (NFR-01) переводится тем же
`ask.russian_titles`, что и в прогоне, под замком модели. Отбор, оценка и порядок не меняются;
имя и `started` отчёта — прежние (BR-45).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
# ⚠️ Под перенаправленным выводом консоль Windows даёт cp1251, и один «⚠️» валит прогон.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from pelican import jobs, locks  # noqa: E402
from pelican.config import settings  # noqa: E402
from pelican.runner import MODEL_LOCK  # noqa: E402
from pelican.weak import card, page  # noqa: E402
from pelican.weak.ask import excluded_why, russian_titles  # noqa: E402

#: Разделы отчёта с названиями кандидатов.
SECTIONS = ("signals", "thin", "watch", "excluded")


def fill_why(result: dict) -> int:
    """Пустые причины исключённых → признаки оценки; сколько дописано."""
    n = 0
    for x in result.get("excluded") or []:
        if not (x.get("why") or "").strip() and (why := excluded_why(x.get("kind", ""), x.get("judgments") or {})):
            x["why"] = why
            n += 1
    return n


def latin_titles(result: dict) -> list[dict]:
    return [x for key in SECTIONS for x in result.get(key) or []
            if isinstance(x, dict) and not card.has_cyrillic(x.get("title") or "")]


def retitle(items: list[dict]) -> int:
    """Русские названия тем же переводом, что в прогоне; сколько получили кириллицу."""
    sigs = [SimpleNamespace(label=x["label"], title=x.get("title") or x["label"]) for x in items]
    with locks.hold(MODEL_LOCK):
        russian_titles(sigs, print)
    n = 0
    for x, s in zip(items, sigs, strict=True):
        if card.has_cyrillic(s.title):
            x["title"] = s.title
            n += 1
    return n


def main() -> None:
    ap = argparse.ArgumentParser(description="причины исключения и русские названия отчётов истории")
    ap.add_argument("names", nargs="*", help="имена отчётов (reports.name)")
    ap.add_argument("--all", action="store_true", help="все отчёты истории")
    ap.add_argument("--save", action="store_true", help="записать отчёт и страницу обратно")
    ap.add_argument("--no-model", action="store_true", help="только причины, без перевода названий")
    args = ap.parse_args()
    with jobs.connect(settings.database_url) as conn:
        names = [r["name"] for r in jobs.reports(conn, limit=500)] if args.all else args.names
        for name in names:
            row = jobs.report(conn, name)
            if row is None:
                print(f"{name}: отчёта нет")
                continue
            result = json.loads(json.dumps(row["result"]))
            whys, latin = fill_why(result), latin_titles(result)
            titled = retitle(latin) if args.save and latin and not args.no_model else 0
            print(f"{name} «{row['query']}»: причин дописано {whys}; латинских названий {len(latin)}"
                  + (f", переведено {titled}" if titled else ""))
            if args.save and (whys or titled):
                html = page.render(page.from_json(result))
                jobs.save_report(conn, name, row["query"], row["started"],
                                 json.dumps(result, ensure_ascii=False), html)


if __name__ == "__main__":
    # Модель занята прогоном — код 75 (EX_TEMPFAIL), как у остальных скриптов под замком.
    try:
        main()
    except locks.Busy as exc:
        print(f"занято: {exc}")
        sys.exit(75)
