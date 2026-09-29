"""Пересчитать подписи ключевых предикторов у сохранённых отчётов — без прогона `ask`.

    python scripts/rescore.py --all            # сухо: сверка уверенности, ничего не пишет
    python scripts/rescore.py --all --save     # записать отчёт и страницу обратно

Подписи признаков (`model.QUERY_LABELS`, `model.phrase`) живут в коде, а в отчёте лежат
готовыми строками — у истории стенда они остаются прежними, пока запрос не прогнан заново
(15–20 минут). Здесь тот же `ask.score` прогоняется по восстановленному пулу скоринга, и
меняются только строки предикторов.

Пул скоринга — все `emerging` запроса ДО проверки тематичности: ТОП, «за пределами ТОП»
и снятые `_on_topic` как другая отрасль (у них первая проверка — «другая отрасль»).
⚠️ Проверка восстановления: уверенность каждого сигнала обязана совпасть с сохранённой.
Не совпала — пул восстановлен не тот, и отчёт не пишется.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from pelican import jobs  # noqa: E402
from pelican.config import settings  # noqa: E402
from pelican.weak import page  # noqa: E402
from pelican.weak.ask import score  # noqa: E402

#: Допуск сверки уверенности: JSON хранит float целиком, расхождение — только округление.
TOLERANCE = 1e-6


def rescore(result: dict) -> tuple[dict, float] | None:
    """(отчёт с новыми предикторами, наибольшее расхождение уверенности) или `None`,
    когда скоринга в отчёте не было (нет предикторов — модель не считалась)."""
    r = page.from_json(result)
    off_topic = [s for s in r.excluded if s.checks and s.checks[0].detail == "другая отрасль"]
    pool = [*r.signals, *r.thin, *off_topic]
    if not pool or not any(s.predictors for s in r.signals):
        return None
    before = {id(s): s.confidence for s in pool}
    if not score(pool):
        return None
    drift = max(abs(s.confidence - before[id(s)]) for s in pool)
    out = json.loads(json.dumps(result))
    for key, sigs in (("signals", r.signals), ("thin", r.thin)):
        for d, s in zip(out.get(key) or [], sigs, strict=True):
            d["predictors"] = s.predictors
    return out, drift


def main() -> None:
    ap = argparse.ArgumentParser(description="пересчитать подписи предикторов отчётов истории")
    ap.add_argument("names", nargs="*", help="имена отчётов (reports.name)")
    ap.add_argument("--all", action="store_true", help="все отчёты истории")
    ap.add_argument("--save", action="store_true", help="записать отчёт и страницу обратно")
    args = ap.parse_args()
    with jobs.connect(settings.database_url) as conn:
        names = [r["name"] for r in jobs.reports(conn, limit=500)] if args.all else args.names
        for name in names:
            row = jobs.report(conn, name)
            if row is None:
                print(f"{name}: отчёта нет")
                continue
            got = rescore(row["result"])
            if got is None:
                print(f"{name} «{row['query']}»: скоринга нет — пропуск")
                continue
            after, drift = got
            ok = drift <= TOLERANCE
            first = (after.get("signals") or [{}])[0].get("predictors", [])
            print(
                f"{name} «{row['query']}»: расхождение уверенности {drift:.2e} "
                f"{'ok' if ok else 'ПУЛ НЕ ТОТ — не пишу'}; №1: {'; '.join(first)}"
            )
            if args.save and ok:
                html = page.render(page.from_json(after))
                jobs.save_report(
                    conn, name, row["query"], row["started"], json.dumps(after, ensure_ascii=False), html
                )


if __name__ == "__main__":
    main()
