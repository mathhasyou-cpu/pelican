"""Ядро технологии и его след в корпусе: объём и год первого появления.

## Зачем ядро, а не ярлык группы

Стадия `tech` называет приём В КОНКРЕТНОЙ СТАТЬЕ, и это часто её собственная
конфигурация: «ensemble of lstm, gru, and stacked autoencoders». Замер по корпусу
(5.49 млн работ, один проход): такая фраза встречается **1 раз** — в своей же статье,
а технология под ней, LSTM, — **7 482 раза с 2008 года**. Судить о зрелости по ярлыку
значит объявлять новым всё, что по-новому названо. Поэтому модель сводит ярлык к ядру
(«LSTM», «graph neural networks», «memory poisoning defense»), и счёт идёт по ядру.

## Две оси — и почему не доля свежих упоминаний

Замер на тех же названиях:

| ядро | работ | первое упоминание | доля за последний год |
|---|---|---|---|
| graph neural networks | 10 360 | 2017 | 46% |
| lstm | 7 482 | 2008 | 66% |
| federated learning | 13 575 | 2016 | 52% |
| memory poisoning | 104 | 2026 | 100% |

⚠️ **Доля за последний год зрелость не различает**: у зрелых технологий она 46–66%,
потому что сам корпус перекошен в свежие годы (срезы openalex набираются от нового к
старому, docs/science-backfill.md). Различают **объём** — это degree of visibility у
Yoon (2012) — и **год первого появления** — radical novelty у Rotolo и др. (2015).
Обе величины показываются модели как свидетельство, а не превращаются в порог: жанр
решает модель (категориальный класс — единственное, что в проекте замеренно разделяло).

⚠️ **Счёт лексический, и это не возврат n-граммы.** n-грамма отвергнута как ЕДИНИЦА
ВЫДАЧИ (docs/todo.md §43) — она неотделима от грамматики. Здесь единицу называет
модель, а строка лишь меряет её частоту в корпусе.

⚠️ **Короткое ядро ищется целым токеном.** Подстрока находит `rag` внутри
`fragment` и `leverage`, и аббревиатура получила бы объём зрелой технологии.

## Считаются ДВА следа: ядра и самого ярлыка

Одного числа не хватает: по следу ядра «зрелое применение зрелого ядра» и «новое
направление на зрелом ядре» неразличимы. Замер на кандидатах прогона по безопасности ИИ:

| ярлык | след ядра | след ярлыка | что это |
|---|---|---|---|
| ai security and threat protection | AI security: 272 | 312 | рыночная категория |
| hallucination detection … | hallucination mitigation: 254 | 0 | новое направление |
| federated learning for privacy… | federated learning: 13 575 | 2 913 | зрелое во всём |

Оба числа показываются модели одной строкой (`corpus_line`), решает она.
⚠️ **Не наступить:** правило «показывать след только голому ярлыку» отсюда убрано —
оно прятало след как раз у рыночных категорий (ярлык не голый, ядро огромное) и не
узнавало аббревиатуру («explainable ai» при ядре «XAI»).
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass

import httpx

from pelican.config import DATA_DIR, settings
from pelican.store import Store
from pelican.llm import LLMClient
from pelican.weak import asof

BATCH = 12
#: Ядро короче этого ищется целым токеном или его множественным числом (см. докстринг).
SHORT_CORE = 6

SYSTEM_PROMPT = """Each ITEM is the name of a technique as one research paper phrased it.
Often it is that paper's own configuration ("ensemble of LSTM, GRU and stacked
autoencoders", "hierarchical retrieval augmented generation for CTI annotation").

For each item return:
  item — echoed back verbatim
  core — the underlying TECHNOLOGY it is an instance of, as researchers name it across
         many papers: 1 to 4 words, lowercase except acronyms ("LSTM", "graph neural
         networks", "retrieval-augmented generation", "memory poisoning defense").
         Keep what makes it a distinct technology; drop the application domain and the
         paper's particular combination.

Answer for every item, in the given order."""


@dataclass(frozen=True, slots=True)
class Footprint:
    core: str
    works: int
    first_year: int | None
    last_year_works: int

    def phrase(self) -> str:
        if not self.works:
            return f'"{self.core}" appears in no indexed paper'
        if self.last_year_works < 0:
            # След восстановлен из карточки без доли за год (`weak.dataset._share`).
            return f'"{self.core}" appears in {self.works} indexed papers since {self.first_year}'
        return (
            f'"{self.core}" appears in {self.works} indexed papers since {self.first_year} '
            f"({self.last_year_works} of them in the last 12 months)"
        )


def corpus_line(core: Footprint, label: Footprint) -> str:
    """Свидетельство CORPUS: след ядра и след самого ярлыка одной строкой."""
    if core.core.strip().lower() == label.core.strip().lower():
        return f"CORPUS: {core.phrase()}."
    return (
        f"CORPUS: the core technology {core.phrase()}; papers about this specific "
        f"combination of words, {label.phrase()}."
    )


def _schema(n: int) -> dict:
    item = {
        "type": "object",
        "properties": {"item": {"type": "string"}, "core": {"type": "string"}},
        "required": ["item", "core"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {"items": {"type": "array", "items": item, "minItems": n, "maxItems": n}},
        "required": ["items"],
        "additionalProperties": False,
    }


async def _cores(labels: list[str]) -> list[str]:
    client = LLMClient(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        timeout_s=settings.llm_timeout_s,
    )
    out: list[str] = []
    async with httpx.AsyncClient() as http:
        for start in range(0, len(labels), BATCH):
            chunk = labels[start : start + BATCH]
            got = await client.json_completion(
                http,
                system=SYSTEM_PROMPT,
                user="\n".join(f"{i + 1}. {x}" for i, x in enumerate(chunk)),
                schema=_schema(len(chunk)),
                name="cores",
                max_tokens=40 * len(chunk) + 200,
            )
            items = got.get("items", [])
            for i, label in enumerate(chunk):
                core = " ".join(str((items[i] if i < len(items) else {}).get("core") or "").split())
                out.append(core or label)
    return out


def cores(labels: list[str]) -> list[str]:
    return asyncio.run(_cores(labels)) if labels else []


#: Служебные слова: в совпадении по словам они не участвуют — иначе «for» и «of»
#: найдутся в каждой работе корпуса.
_STOP = frozenset(
    [
        "a",
        "an",
        "the",
        "of",
        "for",
        "and",
        "or",
        "in",
        "on",
        "with",
        "via",
        "to",
        "by",
        "from",
        "using",
        "based",
        "as",
        "at",
        "into",
        "over",
    ]
)


def significant(name: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9+#-]{3,}", name.lower()) if w not in _STOP]


#: Сколько значимых слов ярлыка участвует в совпадении. ⚠️ Догадка: берутся самые
#: длинные — они различительнее.
LOOSE_WORDS = 4
#: Сколько имён сохраняется в кэш следа за раз: прерванный запрос не теряет посчитанное.
SCAN_CHUNK = 24


def match_sql(match: str) -> str:
    """Условие SQL по строке `_match` — для запросов, собирающих много имён разом."""
    if not match:
        return "FALSE"
    return "tsv @@ to_tsquery('simple', '" + match.replace("'", "''") + "')"


def _tokens(text: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", text.lower()) if t]


def _match(name: str, loose: bool = False) -> str:
    """Полнотекстовый запрос «работа говорит про это» — строка для `to_tsquery('simple', …)`.

    ⚠️ Две меры, и это не прихоть. **Ядро ищется фразой**: «federated learning» —
    устоявшееся имя; токены подряд, последний префиксом (`federated <-> learn:*`), чтобы
    ловить и «learnings». Короткое ядро («rag», «gnn») — токен или его множественное
    число. **Ярлык ищется по НАБОРУ СЛОВ**: ярлык длиной 2–8 слов («security and defense
    for large language models») дословно не пишет никто, и фраза давала бы 0 у всех
    кандидатов подряд — мера, одинаковая для всех, не мера.

    Полнотекст вместо подстроки (`ILIKE '%…%'`) — замером: на 160 именах из прошлых ТОП и
    15 млн работ ранговая корреляция числа работ 0.999 у ядер и 0.96 у ярлыков, медиана
    отношения 1.00, а время — 42 с против 1249 с полного прохода (`scripts/
    measure_pg_footprint.py`). Расхождения двух видов: токены ловят написание через дефис
    и раздельно («multi-key» и «multi key»), а подстрока — слово внутри составного
    («security» в «cybersecurity»).
    """
    low = name.lower()
    words = significant(low)
    if loose and len(words) > 1:
        chosen = sorted(sorted(set(words), key=len, reverse=True)[:LOOSE_WORDS])
        parts = [t for w in chosen for t in _tokens(w)]
        return " & ".join(f"{t}:*" for t in parts)
    toks = _tokens(low)
    if not toks:
        return ""
    if len(low) < SHORT_CORE and len(toks) == 1:
        return f"{toks[0]} | {toks[0]}s"
    return " <-> ".join(toks[:-1] + [f"{toks[-1]}:*"])


#: След в корпусе, посчитанный раньше: имя и мера → числа. Корпус растёт медленно, а
#: проход стоит секунды на имя, поэтому повтор запроса не платит за то же дважды.
#: ⚠️ Неделя — догадка: за неделю счётчики сдвигаются на проценты, а прогон замера по
#: шести доменам укладывается в часы.
CACHE = DATA_DIR / "footprints.json"
CACHE_TTL_S = 7 * 24 * 3600
_cache: dict[str, list] | None = None
#: Размер корпуса, при котором кэш собран. ⚠️ Кэш обязан сам замечать выросший корпус —
#: тот же приём, что у масок `weak.corpus.future_ids`: после бэкфилла openalex след
#: «работ у ядра на срезе 2024» иначе остаётся заниженным на неделю, и правило «снести
#: файл рукой» отказывает ровно так, как отказывает любое правило, о котором нельзя
#: узнать, что его забыли. Но, в отличие от масок, сброс не на любое изменение: суточный
#: сбор добавляет доли процента, а полный пересчёт следа стоит минуты на каждый запрос.
CACHE_STAMP = DATA_DIR / "footprints.count"
#: Доля роста корпуса, с которой кэш сбрасывается. ⚠️ Догадка: суточный сбор — ~0.02%,
#: докачка одного тонкого месяца openalex (docs/todo.md §84) — ~1.5%.
CACHE_DRIFT = 0.01
_checked = False
#: Доля работ с компанией среди авторов — свой кэш с тем же ключом по срезу и тем же
#: сбросом по штампу; формат `[с компанией, с институтами, ts]`.
INDUSTRY_CACHE = DATA_DIR / "industry.json"
#: Институты OpenAlex с `type:company` (`scripts/fetch_openalex_companies.py`).
COMPANIES = DATA_DIR / "openalex-companies.json"
#: Меньше работ с институтами — доля не измерена (`None`). ⚠️ Догадка.
INDUSTRY_MIN_WORKS = 20


def _cache_check(store: Store) -> None:
    """Сбросить кэш следа, если корпус вырос сильнее `CACHE_DRIFT` с момента его сборки."""
    global _cache
    from pelican.weak.corpus import science_count

    total = science_count(store)
    try:
        held = int(CACHE_STAMP.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        held = 0
    if held and total <= held * (1 + CACHE_DRIFT):
        return
    # Нет штампа — когда собран кэш, неизвестно: он сбрасывается один раз.
    CACHE.unlink(missing_ok=True)
    INDUSTRY_CACHE.unlink(missing_ok=True)
    _cache = {}
    try:
        CACHE_STAMP.parent.mkdir(parents=True, exist_ok=True)
        CACHE_STAMP.write_text(str(total), encoding="utf-8")
    except OSError:
        pass


def _cache_load() -> dict[str, list]:
    global _cache
    if _cache is None:
        try:
            payload = json.loads(CACHE.read_text(encoding="utf-8"))
            fresh = time.time() - CACHE_TTL_S
            _cache = {k: v for k, v in payload.items() if v[3] > fresh}
        except (OSError, ValueError, TypeError, IndexError):
            _cache = {}
    return _cache


def _cache_save() -> None:
    try:
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps(_cache_load(), ensure_ascii=False), encoding="utf-8")
    except OSError:
        # Кэш — ускорение, а не хранилище: отказ диска не должен ронять запрос.
        pass



#: Срезы openalex (`payload_json.field`), которыми считается след; `None` — весь корпус.
#: Состояние процесса, как `asof.AS_OF`, а не параметр вызова: бэктест меряет прирост
#: следа между двумя датами, и оба конца обязаны считаться по ОДНОМУ набору срезов —
#: CS и Engineering в базе начинаются с 2023-01, и под срезом 2022 их нет вовсе, а через
#: два года их 8 млн строк. Это включение источника, а не рост, и медиана домена его не
#: снимает: софтовому ядру оно даёт ×N, материаловедческому — ничего. arxiv входит всегда.
SLICES: frozenset[str] | None = None


def _cutoff() -> str:
    """Хвост условия: под срезом корпус обрезается по дату среза, иначе не обрезается."""
    return "" if not asof.active() else f" AND observed_at <= DATE '{asof.today().isoformat()}'"


def _slice() -> str:
    """Хвост условия по `SLICES`: arxiv целиком плюс названные срезы openalex."""
    if SLICES is None:
        return ""
    fields = ", ".join(f"'{f}'" for f in sorted(SLICES))
    return (
        f" AND (source = 'arxiv' OR field IN ({fields}))"
    )


def _key(match: str) -> str:
    """Ключ кэша следа: срез времени И набор срезов корпуса — иначе след «все срезы» под
    2024 перепутался бы со следом «лёгкие срезы» под 2022 для тех же слов."""
    if SLICES is None:
        return asof.key(match)
    return asof.key(f"{'|'.join(sorted(SLICES))}::{match}")


def _year_ago() -> str:
    """Граница «последнего года» — от среза, а не от нашего сегодня."""
    if not asof.active():
        return "now() - interval '365 days'"
    return f"DATE '{asof.today().isoformat()}' - interval '365 days'"


def _scan(store: Store, specs: list[tuple[str, str]]) -> list[Footprint]:
    """Один проход по научному корпусу на все условия сразу.

    ⚠️ Проход дорогой (5.5 млн строк, ~7 с на десяток условий), поэтому ядра и ярлыки
    считаются ВМЕСТЕ: два отдельных вызова удваивали время запроса на ровном месте.
    ⚠️ 1 января выброшено из года первого появления: у openalex это заглушка вместо
    даты (docs/sources-science.md), и технология «появлялась» бы на год раньше.
    """
    if not specs:
        return []
    global _checked
    if not _checked:
        # Раз на процесс: один `count(*)` против штампа, дешевле любого следа.
        _cache_check(store)
        _checked = True
    known = _cache_load()
    # ⚠️ Ключ кэша несёт срез: те же слова на срезе 2024 дают ДРУГОЙ след, и общий ключ
    # отдал бы бэктесту сегодняшние числа (`weak.asof`).
    todo = [s for s in specs if _key(s[1]) not in known]
    if todo and len(todo) < len(specs):
        # Считаем только новые имена, остальные берём из кэша.
        _scan(store, todo)
        known = _cache_load()
        todo = []
    if not todo:
        return [Footprint(name, *known[_key(match)][:3]) for name, match in specs]
    if len(specs) > SCAN_CHUNK:
        # ⚠️ Имена считаются пачками: широкий промежуточный результат (булев столбец на
        # каждое имя поверх 5.5 млн строк) переполнял память сервера, и прогон на 130
        # названиях убивала система.
        out: list[Footprint] = []
        for start in range(0, len(specs), SCAN_CHUNK):
            out.extend(_scan(store, specs[start : start + SCAN_CHUNK]))
        return out
    # Запрос на имя, а не один проход на пачку: GIN-индекс отдаёт только совпавшие строки
    # (медиана 0.16 с на имя), а общий проход читал бы все 15 млн строк ради каждой пачки.
    sql = (
        "SELECT count(*), (min(extract(year FROM observed_at)) FILTER (WHERE NOT "
        "(extract(month FROM observed_at) = 1 AND extract(day FROM observed_at) = 1)))::int, "
        f"count(*) FILTER (WHERE observed_at >= {_year_ago()}) "
        f"FROM works WHERE tsv @@ to_tsquery('simple', ?){_cutoff()}{_slice()}"
    )
    got = []
    for name, match in specs:
        row = store.conn.execute(sql, [match]).fetchone() if match else (0, None, 0)
        got.append(Footprint(name, int(row[0] or 0), row[1], int(row[2] or 0)))
    now = time.time()
    for (_name, match), f in zip(specs, got, strict=True):
        known[_key(match)] = [f.works, f.first_year, f.last_year_works, now]
    _cache_save()
    return got


def footprints(store: Store, names: list[str], loose: bool = False) -> dict[str, Footprint]:
    """Имя → след в корпусе. `loose` — совпадение по набору слов (см. `_match`)."""
    uniq = sorted({n.strip() for n in names if n.strip()})
    specs = [(n, _match(n, loose)) for n in uniq]
    return {f.core: f for f in _scan(store, specs)}


def footprints_pair(
    store: Store, exact: list[str], loose: list[str]
) -> tuple[dict[str, Footprint], dict[str, Footprint]]:
    """Следы ядер (точной фразой) и ярлыков (по набору слов) — ОДНИМ проходом."""
    exact_uniq = sorted({n.strip() for n in exact if n.strip()})
    loose_uniq = sorted({n.strip() for n in loose if n.strip()})
    specs = [(n, _match(n, False)) for n in exact_uniq]
    specs += [(n, _match(n, True)) for n in loose_uniq]
    got = _scan(store, specs)
    return (
        {f.core: f for f in got[: len(exact_uniq)]},
        {f.core: f for f in got[len(exact_uniq) :]},
    )


_companies: frozenset[str] | None = None


def companies() -> frozenset[str]:
    """Имена институтов-компаний OpenAlex; пусто, если файл не скачан."""
    global _companies
    if _companies is None:
        try:
            _companies = frozenset(json.loads(COMPANIES.read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError):
            _companies = frozenset()
    return _companies


def _industry_load() -> dict[str, list]:
    try:
        payload = json.loads(INDUSTRY_CACHE.read_text(encoding="utf-8"))
        fresh = time.time() - CACHE_TTL_S
        return {k: v for k, v in payload.items() if v[2] > fresh}
    except (OSError, ValueError, TypeError, IndexError):
        return {}


def _industry_scan(store: Store, specs: list[tuple[str, str]]) -> list[tuple[int, int]]:
    """Для каждого условия — (работ с компанией среди авторов, работ с институтами).

    ⚠️ Институты авторов в `works` не хранятся (корпус перелит без полезной нагрузки
    openalex), поэтому признак не считается, а ПАДАЕТ: тихий `None` у всех имён сделал бы
    модель списка, которая его берёт, молча другой. Нужен он только ей
    (`WEAK_LISTED_IN_ASK`) и бэктестам, а в живой выдаче модель списка выключена.
    """
    raise RuntimeError(
        "доля работ с компанией не считается: институты авторов не перелиты в works"
    )


def industry_share(store: Store, names: list[str], loose: bool = False) -> dict[str, float | None]:
    """Имя → доля работ (под срезом) с компанией среди авторов; `None` — работ с институтами
    меньше `INDUSTRY_MIN_WORKS` или список компаний не скачан.

    Приём — university–industry co-publication как индикатор коммерциализации (Wong &
    Singh 2013, Scientometrics; Triple Helix у Leydesdorff). Знаменатель — только работы с
    институтами: arXiv их не несёт, и «нет института» — не «нет компании».
    """
    uniq = sorted({n.strip() for n in names if n.strip()})
    if not uniq or not companies():
        return dict.fromkeys(uniq)
    specs = [(n, _match(n, loose)) for n in uniq]
    known = _industry_load()
    todo = [s for s in specs if _key(s[1]) not in known]
    if todo:
        now = time.time()
        for start in range(0, len(todo), SCAN_CHUNK):
            chunk = todo[start : start + SCAN_CHUNK]
            for (_n, match), (num, den) in zip(chunk, _industry_scan(store, chunk), strict=True):
                known[_key(match)] = [num, den, now]
        try:
            INDUSTRY_CACHE.parent.mkdir(parents=True, exist_ok=True)
            INDUSTRY_CACHE.write_text(json.dumps(known, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
    out: dict[str, float | None] = {}
    for name, match in specs:
        num, den, _ts = known[asof.key(match)]
        out[name] = num / den if den >= INDUSTRY_MIN_WORKS else None
    return out
