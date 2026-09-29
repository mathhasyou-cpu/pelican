"""Как идут два долгих прогона: векторы корпуса и добор срезов OpenAlex.

    python scripts/science_progress.py

Оба идут часами и наружу почти немы: эмбеддер пишет шард раз в три минуты, а
`backfill` отдаёт кусок только когда закрыто целое окно по всем срезам сразу
(полчаса на свежих окнах). Здесь — одно место, где видно, живы ли они.

⚠️ `collected_at` в БД **в UTC**, а часы машины — местные (`docs/data-model.md`).
Фильтр по локальному времени тихо отдаёт пустую таблицу и читается как «ничего
не собирается», хотя строки идут. Поэтому здесь печатаются оба времени.

Стоит один запрос к OpenAlex ($0.0001): остаток суточного бюджета живёт только в
заголовках ответа (`x-ratelimit-remaining`), из БД его не видно.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from pelican.config import DATA_DIR, settings  # noqa: E402
from pelican.store import Store  # noqa: E402

STATE = DATA_DIR / "emb-science" / "state.json"

#: Срезы, которые набираются от свежего к старому и глубины ещё не имеют.
YOUNG_SLICES = ("Computer Science", "Engineering")


def vectors() -> None:
    print("== векторы научного корпуса ==")
    if not STATE.exists():
        print("  не начинали")
        return
    state = json.loads(STATE.read_text(encoding="utf-8"))
    shards = sorted((STATE.parent).glob("shard-*.npz"))
    fresh = max((f.stat().st_mtime for f in shards), default=0)
    ago = datetime.now().timestamp() - fresh
    with Store(settings.storage_target) as store:
        total = store.query("SELECT count(*) FROM works WHERE source IN ('arxiv', 'openalex')")[
            0
        ][0]
    done = state.get("done", 0)
    print(f"  {done} из {total} ({done / total:.1%}), шардов {state.get('shards')}")
    print(f"  дошли вглубь до {state.get('observed_at', '')[:10]}")
    print(f"  последний шард записан {ago / 60:.1f} мин назад")


def backfill() -> None:
    print("== добор срезов OpenAlex ==")
    with Store(settings.storage_target) as store:
        rows = store.query(
            """
            SELECT field,
                   count(*), min(observed_at)::date, max(collected_at)
            FROM works
            WHERE source = 'openalex'
              AND field IN (?, ?)
            GROUP BY 1 ORDER BY 2 DESC
            """,
            list(YOUNG_SLICES),
        )
    now = datetime.now(UTC).replace(tzinfo=None)
    for field, count, deep, last in rows:
        ago = (now - last).total_seconds() / 60
        print(f"  {field:<18} {count:>8} строк, вглубь до {deep}, запись {ago:.0f} мин назад")
    if not rows:
        print("  ни одной строки: первое окно ещё не закрыто (это полчаса)")
    print(f"  (время UTC {now:%H:%M}, местное {datetime.now():%H:%M})")


def budget() -> None:
    resp = httpx.get(
        "https://api.openalex.org/works",
        params={
            "filter": "primary_topic.field.id:fields/17,"
            "from_publication_date:2026-09-01,to_publication_date:2026-09-01",
            "per-page": 1,
            "api_key": settings.openalex_api_key,
        },
        headers={"User-Agent": settings.user_agent},
        timeout=60,
    )
    left = int(resp.headers.get("x-ratelimit-remaining", 0))
    limit = int(resp.headers.get("x-ratelimit-limit", 0))
    reset = int(resp.headers.get("x-ratelimit-reset", 0))
    when = datetime.now() + timedelta(seconds=reset)
    print("== суточный бюджет ключа ==")
    print(f"  осталось {left} из {limit} запросов (~{left * 200} работ)")
    print(f"  сброс через {reset // 3600} ч {reset % 3600 // 60} мин, в {when:%H:%M}")


if __name__ == "__main__":
    vectors()
    print()
    backfill()
    print()
    budget()
