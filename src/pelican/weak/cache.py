"""Суточный кэш живого поиска: одинаковые свидетельства для сравнимых прогонов.

## Зачем он нужен именно здесь

`trends ask` ходит в Google News, и выдача поиска меняется между двумя вызовами сама по
себе. Пока она менялась, **сравнить две версии кода было нечем**: два прогона отличались
и правкой, и новостями, а разделить вклады нельзя. Правило проекта «сначала замер, потом
коммит» на таком входе не работает вовсе.

Поэтому ответы источников кладутся на диск на сутки, и замер правки идёт на
зафиксированных свидетельствах. Обратная сторона — нулевой контроль нестабильности
(три прогона одного запроса, `scripts/measure_ask.py --repeat`) кэш обязан ВЫКЛЮЧАТЬ:
он меряет как раз разброс источников.

⚠️ **Кэшируется разобранный документ, а не сырой ответ.** Разбор меняется вместе с кодом
(`weak/live.py`), и старый сырой XML пришлось бы перечитывать; зато при правке разборщика
кэш надо снести — `trends ask --fresh` или удалить каталог.

⚠️ **Ключ включает язык и вид источника.** Один и тот же текст запроса к Google News на
ru и en даёт разные документы, и общий ключ молча смешал бы их.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from pathlib import Path

from pelican.config import DATA_DIR
from pelican.weak import asof
from pelican.weak.live import LiveDoc

DIR = DATA_DIR / "live-cache"
#: Сутки: за меньшее окно новостной поиск заметно не меняется, а замеры правок идут
#: часами. ⚠️ Догадка, замером не подпирается.
TTL_S = 24 * 3600
#: Выключается на время нулевого контроля и флагом `--fresh`.
ENABLED = True

_swept = False


def key_of(kind: str, query: str) -> str:
    # ⚠️ Срез входит в ключ: под ним тот же запрос идёт в другом окне дат, и общий
    # ключ отдал бы бэктесту сегодняшние заметки (`weak.asof`).
    return hashlib.sha1(f"{asof.key(kind)}\n{query}".encode()).hexdigest()


def _path(kind: str, query: str) -> Path:
    return DIR / f"{key_of(kind, query)}.json"


def load(kind: str, query: str) -> list[LiveDoc] | None:
    """Документы из кэша или None. Битый и просроченный файл — как будто его нет."""
    if not ENABLED:
        return None
    path = _path(kind, query)
    try:
        if time.time() - path.stat().st_mtime > TTL_S:
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        return [LiveDoc(**d) for d in payload["docs"]]
    except (OSError, ValueError, KeyError, TypeError):
        return None


def save(kind: str, query: str, docs: list[LiveDoc]) -> None:
    if not ENABLED:
        return
    _sweep()
    body = {"kind": kind, "query": query, "docs": [asdict(d) for d in docs]}
    path = _path(kind, query)
    tmp = path.with_suffix(".tmp")
    try:
        DIR.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        # Кэш — ускорение, а не хранилище: отказ диска не должен ронять запрос.
        pass


def _sweep() -> None:
    """Просроченное убирается один раз за процесс: каталог растёт запросами навсегда."""
    global _swept
    if _swept:
        return
    _swept = True
    limit = time.time() - TTL_S
    try:
        for f in DIR.glob("*.json"):
            if f.stat().st_mtime < limit:
                f.unlink(missing_ok=True)
    except OSError:
        pass


async def through(
    kind: str, query: str, fetch: Callable[[], Awaitable[list[LiveDoc]]]
) -> list[LiveDoc]:
    """Ответ из кэша, иначе живой запрос — и в кэш. Ошибку источника не кэширует."""
    hit = load(kind, query)
    if hit is not None:
        return hit
    docs = await fetch()
    save(kind, query, docs)
    return docs


async def through_json(kind: str, query: str, fetch: Callable[[], Awaitable[object]]) -> object:
    """То же, но для ответа, который не документ (счётчик патентов по окну).

    ⚠️ Отдельная дверь нужна потому, что `load`/`save` собирают `LiveDoc`, а число
    через них прошло бы только притворившись документом. Правила те же: тот же срок,
    тот же ключ со срезом, отказ источника не кэшируется.
    """
    path = _path(f"json:{kind}", query)
    if ENABLED:
        try:
            if time.time() - path.stat().st_mtime <= TTL_S:
                return json.loads(path.read_text(encoding="utf-8"))["value"]
        except (OSError, ValueError, KeyError, TypeError):
            pass
    value = await fetch()
    if ENABLED and value is not None:
        _sweep()
        tmp = path.with_suffix(".tmp")
        try:
            DIR.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps({"value": value}, ensure_ascii=False), encoding="utf-8")
            tmp.replace(path)
        except OSError:
            pass
    return value
