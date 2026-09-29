"""Живой поиск по открытым источникам: то, чего нет в локальном корпусе.

ТЗ требует поиск «в реальном времени по открытым источникам ... без ограничений
рамками стартового датасета». Локальное зеркало arXiv/OpenAlex отвечает на научный
конец воронки; здесь — остальное, и каждое по своей причине:

| источник | зачем | проверка доступа 2026-09-15 |
|---|---|---|
| Google News RSS, поиск | **ось денег**: раунды, пилоты, компании | 200, 0.7 с, EN и RU |
| CyberLeninka `/api/search` | русскоязычная наука | 200, 0.6 с |
| Хабр RSS-поиск | русскоязычная практика | 200, 1.5 с |
| GitHub search | ось предложения: есть ли код | 200, 0.6 с |

⚠️ **Почему ось денег обязательна, а не украшение.** Разбор колонки «Почему это слабый
сигнал» датасета: 105 упоминаний раунда/суммы против 23 «мало публикаций». Заказчик
определяет слабый сигнал деньгами и компаниями, а наш корпус — публикации
(docs/weak-signals.md).

⚠️ **Чего здесь нет намеренно.** Живой arXiv: 16 с на запрос и блокировка по IP на
СУТКИ при перегрузе (docs/science-backfill.md) — на демо это провал; локальное зеркало
отвечает быстрее и полнее. Живой OpenAlex: платный бюджет, и в день проверки он был
съеден бэкфиллом (429). HN Algolia: `query` матчит слишком вольно, на запрос про
отравление памяти агентов отдал игровые серверы.

⚠️ **Google News отдаёт ссылку-переадресацию, а не адрес статьи.** Браузер пользователя
её разворачивает, серверный клиент упирается в страницу согласия. Поэтому домен
издателя берётся из `<source url>`, и по нему считается доверенность (`weak.trust`).

⚠️ **Отказ источника не роняет запрос.** Каждый источник — свой отсек (bulkhead, как в
`pipeline/collect.py`): упавший возвращает пустой список и строку в `errors`, а выдача
собирается из остальных и говорит вслух, чего не хватило.
"""

from __future__ import annotations

import asyncio
import html
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import quote

import httpx

from pelican.config import settings
from pelican.weak import asof
from pelican.weak.trust import classify, domain_of

TIMEOUT_S = 20.0
PER_SOURCE = 10

_TAG = re.compile(r"<[^>]+>")


@dataclass(frozen=True, slots=True)
class LiveDoc:
    source: str  # google_news / cyberleninka / habr / github
    title: str
    url: str
    published: str  # ISO-дата или пусто
    publisher: str  # издатель или журнал
    lang: str  # ru / en
    snippet: str = ""
    domain: str = ""

    @property
    def trust(self):
        return classify(self.domain or self.url)


@dataclass(slots=True)
class LiveResult:
    docs: list[LiveDoc] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)
    queried: dict[str, int] = field(default_factory=dict)


def _clean(text: str) -> str:
    return " ".join(html.unescape(_TAG.sub(" ", text or "")).split())


def _rss_items(xml: str) -> list[str]:
    return re.findall(r"<item>(.*?)</item>", xml, flags=re.S)


def _field(item: str, name: str) -> str:
    m = re.search(rf"<{name}[^>]*>(.*?)</{name}>", item, flags=re.S)
    if not m:
        return ""
    value = m.group(1).strip()
    if value.startswith("<![CDATA["):
        value = value[9:-3]
    return value


def _rfc822(text: str) -> str:
    try:
        return parsedate_to_datetime(text).astimezone(UTC).date().isoformat()
    except (TypeError, ValueError):
        return ""


def parse_google_news(xml: str, lang: str) -> list[LiveDoc]:
    out = []
    for item in _rss_items(xml):
        raw_title = _clean(_field(item, "title"))
        src = re.search(r'<source url="(.*?)">(.*?)</source>', item, flags=re.S)
        publisher = _clean(src.group(2)) if src else ""
        domain = domain_of(src.group(1)) if src else ""
        # Google дописывает издателя в заголовок через « - »; в карточке он свой.
        title = (
            raw_title[: -len(publisher) - 3]
            if publisher and raw_title.endswith(" - " + publisher)
            else raw_title
        )
        out.append(
            LiveDoc(
                "google_news",
                title,
                _field(item, "link"),
                _rfc822(_field(item, "pubDate")),
                publisher,
                lang,
                domain=domain,
            )
        )
    return out


def parse_habr(xml: str) -> list[LiveDoc]:
    out = []
    for item in _rss_items(xml):
        url = _field(item, "guid") or _field(item, "link")
        out.append(
            LiveDoc(
                "habr",
                _clean(_field(item, "title")),
                url,
                _rfc822(_field(item, "pubDate")),
                "Хабр",
                "ru",
                _clean(_field(item, "description"))[:400],
                domain="habr.com",
            )
        )
    return out


def parse_cyberleninka(payload: dict) -> list[LiveDoc]:
    out = []
    for a in payload.get("articles", []):
        year = a.get("year")
        out.append(
            LiveDoc(
                "cyberleninka",
                _clean(a.get("name", "")),
                "https://cyberleninka.ru" + a.get("link", ""),
                f"{year}" if year else "",
                _clean(a.get("journal", "")),
                "ru",
                _clean(a.get("annotation", ""))[:400],
                domain="cyberleninka.ru",
            )
        )
    return out


def parse_github(payload: dict) -> list[LiveDoc]:
    out = []
    for r in payload.get("items", []):
        out.append(
            LiveDoc(
                "github",
                r.get("full_name", ""),
                r.get("html_url", ""),
                (r.get("created_at") or "")[:10],
                f"★ {r.get('stargazers_count', 0)}",
                "en",
                _clean(r.get("description") or "")[:300],
                domain="github.com",
            )
        )
    return out


async def _cached(kind: str, query: str, fetch):
    """Ответ источника через суточный кэш (`weak.cache`).

    ⚠️ Импорт отложенный: `cache` знает `LiveDoc`, и на уровне модуля вышел бы цикл.
    """
    from pelican.weak.cache import through

    docs = await through(kind, query, fetch)
    # ⚠️ Единственная дверь всех пяти источников: срез отсекается здесь, иначе каждый
    # источник пришлось бы чинить отдельно, а забытый молча показывал бы будущее.
    return [d for d in docs if not asof.too_late(d.published)]


async def _google_news(
    http: httpx.AsyncClient,
    query: str,
    lang: str,
    window: str | None = None,
    limit: int = PER_SOURCE,
) -> list[LiveDoc]:
    """`window` — явное окно `after:… before:…` (денежный след по окну, `weak.rounds`),
    иначе окно продукта или среза (`asof.news_window`).

    ⚠️ Явное окно входит в текст запроса ДО кэша, а нестандартный `limit` — в вид ключа:
    иначе ответ по окну бэктеста перезаписал бы суточный ответ продукта по тому же запросу.
    """
    kind = f"google_news:{lang}" if limit == PER_SOURCE else f"google_news:{lang}:{limit}"
    if window is None:
        fetch = lambda: _fetch_google_news(http, query, lang, asof.news_window(), limit)  # noqa: E731
        return await _cached(kind, query, fetch)
    fetch = lambda: _fetch_google_news(http, query, lang, window, limit)  # noqa: E731
    return await _cached(kind, f"{query} {window}", fetch)


async def _fetch_google_news(
    http: httpx.AsyncClient, query: str, lang: str, window: str, limit: int = PER_SOURCE
) -> list[LiveDoc]:
    region = "hl=ru&gl=RU&ceid=RU:ru" if lang == "ru" else "hl=en-US&gl=US&ceid=US:en"
    # Окно Google News: без него верх занимают старые громкие заметки. Под срезом это
    # `after:`/`before:` — проверено, что поиск отыгрывается назад (`weak.asof`).
    r = await http.get(
        f"https://news.google.com/rss/search?q={quote(query + ' ' + window)}&{region}"
    )
    r.raise_for_status()
    return parse_google_news(r.text, lang)[:limit]


async def _habr(http: httpx.AsyncClient, query: str) -> list[LiveDoc]:
    return await _cached("habr", query, lambda: _fetch_habr(http, query))


async def _fetch_habr(http: httpx.AsyncClient, query: str) -> list[LiveDoc]:
    r = await http.get(
        f"https://habr.com/ru/rss/search/?q={quote(query)}&target_type=posts&order=relevance"
    )
    r.raise_for_status()
    return parse_habr(r.text)[:PER_SOURCE]


async def _cyberleninka(http: httpx.AsyncClient, query: str) -> list[LiveDoc]:
    return await _cached("cyberleninka", query, lambda: _fetch_cyberleninka(http, query))


async def _fetch_cyberleninka(http: httpx.AsyncClient, query: str) -> list[LiveDoc]:
    r = await http.post(
        "https://cyberleninka.ru/api/search",
        json={"mode": "articles", "q": query, "size": PER_SOURCE, "from": 0},
    )
    r.raise_for_status()
    return parse_cyberleninka(r.json())


async def _github(http: httpx.AsyncClient, query: str) -> list[LiveDoc]:
    return await _cached("github", query, lambda: _fetch_github(http, query))


async def _fetch_github(http: httpx.AsyncClient, query: str) -> list[LiveDoc]:
    headers = {"Authorization": f"Bearer {settings.github_token}"} if settings.github_token else {}
    r = await http.get(
        f"https://api.github.com/search/repositories?q={quote(query)}&sort=stars&per_page={PER_SOURCE}",
        headers=headers,
    )
    r.raise_for_status()
    return parse_github(r.json())


async def _search(query_ru: str, query_en: str) -> LiveResult:
    result = LiveResult()
    tasks = {
        "google_news_en": lambda h: _google_news(h, query_en, "en"),
        "google_news_ru": lambda h: _google_news(h, query_ru, "ru"),
        "cyberleninka": lambda h: _cyberleninka(h, query_ru),
        "habr": lambda h: _habr(h, query_ru),
        "github": lambda h: _github(h, query_en),
    }
    async with httpx.AsyncClient(
        timeout=TIMEOUT_S, follow_redirects=True, headers={"User-Agent": settings.user_agent}
    ) as http:
        got = await asyncio.gather(*(fn(http) for fn in tasks.values()), return_exceptions=True)
    for name, value in zip(tasks, got, strict=True):
        if isinstance(value, BaseException):
            result.errors[name] = f"{type(value).__name__}: {str(value)[:120]}"
            result.queried[name] = 0
        else:
            result.docs.extend(value)
            result.queried[name] = len(value)
    return result


def search(query_ru: str, query_en: str) -> LiveResult:
    """Один проход по всем живым источникам. Упавший источник — пустой, а не исключение."""
    return asyncio.run(_search(query_ru, query_en))


def now_iso() -> str:
    return datetime.now(UTC).date().isoformat()


#: Одновременных запросов к Google News. ⚠️ Догадка, не замер: пачка из 130
#: запросов без паузы похожа на скрейпинг, и отказ по частоте убил бы демо.
NEWS_CONCURRENCY = 4


async def _news_many(
    queries: list[str], lang: str, window: str | None = None, limit: int = PER_SOURCE
) -> list[list[LiveDoc]]:
    gate = asyncio.Semaphore(NEWS_CONCURRENCY)

    async def one(http: httpx.AsyncClient, q: str) -> list[LiveDoc]:
        async with gate:
            try:
                return await _google_news(http, q, lang, window, limit)
            except httpx.HTTPError:
                return []

    async with httpx.AsyncClient(
        timeout=TIMEOUT_S, follow_redirects=True, headers={"User-Agent": settings.user_agent}
    ) as http:
        return list(await asyncio.gather(*(one(http, q) for q in queries)))


def news_many(
    queries: list[str], lang: str = "en", *, window: str | None = None, limit: int = PER_SOURCE
) -> list[list[LiveDoc]]:
    """Новости по списку запросов. Отказ одного запроса — пустой список, не исключение.

    `window` — явное окно `after:… before:…` вместо окна продукта/среза; `limit` — сколько
    заголовков держать на запрос (RSS отдаёт до 100).
    """
    return asyncio.run(_news_many(queries, lang, window, limit)) if queries else []
