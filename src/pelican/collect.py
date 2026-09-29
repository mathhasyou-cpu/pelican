"""Сбор научного корпуса: суточное окно и загрузка истории.

    python -m pelican.collect                              # суточное окно, оба источника
    python -m pelican.collect -s openalex --days 365       # история за год
    python -m pelican.collect -s arxiv --days 520 --skip-days 260

Сбор идёт десятки минут, поэтому улов пишется кусками по ходу, а не копится до конца:
источники читаются конкурентно (producer), запись в Postgres идёт одна и по очереди
(consumer, `asyncio.Queue`). Упавший источник не останавливает остальных, а записанное
до отказа остаётся в базе. Запись уходит в поток: иначе вставшая база была бы неотличима
от вставшей сети.

Исход прогона — один из трёх, и он виден по коду возврата: 0 — всё собрано, 23 — собрано
не целиком (квота, обрыв посреди окна; данные сохранены, следующий прогон доберёт),
1 — источник отказал.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass

from pelican.config import settings
from pelican.sources import REGISTRY, build, build_backfill
from pelican.sources.base import Chunk, PartialFetch, Source
from pelican.store import Store

#: Сколько кусков может ждать записи. Предохранитель от разрастания памяти: писатель
#: быстрее сети на порядки, и очередь в норме пуста.
QUEUE_SIZE = 64
EXIT_PARTIAL = 23


@dataclass(slots=True)
class CollectResult:
    source: str
    fetched: int = 0
    inserted: int = 0
    chunks: int = 0
    error: str | None = None
    #: Собрано не целиком, но записанное сохранено — это не отказ.
    note: str | None = None


def _stream(source: Source) -> AsyncIterator[Chunk]:
    streaming = getattr(source, "fetch_stream", None)
    if streaming is not None:
        return streaming()

    async def single() -> AsyncIterator[Chunk]:
        yield Chunk(list(await source.fetch()))

    return single()


async def _produce(source: Source, queue: asyncio.Queue) -> None:
    """Читает источник в очередь; последним всегда кладётся маркер конца (`None`)."""
    error: str | None = None
    note: str | None = None
    try:
        async for chunk in _stream(source):
            await queue.put((source, chunk, None, None))
    except PartialFetch as exc:
        note = str(exc)
        if exc.signals:
            await queue.put((source, Chunk(list(exc.signals)), None, None))
    except Exception as exc:  # noqa: BLE001 — отказ одного источника не валит остальных
        error = f"{type(exc).__name__}: {exc}"
    await queue.put((source, None, error, note))


async def collect(store: Store, sources: list[Source]) -> list[CollectResult]:
    """Сеть — конкурентно, запись — последовательно и по ходу дела."""
    queue: asyncio.Queue = asyncio.Queue(QUEUE_SIZE)
    results = {s.name: CollectResult(source=s.name) for s in sources}
    tasks = [asyncio.create_task(_produce(s, queue)) for s in sources]
    try:
        pending = len(sources)
        while pending:
            source, chunk, error, note = await queue.get()
            result = results[source.name]
            if chunk is None:
                result.error, result.note = error, note
                pending -= 1
                continue
            result.fetched += len(chunk.signals)
            result.chunks += 1
            result.inserted += await asyncio.to_thread(store.insert_signals, chunk.signals)
            print(
                f"  {source.name}: кусок {result.chunks}, получено {result.fetched:,}, "
                f"новых {result.inserted:,}",
                flush=True,
            )
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    return [results[s.name] for s in sources]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Сбор научного корпуса в Postgres")
    ap.add_argument("-s", "--source", action="append", choices=sorted(REGISTRY),
                    help="источник (по умолчанию оба)")
    ap.add_argument("--days", type=int, default=0, help="загрузка истории: окно в сутках")
    ap.add_argument("--skip-days", type=int, default=0,
                    help="сдвинуть свежий край окна истории (продолжение прерванной загрузки)")
    args = ap.parse_args(argv)
    names = args.source or sorted(REGISTRY)
    sources = [
        build_backfill(n, args.days, args.skip_days) if args.days else build(n) for n in names
    ]
    started = time.monotonic()
    with Store(settings.database_url) as store:
        store.init_schema(indexes=False)
        results = asyncio.run(collect(store, sources))
    code = 0
    for r in results:
        state = "отказ" if r.error else ("частично" if r.note else "ок")
        print(f"{r.source}: {state}; получено {r.fetched:,}, новых {r.inserted:,}"
              + (f" — {r.error or r.note}" if r.error or r.note else ""))
        if r.error:
            code = 1
        elif r.note and code == 0:
            code = EXIT_PARTIAL
    print(f"за {time.monotonic() - started:,.0f} с")
    return code


if __name__ == "__main__":
    sys.exit(main())
