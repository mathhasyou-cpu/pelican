"""Модель роста: учится на том, что стало с кандидатами ПОСЛЕ среза, а не на датасете.

    python scripts/backtest_classifier.py --dir reports/bt-2024-09-15
    python scripts/train_growth_model.py --dir reports/bt-2024-09-15
    python scripts/train_growth_model.py --dir reports/bt-2024-09-15 --outcome money_grew

## Зачем

Модель этапа 1 (`weak/model.json`) на бэктесте рост не предсказывает: она обучена отвечать
«похоже ли на строку датасета 2026». Здесь цель обучения другая — **избыточный рост после
среза** (`excess`, Klavans, Boyack & Murdick 2020: рост сверх базового уровня), а признаки —
индикаторы эмерджентности на срезе (`M`: сообщество, деньги, видимость, новизна, momentum —
Carley, Newman, Porter & Garner 2018; Yoon 2012) плюс чтения `J`, числа `N` и соседство `E`.

## Как проверяется — до прогона

- **Leave-one-domain-out** (`GroupKFold` по домену запроса): модель не должна выучить
  «в Edge всё растёт». Рядом печатается обычная стратифицированная CV — разрыв между ними
  и есть утечка домена.
- Пол случайности — 95-й перцентиль AUC при перестановке исходов на тех же числах
  (как в `backtest_signals._separation`).
- Базовые линии на тех же строках: ТОП конвейера, модель этапа 1, gemma, одиночные
  индикаторы (`players`, `core_momentum`, `low_visibility`) — чтобы было видно, что несёт
  сигнал, а не только «модель лучше».

**Правило зачёта**: предсказательная сила показана, если LODO-AUC ≥ пола И ≥ AUC ТОП
конвейера на том же исходе И медиана прироста верха модели (того же размера, что ТОП
конвейера, по доменам) ≥ медианы верха конвейера. Не прошло — «признаки на срезе рост не
предсказывают», и это результат, не повод крутить пороги.

⚠️ ~200 кандидатов, 6 доменов, один срез: LODO снимает утечку домена, но не утечку эпохи.
Настоящая проверка — **temporal holdout** (`--test-dir`): обучить на срезе 2022 целиком,
проверить на 2024 (out-of-time validation, как у Klavans/Boyack):

    python scripts/train_growth_model.py --dir reports/bt-2022-09-15 --test-dir reports/bt-2024

Набор признаков для holdout выбирается по LODO на train (это же правило, что выше), на test
он только проверяется. Правило зачёта holdout — в докстринге `holdout`; отчёт —
`reports/weak-growth-holdout.*`. Исход обоих срезов обязан быть посчитан на одном горизонте
и одном наборе срезов корпуса (`backtest_signals.HORIZON_YEARS`, `weak.core.SLICES`).

## Против дрейфа эпохи — правила, записанные ДО среза 2020

Holdout 2022 → 2024 дал 0.61: признаки прессы и денег (игроки, новости, денежные
заголовки) на 2022 делят исход в одну сторону, на 2024 — в другую, и модель, выучившая
один период, вне его промахивается (temporal concept drift). Поэтому:

    python scripts/train_growth_model.py --dir reports/bt-2022-09-15,reports/bt-2024-09-15
        --test-dir reports/bt-2020-09-15

- `--dir a,b` — обучение на ПУЛЕ срезов (LODO по домену внутри пула): больше строк,
  меньше дисперсии коэффициентов.
- набор `S` — признаки, устойчивые по знаку между обучающими срезами (`stable_features`,
  `STABLE_MIN_GAP`); считается скриптом, не пишется рукой.
- парсимония `L1_MARGIN`: L1, если не хуже L2 на 0.02 (`pick_best`).
- зачёт на test: ≥ пола, ≥ ТОП конвейера, ≥ одиночного momentum ядра, медиана верха ≥
  медианы верха конвейера. Ожидание до прогона: 0.70–0.78 (одиночные устойчивые линии дают
  0.77–0.80 на 2024, интервал ±0.08). Не побит momentum — вывод «модель = momentum».

## Деньги (`--outcome money_grew` / `money_top`)

Исход — денежные заголовки за два года ПОСЛЕ среза сверх медианы домена (`weak.rounds`,
`backtest_classifier.outcomes`); наборы — `$` (денежный след ДО среза: recency / momentum /
breadth, семейство arXiv:2510.09465), `I` (доля работ ядра с компанией среди авторов, Wong &
Singh 2013) и они же поверх `M`, `J`, `N`. Отчёт и артефакт — `weak-money-model.*`,
`weak/money.json`.

**Правило зачёта для денег** — то же плюс одно: LODO-AUC лучшего набора ≥ AUC лучшей
одиночной денежной линии («денег за год до среза», «momentum денег») + `MONEY_MARGIN`.
Иначе вердикт — «деньги предсказываются только деньгами»: сложить признаки не за что, и
это результат для отчёта, не повод крутить. «Медиана верха» для денег — медиана
`money_after_n` верха модели против верха конвейера. Ожидание до прогона: 0.65–0.75, не 0.9
— у Crunchbase-панелей сотни тысяч фирма-кварталов и сделки, у нас 196 строк и заголовки.

## Рост публикаций ИЛИ денег (`--outcome grew_or_money`)

Модель скоринга выдачи (`--in-query`) на цели «только публикации» ценит одну новизну:
вклад свежести ядра +1.25…+1.71 против +0.14 у «есть компании». У заказчика слабый сигнал
определён деньгами, поэтому исход — рост научного следа ИЛИ денег сверх медианы домена
(prominent impact у Rotolo и др. 2015), а к набору `Q` добавлен `Q+C` — коммерческая
стадия по чтениям свидетельств (`model.QUERY_COMMERCE`).

    python scripts/train_growth_model.py --in-query --outcome grew_or_money \
        --dir reports/bt-2022-09-15,reports/bt-2024-09-15 --test-dir reports/bt-2020-09-15 \
        --holdout-tag money-pool-to-2020     # и два других фолда

**Правило зачёта — записано до прогона**: гейт тот же, что у нынешней модели, на всех трёх
фолдах holdout; и на исходе `grew_or_money` выбранная модель на test не хуже нынешней
`weak/growth.json`, применённой к тем же строкам (`scripts/compare_growth_models.py`). Не
прошло — модель не ставится. Ожидание: коммерческие признаки получают ненулевой вес, AUC
0.70–0.85.

## Список заказчика (`--outcome listed`)

Исход — кандидат на срезе сопоставлен судьёй со строкой списка методолога 2026
(`scripts/label_listed.py`). Это метка, по которой нас судят, и она НЕ рост публикаций:
на трёх срезах рост следа ядра после среза делит её на 0.50–0.64, деньги до и после —
0.33–0.53. Приём — **экспертный список как метка, признаки за год до входа в список**
(Zhou et al., FTA 2018 / Scientometrics 2020; Kyebambe et al., TFSC 2017: положительный
класс — технология, впервые вошедшая в Gartner Hype Cycle в год T, признаки по T−1).

⚠️ Отрицательных здесь нет: `listed = 0` — «не сопоставлен с одной из 100 строк», а список
— выборка (неполнота пула, docs/weak-audit.md). Это **positive-unlabeled learning**
(Elkan & Noto 2008; Bekker & Davis 2020, arXiv:1811.04820), и отсюда три правила:
- обучать LR на PU-метке для РАНЖИРА законно: при SCAR `p(listed|x) = c · p(signal|x)`,
  порядок тот же (Elkan & Noto, лемма 1);
- AUC по PU-метке занижен по построению (Jain, White & Radivojac, AAAI 2017,
  arXiv:1702.00518): скрытые положительные среди неразмеченных считаются ошибками; потолок
  идеального ранжира `1 − π_u/2`. Оценка Элкана–Ното `c` — средний out-of-fold p на
  размеченных положительных, `π_u` от него — печатается в отчёте как ОЦЕНКА под SCAR;
- **правило зачёта — hits@k, а не AUC**: строк списка в верхе модели (того же размера,
  что ТОП конвейера, по доменам) ≥ строк списка в верхе линии `core_last_year_share` —
  нынешнего ключа порядка ТОП (`weak/ask.py`) — И ≥ верха старого ТОП конвейера; AUC ≥
  пола — справочно. Не побита линия доли свежих работ — вердикт «список заказчика на срезе
  не предсказывается лучше динамики следа ядра», `weak/listed.json` не ставится.
Наборы — `LISTED_SETS`: к `M`, `J`, `N`, `E` добавлены денежные `$` и `I` (слабый сигнал у
заказчика определён деньгами, docs/weak-signals.md); `S` считается от самого широкого.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import statistics
import sys
from datetime import date
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scipy.stats import spearmanr  # noqa: E402
from sklearn.impute import SimpleImputer  # noqa: E402
from sklearn.linear_model import LogisticRegression  # noqa: E402
from sklearn.metrics import roc_auc_score  # noqa: E402
from sklearn.model_selection import GroupKFold, StratifiedKFold  # noqa: E402
from sklearn.pipeline import Pipeline  # noqa: E402
from sklearn.preprocessing import StandardScaler  # noqa: E402

from pelican.weak.dataset import (  # noqa: E402
    EMBED_FEATURES,
    FEATURE_LABELS,
    FEATURE_NAMES,
    JUDGMENT_FEATURES,
    LOG_FEATURES,
)
from pelican.weak.kinds import EMERGING  # noqa: E402
from pelican.weak.model import (  # noqa: E402
    COMMERCE_SOURCE,
    QUERY_FEATURES,
    QUERY_LABELS,
    QUERY_SCIENCE,
    phrase,
    query_features,
)

MOMENTUM: tuple[str, ...] = (
    "players",
    "money_stream",
    "low_visibility",
    "core_age_at_cut",
    "core_momentum",
    "label_momentum",
)
LABELS = {
    **FEATURE_LABELS,
    "players": "независимых компаний в карточке",
    "money_stream": "кандидат из денежного потока",
    "low_visibility": "видимость ниже медианы пула",
    "core_age_at_cut": "возраст ядра на срезе, лет",
    "core_momentum": "работ ядра за год до среза к году до того",
    "label_momentum": "работ ярлыка за год до среза к году до того",
}
DOLLARS: tuple[str, ...] = (
    "money_last_n",
    "money_prev_n",
    "money_momentum",
    "money_last_usd",
    "money_recency_days",
    "money_publishers",
)
INDUSTRY: tuple[str, ...] = ("industry_share",)
LABELS.update(QUERY_LABELS)
LABELS.update(
    {
        "money_last_n": "денежных заголовков за год до среза",
        "money_prev_n": "денежных заголовков за год до того",
        "money_momentum": "денежных заголовков за год до среза к году до того",
        "money_last_usd": "распознанных сумм за год до среза, $",
        "money_recency_days": "дней от последнего денежного заголовка до среза",
        "money_publishers": "изданий с денежными заголовками за год до среза",
        "industry_share": "доля работ ядра с компанией среди авторов",
    }
)
LOGGED = LOG_FEATURES | {
    "players",
    "core_momentum",
    "label_momentum",
    "money_last_n",
    "money_prev_n",
    "money_momentum",
    "money_last_usd",
    "money_recency_days",
    "money_publishers",
}
SETS: dict[str, tuple[str, ...]] = {
    "M": MOMENTUM,
    "M+J": (*MOMENTUM, *JUDGMENT_FEATURES),
    "M+J+N": (*MOMENTUM, *JUDGMENT_FEATURES, *FEATURE_NAMES),
    "M+J+N+E": (*MOMENTUM, *JUDGMENT_FEATURES, *FEATURE_NAMES, *EMBED_FEATURES),
}
MONEY_SETS: dict[str, tuple[str, ...]] = {
    "$": DOLLARS,
    "$+I": (*DOLLARS, *INDUSTRY),
    "M+$+I": (*MOMENTUM, *DOLLARS, *INDUSTRY),
    "M+J+N+$+I": (*MOMENTUM, *JUDGMENT_FEATURES, *FEATURE_NAMES, *DOLLARS, *INDUSTRY),
}
#: Наборы для метки списка: денежные `$` и `I` поверх остальных (см. докстринг).
LISTED_SETS: dict[str, tuple[str, ...]] = {
    "M": MOMENTUM,
    "M+J+N": (*MOMENTUM, *JUDGMENT_FEATURES, *FEATURE_NAMES),
    "M+$+I": (*MOMENTUM, *DOLLARS, *INDUSTRY),
    "M+J+N+$+I": (*MOMENTUM, *JUDGMENT_FEATURES, *FEATURE_NAMES, *DOLLARS, *INDUSTRY),
    "M+J+N+$+I+E": (
        *MOMENTUM, *JUDGMENT_FEATURES, *FEATURE_NAMES, *DOLLARS, *INDUSTRY, *EMBED_FEATURES,
    ),
}
SINGLE: tuple[str, ...] = ("players", "core_momentum", "low_visibility", "money_stream")
#: Линия, которую модель списка обязана побить по hits@k: нынешний ключ порядка ТОП.
SHARE_LINE = "core_last_year_share"
SINGLE_LISTED: tuple[str, ...] = (*SINGLE, SHARE_LINE)
#: Одиночные денежные линии: правило для денег требует побить лучшую из них.
SINGLE_MONEY: tuple[str, ...] = ("money_last_n", "money_momentum", "money_recency_days")
MONEY_MARGIN = 0.03
#: `listed` — вошёл ли кандидат в список заказчика 2026 (`scripts/label_listed.py`): метка,
#: по которой нас судят, а не рост публикаций.
#: `grew_or_money` — рост научного следа ИЛИ денег сверх медианы домена: у заказчика слабый
#: сигнал определён деньгами (docs/weak-signals.md), и цель «только публикации» учила
#: модель скоринга ценить одну новизну.
OUTCOMES: tuple[str, ...] = (
    "grew_excess", "top_tercile", "money_grew", "money_top", "listed", "grew_or_money",
)
C_GRID = (0.03, 0.1, 0.3, 1.0)
SEED = 20260918
SHUFFLES = 200
OUT_MD = Path("reports/weak-growth-model.md")
OUT_JSON = Path("reports/weak-growth-model.json")
INSTALL_JSON = REPO / "src" / "pelican" / "weak" / "growth.json"
MONEY_MD = Path("reports/weak-money-model.md")
MONEY_JSON = Path("reports/weak-money-model.json")
MONEY_INSTALL = REPO / "src" / "pelican" / "weak" / "money.json"
LISTED_MD = Path("reports/weak-listed-model.md")
LISTED_JSON = Path("reports/weak-listed-model.json")
LISTED_INSTALL = REPO / "src" / "pelican" / "weak" / "listed.json"


def read_tsv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def in_query(rows: list[dict]) -> list[dict]:
    """Пул скоринга выдачи: только `emerging` по gemma (фильтр мейнстрима стоит ДО
    скоринга, как в `weak.ask`), и признаки `QUERY_FEATURES`, посчитанные внутри каждого
    запроса — домена одного среза — той же функцией, что в выдаче."""
    pool = [r for r in rows if r["gemma на срезе"] == EMERGING]
    for key in sorted({(r.get("срез", ""), r["домен"]) for r in pool}):
        group = [r for r in pool if (r.get("срез", ""), r["домен"]) == key]
        raw = [
            {
                "core_last_year_share": float(r["core_last_year_share"])
                if r.get("core_last_year_share") not in ("", None)
                else None,
                "core_works": float(r.get("core_works") or 0.0),
                "players": float(r.get("players") or 0.0),
                "money_stream": float(r.get("money_stream") or 0.0),
                **{src: float(r.get(src) or 0.0) for src in COMMERCE_SOURCE.values()},
            }
            for r in group
        ]
        for r, feats in zip(group, query_features(raw)):
            r.update({k: str(v) for k, v in feats.items()})
    return pool


def column(rows: list[dict], name: str) -> np.ndarray:
    out = []
    for r in rows:
        v = r.get(name, "")
        if v in ("", None):
            out.append(math.nan)
        else:
            x = float(v)
            out.append(math.log1p(max(x, 0.0)) if name in LOGGED else x)
    return np.array(out, dtype=float)


def usable(rows: list[dict], names: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(n for n in names if not np.isnan(column(rows, n)).all())


def matrix(rows: list[dict], names: tuple[str, ...]) -> np.ndarray:
    return np.column_stack([column(rows, n) for n in names])


def outcome_vector(rows: list[dict], outcome: str) -> np.ndarray:
    if outcome == "grew_or_money":
        return np.array(
            [int(float(r["grew_excess"]) > 0 or float(r["money_grew"]) > 0) for r in rows]
        )
    return np.array([int(float(r[outcome])) for r in rows])


def is_money(outcome: str) -> bool:
    return outcome.startswith("money_")


def value_column(outcome: str) -> str:
    """Сырой исход, по которому считается «медиана верха»: прирост или деньги после среза."""
    if outcome == "listed":
        return "listed"
    return "money_after_n" if is_money(outcome) else "во сколько раз"


def pipeline(c: float, penalty: str) -> Pipeline:
    solver = "liblinear" if penalty == "l1" else "lbfgs"
    return Pipeline(
        [
            ("impute", SimpleImputer(strategy="median")),
            ("scale", StandardScaler()),
            (
                "est",
                LogisticRegression(
                    C=c, penalty=penalty, solver=solver, class_weight="balanced", max_iter=3000
                ),
            ),
        ]
    )


def oof_scores(
    x: np.ndarray, y: np.ndarray, groups: np.ndarray, penalty: str, lodo: bool
) -> np.ndarray:
    """Out-of-fold вероятности: по доменам (LODO) или обычной стратифицированной CV.
    `C` — по внутренней CV на обучающей части каждого фолда (nested)."""
    n_groups = len(set(groups.tolist()))
    splits = (
        GroupKFold(n_splits=n_groups).split(x, y, groups)
        if lodo
        else StratifiedKFold(n_splits=5, shuffle=True, random_state=SEED).split(x, y)
    )
    out = np.zeros(len(y))
    for tr, te in splits:
        model = pipeline(best_c(x[tr], y[tr], penalty), penalty).fit(x[tr], y[tr])
        out[te] = model.predict_proba(x[te])[:, 1]
    return out


def best_c(x: np.ndarray, y: np.ndarray, penalty: str) -> float:
    """`C` по внутренней 3-fold CV на переданных строках (nested: тест их не видит)."""
    chosen, chosen_auc = C_GRID[0], -1.0
    inner = StratifiedKFold(n_splits=3, shuffle=True, random_state=SEED)
    for c in C_GRID:
        pred = np.zeros(len(y))
        for itr, ite in inner.split(x, y):
            m = pipeline(c, penalty).fit(x[itr], y[itr])
            pred[ite] = m.predict_proba(x[ite])[:, 1]
        a = roc_auc_score(y, pred) if len(set(y.tolist())) > 1 else 0.5
        if a > chosen_auc:
            chosen, chosen_auc = c, a
    return chosen


def auc_floor(scores: np.ndarray, y: np.ndarray) -> float:
    rnd = random.Random(SEED)
    labels = y.tolist()
    got = []
    for _ in range(SHUFFLES):
        rnd.shuffle(labels)
        got.append(roc_auc_score(labels, scores))
    got.sort()
    return float(got[int(0.95 * len(got))])


def top_median(
    rows: list[dict], scores: np.ndarray, value: str = "во сколько раз"
) -> tuple[float, int]:
    """Медиана исхода `value` у верха модели того же размера, что ТОП конвейера, по доменам."""
    k = {}
    for r in rows:
        k[r["домен"]] = k.get(r["домен"], 0) + (r["где"] == "ТОП")
    chosen: list[float] = []
    for domain, kk in k.items():
        idx = [i for i, r in enumerate(rows) if r["домен"] == domain]
        idx.sort(key=lambda i: -scores[i])
        chosen += [float(rows[i][value]) for i in idx[:kk]]
    return top_stat(chosen, value), len(chosen)


def top_stat(values: list[float], value: str) -> float:
    """Медиана исхода верха; для метки списка — СКОЛЬКО строк верха в списке (медиана
    двоичного столбца бессмысленна)."""
    if value == "listed":
        return float(sum(values))
    return statistics.median(values)


#: Правило устойчивости признака между срезами (записано до среза 2020): одномерный AUC по
#: исходу на КАЖДОМ обучающем срезе лежит по одну сторону от 0.5, и хотя бы на одном
#: отстоит от 0.5 не меньше чем на `STABLE_MIN_GAP`. Приём — adversarial validation /
#: отбор по временному дрейфу (arXiv:2004.03045): признак, у которого знак вклада
#: меняется от периода к периоду, вне своего периода тянет вниз. Замер 2022/2024: у
#: игроков, новостей и денег AUC 0.43–0.48 на одном срезе и 0.59–0.70 на другом.
STABLE_MIN_GAP = 0.10
#: Парсимония: L1 предпочитается L2 на том же наборе, если LODO-AUC не хуже на столько.
#: То же правило, что у модели этапа 1 (one-standard-error rule, ESL §7.10); записано до
#: среза 2020 — на holdout 2022 → 2024 L1 давал 0.81 против 0.61 у L2, но post-hoc.
L1_MARGIN = 0.02


def univariate_auc(rows: list[dict], name: str, outcome: str) -> float | None:
    """AUC самого признака (медианная импутация); `None` — столбец пуст или постоянен."""
    x = column(rows, name)
    if np.isnan(x).all():
        return None
    x = np.where(np.isnan(x), np.nanmedian(x), x)
    if len(set(x.tolist())) < 2:
        return None
    y = outcome_vector(rows, outcome)
    if len(set(y.tolist())) < 2:
        return None
    return float(roc_auc_score(y, x))


def stable_features(
    by_cut: dict[str, list[dict]], names: tuple[str, ...], outcome: str
) -> tuple[str, ...]:
    """Набор `S`: признаки, устойчивые по знаку между обучающими срезами (см. `STABLE_MIN_GAP`).
    Печатает таблицу одномерных AUC по срезам — список признаков не пишется рукой."""
    cuts = list(by_cut)
    print(f"\n=== устойчивость признаков между срезами {', '.join(cuts)} ===")
    kept: list[str] = []
    for n in names:
        aucs = [univariate_auc(by_cut[c], n, outcome) for c in cuts]
        if any(a is None for a in aucs):
            print(f"  {n:24s} " + "  ".join("  -  " if a is None else f"{a:.2f}" for a in aucs))
            continue
        same_side = all(a > 0.5 for a in aucs) or all(a < 0.5 for a in aucs)
        strong = max(abs(a - 0.5) for a in aucs) >= STABLE_MIN_GAP
        ok = same_side and strong
        print(f"  {n:24s} " + "  ".join(f"{a:.2f}" for a in aucs) + ("  ← S" if ok else ""))
        if ok:
            kept.append(n)
    print(f"  S = {', '.join(kept) or '—'}")
    return tuple(kept)


def pick_best(results: dict[str, dict], live_sets: dict[str, tuple[str, ...]]) -> str:
    """Лучший ключ по LODO с парсимонией: внутри набора L1, если он не хуже L2 на
    `L1_MARGIN`; между наборами — максимум выбранного."""
    chosen: dict[str, float] = {}
    for set_name in live_sets:
        l1, l2 = f"lr_l1 / {set_name}", f"lr_l2 / {set_name}"
        if l1 not in results or l2 not in results:
            continue
        key = l1 if results[l1]["auc"] >= results[l2]["auc"] - L1_MARGIN else l2
        chosen[key] = results[key]["auc"]
    return max(chosen, key=chosen.get)


def pu_ceiling(oof: np.ndarray, y: np.ndarray) -> dict[str, float]:
    """Оценка Элкана–Ното под SCAR: `c` — средний out-of-fold p на размеченных
    положительных; доля положительных в выборке `π = P(s=1)/c`; среди неразмеченных
    `π_u = (π − P(s=1)) / (1 − P(s=1))`; потолок AUC идеального ранжира по PU-метке
    `1 − π_u/2` (Jain, White & Radivojac 2017). ⚠️ Оценка, не замер: SCAR у методолога
    не проверялся, `c` по 100–200 строкам шумит."""
    labelled = float(y.mean())
    c = float(np.clip(oof[y == 1].mean(), 1e-6, 1.0))
    prior = min(labelled / c, 1.0)
    pi_u = max((prior - labelled) / (1 - labelled), 0.0) if labelled < 1 else 0.0
    return {"c": round(c, 3), "pi_u": round(pi_u, 3), "auc_ceiling": round(1 - pi_u / 2, 3)}


def baselines_of(rows: list[dict], money: bool, listed: bool = False) -> dict[str, np.ndarray]:
    """Базовые линии на тех же строках: ТОП конвейера, модель этапа 1, gemma, одиночные;
    для метки списка — ещё нынешний ключ порядка ТОП (`SHARE_LINE`)."""
    singles = (*SINGLE, *SINGLE_MONEY) if money else SINGLE_LISTED if listed else SINGLE
    out = {
        "ТОП конвейера": np.array([float(r["где"] == "ТОП") for r in rows]),
        "модель этапа 1 (p)": np.array([float(r["p модели"]) for r in rows]),
        "gemma на срезе": np.array([float(r["gemma на срезе"] == EMERGING) for r in rows]),
        **{f"только {LABELS[n]}": np.nan_to_num(column(rows, n), nan=0.0) for n in singles},
    }
    if money:
        # Давность считается «чем меньше, тем денежнее»: как линия — со знаком минус.
        key = f"только {LABELS['money_recency_days']}"
        out[key] = -np.nan_to_num(column(rows, "money_recency_days"), nan=1e9)
        # Модель роста (публикаций) как базовая линия для денег: одно ли это?
        growth_p = column(rows, "p роста") if "p роста" in rows[0] else None
        if growth_p is not None and not np.isnan(growth_p).all():
            out["модель роста (p)"] = np.nan_to_num(growth_p, nan=0.0)
    return out


def holdout(
    train: list[dict],
    test: list[dict],
    outcome: str,
    sets: dict[str, tuple[str, ...]],
    chosen: str,
    out_md: Path,
    out_json: Path,
    dirs: tuple[str, str],
) -> dict:
    """Temporal holdout: обучить на одном срезе целиком, проверить на другом.

    Приём — rolling-origin / out-of-time validation (Klavans & Boyack): LODO снимает
    утечку домена, но не утечку эпохи. ⚠️ Набор признаков `chosen` выбран по LODO на
    train ДО взгляда на test — иначе это подгонка под тест; остальные наборы печатаются
    справкой. Правило зачёта: test-AUC выбранного набора ≥ пола на test И ≥ AUC ТОП
    конвейера на test И медиана верха на test ≥ медианы верха конвейера на test.
    """
    money = is_money(outcome)
    listed = outcome == "listed"
    value = value_column(outcome)
    unit = "" if money or listed else "×"
    excess_col = "money_excess" if money else "excess"
    y_tr = outcome_vector(train, outcome)
    y_te = outcome_vector(test, outcome)
    pipe_median = top_stat([float(r[value]) for r in test if r["где"] == "ТОП"], value)
    results: dict[str, dict] = {}
    print(
        f"\n=== HOLDOUT: обучение на {len(train)} строках, проверка на {len(test)} "
        f"(положительных {int(y_te.sum())}), исход «{outcome}» ==="
    )
    for name, scores in baselines_of(test, money, listed).items():
        a = roc_auc_score(y_te, scores)
        results[name] = {"auc": round(float(a), 3), "floor": round(auc_floor(scores, y_te), 3), "kind": "baseline"}  # noqa: E501
        if listed:
            # hits@k у базовой линии: сколько строк списка в её верхе того же размера.
            results[name]["top_median"] = round(top_median(test, scores, value)[0], 2)
        print(f"  {name:44s} AUC {a:.2f}  (пол {results[name]['floor']:.2f})")
    for set_name, wanted in sets.items():
        # Столбец, пустой на любом из срезов, выбрасывается на обоих.
        names = tuple(n for n in usable(train, wanted) if n in usable(test, wanted))
        if not names:
            continue
        x_tr, x_te = matrix(train, names), matrix(test, names)
        for penalty in ("l2", "l1"):
            key = f"lr_{penalty} / {set_name}"
            model = pipeline(best_c(x_tr, y_tr, penalty), penalty).fit(x_tr, y_tr)
            p = model.predict_proba(x_te)[:, 1]
            a = roc_auc_score(y_te, p)
            rho = spearmanr(p, [float(r[excess_col]) for r in test]).correlation
            med, _k = top_median(test, p, value)
            results[key] = {
                "auc": round(float(a), 3),
                "floor": round(auc_floor(p, y_te), 3),
                "spearman_excess": round(float(rho), 3),
                "top_median": round(med, 2),
                "features": list(names),
                "kind": "model",
            }
            print(
                f"  {key:44s} test-AUC {a:.2f} (пол {results[key]['floor']:.2f}); "
                f"Спирмен с {excess_col} {rho:+.2f}; медиана верха {unit}{med:.2f}"
            )
    top_auc = results["ТОП конвейера"]["auc"]
    # Модель обязана бить одиночную линию momentum на тесте — иначе она не нужна.
    single_key = f"только {LABELS['core_momentum']}"
    single_auc = results[single_key]["auc"] if not money else 0.0
    got = results.get(chosen)
    if listed:
        # Метка списка — PU: правило по hits@k против линии, которую модель заменяет
        # в порядке ТОП (`SHARE_LINE`), и против старого ТОП; AUC — только ≥ пола.
        share_hits = results[f"только {LABELS[SHARE_LINE]}"]["top_median"]
        passed = (
            got is not None
            and got["auc"] >= got["floor"]
            and got["top_median"] >= share_hits
            and got["top_median"] >= pipe_median
        )
        verdict = (
            f"выбранный по LODO на train набор «{chosen}»: "
            + (
                f"строк списка в верхе {got['top_median']:.0f} против {share_hits:.0f} у линии "
                f"«доля свежих работ ядра» и {pipe_median:.0f} у старого ТОП; test-AUC "
                f"{got['auc']:.2f} при поле {got['floor']:.2f}"
                if got
                else "на test не считается (нет общих столбцов)"
            )
            + f" → правило holdout {'ВЫПОЛНЕНО' if passed else 'НЕ выполнено'}"
        )
    else:
        passed = (
            got is not None
            and got["auc"] >= got["floor"]
            and got["auc"] >= top_auc
            and got["auc"] >= single_auc
            and got["top_median"] >= pipe_median
        )
        verdict = (
            f"выбранный по LODO на train набор «{chosen}»: "
            + (
                f"test-AUC {got['auc']:.2f} при поле {got['floor']:.2f}, ТОП конвейера {top_auc:.2f}, "
                f"один momentum {single_auc:.2f}; медиана верха {unit}{got['top_median']:.2f} "
                f"против {unit}{pipe_median:.2f} у конвейера"
                if got
                else "на test не считается (нет общих столбцов)"
            )
            + f" → правило holdout {'ВЫПОЛНЕНО' if passed else 'НЕ выполнено'}"
        )
    print("\n" + verdict)
    lines = [
        "# Temporal holdout: обучено на одном срезе, проверено на другом",
        "",
        f"Сгенерировано `scripts/train_growth_model.py --test-dir` {date.today():%d.%m.%Y}: "
        f"обучение — `{dirs[0]}` ({len(train)} кандидатов), проверка — `{dirs[1]}` ({len(test)}, "
        f"{int(y_te.sum())} положительных), исход «{outcome}».",
        "",
        "Приём — out-of-time validation (rolling origin): LODO снимает утечку домена, но не "
        "утечку эпохи. Набор признаков выбран по LODO на train до взгляда на test.",
        "",
        (
            f"**Правило зачёта** (до прогона, hits@k — метка PU): строк списка в верхе модели "
            f"того же размера, что ТОП конвейера, ≥ чем у линии «доля свежих работ ядра» "
            f"(нынешний ключ порядка ТОП) и ≥ {pipe_median:.0f} (старый ТОП на test); "
            f"test-AUC ≥ пола. **{verdict}.**"
            if listed
            else f"**Правило зачёта** (до прогона): test-AUC выбранного набора ≥ пола на test, "
            f"≥ AUC ТОП конвейера на test, ≥ одиночного momentum ядра на test, медиана верха ≥ "
            f"{unit}{pipe_median:.2f} (верх конвейера на test). "
            f"**{verdict}.**"
        ),
        "",
        "## На test",
        "",
        f"| оценщик | test-AUC | пол случайности | Спирмен с {excess_col} | "
        f"{'строк списка в верхе' if listed else 'медиана верха'} |",
        "|---|---|---|---|---|",
    ]
    for name, r in results.items():
        mark = " **←**" if name == chosen else ""
        rho = f"{r['spearman_excess']:+.2f}" if "spearman_excess" in r else "—"
        med = f"{unit}{r['top_median']:.2f}" if "top_median" in r else "—"
        lines.append(f"| {name}{mark} | {r['auc']:.2f} | {r['floor']:.2f} | {rho} | {med} |")
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    summary = {
        "outcome": outcome,
        "train_dir": dirs[0],
        "test_dir": dirs[1],
        "train": len(train),
        "test": len(test),
        "chosen": chosen,
        "verdict": verdict,
        "passed": bool(passed),
        "results": results,
        "pipeline_top_median": pipe_median,
    }
    out_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> None:
    ap = argparse.ArgumentParser(description="модель роста на бэктесте")
    ap.add_argument(
        "--dir", required=True, help="папка прогона под срезом; несколько — через запятую (пул)"
    )
    ap.add_argument("--outcome", default="grew_excess", choices=OUTCOMES, help="исход")
    ap.add_argument(
        "--install",
        action="store_true",
        help=f"при выполненном правиле записать модель в {INSTALL_JSON} (деньги: {MONEY_INSTALL})",
    )
    ap.add_argument(
        "--test-dir",
        help="temporal holdout: обучить на --dir целиком, проверить на этой папке "
        "(отчёт weak-growth-holdout.* / weak-money-holdout.* / weak-listed-holdout.*)",
    )
    ap.add_argument(
        "--drop-embed",
        action="store_true",
        help="убрать соседство в векторах `E` из всех наборов: замер цены `E` во времени "
        "живого `ask` (проход по шардам и маски окон — минуты, docs/todo.md §93)",
    )
    ap.add_argument(
        "--in-query",
        action="store_true",
        help="модель скоринга выдачи: пул `emerging`, признаки внутри запроса "
        "(QUERY_FEATURES, weak/model.py) — тот же пул и те же числа, что ранжирует `ask`",
    )
    ap.add_argument(
        "--holdout-tag",
        default="",
        help="суффикс файлов holdout (например pool-to-2020), чтобы фолды не затирали друг друга",
    )
    args = ap.parse_args()
    folders = [REPO / d.strip() for d in args.dir.split(",") if d.strip()]
    by_cut = {f.name: read_tsv(f / "backtest-classifier.tsv") for f in folders}
    for name, cut_rows in by_cut.items():
        for r in cut_rows:
            r["срез"] = name
    rows = [r for cut_rows in by_cut.values() for r in cut_rows]
    if args.in_query:
        rows = in_query(rows)
    folder = folders[0] if len(folders) == 1 else Path("+".join(f.name for f in folders))
    groups = np.array([r["домен"] for r in rows])
    n_dom = len(set(groups.tolist()))
    print(f"строк {len(rows)}, доменов {n_dom}, срезов {len(by_cut)}")

    money = is_money(args.outcome)
    listed = args.outcome == "listed"
    value = value_column(args.outcome)
    unit = "" if money or listed else "×"  # прирост — «×4.4», деньги — «65 заголовков»
    excess_col = "money_excess" if money else "excess"
    out_md, out_json, install_json = (
        (MONEY_MD, MONEY_JSON, MONEY_INSTALL)
        if money
        else (LISTED_MD, LISTED_JSON, LISTED_INSTALL)
        if listed
        else (OUT_MD, OUT_JSON, INSTALL_JSON)
    )
    y_main = outcome_vector(rows, args.outcome)
    pipe_median = top_stat([float(r[value]) for r in rows if r["где"] == "ТОП"], value)
    baselines = baselines_of(rows, money, listed)
    if args.in_query:
        # Линия, которую скоринг обязан держать: прежняя формула порядка ТОП.
        baselines["формула: доля ядра внутри запроса"] = column(rows, "query_share")

    results: dict[str, dict] = {}
    print(f"\n=== исход «{args.outcome}»: положительных {int(y_main.sum())} из {len(y_main)} ===")
    for name, scores in baselines.items():
        a = roc_auc_score(y_main, scores)
        fl = auc_floor(scores, y_main)
        results[name] = {"auc": round(float(a), 3), "floor": round(fl, 3), "kind": "baseline"}
        if listed:
            results[name]["top_median"] = round(top_median(rows, scores, value)[0], 2)
        print(f"  {name:44s} AUC {a:.2f}  (пол {fl:.2f})")

    sets = dict(MONEY_SETS if money else LISTED_SETS if listed else SETS)
    if args.drop_embed:
        sets = {k: tuple(f for f in v if f not in EMBED_FEATURES) for k, v in sets.items()}
    if args.in_query:
        # «Q+C» — те же признаки плюс коммерческая стадия; парсимония `pick_best` решает,
        # нужна ли она модели.
        sets = {"Q": QUERY_SCIENCE, "Q+C": QUERY_FEATURES}
    elif len(by_cut) > 1:
        # Устойчивый набор — только когда обучающих срезов больше одного: устойчивость
        # между периодами на одном периоде не меряется.
        widest = sets["M+J+N+$+I+E"] if listed else sets["M+J+N+$+I"] if money else sets["M+J+N+E"]
        sets["S"] = stable_features(by_cut, widest, args.outcome)
    live_sets = {k: usable(rows, v) for k, v in sets.items()}
    for k, v in sets.items():
        dropped = sorted(set(v) - set(live_sets[k]))
        if dropped:
            print(f"⚠️ набор {k}: пустые столбцы выброшены: {', '.join(dropped)}")
    best_key, best = None, None
    oofs: dict[str, np.ndarray] = {}
    for set_name, names in live_sets.items():
        if not names:
            continue
        x = matrix(rows, names)
        for penalty in ("l2", "l1"):
            key = f"lr_{penalty} / {set_name}"
            lodo = oof_scores(x, y_main, groups, penalty, lodo=True)
            oofs[key] = lodo
            plain = oof_scores(x, y_main, groups, penalty, lodo=False)
            a_lodo = roc_auc_score(y_main, lodo)
            a_plain = roc_auc_score(y_main, plain)
            fl = auc_floor(lodo, y_main)
            rho = spearmanr(lodo, [float(r[excess_col]) for r in rows]).correlation
            med, _k = top_median(rows, lodo, value)
            results[key] = {
                "auc": round(float(a_lodo), 3),
                "auc_plain_cv": round(float(a_plain), 3),
                "floor": round(fl, 3),
                "spearman_excess": round(float(rho), 3),
                "top_median": round(med, 2),
                "kind": "model",
            }
            print(
                f"  {key:44s} LODO-AUC {a_lodo:.2f} (обычная CV {a_plain:.2f}, пол {fl:.2f}); "
                f"Спирмен с {excess_col} {rho:+.2f}; медиана верха {unit}{med:.2f}"
            )

    best_key = pick_best(results, live_sets)
    best = results[best_key]
    best_oof = oofs[best_key]

    top_auc = results["ТОП конвейера"]["auc"]
    passed = (
        best is not None
        and best["auc"] >= best["floor"]
        and best["auc"] >= top_auc
        and best["top_median"] >= pipe_median
    )
    verdict = (
        f"лучший — «{best_key}»: LODO-AUC {best['auc']:.2f} при поле {best['floor']:.2f}, ТОП "
        f"конвейера {top_auc:.2f}; медиана верха {unit}{best['top_median']:.2f} против "
        f"{unit}{pipe_median:.2f} у конвейера"
    )
    ceiling: dict[str, float] = {}
    if listed:
        # Метка PU: зачёт по hits@k против нынешнего ключа порядка ТОП (см. докстринг);
        # AUC ТОП конвейера не требуется — сам ТОП тут не ранжир, а отбор.
        share_hits = results[f"только {LABELS[SHARE_LINE]}"]["top_median"]
        passed = (
            best is not None
            and best["auc"] >= best["floor"]
            and best["top_median"] >= share_hits
            and best["top_median"] >= pipe_median
        )
        ceiling = pu_ceiling(best_oof, y_main)
        verdict = (
            f"лучший — «{best_key}»: строк списка в верхе {best['top_median']:.0f} против "
            f"{share_hits:.0f} у линии «доля свежих работ ядра» и {pipe_median:.0f} у старого "
            f"ТОП; LODO-AUC {best['auc']:.2f} при поле {best['floor']:.2f} (оценка потолка по "
            f"PU-метке {ceiling['auc_ceiling']:.2f}: c = {ceiling['c']:.2f}, "
            f"π_u = {ceiling['pi_u']:.2f})"
        )
    if money:
        # Деньги предсказываются деньгами: сложная модель обязана бить одиночную линию.
        single_auc = max(results[f"только {LABELS[n]}"]["auc"] for n in SINGLE_MONEY)
        beats_single = best is not None and best["auc"] >= single_auc + MONEY_MARGIN
        passed = passed and beats_single
        verdict += (
            f"; лучшая одиночная денежная линия {single_auc:.2f} "
            f"({'побита' if beats_single else 'НЕ побита'} на {MONEY_MARGIN})"
        )
    verdict += f" → правило {'ВЫПОЛНЕНО' if passed else 'НЕ выполнено'}"
    if money and not passed:
        verdict += "; вердикт: деньги на срезе предсказываются только деньгами"
    print("\n" + verdict)

    if args.test_dir:
        test_rows = read_tsv(REPO / args.test_dir / "backtest-classifier.tsv")
        if args.in_query:
            test_rows = in_query(test_rows)
        stem = "weak-growth-holdout" + (".in-query" if args.in_query else "")
        if money:
            stem = "weak-money-holdout"
        elif listed:
            stem = "weak-listed-holdout"
        if args.holdout_tag:
            stem += f".{args.holdout_tag}"
        holdout(
            rows,
            test_rows,
            args.outcome,
            sets,
            best_key,
            Path("reports") / f"{stem}.md",
            Path("reports") / f"{stem}.json",
            (folder.name, Path(args.test_dir).name),
        )

    # Коэффициенты лучшего набора на всех строках — для отчёта и предикторов.
    est_name, set_name = best_key.split(" / ")
    names = live_sets[set_name]
    x = matrix(rows, names)
    final = pipeline(0.3, est_name.removeprefix("lr_")).fit(x, y_main)
    coef = final.named_steps["est"].coef_[0]
    order = np.argsort(-np.abs(coef))
    imp = final.named_steps["impute"].transform(x)
    z = (imp - final.named_steps["scale"].mean_) / np.where(
        final.named_steps["scale"].scale_ == 0, 1.0, final.named_steps["scale"].scale_
    )
    contrib = z * coef

    title = (
        "# Модель денег: что на срезе предсказывает деньги после него"
        if money
        else "# Модель списка заказчика: что на срезе предсказывает попадание в список 2026"
        if listed
        else "# Модель роста: что на срезе предсказывает рост после него"
    )
    methods = (
        "Приёмы: экспертный список как метка, признаки за год до входа в список (Zhou et al., "
        "FTA 2018 / Scientometrics 2020; Kyebambe et al., TFSC 2017 — Gartner Hype Cycle); метка "
        "positive-unlabeled (Elkan & Noto 2008; Bekker & Davis 2020): ранжир по ней законен под "
        "SCAR, AUC занижен по построению (Jain, White & Radivojac 2017) — зачёт по hits@k; "
        "денежный след до среза (`weak.rounds`, arXiv:2510.09465) и доля индустриальных работ "
        "ядра (Wong & Singh 2013); leave-one-domain-out CV; пол случайности перестановкой."
        if listed
        else
        "Приёмы: денежный след направления заголовками Google News по окну (`weak.rounds`) — "
        "заголовки, не сделки; признаки financing recency / momentum / breadth из панелей "
        "Crunchbase (arXiv:2510.09465: «деньги предсказываются деньгами»); доля работ ядра с "
        "компанией среди авторов — university–industry co-publication (Wong & Singh 2013, "
        "Scientometrics); исход сверх медианы домена (Klavans, Boyack & Murdick 2020); "
        "leave-one-domain-out CV (`GroupKFold`); пол случайности перестановкой исходов."
        if money
        else "Приёмы: избыточный рост над базовым уровнем домена и структура сообщества как "
        "признак (Klavans, Boyack & Murdick 2020, PLOS One); индикаторы эмерджентности — "
        "novelty / growth / community (Carley, Newman, Porter & Garner 2018); DoV (Yoon 2012); "
        "leave-one-domain-out CV (`GroupKFold`); пол случайности перестановкой исходов."
    )
    rule = (
        f"**Правило зачёта** (до прогона): LODO-AUC ≥ пола, ≥ AUC ТОП конвейера, медиана "
        f"денежных заголовков после среза у верха ≥ {pipe_median:.2f} (верх конвейера) и "
        f"≥ лучшей одиночной денежной линии + {MONEY_MARGIN}. **{verdict}.**"
        if money
        else f"**Правило зачёта** (до прогона, метка PU — hits@k): строк списка в верхе модели "
        f"того же размера, что ТОП конвейера, ≥ чем у линии «доля свежих работ ядра» "
        f"(нынешний ключ порядка ТОП в `weak/ask.py`) и ≥ {pipe_median:.0f} (старый ТОП); "
        f"LODO-AUC ≥ пола — справочно, рядом с оценкой потолка по PU-метке "
        f"(`1 − π_u/2`, Элкан–Ното под SCAR; оценка, не замер). **{verdict}.**"
        if listed
        else f"**Правило зачёта** (до прогона): LODO-AUC ≥ пола, ≥ AUC ТОП конвейера, медиана "
        f"прироста верха ≥ ×{pipe_median:.2f} (верх конвейера). **{verdict}.**"
    )
    lines = [
        title,
        "",
        f"Сгенерировано `scripts/train_growth_model.py` {date.today():%d.%m.%Y} из "
        f"`{folder.name}/backtest-classifier.tsv`: {len(rows)} кандидатов, {n_dom} доменов, "
        f"исход «{args.outcome}» ({int(y_main.sum())} положительных).",
        "",
        methods,
        "",
        rule,
        "",
        *(
            [
                f"⚠️ Метка PU: `listed = 0` — «не сопоставлен с одной из 100 строк», а не «не "
                f"сигнал». Оценка Элкана–Ното по OOF-p лучшего набора: c = {ceiling['c']:.2f}, "
                f"доля скрытых положительных среди неразмеченных π_u = {ceiling['pi_u']:.2f}, "
                f"потолок AUC идеального ранжира {ceiling['auc_ceiling']:.2f}. Это оценка под "
                f"SCAR, не замер.",
                "",
            ]
            if ceiling
            else []
        ),
        "⚠️ Один срез: LODO снимает утечку домена, но не утечку эпохи — обучение и проверка "
        "на одном периоде. Настоящая проверка — срез 2022 → проверка на 2024 (docs/todo.md §86).",
        "",
        "## Базовые линии и модели",
        "",
        f"| оценщик | AUC (LODO) | обычная CV | пол случайности | Спирмен с {excess_col} "
        f"| {'строк списка в верхе' if listed else 'медиана верха'} |",
        "|---|---|---|---|---|---|",
    ]
    for name, r in results.items():
        mark = " **←**" if name == best_key else ""
        plain = f"{r['auc_plain_cv']:.2f}" if "auc_plain_cv" in r else "—"
        rho = f"{r['spearman_excess']:+.2f}" if "spearman_excess" in r else "—"
        med = f"{unit}{r['top_median']:.2f}" if "top_median" in r else "—"
        lines.append(
            f"| {name}{mark} | {r['auc']:.2f} | {plain} | {r['floor']:.2f} | {rho} | {med} |"
        )
    lines += [
        "",
        "⚠️ Разрыв «обычная CV» − «LODO» у модели — то, что она выучила про ДОМЕН, а не про рост.",
        "",
        f"## Коэффициенты лучшего набора (`{best_key}`, C=0.3, все строки)",
        "",
        "| признак | коэффициент | читается |",
        "|---|---|---|",
    ]
    for i in order:
        c = float(coef[i])
        n = names[i]
        read = (
            "не участвует" if c == 0 else
            f"чем больше «{LABELS.get(n, n)}», тем {'выше' if c > 0 else 'ниже'} шанс "
            f"{'денег' if money else 'попасть в список' if listed else 'роста'}"
        )  # fmt: skip
        lines.append(f"| `{n}` — {LABELS.get(n, n)} | {c:+.2f} | {read} |")
    lines += ["", "## Пять строк с разложением", ""]
    picks = list(range(0, len(rows), max(len(rows) // 5, 1)))[:5]
    for i in picks:
        parts = sorted(zip(names, contrib[i], x[i], strict=True), key=lambda t: -abs(t[1]))[:3]
        top = ", ".join(
            phrase(n, None if math.isnan(v) else float(v), float(p)) for n, p, v in parts
        )
        lines.append(
            f"- **{rows[i]['направление']}** ({rows[i]['где']}, "
            f"{unit}{float(rows[i][value]):.1f}, "
            f"{args.outcome}={int(y_main[i])}): {top}"
        )
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # Модель роста в формате `weak/model.py`: Платт — на out-of-fold отступах LODO.
    margin_oof = np.log(np.clip(best_oof, 1e-6, 1 - 1e-6) / np.clip(1 - best_oof, 1e-6, 1))
    platt = LogisticRegression(C=1e6, max_iter=5000).fit(margin_oof.reshape(-1, 1), y_main)
    params = {
        "trained": date.today().isoformat(),
        "source": f"train_growth_model {folder.name} / {args.outcome}",
        "winner": best_key,
        "features": list(names),
        "impute_median": [float(v) for v in final.named_steps["impute"].statistics_],
        "scale_mean": [float(v) for v in final.named_steps["scale"].mean_],
        "scale_std": [float(v) for v in final.named_steps["scale"].scale_],
        "coef": [float(v) for v in coef],
        "intercept": float(final.named_steps["est"].intercept_[0]),
        "platt": {"a": float(platt.coef_[0][0]), "b": float(platt.intercept_[0])},
        "log1p": sorted(LOGGED & set(names)),
        "metrics": {k: best[k] for k in ("auc", "auc_plain_cv", "floor", "top_median")},
        "gate": {"passed": bool(passed), "top_auc": top_auc, "pipeline_top_median": pipe_median},
        **({"pu_ceiling": ceiling} if ceiling else {}),
        "rows": {"total": len(rows), "positive": int(y_main.sum()), "domains": n_dom},
    }
    out_json.with_name(out_json.stem.removesuffix("-model") + "-params.json").write_text(
        json.dumps(params, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if args.install:
        if not passed:
            print(f"⚠️ правило не выполнено — в {install_json} не пишу")
        else:
            install_json.write_text(
                json.dumps(params, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(f"установлено: {install_json}")
    out_json.write_text(
        json.dumps(
            {
                "outcome": args.outcome,
                "verdict": verdict,
                "passed": bool(passed),
                "best": best_key,
                "results": results,
                "pipeline_top_median": pipe_median,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\nотчёт: {out_md}\nтаблица: {out_json}")


if __name__ == "__main__":
    main()
