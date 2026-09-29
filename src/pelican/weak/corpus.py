"""Что из научного корпуса вообще годится в поиск, а что — хаб без содержания.

## Замер: 303 349 строк openalex — это не работы

Первый же прогон привязки (`scripts/measure_grounding.py`) показал, что верх
выдачи занимают одни и те же короткие строки: «Machine Learning» взята 90 раз на
500 запросов, «Deep Learning» — 41, «neural computation» — 38, «Monnaie et
finance écologiques» — 54. Рядом с ними `ce`, `spe`, `netzen`, `Wetting agents`.

Разбор по базе:

| | строк | ср. длина аннотации | пустая аннотация |
|---|---|---|---|
| `term_raw` ≥ 60 знаков | 4 052 323 | 888 | 26% |
| `term_raw` < 60 знаков | **303 349** | **2.2** | **92%** |

То есть у короткой строки за заголовком нет ничего. Состав по типу работы:
`reference-entry` 85 678, `article` 70 021, `dataset` 44 720, `book-chapter`
37 867, `book` 23 206, `paratext` 12 484. Верхний класс — **словарные статьи**
вроде «Machine Learning», и это не публикация, а рубрика.

⚠️ **Это не порог, а правило о содержании.** Строка без аннотации не «слабее»
остальных — по ней нечего считать вовсе: ни новизны, ни связности, ни темы.
Поэтому она убирается, а не взвешивается. У `arxiv` таких строк 4 на 1 138 210,
то есть правило целиком про `openalex`.

## Почему хабом становится именно короткая строка

Приём, который здесь срабатывает против нас, — **length bias** двухбашенного
эмбеддера: короткий запрос ближе к короткому документу просто потому, что оба
несут мало слов. Замер по тому же прогону: корреляция длины запроса и длины
найденного +0.59 на плече с английским переводом (короткие запросы) и +0.25 на
русском (длинные). Косинус при этом РАСТЁТ — у плеча `en-llm+expand` медиана
первого места 0.769 против 0.639 у русского, и при этом 57% находок короче
шестидесяти знаков против 11%.

⚠️ **Отсюда главное: балл косинуса здесь не мера качества и приговором быть не
может.** Ровно то же правило записано в [search](../../docs/search.md) — у разных
тем «похоже» начинается на разных числах.

Хабность лечится и поправкой (CSLS в `pipeline/cluster.py`, local scaling в
`scripts/search_corpus.py`), но здесь она лечится ДЕШЕВЛЕ и честнее: хаб — это
строка без содержания, и её надо убрать из корпуса, а не штрафовать балл.
"""

from __future__ import annotations

import time
from datetime import date

import numpy as np

from pelican.config import DATA_DIR
from pelican.store import Store

#: Ниже этой длины `term_raw` — это голый заголовок без аннотации (замер выше:
#: средняя аннотация 2.2 знака, пустая у 92%).
MIN_TERM_CHARS = 60

#: Типы работ openalex, у которых текста нет по самой их природе. ⚠️ `article` в
#: список НЕ входит: короткие статьи бывают настоящими, и их отсекает длина.
JUNK_TYPES = ("reference-entry", "paratext", "dataset", "book", "book-chapter")



def is_junk(source: str, term_raw: str, work_type: str | None) -> bool:
    """Строка без содержания: голый заголовок или тип работы, у которого текста нет.

    Считается один раз — при записи работы (`works.junk`), а не на каждом запросе:
    правило одно на маску поиска (`junk_ids`) и очередь модели (`Store.untagged_science`).
    """
    return source == "openalex" and (len(term_raw) < MIN_TERM_CHARS or work_type in JUNK_TYPES)


_JUNK_SQL = "SELECT id FROM works WHERE junk"


#: Кэш маски на диске. ⚠️ Выборка 303 тысяч id по сети стоит ~20 с, и платить их на
#: КАЖДЫЙ открытый запрос нельзя — жюри вводит запросы вживую. Сутки жизни: строки,
#: пришедшие за день, в маску не попадут, а это максимум пара хабов в выдаче —
#: дешевле, чем 20 с на каждый запрос.
CACHE = DATA_DIR / ".junk-ids.npy"
CACHE_TTL_S = 24 * 3600


def junk_ids(store: Store, fresh: bool = False) -> np.ndarray:
    """Отсортированные id строк, которые в поиск не пускаются.

    Отсортированные — потому что маска в `top_k` ищет по ним `searchsorted`, а
    не строит множество на каждый шард.
    """
    if not fresh and CACHE.exists() and time.time() - CACHE.stat().st_mtime < CACHE_TTL_S:
        return np.load(CACHE, allow_pickle=False)
    rows = store.conn.execute(_JUNK_SQL).fetchall()
    ids = np.fromiter((int(r[0]) for r in rows), dtype=np.int64, count=len(rows))
    ids.sort()
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    np.save(CACHE, ids)
    return ids


_FUTURE_SQL = (
    "SELECT id FROM works WHERE source IN ('arxiv', 'openalex') AND observed_at > DATE '{day}'"
)
_FUTURE_COUNT_SQL = (
    "SELECT count(*) FROM works WHERE source IN ('arxiv', 'openalex') "
    "AND observed_at > DATE '{day}'"
)


_SCIENCE_COUNT_SQL = "SELECT count(*) FROM works WHERE source IN ('arxiv', 'openalex')"


def science_count(store: Store) -> int:
    """Сколько всего работ в научном корпусе (обоих источников)."""
    return int(store.conn.execute(_SCIENCE_COUNT_SQL).fetchone()[0])


def future_ids(store: Store, as_of: date) -> np.ndarray:
    """Отсортированные id работ, вышедших ПОСЛЕ среза: в бэктесте их не существует.

    ⚠️ Маска, а не фильтр запроса: поиск идёт по шардам на диске, и единственная дверь,
    через которую из него можно что-то убрать, — `exclude` в `weak.shards.top_k`.

    Кэш на диске по дате среза: выборка миллиона id по сети идёт около минуты, а срез в
    бэктесте один и тот же от прогона к прогону.

    ⚠️ **Кэш обязан сам замечать выросший корпус, и раньше не замечал.** Правило «после
    бэкфилла снести файл рукой» отказало ровно так, как отказывает любое правило, о
    котором нельзя узнать, что его забыли: замер 2026-09-17 показал в кэше среза
    2024-09-15 три миллиона id при 5.6 млн работ после этой даты в базе — то есть
    2.5 миллиона работ ИЗ БУДУЩЕГО в маску не попадали и были видны детектору. Это ровно
    тот look-ahead bias, ради которого написан `weak.asof`, и молчал он полностью.
    Поэтому рядом с id хранится ЧИСЛО строк на момент сборки, и расхождение пересобирает
    маску само: один `count(*)` дешевле минуты выборки и несравнимо дешевле бэктеста,
    посчитанного по будущему.
    """
    cache = DATA_DIR / f".future-ids-{as_of.isoformat()}.npz"
    total = int(store.conn.execute(_FUTURE_COUNT_SQL.format(day=as_of.isoformat())).fetchone()[0])
    if cache.exists():
        try:
            held = np.load(cache, allow_pickle=False)
            if int(held["total"][0]) == total:
                return held["ids"]
        except (OSError, ValueError, KeyError, IndexError):
            pass  # битый кэш — как будто его нет
    rows = store.conn.execute(_FUTURE_SQL.format(day=as_of.isoformat())).fetchall()
    ids = np.fromiter((int(r[0]) for r in rows), dtype=np.int64, count=len(rows))
    ids.sort()
    cache.parent.mkdir(parents=True, exist_ok=True)
    np.savez(cache, ids=ids, total=np.array([total], dtype=np.int64))
    return ids
