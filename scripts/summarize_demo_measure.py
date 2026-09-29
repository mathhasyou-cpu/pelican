"""Сводка ночного замера демо-надёжности (docs/todo.md §76, §91–93) — решение по правилу.

Читает то, что оставил `scripts/night_demo_measure.sh`:

- `reports/ask-A/judge-coverage.tsv` и `reports/ask-B/judge-coverage.tsv` — покрытие строк
  методолога без подрынков (A) и с ними (B), в одном окне кэша;
- `reports/ask-A/ask-tz.tsv` (ТЗ-запросы без подрынков, по одному) и `reports/ask-tz.tsv`
  (с подрынками, по три) — секунды и число позиций.

Правило зачёта записано до прогона (todo §76): подрынки остаются, если покрытие на
`HOLDOUT` не упало, на `TUNE` выросло, медиана ТОП на ТЗ-запросах выросла и медиана
секунд < `DEMO_LIMIT_S`. Скрипт печатает числа и вердикт по каждому условию — решение
принимает человек, глядя ещё и на сами карточки.

    python scripts/summarize_demo_measure.py
"""

from __future__ import annotations

import csv
import sys
from collections import defaultdict
from pathlib import Path
from statistics import median

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from measure_ask import DEMO_LIMIT_S, HOLDOUT, QUERIES, TUNE  # noqa: E402


def coverage(path: Path) -> dict[str, int]:
    """Уникальные строки датасета по доменам — так же, как считает `judge_match.py`."""
    rows: dict[str, set[str]] = defaultdict(set)
    if not path.exists():
        return {}
    with path.open(encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f, delimiter="\t"):
            rows[r["домен"]].add(r["строка датасета"])
    return {d: len(rows[d]) for d in QUERIES}


def tz(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def main() -> None:
    a = coverage(REPO / "reports" / "ask-A" / "judge-coverage.tsv")
    b = coverage(REPO / "reports" / "ask-B" / "judge-coverage.tsv")
    print("== покрытие строк методолога: A (без подрынков) → B (с подрынками)")
    for group, name in ((TUNE, "подбор"), (HOLDOUT, "отложенные")):
        sa, sb = sum(a.get(d, 0) for d in group), sum(b.get(d, 0) for d in group)
        detail = " · ".join(f"{d} {a.get(d, 0)}→{b.get(d, 0)}" for d in group)
        print(f"  {name}: {sa} → {sb}   ({detail})")
    tune_up = sum(b.get(d, 0) for d in TUNE) > sum(a.get(d, 0) for d in TUNE)
    hold_ok = sum(b.get(d, 0) for d in HOLDOUT) >= sum(a.get(d, 0) for d in HOLDOUT)

    base = tz(REPO / "reports" / "ask-A" / "ask-tz.tsv")
    full = tz(REPO / "reports" / "ask-tz.tsv")
    print("\n== ТЗ-запросы: секунды и позиции")
    for name, rows in (("без подрынков", base), ("с подрынками", full)):
        if not rows:
            print(f"  {name}: нет данных")
            continue
        secs = [int(r["секунд"]) for r in rows]
        tops = [int(r["ТОП"]) for r in rows]
        print(
            f"  {name}: прогонов {len(rows)} · секунд медиана {median(secs):.0f}, "
            f"максимум {max(secs)} (потолок {DEMO_LIMIT_S}) · ТОП медиана {median(tops):.0f}, "
            f"минимум {min(tops)}"
        )
        for r in rows:
            print(
                f"    {r['запрос'][:44]:<44} #{r['прогон']}  {r['секунд']:>5} с  "
                f"ТОП {r['ТОП']:>2}  денежных {r['денежных']:>2}  "
                f"держатся {r['устойчивых']}/{r['ТОП']} при {r['прошлых прогонов']} прошлых"
            )
    top_up = bool(base and full) and median(int(r["ТОП"]) for r in full) > median(
        int(r["ТОП"]) for r in base
    )
    time_ok = bool(full) and median(int(r["секунд"]) for r in full) < DEMO_LIMIT_S

    print("\n== правило зачёта (todo §76)")
    for label, ok in (
        ("покрытие на отложенных не упало", hold_ok),
        ("покрытие на подборе выросло", tune_up),
        ("медиана ТОП на ТЗ-запросах выросла", top_up),
        (f"медиана секунд < {DEMO_LIMIT_S}", time_ok),
    ):
        print(f"  {'✔' if ok else '✘'} {label}")
    kept = all((hold_ok, tune_up, top_up, time_ok))
    print("  итог: " + ("подрынки остаются" if kept else "откат подрынков или разбор глазами"))


if __name__ == "__main__":
    main()
