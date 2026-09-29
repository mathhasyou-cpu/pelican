"""Бэктест модели этапа 1: разделяет ли классификатор на срезе 2024 то, что взлетело к 2026.

    python scripts/backtest_classifier.py --dir reports/bt-2024-09-15
    python scripts/backtest_classifier.py --dir reports/bt-2024-09-15 --no-judge   # без LLM-судьи

## Зачем

`backtest_signals.py` отвечает про ТОП конвейера с gemma: верх выдачи на срезе 2024-09
растёт к 2026 чаще контроля (AUC 0.62 при поле случайности 0.58). Модель этапа 1
(`weak/model.json`) обучена на сотне строк 2026 года — и вопрос, знает ли она что-то про
БУДУЩЕЕ или выучила стиль датасета, решается только так: посчитать её на тех же
кандидатах того же прогона под срезом и сравнить с тем, что стало потом.

## Как

Кандидаты — все три группы прогона под срезом (`ask-cards-*.json`: ТОП, снятые по жанру,
с одним игроком), у каждого свидетельства РОВНО те, что видел конвейер на срезе
(`sources`: новости и работы с датами до среза, след ядра и ярлыка — поля карточки).
По ним заново идёт оценка `weak.assess` — тем же промптом, что у `trends ask` сегодня, —
она отдаёт атомарные чтения `J`; числа `N` считаются `weak.dataset.features` из тех же
свидетельств; вероятность — `weak.model.probability` по установленной модели.

⚠️ Расхождения с прогоном на срезе, названные: (а) у работ здесь только заголовок, а
конвейер показывал 320 знаков аннотации; (б) косинусов привязки в карточке нет — они
идут медианой обучения (у установленной модели их коэффициенты нулевые).

## Чем меряется и с чем сравнивается

Исход — тот же, что у `backtest_signals.py`: во сколько раз вырос след ядра в корпусе
после среза (`backtest.tsv`, «выросло» = ≥ `GROWTH`). Три оценщика на одних кандидатах:
- вероятность классификатора;
- gemma на срезе — жанр `emerging` (0/1), как было в прогоне;
- членство в ТОП конвейера (0/1) — это и есть 0.62 из `backtest_signals.py`.
AUC Манна–Уитни «выросшие против невыросших» по каждому, пол случайности — перестановкой
исходов на тех же числах (как в `backtest_signals._separation`). Плюс покрытие списка
методолога 2026 года верхом классификатора того же размера, что ТОП конвейера, судьёй
`judge_match` (⚠️ судья видит только названия).

⚠️ Правило зачёта, записанное до прогона: предсказательная сила показана, если AUC
классификатора выше пола случайности И не ниже AUC ТОП конвейера. Иначе — модель
выучила датасет, а не будущее, и так и предъявляется.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import importlib.util
import json
import math
import random
import statistics
import sys
from datetime import date, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from pelican.config import settings  # noqa: E402
from pelican.store import Store  # noqa: E402
from pelican.weak import asof  # noqa: E402
from pelican.weak import model as model_mod  # noqa: E402
from pelican.weak.assess import (  # noqa: E402
    CORPUS,
    JUDGMENT_NAMES,
    NEWS,
    PAPER,
    Assessment,
    Candidate,
    Evidence,
)
from pelican.weak.core import Footprint, corpus_line, footprints, industry_share  # noqa: E402
from pelican.weak.dataset import (  # noqa: E402
    EMBED_FEATURES,
    FEATURE_NAMES,
    JUDGMENT_FEATURES,
    Row,
    assess_many,
    embed_features,
    features,
    judgment_features,
    llm_features,
)
from pelican.weak.kinds import EMERGING  # noqa: E402
from pelican.weak.live import LiveDoc  # noqa: E402
from pelican.weak.neighbours import neighbourhoods  # noqa: E402
from pelican.weak import rounds  # noqa: E402
from pelican.weak.rounds import MoneyTrace, trace  # noqa: E402
from pelican.weak.stats import span  # noqa: E402

_JUDGE = REPO / "scripts" / "judge_match.py"
_spec = importlib.util.spec_from_file_location("judge_match", _JUDGE)
judge_match = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(judge_match)

#: Порог роста — тот же, что в `backtest_signals.py`. ⚠️ На корпусе про ИИ его проходят
#: 170 из 196 — исход почти не делит; основной исход — избыточный рост (`outcomes`).
GROWTH = 2.0
SHUFFLES = 200
#: Верхняя доля домена по избыточному росту, считаемая «взлетело».
TOP_SHARE = 1 / 3

#: Признаки-индикаторы на срезе (`M`): сообщество, деньги, видимость, новизна, momentum.
#: Приёмы: community/growth/novelty у Carley, Newman, Porter & Garner 2018 («An indicator
#: of technical emergence»); избыточный рост и структура сообщества у Klavans, Boyack &
#: Murdick 2020 (PLOS One); DoV у Yoon 2012.
MOMENTUM_FEATURES: tuple[str, ...] = (
    "players",
    "money_stream",
    "low_visibility",
    "core_age_at_cut",
    "core_momentum",
    "label_momentum",
)
MOMENTUM_LABELS = {
    "players": "независимых компаний в карточке",
    "money_stream": "кандидат из денежного потока",
    "low_visibility": "видимость ниже медианы пула",
    "core_age_at_cut": "возраст ядра на срезе, лет",
    "core_momentum": "работ ядра за год до среза к году до того",
    "label_momentum": "работ ярлыка за год до среза к году до того",
}
#: Денежный след ДО среза (`$`) и индустриальная аффилиация (`I`). Приём — financing
#: recency / momentum / breadth из панелей Crunchbase (arXiv:2510.09465: «деньги
#: предсказываются деньгами») и university–industry co-publication (Wong & Singh 2013).
MONEY_FEATURES: tuple[str, ...] = (
    "money_last_n",
    "money_prev_n",
    "money_momentum",
    "money_last_usd",
    "money_recency_days",
    "money_publishers",
    "industry_share",
)
MONEY_LABELS = {
    "money_last_n": "денежных заголовков за год до среза",
    "money_prev_n": "денежных заголовков за год до того",
    "money_momentum": "денежных заголовков за год до среза к году до того",
    "money_last_usd": "распознанных сумм за год до среза, $",
    "money_recency_days": "дней от последнего денежного заголовка до среза",
    "money_publishers": "изданий с денежными заголовками за год до среза",
    "industry_share": "доля работ ядра с компанией среди авторов",
}
#: Исходы после среза.
OUTCOMES: tuple[str, ...] = (
    "excess",
    "grew_excess",
    "top_tercile",
    "money_excess",
    "money_grew",
    "money_top",
    "grew",
)
#: Столько же новостей и работ, сколько показывает `trends ask` (`ask.NEWS_PER_CARD`,
#: `ask.PAPERS_PER_CARD`): состав свидетельств обязан совпадать с прогоном.
NEWS_PER_CARD = 5
PAPERS_PER_CARD = 2


def load_candidates(folder: Path) -> list[dict]:
    out = []
    for path in sorted(folder.glob("ask-cards-*.json")):
        domain = path.stem.removeprefix("ask-cards-").replace("-", " ")
        run = json.loads(path.read_text(encoding="utf-8"))
        groups = [("ТОП", run.get("signals", []))]
        groups.append(("контроль", run.get("excluded", []) + run.get("thin", [])))
        for where, sigs in groups:
            for s in sigs:
                out.append({"domain": domain, "where": where, "area": run.get("area_en", ""), **s})
    return out


def footprints_at(store: Store, names: list[str], cut: date) -> dict[str, Footprint]:
    """След корпуса ПОД СРЕЗОМ (кэш `weak.core` ключуется срезом — повтор бесплатен)."""
    held = asof.AS_OF
    asof.AS_OF = cut
    try:
        return footprints(store, names)
    finally:
        asof.AS_OF = held


def momentum(now: Footprint | None, before: Footprint | None) -> float | None:
    """Работ за год до среза к работам за год до того. `None` — измерить нечем."""
    if now is None or before is None or now.last_year_works < 0 or before.last_year_works < 0:
        return None
    if not before.last_year_works and not now.last_year_works:
        return None
    return (now.last_year_works + 1) / (before.last_year_works + 1)


def momentum_features(c: dict, cut: date, at_cut: dict, year_before: dict) -> dict:
    """Признаки НА СРЕЗЕ считаются по всему корпусу, что был к той дате, без `SLICES`:
    это то, что детектор видел бы живьём. ⚠️ На срезе 2024 у `core_momentum` год до
    среза (2023-09…2024-09) содержит CS/Engineering целиком, а год до того — только с
    2023-01 (край срезов в БД): у софтовых ядер momentum чуть завышен включением
    источника. Под срезом 2022 оба окна без CS. Исход же (`backtest_signals`) считается
    по одному набору срезов на обоих концах."""
    core = str(c.get("core") or c.get("label"))
    label = str(c["label"])
    since = c.get("core_since")
    return {
        "players": float(len(c.get("companies") or [])),
        "money_stream": 1.0 if c.get("stream") == "деньги" else 0.0,
        "low_visibility": 1.0 if c.get("low_visibility") in (True, "True") else 0.0,
        "core_age_at_cut": float(max(cut.year - int(since), 0)) if since else None,
        "core_momentum": momentum(at_cut.get(core), year_before.get(core)),
        "label_momentum": momentum(at_cut.get(label), year_before.get(label)),
    }


def money_features(
    last: MoneyTrace, prev: MoneyTrace, cut: date, industry: float | None
) -> dict[str, float | None]:
    """Признаки `$`+`I` из двух денежных следов до среза и доли индустриальных работ.
    Арифметика `$` — в `weak.rounds.features_of`, общая с живым `ask`."""
    return {**rounds.features_of(last, prev, cut), "industry_share": industry}


def to_row(c: dict, at_cut: dict[str, Footprint] | None = None) -> Row:
    """Карточка на срезе → `Row` с теми же свидетельствами (след — под срезом, если есть)."""
    news = [
        LiveDoc(
            "google_news",
            s["title"],
            s["url"],
            s.get("date", ""),
            s.get("publisher", ""),
            s.get("lang", "en"),
            domain=s.get("domain", ""),
        )
        for s in c["sources"]
        if s.get("evidence") == NEWS
    ]
    papers = [
        Evidence(-1, s.get("date", ""), s["title"], PAPER, s["url"], s.get("publisher", ""))
        for s in c["sources"]
        if s.get("evidence") == PAPER
    ]
    core = str(c.get("core") or c.get("label"))
    since = c.get("core_since")
    # Доли за год в карточке нет: −1 — «не считалось», признак пойдёт медианой обучения.
    unknown = -1
    at_cut = at_cut or {}
    core_print = at_cut.get(core) or Footprint(
        core, int(c.get("core_works") or 0), int(since) if since else None, unknown
    )
    label_print = at_cut.get(c["label"]) or Footprint(
        c["label"], int(c.get("label_works") or 0), None, unknown
    )
    return Row(
        set=c["where"],
        n=c["label"],
        tech=c["label"],
        label_en=c["label"],
        papers=papers,
        news=news,
        core=core,
        core_print=core_print,
        label_print=label_print,
    )


def to_candidate(row: Row, area: str) -> Candidate:
    ev = [
        Evidence(-1, d.published, d.title, NEWS, d.url, d.publisher)
        for d in row.news[:NEWS_PER_CARD]
    ]
    ev += row.papers[:PAPERS_PER_CARD]
    ev.append(Evidence(-1, "", corpus_line(row.core_print, row.label_print), CORPUS))
    return Candidate(key=row.n, name=row.tech, evidence=ev, area=area)


def outcomes(rows: list[dict]) -> None:
    """Исходы после среза — в строки на месте.

    `excess` — log(во сколько раз) минус медиана log по ДОМЕНУ: рост сверх базового
    (Klavans, Boyack & Murdick 2020), потому что «всё про ИИ выросло вдвое» — не сигнал.
    `grew_excess` — выше медианы домена; `top_tercile` — верхняя треть домена; `grew` —
    старый порог ≥2×. Деньги — тем же приёмом по `money_after_n` (денежных заголовков за
    два года после среза, `weak.rounds`): `money_excess`, `money_grew`, `money_top`.
    ⚠️ `money_ratio` («после» к «до») печатается как справка и не идёт в обучение:
    знаменатель — тот же признак `money_last_n`, и такой исход — регрессия к среднему.
    """
    _excess(rows, "log_ratio", "excess", "grew_excess", "top_tercile")
    _excess(rows, "log_money", "money_excess", "money_grew", "money_top")
    for r in rows:
        r["grew"] = int(float(r["во сколько раз"]) >= GROWTH)
        last = float(r.get("money_last_n") or 0)
        r["money_ratio"] = round(math.log((float(r["money_after_n"]) / 2 + 1) / (last + 1)), 3)


def _excess(rows: list[dict], value: str, excess: str, above: str, top: str) -> None:
    """Значение сверх медианы домена, флаги «выше медианы» и «верхняя треть»."""
    by_domain: dict[str, list[float]] = {}
    for r in rows:
        if value == "log_ratio":
            r[value] = math.log(max(float(r["во сколько раз"]), 0.05))
        else:
            r[value] = math.log1p(float(r["money_after_n"]))
        by_domain.setdefault(r["домен"], []).append(r[value])
    median = {d: statistics.median(v) for d, v in by_domain.items()}
    cutoff = {
        d: sorted(v, reverse=True)[max(int(len(v) * TOP_SHARE) - 1, 0)]
        for d, v in by_domain.items()
    }
    for r in rows:
        d = r["домен"]
        r[excess] = round(r[value] - median[d], 3)
        r[above] = int(r[value] > median[d])
        # ⚠️ Верхняя треть по денежному исходу при медиане 0 — «≥ порога», но порог может
        # быть 0 у домена, где деньги нашлись у трети и меньше: тогда флаг требует > 0.
        r[top] = int(r[value] >= cutoff[d] and r[value] > 0) if value == "log_money" else int(
            r[value] >= cutoff[d]
        )


def read_prior(path: Path) -> dict[tuple[str, str], dict]:
    """Прошлый TSV бэктеста: чтения `J` и жанр «сегодня», чтобы не гонять оценку заново."""
    if not path.exists():
        return {}
    with path.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f, delimiter="\t"))
    if not rows or "novelty_claimed" not in rows[0]:
        return {}
    return {(r["домен"], r["направление"]): r for r in rows}


def _cell(v: float | None) -> str:
    return "" if v is None else f"{v:.4f}"


def auc(pos: list[float], neg: list[float]) -> float:
    wins = sum(1 for x in pos for y in neg if x > y)
    ties = sum(1 for x in pos for y in neg if x == y)
    return (wins + 0.5 * ties) / max(len(pos) * len(neg), 1)


def floor(scores: list[float], grew: list[bool]) -> float:
    """95-й перцентиль AUC при случайной перестановке исходов на тех же числах."""
    rnd = random.Random(20260918)
    labels = list(grew)
    got = []
    for _ in range(SHUFFLES):
        rnd.shuffle(labels)
        got.append(auc([s for s, g in zip(scores, labels, strict=True) if g],
                       [s for s, g in zip(scores, labels, strict=True) if not g]))  # fmt: skip
    got.sort()
    return got[int(0.95 * len(got))]


def main() -> None:
    ap = argparse.ArgumentParser(description="бэктест классификатора этапа 1")
    ap.add_argument("--dir", required=True, help="папка прогона под срезом (после backtest_signals)")  # noqa: E501
    ap.add_argument("--no-judge", action="store_true", help="без покрытия списка судьёй")
    ap.add_argument("--limit", type=int, default=0, help="первые N кандидатов (отладка)")
    ap.add_argument("--as-of", help="дата среза, если её нет в имени папки")
    ap.add_argument(
        "--reassess",
        action="store_true",
        help="переоценить моделью даже если чтения J уже есть в прошлом TSV",
    )
    ap.add_argument(
        "--embed",
        action="store_true",
        help="соседство в векторах под срезом (E): проход по шардам; без покрытия окон — пусто",
    )
    args = ap.parse_args()
    folder = REPO / args.dir
    cut = (
        date.fromisoformat(args.as_of)
        if args.as_of
        else date.fromisoformat("-".join(folder.name.rsplit("-", 3)[-3:]))
    )

    params = model_mod.load()
    if params is None:
        print("нет установленной модели: train_signal_model.py --install")
        raise SystemExit(1)
    # Модель роста, если установлена, — базовая линия для денежного исхода (одно ли это?).
    # ⚠️ На срезе, где она обучена, её p — in-sample; честна только на другом срезе.
    growth_params = model_mod.load(model_mod.GROWTH_PARAMS)
    cands = load_candidates(folder)
    if args.limit:
        cands = cands[: args.limit]
    growth: dict[tuple[str, str], dict] = {}
    with (folder / "backtest.tsv").open(encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f, delimiter="\t"):
            growth[(r["домен"], r["направление"])] = r
    print(
        f"срез {cut}: кандидатов {len(cands)} "
        f"(ТОП {sum(c['where'] == 'ТОП' for c in cands)})"
    )
    out = folder / "backtest-classifier.tsv"
    prior = {} if args.reassess else read_prior(out)

    # След ядер и ярлыков ПОД СРЕЗОМ и годом раньше — для momentum (кэш по срезу).
    names = sorted({str(c.get("core") or c["label"]) for c in cands} | {c["label"] for c in cands})
    with Store(settings.storage_target) as store:
        print(f"след {len(names)} имён под срезом {cut} и годом раньше (кэш по срезу)...")
        at_cut = footprints_at(store, names, cut)
        year_before = footprints_at(store, names, cut - timedelta(days=365))
        cores = sorted({str(c.get("core") or c["label"]) for c in cands})
        print(f"доля индустриальных работ у {len(cores)} ядер под срезом (кэш по срезу)...")
        held = asof.AS_OF
        asof.AS_OF = cut
        try:
            industry = industry_share(store, cores)
        finally:
            asof.AS_OF = held
        nbrs: list = [None] * len(cands)
        if args.embed:
            found, cov, _ = neighbourhoods(
                store, [c["label"] for c in cands], cut, say=lambda m: print(f"  {m}")
            )
            nbrs = list(found)
            print(
                f"  покрытие векторами под срезом: последнее окно {cov.last:.0%}, "
                f"предыдущее {cov.prev:.0%}, всё до {cov.asof} {cov.before:.0%}"
            )

    rows = [to_row(c, at_cut) for c in cands]
    for row, nbr in zip(rows, nbrs, strict=True):
        row.nbr = nbr
    todo = [i for i, c in enumerate(cands) if (c["domain"], c["label"]) not in prior]
    got: list[Assessment | None] = [None] * len(cands)
    for i, c in enumerate(cands):
        p = prior.get((c["domain"], c["label"]))
        if p is not None:
            judged = {k: float(p[k]) for k in JUDGMENT_NAMES if p.get(k, "") != ""}
            got[i] = Assessment(c["label"], p["gemma сегодня"], "", "", [], judged)
    if todo:
        print(f"оценка {len(todo)} кандидатов моделью (чтения J)...", flush=True)
        fresh = assess_many(
            [to_candidate(rows[i], cands[i]["area"]) for i in todo],
            say=lambda m: print(f"  {m}", flush=True),
        )
        for i, a in zip(todo, fresh, strict=True):
            got[i] = a
    else:
        print("чтения J взяты из прошлого TSV (--reassess — переоценить)")

    # Денежный след по трём окнам: два года ПОСЛЕ среза — исход, год до среза и год до
    # того — признаки. Один и тот же инструмент, чтобы исход и признак мерялись одинаково.
    labels = [c["label"] for c in cands]
    say = lambda m: print(f"  {m}", flush=True)  # noqa: E731
    after = trace(labels, cut, cut + timedelta(days=730), say)
    last = trace(labels, cut - timedelta(days=365), cut, say)
    prev = trace(labels, cut - timedelta(days=730), cut - timedelta(days=365), say)
    zeros = sum(t.empty for t in after)
    print(f"  после среза без денежных заголовков: {zeros} из {len(after)}")

    out_rows = []
    for c, row, a, t_after, t_last, t_prev in zip(cands, rows, got, after, last, prev, strict=True):
        n_feats = features(row)
        j_feats = judgment_features(a)
        e_feats = embed_features(row)
        m_feats = momentum_features(c, cut, at_cut, year_before)
        d_feats = money_features(
            t_last, t_prev, cut, industry.get(str(c.get("core") or c["label"]))
        )
        feats = {
            **n_feats,
            **llm_features({"corpus": (c.get("kind", ""), c.get("stage", ""))}),
            **j_feats,
            **e_feats,
        }
        pred = model_mod.probability(feats, params)
        p_growth = ""
        if growth_params is not None:
            grown = model_mod.probability({**feats, **m_feats}, growth_params)
            p_growth = round(grown.probability, 3)
        g = growth.get((c["domain"], c["label"]), {})
        ratio = float(g.get("во сколько раз") or 0.0)
        out_rows.append(
            {
                "домен": c["domain"],
                "где": c["where"],
                "направление": c["label"],
                "gemma на срезе": c.get("kind", ""),
                "gemma сегодня": a.kind if a else "",
                "p модели": round(pred.probability, 3),
                "предикторы": "; ".join(pred.predictors),
                "p роста": p_growth,
                "во сколько раз": ratio,
                "новостей после среза": int(g.get("новостей после среза") or 0),
                "money_after_n": t_after.n,
                "money_after_usd": round(t_after.usd),
                **{k: _cell(m_feats[k]) for k in MOMENTUM_FEATURES},
                **{k: _cell(d_feats[k]) for k in MONEY_FEATURES},
                **{k: _cell(j_feats[k]) for k in JUDGMENT_FEATURES},
                **{k: _cell(n_feats[k]) for k in FEATURE_NAMES},
                **{k: _cell(e_feats[k]) for k in EMBED_FEATURES},
            }
        )
    outcomes(out_rows)

    with out.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(out_rows[0]), delimiter="\t")
        w.writeheader()
        w.writerows(out_rows)

    scorers = {
        "классификатор (p)": [r["p модели"] for r in out_rows],
        "gemma на срезе (emerging)": [float(r["gemma на срезе"] == EMERGING) for r in out_rows],
        "gemma сегодня (emerging)": [float(r["gemma сегодня"] == EMERGING) for r in out_rows],
        "ТОП конвейера": [float(r["где"] == "ТОП") for r in out_rows],
        "игроков в карточке": [float(r["players"] or 0) for r in out_rows],
        "momentum ядра": [float(r["core_momentum"] or 0) for r in out_rows],
        "денег за год до среза": [float(r["money_last_n"] or 0) for r in out_rows],
        "momentum денег": [float(r["money_momentum"] or 0) for r in out_rows],
        "доля индустриальных работ": [float(r["industry_share"] or 0) for r in out_rows],
    }
    if growth_params is not None:
        # На чужом срезе это temporal holdout установленной модели роста (обучена на
        # 2024, проверена на 2022 — обратное направление); на своём — in-sample.
        scorers["модель роста (p)"] = [float(r["p роста"] or 0) for r in out_rows]
    summary: dict[str, dict] = {}
    for outcome in ("grew", "grew_excess", "top_tercile", "money_grew", "money_top"):
        flags = [bool(r[outcome]) for r in out_rows]
        n_pos = sum(flags)
        print(
            f"\n=== РАЗДЕЛЕНИЕ по исходу «{outcome}» (AUC Манна–Уитни; "
            f"положительных {n_pos} из {len(flags)}) ==="
        )
        for name, scores in scorers.items():
            pos = [s for s, g in zip(scores, flags, strict=True) if g]
            neg = [s for s, g in zip(scores, flags, strict=True) if not g]
            got_auc = auc(pos, neg)
            fl = floor(scores, flags)
            summary[f"{name} / {outcome}"] = {"auc": round(got_auc, 2), "пол": round(fl, 2)}
            print(f"  {name:28s} AUC {got_auc:.2f}  (пол случайности {fl:.2f})")

    # Деньги С НУЛЯ: среди направлений без денежных заголовков за год до среза — у каких
    # деньги появились после. Это и есть вопрос заказчика про слабый сигнал, потому что
    # уровень денег после среза предсказывается уровнем до него (широкая категория остаётся
    # широкой), а не зарождением. ⚠️ Подмножество определено ПОСЛЕ первого прогона
    # (post-hoc) на срезе 2024; слово «предсказывает» — только после среза 2022 (§86).
    zero_idx = [i for i, r in enumerate(out_rows) if float(r["money_last_n"] or 0) == 0]
    zero = [out_rows[i] for i in zero_idx]
    flags = [float(r["money_after_n"]) > 0 for r in zero]
    print(
        f"\n=== ДЕНЬГИ С НУЛЯ (post-hoc): без денег за год до среза {len(zero)}, "
        f"появились после у {sum(flags)} ==="
    )
    from_zero = {
        **{k: [v[i] for i in zero_idx] for k, v in scorers.items()},
        "изданий в карточке": [float(r["publishers_distinct"] or 0) for r in zero],
        "новостей в карточке": [float(r["news_found"] or 0) for r in zero],
    }
    for name, scores in from_zero.items():
        if len(scores) != len(zero) or len(set(scores)) < 2:
            continue
        pos = [s for s, g in zip(scores, flags, strict=True) if g]
        neg = [s for s, g in zip(scores, flags, strict=True) if not g]
        got_auc = auc(pos, neg)
        fl = floor(scores, flags)
        summary[f"{name} / money_from_zero"] = {"auc": round(got_auc, 2), "пол": round(fl, 2)}
        print(f"  {name:28s} AUC {got_auc:.2f}  (пол случайности {fl:.2f})")

    # Верх классификатора того же размера, что ТОП конвейера, по доменам.
    k_by_domain = {}
    for r in out_rows:
        k_by_domain.setdefault(r["домен"], 0)
        k_by_domain[r["домен"]] += r["где"] == "ТОП"
    top_cls: list[dict] = []
    for domain, k in k_by_domain.items():
        here = sorted(
            (r for r in out_rows if r["домен"] == domain), key=lambda r: -r["p модели"]
        )
        top_cls += here[:k]
    top_pipe = [r for r in out_rows if r["где"] == "ТОП"]
    overlap = len({r["направление"] for r in top_cls} & {r["направление"] for r in top_pipe})
    print(f"\n=== ВЕРХ классификатора (по {len(top_cls)}, как ТОП конвейера) ===")
    print(f"  совпадает с ТОП конвейера: {overlap} из {len(top_pipe)}")
    for name, group in (("ТОП конвейера", top_pipe), ("верх классификатора", top_cls)):
        ratios = [r["во сколько раз"] for r in group]
        grown = sum(1 for r in group if r["во сколько раз"] >= GROWTH)
        print(
            f"  {name:22s} медиана прироста ×{statistics.median(ratios):.2f}, "
            f"выросли {grown} ({grown / len(group):.0%}) {span(grown, len(group))}"
        )
        summary[name] = {
            "медиана": round(statistics.median(ratios), 2),
            "выросли": grown,
            "всего": len(group),
        }

    if not args.no_judge:
        # Покрытие списка методолога 2026 верхом классификатора — тем же судьёй.
        data = judge_match._dataset()
        covered: set[str] = set()
        prior: set[str] = set()
        jc = folder / "judge-coverage.tsv"
        if jc.exists():
            with jc.open(encoding="utf-8", newline="") as f:
                prior = {r["строка датасета"] for r in csv.DictReader(f, delimiter="\t")}
        items = [(r["направление"], data.get(r["домен"], [])) for r in top_cls]
        print(f"\nсудья: {len(items)} карточек верха классификатора против списка 2026...")
        judged = [it for it in items if it[1]]
        verdicts = asyncio.run(judge_match._judge_many(judged))
        for (_label, rows_), (n, _why) in zip(judged, verdicts, strict=True):
            if n:
                covered.add(rows_[n - 1])
        total = sum(len(v) for v in data.values())
        print(
            f"=== ПОКРЫТИЕ списка 2026: верх классификатора {len(covered)} строк из {total}; "
            f"ТОП конвейера (judge-coverage.tsv) {len(prior)}"
        )
        summary["покрытие 2026"] = {"классификатор": len(covered), "конвейер": len(prior)}

    (folder / "backtest-classifier.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\nвыписано: {out}")


if __name__ == "__main__":
    main()
