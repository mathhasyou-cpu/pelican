"""Сверка научного корпуса с источником: полон ли он на самом деле.

⚠️ **min/max диапазона полноту не доказывают.** Корпус `openalex` полгода
выглядел собранным («862 782 работы, 2021-10-09 … 2026-09-09») и при этом не
имел девятнадцати месяцев подряд и был срезан на потолке ёмкости в каждом
январе. Видно это только помесячной сверкой с `meta.count` ТОГО ЖЕ запроса —
той самой, которой на arXiv поймали потерю 12% старых годов
(`docs/sources-science.md`).

Правило простое: после каждой докачки — этот скрипт, и только он считается
доказательством, что история собрана.

Запуск (без аргументов — все срезы из настроек плюс arxiv):

    python scripts/verify_science_corpus.py
    python scripts/verify_science_corpus.py --source openalex --years 5
    python scripts/verify_science_corpus.py --source arxiv

⚠️ В БД ничего не пишется: открывается `settings.storage_target`, то есть
сервер `trends serve`, если он настроен.
"""

from __future__ import annotations

import argparse
import calendar
import sys
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

# ⚠️ Консоль Windows отдаёт cp1251, и первое же «—» роняет весь отчёт.
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

import httpx  # noqa: E402

from pelican.config import settings  # noqa: E402
from pelican.store import Store  # noqa: E402
from pelican.sources.arxiv import API_URL as ARXIV_URL  # noqa: E402
from pelican.sources.arxiv import DELAY_S as ARXIV_DELAY  # noqa: E402
from pelican.sources.arxiv import ArXiv, _window, total_results  # noqa: E402
from pelican.sources.openalex import API_URL as OPENALEX_URL  # noqa: E402
from pelican.sources.openalex import DELAY_S as OPENALEX_DELAY  # noqa: E402

#: Расхождение, ниже которого месяц считается собранным. Собранные месяцы у нас
#: расходятся с API на 0.0-0.2% (работа доезжает в индекс и после сбора).
HOLE_RATIO = 0.01
#: ⚠️ Свежий край недосчитан ВСЕГДА: `publication_date` ставит издатель, а в
#: OpenAlex работа попадает позже, и месяц наполняется неделями. Без этой скидки
#: скрипт рапортовал бы ложную дыру на каждом запуске.
TAIL_MONTHS = 2
#: Куски докачки: длиннее — дольше живёт один процесс, а падение уносит больше.
CHUNK_DAYS = 300
#: Сколько раз переспрашивать arXiv, поймав отказ по частоте.
ARXIV_TRIES = 3
#: После скольких годов подряд без ответа перебор прекращается.
REFUSALS_BEFORE_STOP = 2


def _months(since: date, until: date) -> list[tuple[date, date]]:
    out, cur = [], since.replace(day=1)
    while cur <= until:
        last = cur.replace(day=calendar.monthrange(cur.year, cur.month)[1])
        out.append((max(cur, since), min(last, until)))
        cur = date(cur.year + (cur.month == 12), cur.month % 12 + 1, 1)
    return out


class BudgetSpent(RuntimeError):
    """Дневной бюджет OpenAlex кончился: сверить сегодня нечем."""


def openalex_count(http: httpx.Client, scope: str, lo: date, hi: date) -> int:
    params: dict[str, object] = {
        "filter": f"from_publication_date:{lo},to_publication_date:{hi},{scope}",
        "per_page": 1,
    }
    if settings.openalex_api_key:
        params["api_key"] = settings.openalex_api_key
    time.sleep(OPENALEX_DELAY)
    r = http.get(OPENALEX_URL, params=params)
    # ⚠️ Отказ по бюджету приходит ОБЫЧНЫМ JSON без `meta`, и раньше сверка падала на нём
    # `KeyError: 'meta'` — то есть «доказательство полноты» выглядело поломкой скрипта, а
    # не сообщением источника. Остаток лежит в заголовке КАЖДОГО ответа, и читать надо
    # его (docs/lessons-and-gotchas.md: у чужого API читают заголовки лимита).
    if r.status_code == 429:
        left = r.headers.get("x-ratelimit-remaining-usd", "?")
        limit = r.headers.get("x-ratelimit-limit-usd", "?")
        after = int(r.headers.get("retry-after", 0) or 0)
        raise BudgetSpent(
            f"бюджет OpenAlex исчерпан: осталось ${left} из ${limit} в сутки, "
            f"сброс через {after // 3600} ч {after % 3600 // 60} мин (полночь UTC). "
            "Сверять полноту сегодня нечем — прогнать скрипт после сброса."
        )
    payload = r.json()
    if "meta" not in payload:
        raise BudgetSpent(f"OpenAlex ответил без meta ({r.status_code}): {str(payload)[:200]}")
    return payload["meta"]["count"]


def field_name(http: httpx.Client, scope: str) -> str:
    """Как срез назван в собранных строках.

    В `payload_json` лежит человеческое имя поля («Materials Science»), а не
    `fields/25`, поэтому имя спрашивается у самого источника — иначе таблица
    соответствий жила бы в скрипте и расходилась бы с таксономией.
    """
    works = http.get(OPENALEX_URL, params={"filter": scope, "per_page": 1}).json()["results"]
    return ((works[0].get("primary_topic") or {}).get("field") or {})["display_name"]


def db_edge(store: Store, field: str) -> date | None:
    """Первый день, который у корпуса вообще есть.

    ⚠️ Без него самый старый месяц рапортовался бы дырой КАЖДЫЙ день: окно
    «пять лет назад» едет вместе с сегодняшним числом, а корпус стоит на месте,
    и разница в одни сутки читалась бы как недобор в 671 работу.
    """
    row = store.conn.execute(
        """
        SELECT min(observed_at)::DATE FROM works
        WHERE source = 'openalex' AND field = ?
        """,
        [field],
    ).fetchone()
    return row[0] if row else None


def db_months(store: Store, field: str) -> dict[str, int]:
    rows = store.conn.execute(
        """
        SELECT to_char(date_trunc('month', observed_at), 'YYYY-MM') AS m, count(*)
        FROM works
        WHERE source = 'openalex' AND field = ?
        GROUP BY 1
        """,
        [field],
    ).fetchall()
    return dict(rows)


def check_openalex(
    store: Store, http: httpx.Client, scope: str, since: date, until: date
) -> list[tuple[date, date]]:
    field = field_name(http, scope)
    have = db_months(store, field)
    edge = db_edge(store, field)
    months = _months(since, until)
    tail = months[-TAIL_MONTHS:]
    print(f"\n=== openalex {scope} ({field}) ===")
    if edge and edge > since:
        print(f"  корпус начинается {edge}, запрошено с {since} — старый край мельче окна")

    holes: list[tuple[date, date]] = []
    total_api = total_db = 0
    for lo, hi in months:
        api = openalex_count(http, scope, lo, hi)
        db = have.get(lo.strftime("%Y-%m"), 0)
        total_api, total_db = total_api + api, total_db + db
        miss = api - db
        if (lo, hi) in tail:
            mark = "свежий край, индекс догоняет"
        elif edge is not None and lo < edge:
            mark = "старый край корпуса"
        elif miss > api * HOLE_RATIO:
            mark = "ДЫРА"
            holes.append((lo, hi))
        else:
            mark = ""
        if mark:
            print(f"  {lo:%Y-%m}  api={api:7d}  бд={db:7d}  не хватает {miss:7d}  {mark}")
    print(f"  итого: api={total_api}  бд={total_db}  не хватает {total_api - total_db}")
    print(f"  дырявых месяцев: {len(holes)}" if holes else "  дыр нет")
    return holes


def refill_commands(scope: str, holes: list[tuple[date, date]], today: date) -> list[str]:
    """Команды докачки под найденные дыры.

    Смещения `--skip-days`/`-d` считаются от СЕГОДНЯ (адаптер в БД не ходит),
    и руками их пересчитывать нельзя: назавтра всё съезжает на сутки.
    """
    ranges: list[list[date]] = []
    for lo, hi in holes:
        if ranges and (lo - ranges[-1][1]).days <= 1:
            ranges[-1][1] = hi
        else:
            ranges.append([lo, hi])

    out = []
    for lo, hi in ranges:
        skip, depth = (today - hi).days, (today - lo).days + 1
        while skip < depth:
            end = min(depth, skip + CHUNK_DAYS)
            out.append(
                f"$env:OPENALEX_FILTERS='{scope}'; "
                f"trends backfill -s openalex --skip-days {skip} -d {end}"
            )
            skip = end
    return out


def check_arxiv(store: Store, http: httpx.Client) -> None:
    """arXiv — по годам против `totalResults` того же запроса.

    Расхождение в одну сторону на СТАРЫХ годах — признак потерянного архива в
    идентификаторе (`cs/0607001`), и стоил он 12% этих годов.
    """
    source = ArXiv()
    rows = store.conn.execute(
        """
        SELECT extract(year FROM observed_at)::int AS y, count(*) FROM works
        WHERE source = 'arxiv' GROUP BY 1 ORDER BY 1
        """
    ).fetchall()
    edge = store.conn.execute(
        "SELECT min(observed_at)::DATE FROM works WHERE source = 'arxiv'"
    ).fetchone()[0]
    print("\n=== arxiv (по годам) ===")
    if edge:
        print(f"  корпус начинается {edge} — год этого края считается неполным по замыслу")
    refused = 0
    for year, db in rows:
        if refused >= REFUSALS_BEFORE_STOP:
            print(f"  {year}  бд={db:7d}  не сверен — arXiv отказывает по частоте")
            continue
        # ⚠️ Пауза arXiv не подбирается и стоит ДО запроса: серия без неё
        # отвечает 429 и отпускает не сразу (docs/sources-science.md).
        time.sleep(ARXIV_DELAY)
        api = arxiv_total(
            http,
            f"{source.query_head} AND "
            + _window(datetime(year, 1, 1), datetime(year, 12, 31, 23, 59)),
        )
        if api is None:
            # ⚠️ Отказ у arXiv — ПО IP, а не по запросу: замер показал тот же
            # `Rate exceeded` на `all:electron` с одним результатом. Значит
            # продолжать перебор лет бессмысленно и вредно — каждый стук
            # продлевает окно лимитера. Останавливаемся и говорим об этом.
            refused += 1
            print(f"  {year}  бд={db:7d}  arXiv не ответил — год не сверен")
            continue
        refused = 0
        miss = api - db
        if edge is not None and year == edge.year:
            mark = "старый край корпуса"
        else:
            mark = "ДЫРА" if miss > api * HOLE_RATIO else ""
        print(f"  {year}  api={api:7d}  бд={db:7d}  разница {miss:6d}  {mark}")


def arxiv_total(http: httpx.Client, query: str) -> int | None:
    """Сколько работ у arXiv по запросу. `None` — не ответил, и это НЕ ноль.

    ⚠️ Отказ по частоте приезжает `429` с телом «Rate exceeded» в text/html, а
    не ошибкой в XML, и разбор его как XML роняет весь отчёт на ровном месте —
    вместе с уже сверенными срезами. Отпускает arXiv не сразу: замер показал
    три отказа подряд с интервалом 15-20 с (`docs/sources-science.md`), поэтому
    пауза растёт, а несверенный год честно помечается несверенным — ложной
    дырой он не становится.
    """
    for attempt in range(ARXIV_TRIES):
        time.sleep(ARXIV_DELAY * (1 + attempt * 8))
        try:
            resp = http.get(ARXIV_URL, params={"search_query": query, "start": 0, "max_results": 1})
        except httpx.HTTPError:
            # ⚠️ Придушенный arXiv не только отвечает 429, но и просто молчит до
            # таймаута — в отчёте это обязано быть тем же «не сверено», а не
            # трейсбеком поверх уже сверенных срезов.
            continue
        if resp.status_code == 200:
            return total_results(resp.text) or 0
    return None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", choices=("all", "arxiv", "openalex"), default="all")
    ap.add_argument("--years", type=int, default=5, help="глубина сверки openalex")
    ap.add_argument("--slice", action="append", help="срез openalex (по умолчанию — из настроек)")
    args = ap.parse_args()

    today = datetime.now(UTC).date()
    since = today - timedelta(days=round(args.years * 365.25))
    store = Store(settings.storage_target, read_only=True)
    commands: list[str] = []
    with store, httpx.Client(timeout=60.0, headers={"User-Agent": "trends-anal"}) as http:
        if args.source in ("all", "openalex"):
            for scope in args.slice or settings.openalex_filters:
                try:
                    commands += refill_commands(
                        scope, check_openalex(store, http, scope, since, today), today
                    )
                except BudgetSpent as exc:
                    # ⚠️ Отказ бюджета не должен уносить с собой сверку arXiv: она
                    # бесплатна, и терять её из-за чужого кошелька незачем.
                    print(f"⚠️ openalex: {exc}")
                    break
        if args.source in ("all", "arxiv"):
            check_arxiv(store, http)

    if commands:
        print("\n=== чем закрыть (по одному процессу за раз, PowerShell) ===")
        for cmd in commands:
            print(f"  {cmd}")
        print("  # после докачки: $env:OPENALEX_FILTERS=$null и этот скрипт заново")


if __name__ == "__main__":
    main()
