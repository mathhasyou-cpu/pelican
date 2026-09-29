"""OpenAlex через открытый REST. Ключ необязателен, но он тут про БЮДЖЕТ:
с февраля 2026 запрос стоит $0.0001, свободный ключ даёт $1 в сутки
(10 000 запросов), без ключа — на порядок меньше (docs/science-backfill.md).

Берёт то, чего нет на [arxiv](arxiv.py): материалы, энергетику и химическую
инженерию, которые живут только в журналах, плюс таксономию тем и организации
авторов. Единица наблюдения — **факт публикации работы в дату**, как и у arXiv.

⚠️ `cited_by_count` кладётся в payload и НИКОГДА в `value`. Это величина «на
сейчас», ровно как `stargazers_count` у [github](github.py) и
`userRatingCount` у [appstore](appstore.py): бэкфилл записал бы сегодняшнее
число цитирований датой пятилетней давности, и любой детектор роста увидел бы
рост, которого не было (`docs/sources.md`, правила бэкфилла).

⚠️ Пагинация только курсорная. Постраничная у OpenAlex упирается в 10 000
результатов, а суточное окно одного среза бывает и больше — потолок обрезал бы
свежие дни молча.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from time import monotonic
from typing import Any

from tenacity import (
    AsyncRetrying,
    RetryCallState,
    retry_if_exception_type,
    stop_after_attempt,
    stop_after_delay,
    wait_exponential,
    wait_random_exponential,
)

from pelican.config import settings
from pelican.sources._http import (
    RETRYABLE,
    HostPacer,
    client,
    raise_for_retryable_status,
)
from pelican.sources.base import Chunk, Signal, drain, term_of

API_URL = "https://api.openalex.org/works"
PER_PAGE = 200  # потолок OpenAlex на страницу
# Вежливость к API: по ЧАСТОТЕ у OpenAlex запас большой, ограничение живёт не
# здесь, а в суточном бюджете (см. `WINDOW_DAYS`), поэтому пауза тут про поток,
# а не про 429.
# ⚠️ Пауза почти ничего не значит, и на этом легко ошибиться в разы: ответ на
# страницу в 200 работ с аннотациями идёт 2.2-6.7 с (замер). Цена сбора у этого
# источника — в ЗАПРОСАХ и в ОТВЕТЕ, а не в паузе, как у `arxiv`
# (docs/science-backfill.md).
DELAY_S = 0.2
PACER = HostPacer()

# ⚠️ ЦЕНА ЗДЕСЬ — В ЗАПРОСАХ, И ОНА ДЕНЕЖНАЯ. С февраля 2026 OpenAlex считает
# usage-based: $0.0001 за запрос, свободный ключ даёт $1 в сутки, то есть
# **10 000 запросов**, без ключа — на порядок меньше
# (blog.openalex.org/openalex-api-new-features-and-usage-based-pricing).
# Остаток виден в заголовках `x-ratelimit-remaining` / `-remaining-usd`.
#
# Отсюда ширина окна: курсор отдаёт 200 работ за запрос независимо от того,
# сутки в окне или месяц, а вот КАЖДОЕ окно стоит минимум двух запросов
# (страница плюс исчерпание курсора). Посуточный обход тонкого среза за пять
# лет — это 1826 окон и ~4 000 запросов при 640 страницах полезных: две трети
# бюджета уходили в пустые хвосты. Замер на `fields/21`: 300 суток (67 тыс.
# работ) окнами по 31 дню стоили **405 запросов** против ~900 поштучно, и на
# времени это не сказалось вовсе — оно уходит в страницы, а не в хвосты.
WINDOW_DAYS = 31

# Предохранитель на одно окно среза, и он же — единственная защита от
# молчаливой обрезки (`_fetch_window`).
#
# ⚠️ Величина замерена по самым объёмным суткам, а не взята по средней
# плотности: 1 января OpenAlex складывает работы, у которых издатель не дал
# точной даты, и такой день на два порядка плотнее обычного. Замер по пяти
# годам: `fields/22` (инженерия) — 323 737 работ на 2025-01-01, `fields/17`
# (CS) — 161 556, `fields/25` — 47 751, `fields/16` — 40 374, `fields/21` —
# 17 244, против сотен в обычный день.
#
# ⚠️ Ёмкость обязана вмещать САМЫЕ ПЛОТНЫЕ сутки целиком, и это не запас на
# всякий случай: схлопнувшееся до суток окно, которое всё равно не влезло, —
# это отказ всего бэкфилла (`_fetch_window`), то есть тесная ёмкость роняет
# прогон ровно на первом январе. 2 750 страниц — 550 000 работ, запас 1.7x к
# худшему наблюдённому дню.
MAX_PAGES_PER_WINDOW = 2750

# Порог ДЕЛЕНИЯ окна — величина отдельная от ёмкости, и путать их нельзя.
#
# Ёмкость выше отвечает на «влезет ли вообще», а этот порог — на «сколько
# держать в памяти до записи»: окно приезжает одним куском, и кусок в полмиллиона
# работ с аннотациями — это гигабайты в процессе сбора. 80 000 — тот размер, на
# котором собран весь нынешний корпус.
#
# ⚠️ Делить можно только окно ШИРЕ СУТОК. Плотные сутки (1 января) делить нечем,
# и они честно идут одним куском до самой ёмкости — иначе первый же январь ронял
# бы весь бэкфилл.
SPLIT_AT = 80_000

# ⚠️ Окно суточного прогона — неделя, и по двум разным причинам.
#
# Первая — отставание индекса. Замер 2026-09-09 по срезу `fields/25`: за
# сегодня 13 работ, за вчера уже 468, дальше 465, 285, 315. Окно в одни сутки
# приносило бы почти пустоту.
#
# Вторая — ⚠️ день ПРОДОЛЖАЕТ наполняться неделями: `publication_date` ставит
# издатель, а в OpenAlex работа попадает позже. Поэтому свежие месяцы всегда
# недосчитаны, и любая метрика роста, посчитанная по хвосту ряда, покажет
# ЛОЖНОЕ ПАДЕНИЕ. Лечится это не окном, а периодическим повторным бэкфиллом;
# неделя здесь — только про то, чтобы суточный прогон не терял свежее.
DAILY_LOOKBACK = timedelta(days=7)

# Ретраи суточного прогона — общие для всех адаптеров (`_http.with_retries`):
# три попытки за ~15 с. Сорок пять минут прогона не ждут одного источника.
DAILY_RETRY: Mapping[str, Any] = {
    "stop": stop_after_attempt(3),
    "wait": wait_exponential(multiplier=1, min=1, max=10),
}

# ⚠️ У бэкфилла терпение к сети другое, и это не щедрость, а цена. Прогон идёт
# часами, окно пишется только целиком, и отказ сети на полминуты уносил всё
# недописанное окно — у CS+Eng это ~1000 оплаченных запросов, после чего прогон
# стоит до ручного перезапуска (так и было: `getaddrinfo failed` посреди окна).
# Повтор отказа СЕТИ при этом бесплатен: запрос до OpenAlex не дошёл.
#
# Приём — truncated exponential backoff с full jitter и ОБЩИМ ДЕДЛАЙНОМ вместо
# числа попыток (AWS Architecture Blog, «Exponential Backoff And Jitter»;
# Google Cloud Storage, «Retry strategy»): для фоновой работы потолок задают
# временем, а не попытками. ⚠️ Час и потолок паузы в 60 с — догадка, не замер
# (docs/todo.md, п. 56): «переждать переподключение VPN/DNS» против «не висеть
# молча при настоящей аварии».
BACKFILL_PATIENCE = timedelta(hours=1)
BACKFILL_RETRY: Mapping[str, Any] = {
    "stop": stop_after_delay(BACKFILL_PATIENCE.total_seconds()),
    "wait": wait_random_exponential(multiplier=1, max=60),
}


@dataclass(slots=True)
class Tally:
    """Как идёт обход. Адаптер пишет, прогресс бэкфилла читает.

    ⚠️ `requests` — это ОТВЕТЫ OpenAlex, то есть оплаченные запросы, а не
    попытки: отказ сети до хоста не доходит и бюджета не тратит.
    """

    windows: list[tuple[date, date]] = field(default_factory=list)
    done: int = 0
    #: Работ во всём диапазоне по всем срезам — `meta.count` на старте.
    planned: int | None = None
    works: int = 0
    requests: int = 0
    budget_left: int | None = None
    budget_limit: int | None = None
    budget_reset_at: datetime | None = None
    started: float = field(default_factory=monotonic)


#: `(событие, счёт, пояснение)`; события: plan, page, window, retry.
ProgressHook = Callable[[str, Tally, str], None]


@dataclass(frozen=True, slots=True)
class Forecast:
    works_left: int
    requests_left: int
    #: Сколько суточных бюджетов ключа уйдёт на остаток.
    budgets_left: float | None
    #: Сколько идти без остановок по бюджету, при нынешней скорости.
    seconds_left: float
    #: На сколько работ хватит остатка сегодняшнего бюджета.
    works_today: int | None
    #: Старый край окна, до которого дотянет остаток бюджета (по средней плотности окна).
    today_reaches: date | None


def forecast(t: Tally, now: float) -> Forecast | None:
    """Прогноз по скорости ЭТОГО прогона, а не по записанным константам.

    Работ на запрос и работ в секунду мерятся тут же: у разных срезов и лет
    плотность разная, и хвосты окон (запрос на исчерпание курсора) съедают
    долю бюджета, которую константа 200 работ на запрос не видит.
    """
    if t.planned is None or not t.done or not t.works or not t.requests:
        return None
    left = max(0, t.planned - t.works)
    per_request = t.works / t.requests
    per_second = t.works / max(now - t.started, 1e-9)
    works_today = reaches = None
    if t.budget_left is not None:
        works_today = int(t.budget_left * per_request)
        per_window = t.works / t.done
        ahead = int(works_today // per_window)
        index = min(len(t.windows), t.done + ahead) - 1
        reaches = t.windows[index][0] if index >= t.done else None
    return Forecast(
        works_left=left,
        requests_left=round(left / per_request),
        budgets_left=left / per_request / t.budget_limit if t.budget_limit else None,
        seconds_left=left / per_second,
        works_today=works_today,
        today_reaches=reaches,
    )


def abstract_of(inverted: dict[str, list[int]] | None) -> str:
    """OpenAlex отдаёт аннотацию инвертированным индексом «слово -> позиции».

    Восстановление — раскладка слов по позициям. Приём документирован самим
    OpenAlex; текст нужен целиком, потому что `term_raw` без тела теряет
    формулировку (то же правило, что у постовых источников, `base.term_of`).
    """
    if not inverted:
        return ""
    words: list[tuple[int, str]] = [
        (pos, word) for word, positions in inverted.items() for pos in positions
    ]
    words.sort()
    return " ".join(word for _, word in words)


def affiliations_of(work: dict[str, Any]) -> dict[str, Any]:
    """Организации, страны и число авторов — из `authorships`, но НЕ целиком.

    ⚠️ Отбор здесь про размер, а не про вкус. `authorships` весит 12.4 КБ на
    работу при 3 КБ у всей нынешней строки, и полные списки авторов радару не
    нужны: вопрос «кто этим занимается» решается организацией и страной, а не
    фамилией. Урезанная проекция вместе с `keywords` и фондами — 784 байта
    против 23 КБ всего отброшенного (замер).

    Число авторов остаётся: это единственное, что теряется без списка, и в
    сциентометрии оно само по себе признак — крупная коллаборация против
    одиночной работы.
    """
    authorships = work.get("authorships") or []
    return {
        "institutions": sorted(
            {
                name
                for a in authorships
                for i in (a.get("institutions") or [])
                if (name := i.get("display_name"))
            }
        )
        or None,
        "countries": sorted({c for a in authorships for c in (a.get("countries") or [])}) or None,
        "n_authors": len(authorships) or None,
    }


def funders_of(work: dict[str, Any]) -> list[str] | None:
    """Кто платил. ⚠️ Поле у OpenAlex переезжало между `awards` и `funders`,
    поэтому читаются оба: пустой результат из-за переименования выглядел бы
    как «работу никто не финансировал»."""
    names = {
        name
        for key, field in (("awards", "funder_display_name"), ("funders", "display_name"))
        for entry in (work.get(key) or [])
        if (name := entry.get(field))
    }
    return sorted(names) or None


def parse_works(payload: dict[str, Any]) -> list[Signal]:
    """Отделено от сети — тестируется на фикстуре."""
    signals: list[Signal] = []
    for work in payload.get("results", []):
        raw_id = (work.get("id") or "").strip()
        title = " ".join((work.get("title") or "").split())
        published = (work.get("publication_date") or "").strip()
        if not raw_id or not title or not published:
            continue  # битая запись — пропускаем молча

        external_id = raw_id.rsplit("/", 1)[-1]
        abstract = abstract_of(work.get("abstract_inverted_index"))
        topic = work.get("primary_topic") or {}
        location = work.get("primary_location") or {}
        source = location.get("source") or {}
        signals.append(
            Signal(
                source="openalex",
                external_id=external_id,
                term_raw=term_of(title, abstract),
                # Дата публикации — сутки без времени; полночь UTC, потому что
                # всё в БД наивный UTC (docs/data-model.md).
                observed_at=datetime.combine(date.fromisoformat(published), time(), tzinfo=UTC),
                metric="works",
                # ⚠️ `None`, а не `cited_by_count`: см. докстринг модуля.
                value=None,
                geo=None,
                url=raw_id,
                payload={
                    "description": abstract,
                    "doi": work.get("doi"),
                    "type": work.get("type"),
                    "language": work.get("language"),
                    # Величина «на сейчас» — только для справки в карточке,
                    # в ряд по датам она не идёт.
                    "cited_by_count": work.get("cited_by_count"),
                    "topic": topic.get("display_name"),
                    "subfield": (topic.get("subfield") or {}).get("display_name"),
                    "field": (topic.get("field") or {}).get("display_name"),
                    "venue": source.get("display_name"),
                    "is_oa": location.get("is_oa"),
                    # Кто этим занимается и на чьи деньги — то, чем карточка
                    # отвечает на «почему это станет важным». Добрать потом
                    # нельзя: ON CONFLICT DO NOTHING строк не обновляет.
                    **affiliations_of(work),
                    "funders": funders_of(work),
                    # Готовые термины от самого OpenAlex — кандидаты в единицу
                    # выдачи, если ею станет термин (docs/todo.md, п. 43).
                    "keywords": [
                        name
                        for k in (work.get("keywords") or [])
                        if (name := k.get("display_name"))
                    ]
                    or None,
                },
            )
        )
    return signals


class OpenAlex:
    name = "openalex"
    requires_credentials = False
    configured = True

    def __init__(
        self,
        lookback: timedelta = DAILY_LOOKBACK,
        filters: Sequence[str] | None = None,
        delay_s: float = DELAY_S,
        skip: timedelta = timedelta(0),
        retry: Mapping[str, Any] = DAILY_RETRY,
    ) -> None:
        self.lookback = lookback
        self.filters = list(filters if filters is not None else settings.openalex_filters)
        self.delay_s = delay_s
        # Насколько отодвинут свежий край окна — см. `ArXiv.skip`.
        self.skip = skip
        self.retry = retry
        self.tally = Tally()
        # Ставит только бэкфилл: с ним на старте уходит по запросу на срез ради
        # `meta.count` всего диапазона, суточному прогону это ни к чему.
        self.on_progress: ProgressHook | None = None

    def _emit(self, event: str, note: str = "") -> None:
        if self.on_progress is not None:
            self.on_progress(event, self.tally, note)

    def _on_retry(self, state: RetryCallState) -> None:
        exc = state.outcome.exception() if state.outcome else None
        wait = state.next_action.sleep if state.next_action else 0
        self._emit(
            "retry",
            f"{type(exc).__name__}: {exc} — попытка {state.attempt_number}, "
            f"повтор через {wait:.0f} с (ждём уже {state.seconds_since_start / 60:.1f} мин)",
        )

    def windows(self, today: date) -> list[tuple[date, date]]:
        """Окна от свежих к старым, встык и без нахлёста. Отделено от сети.

        ⚠️ Сегодняшний день входит при `skip = 0`: `publication_date` ставит
        издатель, а не индексатор, и «со вчера» отрезало бы свежее.

        ⚠️ Окно ШИРЕ СУТОК не ради скорости, а ради бюджета: каждое окно стоит
        минимум двух запросов, а запрос стоит денег (см. `WINDOW_DAYS`).
        """
        first = self.skip.days
        last = max(first + 1, self.lookback.days)
        out: list[tuple[date, date]] = []
        n = first
        while n < last:
            hi = today - timedelta(days=n)
            n = min(last, n + WINDOW_DAYS)
            out.append((today - timedelta(days=n - 1), hi))
        return out

    def _params(self, scope: str, lo: date, hi: date, cursor: str) -> dict[str, Any]:
        params = {
            "filter": f"from_publication_date:{lo},to_publication_date:{hi},{scope}",
            "per_page": PER_PAGE,
            "cursor": cursor,
        }
        if settings.openalex_api_key:
            params["api_key"] = settings.openalex_api_key
        return params

    async def _get(self, http, params: dict[str, Any]) -> dict[str, Any]:
        async for attempt in AsyncRetrying(
            retry=retry_if_exception_type(RETRYABLE),
            reraise=True,
            before_sleep=self._on_retry,
            **self.retry,
        ):
            with attempt:
                await PACER.wait(self.delay_s)
                resp = await http.get(API_URL, params=params)
                self._read_budget(resp)
                # ⚠️ 429 здесь — не «частим», а «кончился суточный бюджет», и
                # ретраями он не лечится: в `retry-after` приходят десятки минут.
                # Отдельная ветка нужна ради сообщения: без неё прогон
                # отчитывается «слишком много запросов», и правится это паузой,
                # которая не при чём.
                if resp.status_code == 429:
                    left = int(resp.headers.get("retry-after") or 0)
                    raise RuntimeError(
                        f"бюджет OpenAlex исчерпан ($0.0001 за запрос, свободный ключ — "
                        f"$1 в сутки); сброс через {left // 60} мин"
                    )
                raise_for_retryable_status(resp)
                return resp.json()
        raise AssertionError("AsyncRetrying с reraise=True не выходит из цикла без ответа")

    def _read_budget(self, resp) -> None:
        """Остаток бюджета живёт только в заголовках ответа — из БД его не видно."""
        self.tally.requests += 1
        headers = resp.headers
        if (left := headers.get("x-ratelimit-remaining")) is not None:
            self.tally.budget_left = int(left)
        if (limit := headers.get("x-ratelimit-limit")) is not None:
            self.tally.budget_limit = int(limit)
        if (reset := headers.get("x-ratelimit-reset")) is not None:
            self.tally.budget_reset_at = datetime.now() + timedelta(seconds=int(reset))

    async def _fetch_page(
        self, http, scope: str, lo: date, hi: date, cursor: str
    ) -> dict[str, Any]:
        return await self._get(http, self._params(scope, lo, hi, cursor))

    async def _count(self, http, scope: str, lo: date, hi: date) -> int:
        """Сколько работ в диапазоне среза: один запрос, без аннотаций."""
        params = self._params(scope, lo, hi, "*")
        params.update(per_page=1, select="id")
        payload = await self._get(http, params)
        return int((payload.get("meta") or {}).get("count") or 0)

    async def _fetch_window(self, http, scope: str, lo: date, hi: date) -> list[Signal]:
        """Одно окно среза. Не влезло — делится, а не обрезается.

        ⚠️ Тот же приём, что у `ArXiv._fetch_window`: `meta.count` первой
        страницы решает, брать окно целиком или пополам. Делится окно шире
        суток; плотные сутки идут одним куском (`SPLIT_AT`).

        ⚠️ Обрезка здесь ловится не счётчиком, а концом цикла: вышли по
        исчерпанному бюджету страниц, а курсор ещё жив — значит окно обрезано,
        и это отказ. Молчаливая обрезка уже стоила каждого января корпуса
        (1 января у OpenAlex — свалка работ без точной даты,
        `docs/sources-science.md`), и год читался бы как спад, а не как поломка
        сбора.
        """
        out: list[Signal] = []
        cursor = "*"
        for page in range(MAX_PAGES_PER_WINDOW):
            payload = await self._fetch_page(http, scope, lo, hi, cursor)
            meta = payload.get("meta") or {}
            if page == 0 and lo < hi and (meta.get("count") or 0) > SPLIT_AT:
                middle = lo + (hi - lo) / 2
                return await self._fetch_window(http, scope, lo, middle) + await self._fetch_window(
                    http, scope, middle + timedelta(days=1), hi
                )
            batch = parse_works(payload)
            out.extend(batch)
            self.tally.works += len(batch)
            self._emit("page")
            cursor = meta.get("next_cursor") or ""
            if not cursor or not batch:
                break
        else:
            raise RuntimeError(
                f"{lo:%Y-%m-%d}..{hi:%Y-%m-%d} по срезу {scope}: "
                f"{MAX_PAGES_PER_WINDOW} страниц кончились, а курсор жив — окно обрезано"
            )
        return out

    async def fetch_stream(self) -> AsyncIterator[Chunk]:
        """Кусок на (окно, срез) — по той же причине, что у arxiv: бэкфилл
        идёт часами, и падение на середине не имеет права уносить собранное.

        ⚠️ Окно — ВНЕШНИЙ цикл, срез — внутренний, и порядок этот обязателен.
        Обратный порядок проходит первый срез на всю глубину и только потом
        берётся за второй, поэтому прерванный (рукой или исчерпанным бюджетом)
        бэкфилл оставляет одно полное поле и одно пустое. С окном снаружи любая
        остановка оставляет ровный временной срез по ВСЕМ полям сразу, а глубина
        перестаёт быть обязательством: `-d` это верхняя граница, а не план.
        """
        windows = self.windows(datetime.now(UTC).date())
        self.tally = Tally(windows=windows)
        async with client() as http:
            if self.on_progress is not None:
                counts = await asyncio.gather(
                    *(
                        self._count(http, scope, windows[-1][0], windows[0][1])
                        for scope in self.filters
                    )
                )
                self.tally.planned = sum(counts)
                self._emit("plan")
            for lo, hi in windows:
                # Срезы одного окна идут ПАРАЛЛЕЛЬНО, и это не про вежливость, а
                # про то, где здесь узкое место. Частота нам не помеха: очередь
                # к хосту общая (`HostPacer`, 0.2 с), а OpenAlex разрешает
                # десяток запросов в секунду. Время уходит в ОТВЕТ — замер на
                # курсоре в глубине окна: 2.2 с на первых страницах и до 10 с
                # дальше. Последовательные срезы складывают эти секунды, а
                # параллельные закрывают окно за время самого медленного.
                batches = await asyncio.gather(
                    *(self._fetch_window(http, scope, lo, hi) for scope in self.filters)
                )
                for signals in batches:
                    yield Chunk(signals=signals)
                self.tally.done += 1
                self._emit("window")

    async def fetch(self) -> Sequence[Signal]:
        return await drain(self.fetch_stream())
