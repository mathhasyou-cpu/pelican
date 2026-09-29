"""Пересобрать карточки сохранённого отчёта: углубление (`weak.deepen`) + карточка.

Отбор, оценка и порядок ТОП не трогаются — меняются только тексты карточек, хронология
и добранные источники. Нужен дважды:

- **замер** правки карточки без 15–20 минут полного `ask` на запрос: `--out DIR` кладёт
  рядом `ask-cards-<имя>-before.json` и `…-after.json`, их читают
  `scripts/measure_card_facts.py --dir DIR --strict` и `--density` этого скрипта;
- **обновление** отчётов истории стенда: `--save` пишет результат и страницу обратно.

    python scripts/recard.py 20260924-171913 --out reports/recard
    python scripts/recard.py --all --save
    python scripts/recard.py --density reports/recard
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
# ⚠️ Под перенаправленным выводом консоль Windows даёт cp1251, и один «⚠️» валит прогон.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from pelican import jobs, locks  # noqa: E402
from pelican.config import settings  # noqa: E402
from pelican.runner import MODEL_LOCK  # noqa: E402
from pelican.weak import page  # noqa: E402
from pelican.weak.ask import deepen_top, write_cards  # noqa: E402
from pelican.weak.assess import Evidence  # noqa: E402

FIELDS = ("description", "advantage", "case")
_ANCHOR = re.compile(r"\d|\b[A-Z][A-Za-z0-9]*[a-z0-9]")


def evidence_of(sig) -> list[Evidence]:
    """Свидетельства в ногу с источниками — как их собрал `ask.run`."""
    return [
        Evidence(-1, s.date, s.title, s.evidence, s.url, s.publisher, s.excerpt)
        for s in sig.sources
    ]


def recard(d: dict) -> tuple[dict, float]:
    result = page.from_json(d)
    shown = [(s, evidence_of(s)) for s in result.signals]
    t0 = time.monotonic()
    # Модель одна на стенд: запрос из очереди и пересборка не должны идти разом.
    with locks.hold(MODEL_LOCK):
        deepen_top(shown, result.live_errors, print)
        write_cards(shown, print)
    return json.loads(page.to_json(result)), time.monotonic() - t0


def density(run: dict) -> dict[str, float]:
    """Пустые разделы, предложения с якорем (число/имя), пункты хронологии — на карточку."""
    sigs = run.get("signals", [])
    n = max(len(sigs), 1)
    empty = {f: sum(1 for s in sigs if not s.get(f)) for f in FIELDS}
    sents = [x for s in sigs for f in FIELDS for x in re.split(r"(?<=[.!?])\s+", s.get(f) or "") if x]
    anchored = sum(1 for x in sents if _ANCHOR.search(re.sub(r"\[[\d,\s]+\]", "", x)))
    return {
        "карточек": len(sigs),
        **{f"пусто {f}": empty[f] for f in FIELDS},
        "предложений на карточку": round(len(sents) / n, 1),
        "с якорем, доля": round(anchored / max(len(sents), 1), 2),
        "фактов хронологии на карточку": round(sum(len(s.get("facts") or []) for s in sigs) / n, 1),
        "источников на карточку": round(sum(len(s.get("sources") or []) for s in sigs) / n, 1),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="пересборка карточек сохранённого отчёта")
    ap.add_argument("names", nargs="*", help="имена отчётов (20260924-171913)")
    ap.add_argument("--all", action="store_true", help="все отчёты истории")
    ap.add_argument("--out", type=Path, help="папка для ask-cards-*-before/after.json")
    ap.add_argument("--save", action="store_true", help="записать отчёт и страницу обратно")
    ap.add_argument("--density", type=Path, help="только напечатать плотность по папке")
    args = ap.parse_args()

    if args.density:
        for path in sorted(args.density.glob("ask-cards-*.json")):
            print(path.stem, json.dumps(density(json.loads(path.read_text("utf-8"))), ensure_ascii=False))
        return

    with jobs.connect(settings.database_url) as conn:
        names = [r["name"] for r in jobs.reports(conn, limit=500)] if args.all else args.names
        for name in names:
            row = jobs.report(conn, name)
            if row is None:
                print(f"{name}: отчёта нет")
                continue
            before = row["result"]
            after, seconds = recard(before)
            print(f"{name} «{row['query']}»: {seconds:.0f} с")
            if args.out:
                args.out.mkdir(parents=True, exist_ok=True)
                for tag, run in (("before", before), ("after", after)):
                    (args.out / f"ask-cards-{name}-{tag}.json").write_text(
                        json.dumps(run, ensure_ascii=False, indent=2), encoding="utf-8"
                    )
            if args.save:
                html = page.render(page.from_json(after))
                jobs.save_report(
                    conn, name, row["query"], row["started"], json.dumps(after, ensure_ascii=False), html
                )


if __name__ == "__main__":
    main()
