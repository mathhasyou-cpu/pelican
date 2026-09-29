"""Патентные базы: третий канал рядом с наукой и деньгами.

Заведён по двум внешним причинам сразу.

1. **ТЗ.** Патентные базы стоят в списке доверенных источников наравне с наукой и
   государством, а до сих пор в выдаче их не было ни одной.
2. **Язык заказчика.** На сессии вопросов-ответов 16.09 зрелость объяснялась через цикл
   Гартнера и «количество патентов по годам», а стадия — через УГТ. Приём не выдуман
   нами: «патентная активность по годам как фаза жизненного цикла» — это Haupt, Kloyer,
   Lange, *Research Policy* (2007), patent indicators for the technology life cycle.
   Счётчик по окнам — компонента **growth** канонического индикатора зарождающегося
   (Porter, Garner, Carley, Newman, *TFSC* 2019: novelty, persistence, growth, community).

Источник — **EPO OPS** (`ops.epo.org`), всемирный охват DOCDB, свободный тариф.

## Что замерено живьём 2026-09-17, а не взято из головы

⚠️ **Ищем по `ta` (заглавие + реферат), а не по `txt`.** Полный текст включает описание,
где технология поминается фоном: «quantum computing» даёт 4 607 по `ta` и 91 960 по
`txt`, «machine learning» — 132 871 против 1 109 850. `txt` меряет не предмет патента,
а осведомлённость патентного поверенного.

⚠️ **Точная фраза узка ровно так же, как в корпусе (§68).** `ta="neuromorphic processor"`
— 49 публикаций, `ta="neuromorphic computing"` — 273. Наши ярлыки длиной в 2–8 слов
дословно не пишет никто, поэтому сюда идёт **ядро** (`weak.core`), а не ярлык целиком.
Отдать сюда ярлык — значит получить ноль у всех подряд и принять его за замер.

⚠️ **Одна выборка OPS отдаёт максимум 2 000 результатов** — потолок жёсткий, и широкий
запрос молча обрежется. Для счётчика это неважно (`Range=1-1` возвращает полное
`total-result-count`), а вот выкачивать список надо срезами по месяцам.

⚠️ **Токен живёт 20 минут** (замер: `expires_in` = 1199 с) — обновляется заранее, а не по
первому 401: на прогоне в десять минут иначе половина запросов уходила бы в повтор.

⚠️ **Свободный тариф — 4 ГБ трафика в неделю**, запись biblio ~4 КБ. Это порядка
800 тысяч записей в неделю, то есть квота тратится ОБЪЁМОМ, а не числом запросов.
Поэтому на прогон идут только счётчики (2 запроса на кандидата) и три свежие публикации.

⚠️ **Квота и ТЕМП — разные вещи, и на темпе мы уже погорели.** Здесь стояли фиксированные
четыре запроса в секунду, взятые из чужого клиента. На 198 запросах подряд OPS ответил
`403 CLIENT.RobotDetected` и закрыл сервис `search` целиком (`search=black:0`) — при том
что трафика было израсходовано меньше процента недельной квоты. Темп у OPS свой на каждый
сервис, он меняется и приезжает в заголовке КАЖДОГО ответа (`_read_throttle`); константа
здесь не «пока непроверенная», а прямо вредная.

⚠️ **`None` — это «измерить нечем», а не ноль** (то же правило, что у оси 1 в
`docs/velocity.md`): при выключенном ключе и при отказе источника счётчик обязан быть
неотличим от отсутствующего, иначе «патентов нет» смешается с «не спросили».
"""

from __future__ import annotations

import asyncio
import base64
import re
import time
from dataclasses import dataclass, field
from datetime import date, timedelta

import httpx

from pelican.config import settings
from pelican.weak import asof
from pelican.weak.live import LiveDoc

BASE = "https://ops.epo.org/3.2"
TIMEOUT_S = 60.0

#: ⚠️ Запас к замеренным 1199 с: токен меняем до отказа, а не после.
TOKEN_TTL_S = 1_050.0
#: Сервис OPS, темп которого нас касается: у `search` своя строка в заголовке троттлинга
#: и своя, гораздо более низкая норма, чем у остальных.
SERVICE = "search"
#: Пока заголовок троттлинга не прочитан — тридцать запросов в минуту, то есть один раз
#: в две секунды. ⚠️ Догадка, но осознанно ЗАНИЖЕННАЯ: это стартовое значение до первого
#: ответа, а дальше темп диктует сам OPS.
START_PER_MIN = 30
#: Потолок одной выборки OPS. Счётчика не касается, выкачки — касается.
RESULT_CAP = 2000
#: Окно оси роста: два года против предыдущих двух.
WINDOW_DAYS = 730
#: Сколько публикаций показывать в карточке.
PER_QUERY = 3

_token: tuple[str, float] | None = None
_last_call = 0.0
#: Разрешённых запросов в минуту по `search` — из заголовка ПОСЛЕДНЕГО ответа.
_per_min = START_PER_MIN
#: Источник закрыт до конца прогона: OPS сказал «робот» или выдал чёрный цвет.
_shut = ""

_THROTTLE = re.compile(r"(\w+)=(\w+):(\d+)")


def reset() -> None:
    """Забыть состояние троттлинга. Нужно тестам и новому прогону."""
    global _per_min, _shut, _token
    _per_min, _shut, _token = START_PER_MIN, "", None


def closed() -> str:
    """Почему источник замолчал, или пустая строка. Печатается в выдаче."""
    return _shut


def _read_throttle(headers) -> None:
    """Темп следующего запроса диктует OPS, а не мы.

    Заголовок каждого ответа выглядит так::

        X-Throttling-Control: busy (images=green:100, inpadoc=green:45,
                                    other=green:1000, retrieval=green:100, search=black:0)

    Число после цвета — разрешённых запросов в МИНУТУ по этому сервису, и у `search` она
    своя: `other=green:1000` рядом с `search=green:30` означает не «тысяча в минуту», а
    «тридцать».

    ⚠️ **Не наступить.** Здесь стояли фиксированные четыре запроса в секунду — догадка,
    взятая из чужого клиента. На 198 запросах подряд 2026-09-17 OPS ответил
    `403 CLIENT.RobotDetected` и закрыл поиск целиком (`search=black:0`), после чего
    источник стал недоступен всему проекту. Темп у OPS — не константа, а переменная,
    которую он сам сообщает в каждом ответе; читать её обязательно.
    """
    global _per_min, _shut
    raw = headers.get("x-throttling-control", "")
    for service, colour, allowed in _THROTTLE.findall(raw):
        if service != SERVICE:
            continue
        _per_min = max(int(allowed), 0)
        if colour == "black" or _per_min == 0:
            _shut = f"OPS закрыл поиск (цвет {colour}), темп 0 запросов в минуту"


def enabled() -> bool:
    """Без пары ключей источник молча выключается — но выключение видно в выдаче."""
    return bool(settings.epo_ops_key and settings.epo_ops_secret)


def _cql(phrase: str, since: date, until: date) -> str:
    """CQL OPS: кавычки внутри фразы её же и ломают, поэтому снимаются."""
    clean = phrase.replace('"', " ").replace("\\", " ").strip()
    return f'ta="{clean}" and pd within "{since:%Y%m%d} {until:%Y%m%d}"'


def windows() -> tuple[tuple[date, date], tuple[date, date]]:
    """Два окна оси роста: свежее и предыдущее.

    ⚠️ Конец берётся у `asof`, а не у `date.today()`: срез обязан попасть во все двери
    разом, иначе бэктест молча считал бы патенты из будущего.
    """
    end = asof.today()
    mid = end - timedelta(days=WINDOW_DAYS)
    start = mid - timedelta(days=WINDOW_DAYS)
    return (mid, end), (start, mid)


async def _access_token(http: httpx.AsyncClient) -> str:
    global _token
    if _token and time.monotonic() - _token[1] < TOKEN_TTL_S:
        return _token[0]
    basic = base64.b64encode(f"{settings.epo_ops_key}:{settings.epo_ops_secret}".encode()).decode()
    r = await http.post(
        f"{BASE}/auth/accesstoken",
        headers={"Authorization": f"Basic {basic}"},
        data={"grant_type": "client_credentials"},
    )
    r.raise_for_status()
    _token = (r.json()["access_token"], time.monotonic())
    return _token[0]


async def _get(http: httpx.AsyncClient, path: str, params: dict) -> httpx.Response | None:
    """Запрос в темпе, который назначил сам OPS.

    ⚠️ **Отказ «робот» не ретраится ВОВСЕ.** `403 CLIENT.RobotDetected` — это приговор
    поведению клиента, а не занятость сервера: повтор его подтверждает и продлевает.
    Источник закрывается до конца прогона, а выдача идёт без патентной строки и говорит
    об этом вслух — молчаливая деградация здесь хуже отсутствующего источника.

    ⚠️ Занятость (`503`) — другое дело: она проходит, и одна попытка повтора уместна.
    """
    global _last_call
    if _shut:
        return None
    for attempt in range(2):
        gap = 60.0 / max(_per_min, 1) - (time.monotonic() - _last_call)
        if gap > 0:
            await asyncio.sleep(gap)
        _last_call = time.monotonic()
        token = await _access_token(http)
        r = await http.get(
            f"{BASE}/rest-services/{path}",
            params=params,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        )
        _read_throttle(r.headers)
        if r.status_code == 403 and "RobotDetected" in r.text:
            globals()["_shut"] = "OPS: CLIENT.RobotDetected — поиск закрыт, темп был превышен"
            return None
        if r.status_code == 503 and attempt == 0:
            await asyncio.sleep(60.0 / max(_per_min, 1))
            continue
        return r
    return None


def _total(payload: dict) -> int:
    body = payload["ops:world-patent-data"]["ops:biblio-search"]
    return int(body["@total-result-count"])


def _text(node) -> str:
    """OPS кладёт значение в `$`, а одиночную запись — не в список."""
    if isinstance(node, dict):
        return str(node.get("$", "")).strip()
    if isinstance(node, list):
        return _text(node[0]) if node else ""
    return str(node or "").strip()


def _as_list(node) -> list:
    if node is None:
        return []
    return node if isinstance(node, list) else [node]


def _iso(raw: str) -> str:
    return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}" if len(raw) >= 8 and raw.isdigit() else ""


def parse_biblio(payload: dict) -> list[LiveDoc]:
    """Публикации из ответа `search/biblio` в тот же `LiveDoc`, что и весь живой поиск.

    ⚠️ Форма именно такая намеренно: карточка, таблица источников и проверки уверенности
    не должны знать, что появился новый вид источника, — иначе каждая правка выдачи
    требовала бы правки в четырёх местах.
    """
    try:
        search = payload["ops:world-patent-data"]["ops:biblio-search"]
        docs = _as_list(search["ops:search-result"]["exchange-documents"])
    except (KeyError, TypeError):
        return []
    out: list[LiveDoc] = []
    for wrapper in docs:
        doc = wrapper.get("exchange-document") if isinstance(wrapper, dict) else None
        if not isinstance(doc, dict):
            continue
        number = f"{doc.get('@country', '')}{doc.get('@doc-number', '')}{doc.get('@kind', '')}"
        if not number:
            continue
        bib = doc.get("bibliographic-data") or {}
        published = ""
        for ident in _as_list((bib.get("publication-reference") or {}).get("document-id")):
            if isinstance(ident, dict) and ident.get("@document-id-type") == "docdb":
                published = _iso(_text(ident.get("date")))
                break
        titles = [
            _text(t)
            for t in _as_list(bib.get("invention-title"))
            if isinstance(t, dict) and t.get("@lang", "en") == "en"
        ]
        title = next((t for t in titles if t), "") or _text(bib.get("invention-title"))
        applicants: list[str] = []
        parties = (bib.get("parties") or {}).get("applicants") or {}
        for app in _as_list(parties.get("applicant")):
            # ⚠️ Берём ОРИГИНАЛЬНОЕ написание: в epodoc-варианте к имени приклеен
            # хвост «[US]», а заявитель отсюда уходит в «кто это делает» как игрок.
            if isinstance(app, dict) and app.get("@data-format") == "original":
                name = _text((app.get("applicant-name") or {}).get("name"))
                if name and name not in applicants:
                    applicants.append(name)
        # ⚠️ `abstract` приезжает СПИСКОМ, когда реферат есть на нескольких языках,
        # и словарём, когда он один. Берём английский, иначе первый попавшийся.
        blocks = [b for b in _as_list(doc.get("abstract")) if isinstance(b, dict)]
        block = next((b for b in blocks if b.get("@lang") == "en"), blocks[0] if blocks else {})
        abstract = " ".join(_text(p) for p in _as_list(block.get("p"))).strip()
        out.append(
            LiveDoc(
                source="patents",
                title=title or number,
                url=f"https://worldwide.espacenet.com/patent/search?q=pn%3D{number}",
                published=published,
                publisher="; ".join(applicants[:3]) or "EPO OPS",
                lang="en",
                snippet=abstract[:600],
                domain="worldwide.espacenet.com",
            )
        )
    return out


@dataclass(slots=True)
class Activity:
    """Патентная активность по одной фразе: два окна и свежие публикации."""

    phrase: str
    recent: int | None = None
    before: int | None = None
    docs: list[LiveDoc] = field(default_factory=list)

    @property
    def measured(self) -> bool:
        return self.recent is not None and self.before is not None

    @property
    def growth(self) -> float | None:
        """Во сколько раз выросла активность. `None` — измерить нечем.

        ⚠️ Ноль в предыдущем окне — это не бесконечный рост, а отсутствие базы: у нового
        направления так будет всегда, и делить на него нельзя.
        """
        if not self.measured or not self.before:
            return None
        return self.recent / self.before

    def line(self) -> str:
        """Строка для карточки: оба числа, а не одно отношение."""
        if not self.measured:
            return ""
        return (
            f"патентных публикаций за два года — {self.recent}, за предыдущие два — {self.before}"
        )


async def _count(http: httpx.AsyncClient, phrase: str, since: date, until: date) -> int | None:
    from pelican.weak.cache import through_json

    q = _cql(phrase, since, until)

    async def fetch() -> int | None:
        r = await _get(http, "published-data/search", {"q": q, "Range": "1-1"})
        if r is None:
            return None
        # ⚠️ Замерено: на пустой выборке OPS отвечает 404 `SERVER.EntityNotFound`, а не
        # нулём в теле. Без этой ветки «патентов нет» приезжало бы как «измерить нечем»,
        # то есть ровно тем, чем ноль НЕ является, — и новое направление, у которого
        # база пуста по построению, молча выпадало бы из оси роста.
        if r.status_code == 404 and "EntityNotFound" in r.text:
            return 0
        if r.status_code != 200:
            return None
        try:
            return _total(r.json())
        except (KeyError, ValueError, TypeError):
            return None

    value = await through_json("patents_count", q, fetch)
    return value if isinstance(value, int) else None


async def _recent(http: httpx.AsyncClient, phrase: str) -> list[LiveDoc]:
    from pelican.weak.cache import through

    (mid, end), _ = windows()
    q = _cql(phrase, mid, end)

    async def fetch() -> list[LiveDoc]:
        r = await _get(http, "published-data/search/biblio", {"q": q, "Range": f"1-{PER_QUERY}"})
        if r is None or r.status_code != 200:
            return []
        try:
            return parse_biblio(r.json())
        except (KeyError, ValueError, TypeError):
            return []

    docs = await through(f"patents_biblio:{PER_QUERY}", q, fetch)
    return [d for d in docs if not asof.too_late(d.published)]


async def _gather(phrases: list[str], with_docs: bool) -> dict[str, Activity]:
    out: dict[str, Activity] = {}
    async with httpx.AsyncClient(timeout=TIMEOUT_S) as http:
        for phrase in phrases:
            if not phrase or phrase in out:
                continue
            (mid, end), (start, _) = windows()
            recent = await _count(http, phrase, mid, end)
            before = await _count(http, phrase, start, mid)
            docs = await _recent(http, phrase) if with_docs else []
            out[phrase] = Activity(phrase=phrase, recent=recent, before=before, docs=docs)
    return out


def activity(phrases: list[str], with_docs: bool = True) -> dict[str, Activity]:
    """Патентная активность по списку фраз. Пустой словарь, если источник выключен.

    ⚠️ Состояние троттлинга сбрасывается на каждый прогон: «закрыто» — приговор ПРОГОНУ,
    а не процессу, иначе один неудачный запрос навсегда выключил бы источник в
    `ask-serve`, который живёт неделями.
    """
    if not enabled():
        return {}
    reset()
    return asyncio.run(_gather(phrases, with_docs))
