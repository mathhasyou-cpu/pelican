"""arXiv через официальный Atom API — бесплатно, без ключа.

Самая ранняя точка научно-технологического сигнала: препринт выкладывают за
месяцы до журнальной публикации и за годы до продукта. Единица наблюдения —
**факт подачи работы**, а не её вес: цитирований у препринта нет вовсе, а те,
что появятся, — величина «на сейчас» и в ряд по датам не ложатся
(`docs/sources-science.md`).

Окно сдвигается назад по `submittedDate`, а не пагинируется вглубь: `start`
у arXiv надёжен только на первых тысячах, а суточное окно по всем техническим
категориям — около 800 работ, то есть один запрос. Приём тот же, что у
[hackernews](hackernews.py), и по той же причине.

⚠️ Дата берётся из `<published>`, а не из `<updated>`: первая — подача v1
(историчный факт), вторая переписывается на каждой новой версии, и ряд по ней
показывал бы не появление темы, а активность правок.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime, timedelta
from xml.etree import ElementTree as ET

from pelican.config import settings
from pelican.sources._http import (
    HostPacer,
    client,
    raise_for_retryable_status,
    with_retries,
)
from pelican.sources.base import Chunk, Signal, drain, term_of

API_URL = "https://export.arxiv.org/api/query"
ATOM = "{http://www.w3.org/2005/Atom}"
ARXIV_NS = "{http://arxiv.org/schemas/atom}"
OPENSEARCH = "{http://a9.com/-/spec/opensearch/1.1/}"

# Потолок среза на один запрос. Документация arXiv: результаты отдаются срезами
# до 2000, запросы длиннее 1000 просят разбивать
# (https://info.arxiv.org/help/api/user-manual.html).
PAGE_SIZE = 2000
# ⚠️ Три секунды — документированный интервал arXiv между запросами, а не
# догадка и не подобранная величина. Бэкфилл за пять лет это ~1800 запросов,
# то есть полтора часа, и сокращать паузу нельзя: вежливость здесь дешевле бана.
DELAY_S = 3.0
# Очередь одна на хост, как у reddit: сейчас на export.arxiv.org ходит один
# адаптер, но пауза обязана соблюдаться и когда их станет двое.
PACER = HostPacer()
# Предохранитель на одно окно. При суточном окне (~800 работ) второй страницы
# не бывает; она означает, что окно слишком широкое, и это видно в логе.
MAX_PAGES_PER_WINDOW = 3

# ⚠️ Окно суточного прогона — трое суток, а не одни.
#
# Поисковый индекс arXiv ОТСТАЁТ от подачи. Замер 2026-09-09 17:24 UTC: в окне
# «трое суток назад → сейчас» самая свежая проиндексированная работа подана
# 2026-09-08 17:59 UTC, то есть отставание 23.4 часа, и обрыв резкий — по часам
# ряд плотный до самой границы. Окно в одни сутки, привязанное к `now`,
# захватывало бы последние ~36 минут индекса и теряло бы 95% каждого дня
# МОЛЧА: источник выглядел бы живым, а свежие дни оказывались бы разреженнее
# исторических — ровно та подмена плотности ростом, от которой предостерегает
# `build_backfill`.
#
# Трое суток покрывают отставание с запасом и стоят трёх запросов: повторный
# сбор бесплатен по построению (ключ `(source, external_id, metric,
# observed_at)`), а дата подачи не переписывается.
DAILY_LOOKBACK = timedelta(days=3)

# ⚠️ Ширина окна определяется ПЛОТНОСТЬЮ ГОДА, а не вкусом, и это не украшение:
# у arXiv цена бэкфилла в ЗАПРОСАХ, а запрос стоит три секунды независимо от
# того, сколько в нём работ. Замер по нашему срезу категорий, работ в сутки:
#
#   2002    6     2014   61     2025  449
#   2006   13     2018  138
#   2010   32     2022  246
#
# Суточное окно на всей глубине — это 8 766 запросов и 7.3 часа, причём
# большая часть за днями, где работ десяток. Окно под `TARGET_PER_WINDOW`
# сводит те же 24 года примерно к 1 400 запросам.
DENSITY_BY_YEAR = ((2025, 449), (2022, 246), (2018, 138), (2014, 61), (2010, 32), (2006, 13))
# Сколько работ целимся уложить в одно окно. Вчетверо ниже ёмкости
# (`PAGE_SIZE * MAX_PAGES_PER_WINDOW`): запас на то, что плотность года —
# среднее, а внутри года есть пики.
TARGET_PER_WINDOW = 1500
MIN_WINDOW_DAYS = 1
MAX_WINDOW_DAYS = 365


def window_days(when: datetime) -> int:
    """Ширина окна для даты — из замеренной плотности её года.

    Ниже самого старого замеренного года плотность только падает, поэтому там
    берётся потолок: лишним широким окном нельзя ничего потерять — обрезку
    ловит `_fetch_window` по `totalResults`.
    """
    for year, per_day in DENSITY_BY_YEAR:
        if when.year >= year:
            return max(MIN_WINDOW_DAYS, min(MAX_WINDOW_DAYS, TARGET_PER_WINDOW // per_day))
    return MAX_WINDOW_DAYS


def _text(node: ET.Element, tag: str) -> str | None:
    """Необязательный тег: пусто и отсутствие — одно и то же, оба дают `None`."""
    return (node.findtext(tag) or "").strip() or None


def _entry_id(raw: str) -> str:
    """`http://arxiv.org/abs/2609.06873v1` -> `2609.06873v1`.

    Версия остаётся частью ключа: v1 и v2 — разные наблюдения, и обе даты
    настоящие. Схлопывать их значило бы терять факт подачи ради факта правки.

    ⚠️ Режется ПРЕФИКС `/abs/`, а не последний сегмент пути. До апреля 2007
    идентификатор содержал архив через слэш — `cs/0607001v1`, — и `rsplit("/")`
    оставлял от него `0607001v1`. Цена ошибки двойная и обе тихие: `cs/0607001`
    и `cond-mat/0607001` сходились в один ключ, и второй пропадал на
    `ON CONFLICT DO NOTHING` (замер: в окне 2006 года из 1 572 работ доезжало
    1 379, потеря 12%), а ссылка `arxiv.org/abs/0701020v12` не открывается
    вовсе — то есть доказательство в карточке переставало быть проверяемым.
    """
    return raw.split("/abs/", 1)[-1]


def parse_entries(xml: str) -> list[Signal]:
    """Отделено от сети — тестируется на фикстуре."""
    root = ET.fromstring(xml)
    signals: list[Signal] = []
    for entry in root.findall(f"{ATOM}entry"):
        raw_id = (entry.findtext(f"{ATOM}id") or "").strip()
        title = " ".join((entry.findtext(f"{ATOM}title") or "").split())
        summary = " ".join((entry.findtext(f"{ATOM}summary") or "").split())
        published = (entry.findtext(f"{ATOM}published") or "").strip()
        if not raw_id or not title or not published:
            continue  # битая запись — пропускаем молча, как у hackernews

        external_id = _entry_id(raw_id)
        primary = entry.find(f"{ARXIV_NS}primary_category")
        signals.append(
            Signal(
                source="arxiv",
                external_id=external_id,
                term_raw=term_of(title, summary),
                observed_at=datetime.fromisoformat(published.replace("Z", "+00:00")),
                metric="submissions",
                # ⚠️ `None`, а не ноль: у препринта нет метрики веса вовсе.
                # Ноль читался бы как «измерили и вышло ничего».
                value=None,
                geo=None,
                url=f"https://arxiv.org/abs/{external_id}",
                payload={
                    "summary": summary,
                    "primary_category": primary.get("term") if primary is not None else None,
                    "categories": [
                        c.get("term") for c in entry.findall(f"{ATOM}category") if c.get("term")
                    ],
                    "authors": [
                        name
                        for a in entry.findall(f"{ATOM}author")
                        if (name := a.findtext(f"{ATOM}name"))
                    ],
                    "updated": (entry.findtext(f"{ATOM}updated") or "").strip() or None,
                    # Необязательные теги. ⚠️ Берутся не «на всякий случай»:
                    # `comment` есть у 39 записей из 60 (замер), и живёт в нём
                    # признание площадкой — «Accepted at NeurIPS 2026». Это
                    # ровно тот внешний факт, которым карточка отвечает на
                    # «почему это станет важным», и добрать его потом нельзя:
                    # вставка идёт ON CONFLICT DO NOTHING и строк не обновляет.
                    "comment": _text(entry, f"{ARXIV_NS}comment"),
                    "journal_ref": _text(entry, f"{ARXIV_NS}journal_ref"),
                    "doi": _text(entry, f"{ARXIV_NS}doi"),
                    "affiliations": [
                        aff
                        for a in entry.findall(f"{ATOM}author")
                        if (aff := (a.findtext(f"{ARXIV_NS}affiliation") or "").strip())
                    ]
                    or None,
                },
            )
        )
    return signals


def total_results(xml: str) -> int | None:
    """Сколько работ в окне по мнению самого arXiv.

    Нужно ровно затем, чтобы отличить «окно кончилось» от «окно обрезано
    потолком среза»: молчаливая обрезка сделала бы старые дни разреженнее
    свежих, а детектор всплеска принял бы разницу плотности за рост.
    """
    root = ET.fromstring(xml)
    raw = root.findtext(f"{OPENSEARCH}totalResults")
    return int(raw) if raw and raw.isdigit() else None


def _window(start: datetime, end: datetime) -> str:
    fmt = "%Y%m%d%H%M"
    return f"submittedDate:[{start.strftime(fmt)} TO {end.strftime(fmt)}]"


class ArXivThrottled(RuntimeError):
    """Отказ по частоте. Отдельный тип — чтобы его НЕ ловили ретраи.

    ⚠️ `with_retries` повторяет `httpx.HTTPStatusError`, поэтому обычный
    `raise_for_status()` на 429 здесь означал бы три запроса вместо одного.
    """


class ArXiv:
    name = "arxiv"
    requires_credentials = False
    configured = True

    def __init__(
        self,
        lookback: timedelta = DAILY_LOOKBACK,
        categories: Sequence[str] | None = None,
        delay_s: float = DELAY_S,
        skip: timedelta = timedelta(0),
    ) -> None:
        self.lookback = lookback
        self.categories = list(categories if categories is not None else settings.arxiv_categories)
        self.delay_s = delay_s
        # Насколько отодвинут СВЕЖИЙ край окна. Нужен, чтобы срез истории
        # начинался там, где кончился предыдущий (`build_backfill`).
        self.skip = skip

    def windows(self, now: datetime) -> list[tuple[datetime, datetime]]:
        """Окна от свежего края к старому, шириной по плотности года.

        Отделено от сети намеренно: арифметика окна — единственное, что
        отличает срез истории от суточного прогона, и проверять её сетью
        значило бы не проверять вовсе.

        ⚠️ План строится ВСЕГДА от `now` на полную глубину, и только потом
        режется по `skip`. Иначе срезы перестали бы стыковаться: ширина окна
        зависит от даты, и у среза, начатого с середины, границы легли бы не
        туда, оставив дыру в истории.
        """
        edge = now - self.lookback
        top = now - self.skip
        out: list[tuple[datetime, datetime]] = []
        end = now
        while end > edge:
            start = max(edge, end - timedelta(days=window_days(end)))
            # Обрезка по краям среза: окно берётся частью, если пересекает
            # границу, и выбрасывается целиком, если лежит вне.
            lo, hi = max(start, edge), min(end, top)
            if lo < hi:
                out.append((lo, hi))
            end = start
        return out

    @property
    def query_head(self) -> str:
        """Дизъюнкция категорий: `(cat:cs.* OR cat:eess.*)`.

        ⚠️ Скобки обязательны. Без них `AND submittedDate:[...]` связывается
        только с последней категорией, и окно молча не применяется к остальным.
        """
        return "(" + " OR ".join(f"cat:{c}" for c in self.categories) + ")"

    @with_retries
    async def _fetch_page(self, http, query: str, start: int) -> str:
        await PACER.wait(self.delay_s)
        resp = await http.get(
            API_URL,
            params={
                "search_query": query,
                "start": start,
                "max_results": PAGE_SIZE,
                "sortBy": "submittedDate",
                "sortOrder": "descending",
            },
        )
        # ⚠️ 429 здесь НЕ РЕТРАИТСЯ, в отличие от всех прочих источников, и это
        # не осторожность, а арифметика. Отказ arXiv держится не секунды:
        # замер — три отказа подряд с интервалом 15-20 с, а в плохом случае
        # счёт идёт на сутки (последний успешный ответ 2026-09-11, дальше
        # `Rate exceeded` на ЛЮБОЙ запрос, включая `all:electron` с одним
        # результатом). Ретраи `with_retries` (до 10 с) такой отказ перекрыть
        # не могут по построению — они только превращают один стук в три,
        # а стук по закрытой двери у лимитера со скользящим окном продлевает
        # само окно.
        if resp.status_code == 429:
            after = resp.headers.get("retry-after", "")
            raise ArXivThrottled(
                "arXiv отказывает по частоте (`Rate exceeded`)"
                + (f", просит подождать {after} с" if after else "")
                + "; это блокировка ПО IP — лёгкий запрос получает тот же отказ"
            )
        raise_for_retryable_status(resp)
        return resp.text

    async def _fetch_window(self, http, start: datetime, end: datetime) -> list[Signal]:
        """Одно окно. Не влезло в ёмкость — делится пополам, а не обрезается.

        ⚠️ Страховка обязательна с тех пор, как ширина окна берётся из
        ПЛОТНОСТИ ГОДА: плотность — среднее, а внутри года бывают пики (дедлайн
        конференции). Молчаливая обрезка сделала бы такой год разреженнее
        соседних, и детектор всплеска принял бы разницу плотности за спад —
        та же грабля, от которой предостерегает `build_backfill`.
        """
        query = f"{self.query_head} AND {_window(start, end)}"
        capacity = PAGE_SIZE * MAX_PAGES_PER_WINDOW
        out: list[Signal] = []
        seen: set[str] = set()
        for page in range(MAX_PAGES_PER_WINDOW):
            xml = await self._fetch_page(http, query, page * PAGE_SIZE)
            if page == 0 and (total := total_results(xml)) is not None and total > capacity:
                middle = start + (end - start) / 2
                if middle <= start or middle >= end:
                    # Делить дальше нечего: окно схлопнулось, а работы не
                    # вместились. Это недостижимо на реальных объёмах arXiv
                    # (пик суток — сотни), и молчать об этом нельзя.
                    raise RuntimeError(
                        f"окно {start:%Y-%m-%d %H:%M}..{end:%Y-%m-%d %H:%M} "
                        f"содержит {total} работ при ёмкости {capacity} и не делится"
                    )
                return await self._fetch_window(http, start, middle) + await self._fetch_window(
                    http, middle, end
                )
            batch = parse_entries(xml)
            for signal in batch:
                if signal.external_id not in seen:
                    seen.add(signal.external_id)
                    out.append(signal)
            if len(batch) < PAGE_SIZE:
                break
        return out

    async def fetch_stream(self) -> AsyncIterator[Chunk]:
        """Кусок на каждое суточное окно.

        ⚠️ Существует ради бэкфилла: пять лет — это 1800 окон и полтора часа
        сети, и падение на середине не имеет права уносить собранное
        (`base.Chunk`).
        """
        # ⚠️ Таймаут вдвое против общего умолчания в 30 с: замер показал ответ
        # на 31.5 с, то есть умолчание превращало УСПЕШНЫЙ медленный ответ в
        # `ReadTimeout` — а его ретраит `with_retries`, и тяжёлый запрос уходил
        # на придушенный хост ещё дважды.
        async with client(timeout=60.0) as http:
            for start, end in self.windows(datetime.now(UTC)):
                yield Chunk(signals=await self._fetch_window(http, start, end))

    async def fetch(self) -> Sequence[Signal]:
        return await drain(self.fetch_stream())
