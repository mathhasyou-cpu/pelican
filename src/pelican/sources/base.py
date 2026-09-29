"""Порт источника.

`Signal` — единственный формат, который знает остальной пайплайн. Всё, что
специфично для конкретного API (пагинация, ключи, форма ответа), остаётся
внутри адаптера. Это то, что позволит позже заменить скрейпинг на платный API,
не трогая ни скоринг, ни отчёты.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

# Сколько тела поста или отзыва приклеивать к заголовку в `term_raw`.
#
# ⚠️ Работа называется в теле, а не в заголовке — там, где источник отдаёт и
# то и другое. Замер на одном корпусе из 484 постов reddit: выход `mine` 50.5%
# по одним заголовкам и 74% со склейкой тела. Заголовки там вида «When to
# apply» и «Cove Replacement» — работы в них нет.
#
# Величина — про то, где кончается формулировка и начинается пересказ эмоции:
# работу человек называет в первых предложениях. Целиком тело в `term_raw` не
# кладётся, оно остаётся в payload.
TERM_BODY_CHARS = 240


def term_of(title: str, body: str) -> str:
    """Заголовок плюс первые `TERM_BODY_CHARS` символов тела.

    Живёт здесь, а не в адаптерах, потому что нужен всем источникам, которые
    отдают заголовок и тело врозь (reddit, msqna, khoros, arxiv, openalex), и
    правило у них одно: работа названа в первых предложениях тела, а заголовок
    — подводка (замер на 484 постах reddit: выход `mine` 50.5% по одним
    заголовкам против 74% со склейкой).

    ⚠️ Знак вопроса сохраняется: это жанр строки, по которому `mine` различает
    `stuck`. Точка ставится только чтобы склейка не дала «...?.».
    """
    head = title.strip()
    tail = body[:TERM_BODY_CHARS].strip()
    if not tail:
        return head
    if not head:
        return tail
    return f"{head} {tail}" if head[-1] in ".!?…" else f"{head}. {tail}"


@dataclass(frozen=True, slots=True)
class Signal:
    """Один факт: источник видел термин в момент времени с такой-то метрикой."""

    source: str
    external_id: str
    """Стабильный идентификатор внутри источника — держит идемпотентность."""
    term_raw: str
    observed_at: datetime
    metric: str
    """Что именно измерено: `traffic`, `pageviews`, `points`, `stars_delta`, ..."""
    value: float | None = None
    geo: str | None = None
    url: str | None = None
    payload: dict[str, Any] | None = field(default=None)


@dataclass(slots=True)
class Chunk:
    """Кусок улова, который уже можно записать.

    ⚠️ Существует ради одного: сбор идёт сорок пять минут (фразовые источники
    ходят по минуте на фразу), и падение на сороковой минуте не имеет права
    уносить улов ВСЕХ восемнадцати источников. Кусок отдаётся сразу, как только
    источник закончил очередную единицу работы — бандл, фразу, страницу.

    ⚠️ Пакетности это не нарушает: кусок — десятки строк за минуту, а не строка.
    Правило «DuckDB — OLAP, пиши пакетами» про построчные INSERT.
    """

    signals: list[Signal]
    phrase: str | None = None
    """Для фразовых источников — какую фразу только что прошли.

    По ней `collect` отмечает проход в `phrase_passes`, и следующая очередь
    начинается с самой давней. ⚠️ Отмечает именно `collect`, а не адаптер:
    адаптеры в БД не ходят.
    """


class PartialFetch(Exception):
    """Источник дошёл до внешней границы, но собранное — годное.

    Отличается от обычного отказа тем, что данные есть и их надо сохранить:
    исчерпанная суточная квота Stack Exchange на 95-м сайте из 184 не повод
    выбрасывать 94 предыдущих. Сбор идемпотентен и инкрементален, следующий
    прогон продолжит; молча потерянная работа — нет.
    """

    def __init__(self, message: str, signals: Sequence[Signal]) -> None:
        super().__init__(message)
        self.signals = list(signals)


@runtime_checkable
class Source(Protocol):
    """Адаптер источника.

    `fetch` возвращает готовые Signal. Ошибки сети адаптер обрабатывает сам
    (ретраи), а неустранимые — поднимает: collect решает, падать или продолжать
    с остальными источниками.

    `configured` отделяет «источник сломался» от «источник не настроен»: без
    ключей Reddit молча пропускается в общем прогоне, но даёт внятную ошибку,
    когда его запросили явно через --source.

    Необязательный `fetch_stream` отдаёт улов кусками по ходу дела; кто его не
    объявил, тот собирается по-старому — `collect` сам завернёт его `fetch` в
    один кусок. Переопределять его есть смысл только тем, кто и так ходит
    циклом с паузой (`reddit`, `reddit_search`, `bluesky`, `x`).
    """

    name: str
    requires_credentials: bool
    configured: bool

    async def fetch(self) -> Sequence[Signal]: ...


async def drain(stream: AsyncIterator[Chunk]) -> list[Signal]:
    """Собирает поток кусков в один список — этим `fetch` выражается через
    `fetch_stream`, чтобы логика сбора не жила в двух копиях.

    ⚠️ `PartialFetch` перевыбрасывается с ПОЛНЫМ уловом: снаружи контракт
    прежний — «данные есть и их надо сохранить». Внутри потока исключение несёт
    только остаток, потому что всё до него уже отдано кусками.
    """
    out: list[Signal] = []
    try:
        async for chunk in stream:
            out.extend(chunk.signals)
    except PartialFetch as exc:
        out.extend(exc.signals)
        raise PartialFetch(str(exc), out) from None
    return out
