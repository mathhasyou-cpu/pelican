"""Пересобрать отчёты истории текущим кодом: полный `ask` по тому же запросу, то же имя.

Отчёт хранит итог той версии конвейера, что его собрала. После правки раскрытия запроса,
денежного потока или оценки старые отчёты показывают то, чего нынешний код уже не выдаёт
(в «art brut и prison art» — self-distillation и LiDAR-решётки). Перезапись под тем же
именем сохраняет ссылки `?report=<имя>` и место в истории (`reports.started` прежний);
время фактического прогона — в самом результате.

Идёт от старых к новым: пометка «держится N из K» (`weak.history`) читает прогоны
того же запроса, начатые раньше, и они к этому моменту уже пересобраны.
⚠️ Полный `ask` — 15–20 минут на отчёт.

    python scripts/rerun.py 20260924-221530
    python scripts/rerun.py --all
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
# ⚠️ Под перенаправленным выводом консоль Windows даёт cp1251, и один «⚠️» валит прогон.
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

from pelican import jobs, locks  # noqa: E402
from pelican.config import settings  # noqa: E402
from pelican.runner import MODEL_LOCK  # noqa: E402
from pelican.store import Store  # noqa: E402
from pelican.weak import history, page  # noqa: E402
from pelican.weak.ask import run as run_ask  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description="пересборка отчётов истории текущим кодом")
    ap.add_argument("names", nargs="*", help="имена отчётов (20260924-221530)")
    ap.add_argument("--all", action="store_true", help="все отчёты истории")
    args = ap.parse_args()

    with jobs.connect(settings.database_url) as conn:
        rows = sorted(jobs.reports(conn, limit=500), key=lambda r: r["started"])
        names = [r["name"] for r in rows] if args.all else args.names
    for name in names:
        with jobs.connect(settings.database_url) as conn:
            row = jobs.report(conn, name)
        if row is None:
            print(f"{name}: отчёта нет")
            continue
        print(f"\n== {name} «{row['query']}»")
        # Модель одна на стенд: запрос из очереди и пересборка не должны идти разом.
        with locks.hold(MODEL_LOCK), Store(settings.storage_target) as store:
            result = run_ask(store, row["query"], on_progress=lambda m: print(f"  · {m}"))
        with jobs.connect(settings.database_url) as conn:
            past = [
                r["result"]
                for r in jobs.reports(conn, limit=500)
                if r["name"] != name and r["started"] < row["started"]
            ]
            history.annotate(result, history.prior_runs(past, row["query"], result.started))
            html, payload = page.render(result), page.to_json(result)
            jobs.save_report(conn, name, row["query"], row["started"], payload, html)
        labels = " · ".join(s.label for s in result.signals) or "пусто"
        print(f"  ТОП {len(result.signals)} за {result.seconds:.0f} с: {labels}")


if __name__ == "__main__":
    main()
