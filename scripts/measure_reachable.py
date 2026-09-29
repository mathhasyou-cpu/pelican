"""Потолок покрытия: сколько строк методолога вообще видно в наших источниках.

## Зачем

Покрытие «22 из 100» (`scripts/judge_match.py`) читается как «нашли пятую часть», хотя
часть строк нашими источниками недостижима в принципе, а часть достижима, но мы про неё
НЕ СПРАШИВАЕМ. Разбор робототехники показал ровно это: из тринадцати ненайденных строк
восемь лежат в источниках дословно и просто не попадают ни в один наш запрос
([todo](../docs/todo.md) §76).

Пока знаменателя нет, непонятно, что чинить: набор источников или набор запросов.

## Как меряется

По каждой строке датасета идёт **идеальный запрос** — её собственное название
по-английски. Это не то, что делает `trends ask` (там запрос пользовательский и широкий),
и в этом весь смысл: так меряется потолок ИСТОЧНИКОВ, а не нашей стратегии запросов.

Каналов ДВА, и считать их надо порознь — они про разные беды:

- **новости** (Google News по-английски) — идеальный запрос названием строки;
- **корпус** (`weak.ground`, dense retrieval по шардам) — тот же запрос сырым русским
  названием, как в `trends ask`.

⚠️ **Одного новостного канала мало**: строки вроде «федеративное обучение для межбанковского
AML» или «дифференциально-приватные синтетические данные» живут в статьях, а не в новостях
о раундах, и по одним новостям они читались бы как недостижимые. Первый прогон замера ровно
так и соврал — 69 из 100 по новостям, — и это был дефект инструмента, а не системы.

Строка достижима, если в каком-то из каналов есть документ, который:

- **называет её компанию** (буквенная проверка, как в `measure_ask.py`: имена с
  различительностью ≤ 2 строк, без общих слов вроде «AI» и «Robotics»), либо
- **признан судьёй** описывающим то же направление (LLM-as-judge с эхо-проверкой номера,
  как в `judge_match.py`).

⚠️ **Достижимость — это ВЕРХНЯЯ оценка того, что мы могли бы найти**, и одновременно
нижняя оценка потолка: источников у нас больше, чем один новостной поиск.

Приём называется и известен в поиске: это **неполнота разметки и смещение пула** (TREC
pooling — Buckley и Voorhees; Zobel 1998). Наш случай — худший из описанных там: список
методолога собран БЕЗ нашего участия, то есть мы ровно та система, которая в пул не
вносила вклад, и всё ненайденное засчитывается против нас, даже когда оно недостижимо.
Знаменатель «сколько вообще достижимо» — это и есть поправка на неполноту пула.

⚠️ **Перевод здесь нужен**, в отличие от привязки к корпусу: новостной поиск идёт по
ключевым словам, а не по вектору ([weak-measurements](../docs/weak-measurements.md)).

    python scripts/measure_reachable.py               # все 100 строк, с кэшем
    python scripts/measure_reachable.py --limit 12    # отладка
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import httpx  # noqa: E402

from pelican.config import settings  # noqa: E402
from pelican.store import Store  # noqa: E402
from pelican.llm import LLMClient  # noqa: E402
from pelican.weak.ground import ground  # noqa: E402
from pelican.weak.live import news_many  # noqa: E402
from pelican.weak.names import discriminating, document_frequency, tokens  # noqa: E402
from pelican.weak.stats import span  # noqa: E402
from pelican.weak.translate import translate  # noqa: E402

DATASET = REPO / "scripts" / "weak_signals_100.tsv"
OUT = REPO / "reports" / "reachable.tsv"
#: Сводка для отчёта сдачи: числа берутся отсюда, а не пересказом.
SUMMARY = REPO / "reports" / "reachable.json"

#: Сколько заголовков смотреть на строку. ⚠️ Догадка: больше — судья начинает
#: засчитывать соседнее, меньше — потолок занижается.
HEADLINES = 6
#: Сколько работ корпуса показывать судье на строку. Меньше, чем заголовков: работа длиннее
#: и судье её труднее пролистывать. ⚠️ Догадка той же природы, что `HEADLINES`.
PAPERS = 4
PROMPT = """You are given ONE technology direction written by an analyst, and a numbered
list of documents: news headlines, or titles and abstracts of research papers.

Answer with the number of the document that is about THE SAME technology direction, or 0
if none of them is.

The same direction means the same technical thing: the same mechanism, artifact, protocol
or capability. A shared field ("both are about AI security", "both are about robots") is
NOT the same direction - answer 0 for those."""


def _schema() -> dict:
    return {
        "type": "object",
        "properties": {"row": {"type": "integer"}, "why": {"type": "string"}},
        "required": ["row", "why"],
        "additionalProperties": False,
    }


def _client() -> LLMClient:
    return LLMClient(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        timeout_s=settings.llm_timeout_s,
    )


def rows() -> list[dict]:
    return list(csv.DictReader(DATASET.read_text(encoding="utf-8").splitlines(), delimiter="\t"))


async def _judge_one(http: httpx.AsyncClient, tech: str, heads: list[str]) -> tuple[int, str]:
    listing = "\n".join(f"{i + 1}. {h}" for i, h in enumerate(heads))
    got = await _client().json_completion(
        http,
        system=PROMPT,
        user=f"ANALYST'S DIRECTION: {tech}\n\nDOCUMENTS:\n{listing}",
        schema=_schema(),
        name="reachable",
        max_tokens=250,
    )
    n = got.get("row")
    n = int(n) if isinstance(n, int) else 0
    if not (0 <= n <= len(heads)):
        # ⚠️ Номер вне диапазона — модель сочиняет; достижимостью это не считается.
        n = 0
    return n, str(got.get("why") or "")[:160]


async def _judge_many(items: list[tuple[str, list[str]]]) -> list[tuple[int, str]]:
    async with httpx.AsyncClient() as http:
        out = []
        for i, (tech, heads) in enumerate(items, 1):
            out.append(await _judge_one(http, tech, heads) if heads else (0, "нет заголовков"))
            if i % 10 == 0:
                print(f"  · судья прошёл {i} из {len(items)}", flush=True)
        return out


def main() -> None:
    ap = argparse.ArgumentParser(description="потолок покрытия: видны ли строки в источниках")
    ap.add_argument("--limit", type=int, default=0, help="первые N строк")
    args = ap.parse_args()

    data = rows()
    if args.limit:
        data = data[: args.limit]
    df = document_frequency(r.get("companies", "") for r in rows())

    print(f"перевод {len(data)} названий для новостного поиска...", flush=True)
    english = [t.en for t in translate([r["tech"] for r in data])]
    print("идеальный запрос по каждой строке...", flush=True)
    found = news_many(english, "en")
    heads = [[d.title for d in docs[:HEADLINES]] for docs in found]
    empty = sum(1 for h in heads if not h)
    print(f"  заголовков {sum(len(h) for h in heads)}, строк без единого: {empty}", flush=True)

    # Буквенная проверка идёт первой: она не ошибается, и судье остаётся меньше работы.
    by_company: list[str] = []
    for r, h in zip(data, heads, strict=True):
        own = discriminating(r.get("companies", ""), df)
        hit = ""
        for title in h:
            common = own & tokens(title)
            if common:
                hit = f"{title[:70]} ({', '.join(sorted(common))})"
                break
        by_company.append(hit)

    got = asyncio.run(_judge_many([(r["tech"], h) for r, h in zip(data, heads, strict=True)]))

    # ⚠️ Второй канал обязателен: половина строк датасета живёт в статьях, а не в новостях
    # о раундах, и по одним новостям они читались бы как недостижимые.
    print(f"привязка {len(data)} названий к корпусу (сырым русским названием)...", flush=True)
    with Store(settings.storage_target) as store:
        hits = ground(store, [r["tech"] for r in data], k=PAPERS)
        ids = sorted({h.signal_id for found in hits for h in found})
        listed = ",".join(str(i) for i in ids)
        texts: dict[int, str] = {}
        if listed:
            for row in store.conn.execute(
                f"SELECT id, term_raw FROM works WHERE id IN ({listed})"
            ).fetchall():
                texts[int(row[0])] = str(row[1])
    papers = [[texts.get(h.signal_id, "")[:200] for h in found if h.signal_id in texts]
              for found in hits]
    print(f"  работ найдено: {sum(len(p) for p in papers)}", flush=True)
    got_corpus = asyncio.run(
        _judge_many([(r["tech"], p) for r, p in zip(data, papers, strict=True)])
    )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    reach = 0
    with OUT.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(
            [
                "n",
                "домен",
                "строка",
                "достижима",
                "канал",
                "по компании",
                "заголовок судьи",
                "работа судьи",
            ]
        )
        news_only = corpus_only = both = 0
        for r, h, pp, company, (n, _why), (m, _why2) in zip(
            data, heads, papers, by_company, got, got_corpus, strict=True
        ):
            judged = h[n - 1] if n else ""
            paper = pp[m - 1] if m else ""
            in_news = bool(company or judged)
            in_corpus = bool(paper)
            ok = in_news or in_corpus
            reach += ok
            news_only += in_news and not in_corpus
            corpus_only += in_corpus and not in_news
            both += in_news and in_corpus
            channel = "новости+корпус" if (in_news and in_corpus) else (
                "новости" if in_news else ("корпус" if in_corpus else "—")
            )
            w.writerow(
                [
                    r["n"],
                    r["domain"],
                    r["tech"][:90],
                    "да" if ok else "нет",
                    channel,
                    company,
                    judged[:90],
                    paper[:90],
                ]
            )

    total = len(data)
    print("")
    print(f"=== ДОСТИЖИМОСТЬ: {reach} из {total} ({reach / total:.0%}) {span(reach, total)} ===")
    print(f"  только в новостях: {news_only} · только в корпусе: {corpus_only} · в обоих: {both}")
    print(f"  по названной компании: {sum(1 for c in by_company if c)}")
    print(f"  по суждению о заголовке: {sum(1 for n, _w in got if n)}")
    print(f"  по суждению о работе: {sum(1 for m, _w in got_corpus if m)}")
    print(f"  строк без единого заголовка: {empty}")
    by_domain: dict[str, list[int]] = {}
    for r, h, pp, company, (n, _why), (m, _why2) in zip(
        data, heads, papers, by_company, got, got_corpus, strict=True
    ):
        by_domain.setdefault(r["domain"], []).append(bool(company or (n and h) or (m and pp)))
    for domain, flags in sorted(by_domain.items()):
        print(f"  {domain:22s} {sum(flags):>3} из {len(flags)}")
    print("")
    print("⚠️ покрытие выдачи читается ПРОТИВ этого числа: что недостижимо — цена источников,")
    print("   что достижимо и не найдено — цена набора запросов (docs/todo.md §76)")
    SUMMARY.write_text(
        json.dumps(
            {
                "строк": total,
                "достижимо": reach,
                "по компании": sum(1 for c in by_company if c),
                "по суждению о заголовке": sum(1 for n, _w in got if n),
                "по суждению о работе": sum(1 for m, _w in got_corpus if m),
                "только новости": news_only,
                "только корпус": corpus_only,
                "оба канала": both,
                "без заголовков": empty,
                "по доменам": {d: sum(f) for d, f in sorted(by_domain.items())},
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"выписано: {OUT} · сводка: {SUMMARY}")


if __name__ == "__main__":
    main()
