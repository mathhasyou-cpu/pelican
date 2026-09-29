"""Строка датасета → свидетельства → признаки. Общий код замера, обучения и предсказания.

Три потребителя, один сбор:

- `scripts/measure_signal_kinds.py` — замер жанра и стадии на 100 + 30 строках;
- `scripts/train_signal_model.py` — обучаемая модель на признаках из этого сбора;
- `scripts/predict_dataset.py` — датасет организаторов на входе, CSV предсказаний на выходе.

Расходиться им нельзя: число, полученное замером, обязано относиться к тому же составу
свидетельств, что идёт в предсказание, иначе F1 отчёта — про другой код.

## Что такое «плечо»

Состав свидетельств, показываемый модели: `papers` — только работы корпуса; `mixed` —
новости и работы; `corpus` — то же плюс след ядра и ярлыка в корпусе. Всего свидетельств
в каждом плече ровно `ARM_EVIDENCE` (новости вытесняют работы, а не добавляются): плечи
обязаны различаться СОСТАВОМ, а не числом. Замеры плеч — `reports/weak-eval.md`.

## Признаки

Числа, которые и так считаются по ходу сбора: привязка (сколько работ, косинусы), новости
(сколько, издатели, типы, доверенность, свежесть), след корпуса (объём, возраст, доля за
год — у ядра и у ярлыка). ⚠️ `None` — «измерить нечем», а не ноль: у направления без следа
в корпусе возраст не нулевой, а неизвестный, и модель получает флаг `*_unseen` плюс
импутацию внутри фолда (docs/velocity.md — то же правило у оси 1).

⚠️ Жанр модели (`llm_kind`) признаком НЕ является внутри этого модуля: он приходит из
оценки и добавляется потребителем отдельной колонкой, чтобы обучение без него и с ним
считалось на одном файле.

Три набора сверх чисел `N` (`FEATURE_NAMES`), у каждого свой источник:

- `J` (`JUDGMENT_FEATURES`) — атомарные чтения свидетельств из той же оценки моделью
  (`assess.JUDGMENTS`): признаки для обучаемой модели этапа 1 ТЗ. ⚠️ Вердикт `kind` в них
  не входит — голос как признак выучивает any-yes (`weak/assess.py`);
- `E` (`EMBED_FEATURES`) — соседство ярлыка в векторах корпуса (`weak.neighbours`):
  новизна на дату, плотность по окнам, связность. Считается по запросу `gather(...,
  neighbours=True)` — проход по всем шардам, и в промпт не идёт;
- `L3` (`LLM_FEATURES`) — голоса-вердикты трёх плеч; оставлены как базовая линия
  зоопарка, в модель выдачи не идут.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date

import httpx

from pelican.config import settings
from pelican.store import Store
from pelican.llm import LLMClient, LLMError
from pelican.weak.assess import (
    CORPUS,
    JUDGMENT_NAMES,
    NEWS,
    SYSTEM_PROMPT,
    Assessment,
    Candidate,
    Evidence,
    build_prompt,
    parse,
    schema,
)
from pelican.weak.core import Footprint, cores, corpus_line, footprints_pair
from pelican.weak.ground import Hit, ground
from pelican.weak.kinds import EMERGING
from pelican.weak.live import PER_SOURCE, LiveDoc, news_many
from pelican.weak.neighbours import Coverage, Neighbourhood, neighbourhoods
from pelican.weak.rubric import STAGE_RANK
from pelican.weak.translate import translate
from pelican.weak.trust import HIGH, SCIENCE

#: Свидетельств в каждом плече ровно столько. (`assess.EVIDENCE` — потолок для
#: `trends ask`, он шире и здесь не мера.)
ARM_EVIDENCE = 5
#: Сколько новостей и сколько работ в плече `mixed` / `corpus`.
MIXED_NEWS = 3
MIXED_PAPERS = 2
#: Новостей на строку сверх плеча — всё, что отдаёт живой поиск (`live.PER_SOURCE`):
#: признаки считаются по ним. ⚠️ Потолок в 5 насыщал `news_found` и `publishers_distinct`
#: у обоих классов (медиана 5 и там, и там), и признак не делил ничего.
NEWS_KEPT = PER_SOURCE

ARMS = ("papers", "mixed", "corpus")

Progress = Callable[[str], None]


@dataclass(slots=True)
class Row:
    """Одна строка датасета со всем, что по ней найдено."""

    set: str  # pos / ctl
    n: str
    tech: str
    expected_kind: str = ""
    expected_stage: str = ""
    hits: list[Hit] = field(default_factory=list)
    papers: list[Evidence] = field(default_factory=list)
    news: list[LiveDoc] = field(default_factory=list)
    label_en: str = ""
    core: str = ""
    core_print: Footprint | None = None
    label_print: Footprint | None = None
    #: Соседство в векторах корпуса (`E`); `None` — не считалось.
    nbr: Neighbourhood | None = None


def evidence_for(store: Store, ids: list[int]) -> dict[int, Evidence]:
    listed = ",".join(str(i) for i in ids)
    if not listed:
        return {}
    rows = store.conn.execute(
        f"SELECT id, CAST(observed_at AS DATE), term_raw FROM works WHERE id IN ({listed})"
    ).fetchall()
    return {int(r[0]): Evidence(int(r[0]), str(r[1]), r[2]) for r in rows}


def gather(
    store: Store,
    rows: list[Row],
    arm: str,
    say: Progress = lambda _msg: None,
    neighbours: bool = False,
) -> Coverage | None:
    """Заполнить строки свидетельствами по составу плеча. Изменяет `rows` на месте.

    `neighbours=True` считает и соседство в векторах (`E`, `weak.neighbours`) — ещё один
    проход по всем шардам; возвращает покрытие окон векторами, иначе `None`.
    """
    if arm not in ARMS:
        raise ValueError(f"плечо вне списка {ARMS}: {arm!r}")
    if not rows:
        return None

    coverage: Coverage | None = None
    if neighbours:
        # ⚠️ По СЫРОМУ названию, как привязка: перевод тянет короткие документы
        # (docs/weak-measurements.md).
        say(f"соседство в векторах для {len(rows)} названий (проход по шардам)...")
        found, coverage, _ = neighbourhoods(store, [r.tech for r in rows], say=say)
        for row, nbr in zip(rows, found, strict=True):
            row.nbr = nbr

    hits = ground(store, [r.tech for r in rows], k=ARM_EVIDENCE)
    ev = evidence_for(store, sorted({h.signal_id for found in hits for h in found}))
    for row, found in zip(rows, hits, strict=True):
        row.hits = [h for h in found if h.signal_id in ev]
        row.papers = [ev[h.signal_id] for h in row.hits]

    if arm == "papers":
        return coverage

    names = [r.tech for r in rows]
    say(f"перевод {len(names)} названий для новостного поиска...")
    english = translate(names)
    for row, tr in zip(rows, english, strict=True):
        row.label_en = tr.en
    found_news = news_many([r.label_en for r in rows], "en")
    for row, docs in zip(rows, found_news, strict=True):
        row.news = docs[:NEWS_KEPT]
    empty = sum(1 for r in rows if not r.news)
    say(f"новостей: {sum(len(r.news) for r in rows)}, без единой новости {empty}")

    if arm == "corpus":
        # След корпуса: ДВА числа, ядра и самого ярлыка (docs/weak-signals.md). Ярлык —
        # английское название, как в `trends ask`, где его даёт модель.
        say(f"ядра для {len(rows)} названий...")
        labels = [r.label_en for r in rows]
        core_names = cores(labels)
        say(f"след по корпусу: {len(set(core_names)) + len(set(labels))} условий, это минуты...")
        prints, label_prints = footprints_pair(store, core_names, labels)
        for row, core in zip(rows, core_names, strict=True):
            row.core = core.strip()
            row.core_print = prints.get(row.core)
            row.label_print = label_prints.get(row.label_en.strip())
    return coverage


def candidate(row: Row, arm: str) -> Candidate:
    """Свидетельства для промпта — ровно тот состав, что замерялся."""
    if arm == "papers":
        evidence = list(row.papers[:ARM_EVIDENCE])
    else:
        # ⚠️ Новости вытесняют работы, а не добавляются к ним.
        fresh = [
            Evidence(-1, d.published, d.title, NEWS, d.url, d.publisher)
            for d in row.news[:MIXED_NEWS]
        ]
        evidence = fresh + list(row.papers[:MIXED_PAPERS])
        if arm == "corpus" and row.core_print is not None and row.label_print is not None:
            # Последним, как в `trends ask`.
            evidence.append(Evidence(-1, "", corpus_line(row.core_print, row.label_print), CORPUS))
    return Candidate(key=row.n, name=row.tech, evidence=evidence)


async def _assess_many(candidates: list[Candidate], say: Progress) -> list[Assessment | None]:
    client = LLMClient(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        timeout_s=settings.llm_timeout_s,
    )
    out: list[Assessment | None] = []
    async with httpx.AsyncClient() as http:
        for i, cand in enumerate(candidates, 1):
            try:
                payload = await client.json_completion(
                    http,
                    system=SYSTEM_PROMPT,
                    user=build_prompt(cand),
                    schema=schema(),
                    name="assessment",
                    max_tokens=400,
                )
                out.append(parse(payload, cand))
            except (LLMError, ValueError, httpx.HTTPError) as exc:
                say(f"{cand.key}: отказ {type(exc).__name__}: {str(exc)[:100]}")
                out.append(None)
            if i % 10 == 0:
                say(f"оценено {i}/{len(candidates)}")
    return out


def assess_many(
    candidates: list[Candidate], say: Progress = lambda _m: None
) -> list[Assessment | None]:
    """Оценка моделью, один кандидат на запрос (`weak.assess`); отказ — `None`, не исключение."""
    return asyncio.run(_assess_many(candidates, say)) if candidates else []


# ---------------------------------------------------------------------------- признаки

#: Порядок признаков закреплён: по нему пишется TSV и читается `model.json`.
FEATURE_NAMES: tuple[str, ...] = (
    "papers_found",
    "cos_max",
    "cos_mean",
    "news_found",
    "publishers_distinct",
    "source_kinds_distinct",
    "trust_high_count",
    "recent_share",
    "core_works",
    "core_age_years",
    "core_unseen",
    "core_last_year_share",
    "label_works",
    "label_last_year_share",
    "label_to_core_ratio",
    "news_first_age_days",
    "news_span_days",
    "funding_share",
    "paper_first_age_days",
    "paper_recent_share",
)

#: Голоса LLM по трём плечам как признаки (stacking, Wolpert 1992; в LLM-литературе —
#: «LLM-stacked»). Считает не `features()`, а потребитель из TSV оценок
#: (`llm_features`): голоса не обучаются, утечки в CV нет. Недостающее плечо — `None`.
LLM_FEATURES: tuple[str, ...] = (
    "llm_papers",
    "llm_mixed",
    "llm_corpus",
    "llm_stage_papers",
    "llm_stage_mixed",
    "llm_stage_corpus",
    "llm_votes",
)

#: Атомарные чтения свидетельств из оценки (`assess.JUDGMENTS`) — набор `J`.
JUDGMENT_FEATURES: tuple[str, ...] = JUDGMENT_NAMES

#: Соседство в векторах корпуса (`weak.neighbours`) — набор `E`.
EMBED_FEATURES: tuple[str, ...] = (
    "nn_sim_now",
    "nn_sim_asof",
    "nn_sim_gain",
    "dense_last",
    "dense_prev",
    "dense_ratio",
    "nbr_coherence",
)

#: Слова заголовка, по которым новость — про деньги (раунд, покупка, оценка). ⚠️ Догадка:
#: список собран по шаблонам денежных запросов `money.TEMPLATES`, на данных не подбирался.
FUNDING_WORDS: frozenset[str] = frozenset(
    {
        "raises", "raised", "raise", "funding", "seed", "series", "round", "investment",
        "invests", "investors", "acquires", "acquisition", "acquired", "valuation", "backed",
    }
)  # fmt: skip

#: Счётчики с тяжёлым хвостом (от нуля до сотен тысяч работ): в линейную модель идут
#: через log1p — стандартная замена для счётных признаков, иначе один GNN на 10 000 работ
#: задаёт масштаб всему коэффициенту. Применяется и при обучении, и в `weak/model.py`.
LOG_FEATURES: frozenset[str] = frozenset(
    {
        "core_works",
        "label_works",
        "news_found",
        "news_first_age_days",
        "paper_first_age_days",
        "dense_last",
        "dense_prev",
        "dense_ratio",
    }
)

#: Как признак читается человеком — для «ключевых предикторов» в выдаче и в отчёте.
FEATURE_LABELS: dict[str, str] = {
    "papers_found": "привязанных работ корпуса",
    "cos_max": "сходство с ближайшей работой",
    "cos_mean": "среднее сходство с работами",
    "news_found": "новостей о направлении",
    "publishers_distinct": "разных издателей среди новостей",
    "source_kinds_distinct": "разных типов источников",
    "trust_high_count": "источников высокой доверенности",
    "recent_share": "доля свидетельств за последний год",
    "core_works": "работ у ядра технологии",
    "core_age_years": "возраст ядра в корпусе, лет",
    "core_unseen": "ядро в корпусе не встречается",
    "core_last_year_share": "доля работ ядра за последний год",
    "label_works": "работ про само направление",
    "label_last_year_share": "доля работ направления за последний год",
    "label_to_core_ratio": "направление к ядру, отношение объёмов",
    "news_first_age_days": "возраст самой ранней новости, дней",
    "news_span_days": "разброс дат новостей, дней",
    "funding_share": "доля новостей про раунды и сделки",
    "paper_first_age_days": "возраст самой ранней работы, дней",
    "paper_recent_share": "доля работ за последний год",
    "llm_papers": "LLM по работам: зарождающаяся",
    "llm_mixed": "LLM по работам и новостям: зарождающаяся",
    "llm_corpus": "LLM по работам, новостям и следу: зарождающаяся",
    "llm_stage_papers": "стадия по LLM (работы)",
    "llm_stage_mixed": "стадия по LLM (работы и новости)",
    "llm_stage_corpus": "стадия по LLM (со следом)",
    "llm_votes": "голосов LLM за зарождающуюся из трёх",
    "players_named": "компаний названо в новостях",
    "funding_mentioned": "упомянут раунд, сумма или сделка",
    "commercial_deployment": "упомянуты продажи или платящие клиенты",
    "pilot_mentioned": "упомянут пилот или полевой тест",
    "market_leaders_named": "названы лидеры сформированного рынка",
    "routine_tool": "упоминается как рутинный инструмент",
    "standard_or_regulation": "это стандарт, спецификация или регламент",
    "field_not_technology": "название — область, а не технология",
    "novelty_claimed": "подаётся как новое или впервые",
    "nn_sim_now": "сходство с ближайшей работой корпуса",
    "nn_sim_asof": "сходство с ближайшей работой до окна",
    "nn_sim_gain": "прирост сходства за окно (новизна)",
    "dense_last": "близких работ за последнее окно",
    "dense_prev": "близких работ за предыдущее окно",
    "dense_ratio": "рост плотности близких работ",
    "nbr_coherence": "связность соседства",
}


def _parse_date(d: str) -> date | None:
    try:
        return date.fromisoformat(d[:10]) if len(d) >= 10 else date(int(d[:4]), 7, 1)
    except ValueError:
        return None


def _ages(dates: list[str], today: date) -> list[int]:
    return [(today - d).days for d in map(_parse_date, dates) if d is not None]


def llm_features(votes: dict[str, tuple[str, str]]) -> dict[str, float | None]:
    """Голоса LLM по плечам → признаки. `votes[плечо] = (kind, stage)`; нет плеча — `None`."""
    out: dict[str, float | None] = {}
    present = []
    for arm in ARMS:
        kind, stage = votes.get(arm, ("", ""))
        out[f"llm_{arm}"] = None if not kind else float(kind == EMERGING)
        out[f"llm_stage_{arm}"] = float(STAGE_RANK[stage]) if stage in STAGE_RANK else None
        if kind:
            present.append(kind == EMERGING)
    out["llm_votes"] = float(sum(present)) if present else None
    return {k: out[k] for k in LLM_FEATURES}


def judgment_features(assessment: Assessment | None) -> dict[str, float | None]:
    """Атомарные чтения из оценки → признаки `J`. Нет оценки или поля — `None`."""
    got = assessment.judgments if assessment is not None else {}
    return {k: got.get(k) for k in JUDGMENT_FEATURES}


def embed_features(row: Row) -> dict[str, float | None]:
    """Соседство в векторах → признаки `E`. Не считалось или нет покрытия — `None`."""
    n = row.nbr
    if n is None:
        return dict.fromkeys(EMBED_FEATURES)
    return {
        "nn_sim_now": n.nn_sim_now,
        "nn_sim_asof": n.nn_sim_asof,
        "nn_sim_gain": n.nn_sim_gain,
        "dense_last": float(n.dense_last) if n.dense_last is not None else None,
        "dense_prev": float(n.dense_prev) if n.dense_prev is not None else None,
        "dense_ratio": n.dense_ratio,
        "nbr_coherence": n.nbr_coherence,
    }


def age_share(dates: list[str], today: date) -> float:
    """Доля дат не старше года. Дата — ISO или один год."""
    parsed = [d for d in map(_parse_date, dates) if d is not None]
    if not parsed:
        return 0.0
    return sum(1 for d in parsed if (today - d).days <= 365) / len(parsed)


def _share(fp: Footprint | None) -> float | None:
    # ⚠️ `last_year_works < 0` — «не считалось» (след восстановлен из карточки, где доли
    # за год нет: `scripts/backtest_classifier.py`); это `None`, а не нулевая доля.
    if fp is None or not fp.works or fp.last_year_works < 0:
        return None
    return fp.last_year_works / fp.works


def features(row: Row, today: date | None = None) -> dict[str, float | None]:
    """Числовые признаки строки. `None` — измерить нечем (см. докстринг модуля)."""
    today = today or date.today()
    scores = [h.score for h in row.hits]
    news = row.news[:NEWS_KEPT]
    trusts = [d.trust for d in news]
    kinds = {t.kind for t in trusts}
    if row.papers:
        kinds.add(SCIENCE)
    news_dates = [d.published for d in news if d.published]
    paper_dates = [e.date for e in row.papers if e.date]
    dated = news_dates + paper_dates
    news_ages = _ages(news_dates, today)
    paper_ages = _ages(paper_dates, today)
    funding = [
        any(w in FUNDING_WORDS for w in d.title.lower().replace("-", " ").split()) for d in news
    ]

    core, label = row.core_print, row.label_print
    core_works = core.works if core is not None else None
    core_age: float | None
    if core is None or not core.works or core.first_year is None:
        core_age = None
    else:
        core_age = float(max(today.year - core.first_year, 0))
    label_works = label.works if label is not None else None
    ratio = None
    if core_works and label_works is not None:
        ratio = label_works / core_works

    return {
        "papers_found": float(len(row.papers)),
        "cos_max": max(scores) if scores else None,
        "cos_mean": sum(scores) / len(scores) if scores else None,
        "news_found": float(len(news)),
        "publishers_distinct": float(
            len({d.publisher or d.domain for d in news if d.publisher or d.domain})
        ),
        "source_kinds_distinct": float(len(kinds)),
        "trust_high_count": float(sum(1 for t in trusts if t.level == HIGH)),
        "recent_share": age_share(dated, today),
        "core_works": float(core_works) if core_works is not None else None,
        "core_age_years": core_age,
        # ⚠️ Три состояния: след не считался (None), ядро не встречается (1), встречается (0).
        "core_unseen": None if core is None else (1.0 if not core.works else 0.0),
        "core_last_year_share": _share(core),
        "label_works": float(label_works) if label_works is not None else None,
        "label_last_year_share": _share(label),
        "label_to_core_ratio": ratio,
        "news_first_age_days": float(max(news_ages)) if news_ages else None,
        "news_span_days": float(max(news_ages) - min(news_ages)) if news_ages else None,
        "funding_share": sum(funding) / len(funding) if funding else None,
        "paper_first_age_days": float(max(paper_ages)) if paper_ages else None,
        "paper_recent_share": age_share(paper_dates, today) if paper_dates else None,
    }
