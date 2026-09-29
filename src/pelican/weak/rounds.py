"""Денежный след направления по окну дат: сколько заголовков о раундах и на какие суммы.

Инструмент для двух вещей сразу — исход бэктеста «деньги ПОСЛЕ среза» и признаки «деньги
ДО среза» (`scripts/backtest_classifier.py`). Приём — семейство *financing recency /
momentum / breadth* из панелей Crunchbase (Interpretable ML for Predicting Startup Funding,
arXiv:2510.09465): деньги предсказываются деньгами, и первый вопрос к любой денежной модели —
бьёт ли она одиночную «деньги вчера».

⚠️ Заголовки, а не сделки: Crunchbase/Dealroom платны, GDELT DOC API с этой машины отдаёт
только «limit requests» (docs/sources-rejected.md), SEC Form D — только США и требует
сведения юр. имён (docs/todo.md). Поэтому единица — заголовок Google News с денежным словом
(`dataset.FUNDING_WORDS`), сумма — распознанная в заголовке (`parse_usd`), а не в сделке.

Окно — явное `after:… before:…` (операторы RSS проверены: отдаёт заголовки строго в окне),
поэтому один и тот же вызов считает и прошлое, и будущее относительно среза — срез `asof`
здесь НЕ применяется, окна несут даты сами.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta

from pelican.weak import asof
from pelican.weak.dataset import FUNDING_WORDS
from pelican.weak.live import LiveDoc, news_many
from pelican.weak.money import TEMPLATES

#: Заголовков держим на запрос: RSS отдаёт до 100, и обрезка продукта (10) здесь слепит
#: инструмент — у 161 из 196 кандидатов бэктеста исход был 0 именно из-за неё.
LIMIT = 100

#: Сумма в заголовке: «$51.6 Million», «$20M», «$1.2bn», «$500K». ⚠️ Только доллары: суммы в
#: евро/рупиях/юанях остаются в счётчике `n`, но не в `usd`.
_USD = re.compile(
    r"\$\s?(\d{1,4}(?:[.,]\d{1,3})?)\s?(billion|million|thousand|bn|mn|m|k)\b", re.IGNORECASE
)
_SCALE = {
    "billion": 1e9,
    "bn": 1e9,
    "million": 1e6,
    "mn": 1e6,
    "m": 1e6,
    "thousand": 1e3,
    "k": 1e3,
}


@dataclass(frozen=True)
class MoneyTrace:
    label: str
    n: int  # заголовков с денежным словом (после дедупа)
    usd: float  # сумма распознанных сумм, доллары
    publishers: int  # уникальных изданий среди денежных заголовков
    last_date: str  # самый поздний денежный заголовок в окне, ISO-дата или пусто

    @property
    def empty(self) -> bool:
        return self.n == 0


def parse_usd(title: str) -> float | None:
    """Первая долларовая сумма заголовка в долларах; `None` — суммы нет."""
    m = _USD.search(title)
    if not m:
        return None
    number = float(m.group(1).replace(",", "."))
    return number * _SCALE[m.group(2).lower()]


def is_funding(title: str) -> bool:
    return any(w in FUNDING_WORDS for w in title.lower().replace("-", " ").split())


def window(start: date, end: date) -> str:
    """Окно `[start, end)` в операторах Google News."""
    return f"after:{start.isoformat()} before:{end.isoformat()}"


def summarize(label: str, docs: list[LiveDoc]) -> MoneyTrace:
    """Денежные заголовки пачки → след. Дедуп по URL, затем по заголовку без регистра."""
    seen_url: set[str] = set()
    seen_title: set[str] = set()
    n = 0
    usd = 0.0
    publishers: set[str] = set()
    last = ""
    for d in docs:
        key_t = " ".join(d.title.lower().split())
        if (d.url and d.url in seen_url) or key_t in seen_title:
            continue
        seen_url.add(d.url)
        seen_title.add(key_t)
        if not is_funding(d.title):
            continue
        n += 1
        usd += parse_usd(d.title) or 0.0
        publishers.add((d.domain or d.publisher or "").lower())
        if d.published and d.published[:10] > last:
            last = d.published[:10]
    return MoneyTrace(label, n, usd, len(publishers), last)


#: Признаки `$` — денежный след ДО даты `cut`: год до неё (`last`) и год до того (`prev`).
#: Один набор и одна арифметика для бэктеста (`scripts/backtest_classifier.py`) и живого
#: `trends ask` (`weak/ask._score_by_model`) — иначе train/serve skew.
MONEY_FEATURES: tuple[str, ...] = (
    "money_last_n",
    "money_prev_n",
    "money_momentum",
    "money_last_usd",
    "money_recency_days",
    "money_publishers",
)
MONEY_LABELS: dict[str, str] = {
    "money_last_n": "денежных заголовков за последний год",
    "money_prev_n": "денежных заголовков за год до того",
    "money_momentum": "денежных заголовков за последний год к году до того",
    "money_last_usd": "распознанных сумм за последний год, $",
    "money_recency_days": "дней от последнего денежного заголовка",
    "money_publishers": "изданий с денежными заголовками за последний год",
}
YEAR = timedelta(days=365)


def features_of(last: MoneyTrace, prev: MoneyTrace | None, cut: date) -> dict[str, float | None]:
    """Признаки `$` из двух денежных следов до `cut`; без `prev` — его признаки `None`
    («измерить нечем», пойдут медианой обучения), а не ноль."""
    recency = None
    if last.last_date:
        recency = float(max((cut - date.fromisoformat(last.last_date)).days, 0))
    return {
        "money_last_n": float(last.n),
        "money_prev_n": float(prev.n) if prev else None,
        "money_momentum": (last.n + 1) / (prev.n + 1) if prev else None,
        "money_last_usd": last.usd,
        "money_recency_days": recency,
        "money_publishers": float(last.publishers),
    }


def features(
    labels: list[str],
    cut: date,
    say: Callable[[str], None] = lambda _m: None,
    with_prev: bool = True,
) -> list[dict[str, float | None]]:
    """Признаки `$` для каждого ярлыка на дату `cut`: окно «год до» и, если `with_prev`,
    «год до того» (второе окно — ещё столько же запросов; живой `ask` берёт его только
    когда модели нужны `money_prev_n` / `money_momentum`)."""
    last = trace(labels, cut - YEAR, cut, say)
    prev = trace(labels, cut - 2 * YEAR, cut - YEAR, say) if with_prev else [None] * len(labels)
    return [features_of(lt, pv, cut) for lt, pv in zip(last, prev, strict=True)]


def trace(
    labels: list[str], start: date, end: date, say: Callable[[str], None] = lambda _m: None
) -> list[MoneyTrace]:
    """Денежный след каждого ярлыка в окне `[start, end)`: пять формул `money.TEMPLATES` ×
    окно, до `LIMIT` заголовков на запрос, ответы через суточный кэш `weak.cache`."""
    if not labels:
        return []
    queries = [tpl.format(q=label) for label in labels for tpl in TEMPLATES]
    say(f"денежный след: {len(labels)} ярлыков × {len(TEMPLATES)} формул, окно {start}..{end}")
    # ⚠️ Срез снимается на время запроса: окно после среза лежит в «будущем» относительно
    # `asof.AS_OF`, и фильтр `too_late` в `live._cached` выбросил бы его целиком.
    held = asof.AS_OF
    asof.AS_OF = None
    try:
        found = news_many(queries, "en", window=window(start, end), limit=LIMIT)
    finally:
        asof.AS_OF = held
    out = []
    per = len(TEMPLATES)
    for i, label in enumerate(labels):
        docs = [d for chunk in found[i * per : (i + 1) * per] for d in chunk]
        out.append(summarize(label, docs))
    return out
