"""Общий HTTP-клиент для адаптеров: единый user-agent, таймауты, ретраи."""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from pelican.config import settings

# Ретраим только сеть и 5xx. 4xx — это ошибка в нашем запросе или в ключах,
# повтор её не вылечит, а лимиты источника съест.
RETRYABLE = (httpx.TransportError, httpx.HTTPStatusError)

with_retries = retry(
    retry=retry_if_exception_type(RETRYABLE),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    reraise=True,
)


class HostPacer:
    """Одна очередь запросов на ХОСТ, а не на адаптер.

    Приём — `per-host politeness delay`: в Scrapy пауза `DOWNLOAD_DELAY`
    действует на **download slot**, а слот это домен, поэтому пауза
    соблюдается между всеми запросами к хосту, сколько бы пауков её ни делало
    (https://docs.scrapy.org/en/latest/topics/settings.html#download-delay).

    ⚠️ Зачем это здесь: `collect` пускает все источники одной `asyncio.gather`
    (`pipeline/collect.py`), а на `www.reddit.com` ходят двое —
    [reddit](reddit.py) и [reddit_search](reddit_search.py). Каждый выдерживал
    свою минуту и о другом не знал, то есть на хост уходило по два запроса
    почти одновременно — фактический интервал 30 с вместо 60. Замер полного
    прогона: **пять запросов из десяти ушли в 429**, reddit потерял три бандла
    из пяти. Отдельно опасно тем, что 429 не роняет прогон (`PartialFetch`), и
    потеря выглядит как «сегодня в сабах было тихо».

    Величину паузы по-прежнему задаёт адаптер (`docs/access.md`:
    частота — часть адаптера, и она замеряется); общей становится только
    очередь. Пауза считается от МОМЕНТА ОТВЕТА, а не от начала запроса: лимит
    у хоста на частоту обращений, а не на частоту наших циклов.
    """

    __slots__ = ("_lock", "_last")

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        # monotonic, а не time(): перевод часов не должен обнулять паузу.
        self._last = float("-inf")

    async def wait(self, delay_s: float) -> None:
        """Придержать вызывающего, пока с прошлого запроса не пройдёт `delay_s`.

        Блокировка держится на время сна намеренно: без неё оба адаптера
        увидели бы одну и ту же метку `_last`, отспали бы одинаково и ушли на
        хост вместе — то есть ровно то, что чинится.
        """
        async with self._lock:
            gap = delay_s - (time.monotonic() - self._last)
            if gap > 0:
                await asyncio.sleep(gap)
            self._last = time.monotonic()


@asynccontextmanager
async def client(**kwargs):
    # Таймаут — умолчание, а не константа: платный пакет на тысячу ключей идёт
    # десятками секунд, и тридцати ему мало (keywords/provider.py).
    headers = {"User-Agent": settings.user_agent, **kwargs.pop("headers", {})}
    timeout = kwargs.pop("timeout", 30.0)
    async with httpx.AsyncClient(
        headers=headers,
        timeout=httpx.Timeout(timeout),
        follow_redirects=True,
        **kwargs,
    ) as c:
        yield c


def raise_for_retryable_status(response: httpx.Response) -> None:
    """5xx и 429 — ретраим; остальные 4xx поднимаем сразу, без повторов."""
    if response.status_code >= 500 or response.status_code == 429:
        response.raise_for_status()
    if response.is_error:
        raise RuntimeError(
            f"{response.request.url} -> HTTP {response.status_code}: {response.text[:300]}"
        )
