"""Направления поиска из всплеска в корпусе, а не из знаний модели.

## Зачем

Направления открытого запроса раскрывает модель (`ask.expand`), и называет она то, что
уже знает, — то есть уже сложившееся. Это и жалоба экспертов «90% — зрелые технологии», и
заглядывание в будущее в ретро-тесте: на срезе 2017-12 gemma раскрыла «технологии в ИИ» в
«мультимодальные LLM с длинным контекстом» и «диффузионные модели», на 2021-03 — прямо в
«LoRA» и «RLHF» (docs/todo.md §112). Новое по определению то, чего модель не видела, и
искать его можно только в самом корпусе.

## Приём

**Детекция всплеска по частоте в документах** (burst detection; Kleinberg 2002, TrendNets —
«тема всплескивает, если её много обсуждают в этом окне и мало в прошлых»):
словосочетания названий работ arXiv за последние 12 месяцев против 24 месяцев до них.
Мера — **log-odds с информативным дирихле-приором** (Monroe, Colaresi & Quinn 2008,
«Fightin' words»), z-оценка: устойчива на малых частотах, где простое отношение долей
шумит. Затем фильтр по области: словосочетание остаётся, если по эмбеддингу оно ближе
других к запросу (`AREA_POOL` ближайших). Порядок внутри области — по z.

⚠️ **Только arXiv.** OpenAlex в корпусе по CS и Engineering собран с 2023-01
(docs/science-backfill.md): в окне до 2023 он заполнил бы «прежнее» окно не той наукой, и
всплеском читался бы сам бэкфилл. arXiv — к тому же фронт препринтов, где новое видно раньше.
⚠️ **Только названия работ.** Аннотация в корпусе обрезана (~300 знаков), и её общие
обороты («in this paper we propose») всплывают сильнее технических.

Расхождение с каноном: у Yoon (2012, карта KIM) слабый сигнал — квадрант «частота ниже
медианы, рост выше медианы». По всему корпусу этот квадрант тонет в шуме чужих наук
(«skin lesion», «ligo», «docker» с частотой 7–9), а графовые сети на 2017-12 (19 против 1)
уходят в «сильные» — медиана частоты там около двух. Поэтому сначала область, потом порядок.

⚠️ Замер (`scripts/measure_retro_reach.py`, 14 строк ИИ ретро-теста): на 2017-12 графовые
свёрточные сети — 7-е место в области ИИ и в направлениях запроса; самообучение на 2020-06 —
30-е; остальные цели ниже порога `MIN_RECENT` — в корпусе на дату у них 1–3 названия за год,
это потолок корпуса. На запросах ТЗ флаг правило включения не прошёл (docs/todo.md §114).
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np

from pelican.config import DATA_DIR
from pelican.store import Store

#: Окно «свежего» и окно «прежнего», дней. ⚠️ Догадка: год против двух лет до него.
RECENT_DAYS = 365
BEFORE_DAYS = 2 * 365
#: Сколько раз словосочетание должно встретиться в свежем окне. ⚠️ Догадка: ниже — шум
#: опечаток и единичных работ; цели ретро-теста с 1–3 названиями за год им отсекаются.
MIN_RECENT = 5
#: Рост доли не меньше, чем вдвое: иначе верх z-оценки занимают объёмные темы, которые
#: растут вместе с корпусом («machine translation» 164 против 126). ⚠️ Догадка.
MIN_GROWTH = 2.0
#: Сила приора Монро. ⚠️ Догадка, взята из примеров статьи (порядок 0.01 от частоты).
ALPHA = 0.01
#: Сколько кандидатов по z пускать в фильтр области: ограничивает число эмбеддингов.
GLOBAL_POOL = 4000
#: Сколько ближайших к запросу оставлять. ⚠️ Догадка.
AREA_POOL = 300
#: Направлений из всплеска в запросе.
FACETS = 6

_STOP = frozenset(
    ["a", "an", "the", "of", "for", "and", "in", "on", "with", "to", "via", "from", "by", "at", "as", "is", "are", "be", "using", "based", "towards", "toward", "its", "we", "our", "under", "over", "into", "new", "learning", "deep", "neural", "network", "networks", "model", "models", "method", "methods", "approach", "approaches", "data", "analysis", "study", "more", "what", "does", "how", "improving", "improved", "efficient", "novel", "towards", "case", "framework", "system", "systems"]
)
_WORD = re.compile(r"[a-z][a-z0-9\-]+")


@dataclass(frozen=True, slots=True)
class Burst:
    phrase: str
    recent: int  # работ с ним в свежем окне
    before: int  # в прежнем
    z: float


def phrases(title: str) -> set[str]:
    """Словосочетания в 2–3 слова без служебных слов по краям."""
    words = _WORD.findall(title.lower())
    out: set[str] = set()
    for n in (2, 3):
        for i in range(len(words) - n + 1):
            g = words[i : i + n]
            if g[0] in _STOP or g[-1] in _STOP:
                continue
            out.add(" ".join(g))
    return out


def counts(
    store: Store, today: date
) -> tuple[Counter[str], Counter[str], int, int, int, int]:
    """(свежее, прежнее, работ в свежем, работ в прежнем, вхождений в свежем, в прежнем)
    по названиям arXiv.

    Кэш на диске по дате: выборка сотен тысяч названий по сети идёт минуту, а дата среза
    и «сегодня» стенда от прогона к прогону одни и те же. ⚠️ Как у `corpus.future_ids`,
    рядом хранится число работ окна, и выросший корпус пересобирает кэш сам.

    ⚠️ Последние два числа — суммы ПО ВСЕМУ словарю окна, до фильтра: в кэш идут только
    частые свежие фразы, а знаменатели log-odds у Монро — полный объём окна. Сумма по
    отфильтрованному словарю теряла угасшие фразы прежнего окна и смещала z (PEL-D-05).
    """
    mid = today - timedelta(days=RECENT_DAYS)
    start = mid - timedelta(days=BEFORE_DAYS)
    where = (
        "FROM works WHERE source = 'arxiv' AND NOT junk "
        f"AND observed_at >= DATE '{start.isoformat()}' AND observed_at <= DATE '{today.isoformat()}'"
    )
    total = int(store.conn.execute(f"SELECT count(*) {where}").fetchone()[0])
    cache = DATA_DIR / f".burst-{today.isoformat()}.json"
    try:
        held = json.loads(cache.read_text(encoding="utf-8"))
        if held["total"] == total:
            return (
                Counter(held["recent"]),
                Counter(held["before"]),
                held["n_recent"],
                held["n_before"],
                held["sum_recent"],
                held["sum_before"],
            )
    except (OSError, ValueError, KeyError, TypeError):
        pass
    recent: Counter[str] = Counter()
    before: Counter[str] = Counter()
    n_recent = n_before = 0
    for text, observed in store.conn.execute(f"SELECT term_raw, observed_at {where}").fetchall():
        got = phrases((text or "").split(". ")[0])
        if observed.date() >= mid:
            recent.update(got)
            n_recent += 1
        else:
            before.update(got)
            n_before += 1
    sum_recent, sum_before = sum(recent.values()), sum(before.values())
    # Единичные словосочетания в кэш не идут: их миллионы, а порог MIN_RECENT их отбросит.
    recent = Counter({g: c for g, c in recent.items() if c >= MIN_RECENT})
    before = Counter({g: before[g] for g in recent if before[g]})
    body = {
        "total": total,
        "recent": recent,
        "before": before,
        "n_recent": n_recent,
        "n_before": n_before,
        "sum_recent": sum_recent,
        "sum_before": sum_before,
    }
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(body), encoding="utf-8")
    except OSError:
        pass
    return recent, before, n_recent, n_before, sum_recent, sum_before


def score(
    recent: Counter[str],
    before: Counter[str],
    n_recent: int,
    n_before: int,
    sum_recent: int | None = None,
    sum_before: int | None = None,
) -> list[Burst]:
    """Всплески по убыванию z (Монро): рост не меньше `MIN_GROWTH` и частота не ниже порога.

    `sum_*` — вхождений по ВСЕМУ словарю окна (`counts`); без них — по переданным
    словарям, что верно, только если те не отфильтрованы.
    """
    prior = recent + before
    a_r = sum(recent.values()) if sum_recent is None else sum_recent
    a_b = sum(before.values()) if sum_before is None else sum_before
    a0 = a_r + a_b
    out = []
    for g, r in recent.items():
        if r < MIN_RECENT:
            continue
        b = before[g]
        if (r / max(n_recent, 1)) < MIN_GROWTH * ((b + 1) / max(n_before, 1)):
            continue
        ag = ALPHA * prior[g]
        delta = math.log((r + ag) / (a_r + ALPHA * a0 - r - ag)) - math.log(
            (b + ag) / (a_b + ALPHA * a0 - b - ag)
        )
        out.append(Burst(g, r, b, delta / math.sqrt(1 / (r + ag) + 1 / (b + ag))))
    out.sort(key=lambda x: -x.z)
    return out


def _distinct(items: list[Burst], n: int) -> list[Burst]:
    """Без повторов одного направления: «generative adversarial» и «generative adversarial
    nets» — одно. Словосочетание пропускается, если его слова — подмножество взятого или наоборот."""
    taken: list[tuple[Burst, set[str]]] = []
    for b in items:
        words = set(b.phrase.split())
        if any(words <= w or w <= words for _, w in taken):
            continue
        taken.append((b, words))
        if len(taken) == n:
            break
    return [b for b, _ in taken]


def facets(store: Store, area_texts: list[str], today: date, n: int = FACETS) -> list[Burst]:
    """`n` всплесков, ближайших к области запроса (тексты области — запрос и `area_en`)."""
    from pelican.weak.ground import embed_texts

    bursts = score(*counts(store, today))[:GLOBAL_POOL]
    if not bursts:
        return []
    vectors = embed_texts([b.phrase for b in bursts])
    area = embed_texts([t for t in area_texts if t.strip()])
    near = (vectors @ area.T).max(axis=1)
    keep = np.argsort(-near)[:AREA_POOL]
    pool = sorted((bursts[i] for i in keep), key=lambda b: -b.z)
    return _distinct(pool, n)
