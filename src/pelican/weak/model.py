"""Применение обученной модели слабого сигнала без sklearn.

Параметры — `weak/model.json`, копия `reports/weak-model.json` из
`scripts/train_signal_model.py`: порядок признаков, медианы импутации, среднее и σ
стандартизации, коэффициенты логистической регрессии и сигмоида Платта. Здесь они
применяются на numpy: выдача (`trends ask`, `predict_dataset.py`) sklearn не тянет.

⚠️ Модель в выдачу идёт только если правило решения из обучения выполнено
(`gate.passed` в JSON): `load()` возвращает `None` и тогда, когда файла нет, и тогда,
когда правило провалено, — страница остаётся с долей проверок (docs/weak-model.md).

⚠️ Вероятность и «ключевые предикторы» — из ОДНОГО и того же линейного разложения:
вклад признака = коэффициент × стандартизованное значение, предикторы — три
наибольших по модулю. Это не объяснение постфактум, а то, из чего сложилась сумма.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

from pelican.weak.dataset import FEATURE_LABELS, JUDGMENT_FEATURES
from pelican.weak.rounds import MONEY_LABELS

PARAMS = Path(__file__).with_name("model.json")
#: Модель роста — тот же формат, другая цель обучения: не «похоже на датасет», а «вырос
#: после среза» (`scripts/train_growth_model.py`, docs/weak-audit.md).
GROWTH_PARAMS = Path(__file__).with_name("growth.json")
#: Модель списка заказчика — тот же формат, цель обучения «вошёл в список методолога 2026»
#: на трёх срезах бэктеста (`scripts/train_growth_model.py --outcome listed`,
#: docs/weak-audit.md). Её вероятность — первый ключ порядка ТОП в `weak/ask.py`.
LISTED_PARAMS = Path(__file__).with_name("listed.json")
#: Признаки моделей роста и списка сверх наборов `weak.dataset` (индикаторы эмерджентности:
#: сообщество, деньги, видимость, новизна, momentum — `scripts/backtest_classifier.py`;
#: денежный след — `weak.rounds.MONEY_LABELS`).
MOMENTUM_LABELS: dict[str, str] = {
    "players": "независимых компаний в карточке",
    "money_stream": "кандидат из денежного потока",
    "low_visibility": "видимость ниже медианы пула",
    "core_age_at_cut": "возраст ядра, лет",
    "core_momentum": "работ ядра за последний год к году до того",
    "label_momentum": "работ ярлыка за последний год к году до того",
    **MONEY_LABELS,
    "industry_share": "доля работ ядра с компанией среди авторов",
}
#: Сколько признаков назвать предикторами.
TOP = 3

#: Потолок премии за число игроков: больше четырёх — это уже широта названия, а не сила
#: сигнала. ⚠️ Догадка, общая для отбора в оценку (`weak.ask`) и для признака скоринга.
PLAYERS_CAP = 4
#: Псевдосчёт сжатия доли свежих работ ядра к медиане пула (additive smoothing, как
#: «взвешенный рейтинг» IMDB): у ядра с 1–3 работами доля 1.0 стояла бы выше ядра с 776
#: работами и долей 0.97. ⚠️ Догадка: на пулах трёх срезов покрытие списка при m = 0…20
#: одинаково.
SHARE_PRIOR_WEIGHT = 10
#: Признаки скоринга, нормированные ВНУТРИ запроса (query-level normalization в
#: learning-to-rank): модель сравнивает кандидата с остальными кандидатами того же запроса,
#: а не с абсолютной шкалой. Абсолютная доля свежих работ дрейфует между эпохами, и модель
#: на ней ранжирует пул хуже простой формулы (бэктест трёх срезов: 0.81 / 0.71 / 0.60
#: против 0.85 / 0.82 / 0.73 на этих признаках).
QUERY_SCIENCE: tuple[str, ...] = ("query_share", "query_share_pct", "backed", "players_cap")
#: Коммерческая стадия — чтения свидетельств той же оценкой зрелости (`assess.JUDGMENTS`):
#: раунд, продажи, пилот. Двоичные, поэтому от эпохи не плывут так, как доли и счётчики;
#: кандидата с раундом модель видит, даже если у всех соседей по запросу раунд тоже есть.
QUERY_COMMERCE: tuple[str, ...] = ("funding", "commercial", "pilot")
QUERY_FEATURES: tuple[str, ...] = (*QUERY_SCIENCE, *QUERY_COMMERCE)
#: Чтение свидетельств (`assess.JUDGMENTS`), из которого берётся признак `QUERY_COMMERCE`.
COMMERCE_SOURCE: dict[str, str] = {
    "funding": "funding_mentioned",
    "commercial": "commercial_deployment",
    "pilot": "pilot_mentioned",
}
#: Подписи короткие: они идут в колонку «Ключевые предикторы» таблицы ТОП, а смысл каждого
#: признака расписан в блоке модели на странице и в docs/techdoc.md.
QUERY_LABELS: dict[str, str] = {
    "query_share": "доля публикаций за последний год",
    "query_share_pct": "публикации свежее, чем у других кандидатов запроса",
    "backed": "за технологией стоят компании",
    "players_cap": "число компаний",
    "funding": "в новостях есть раунд или сделка",
    "commercial": "есть продажи или платящие клиенты",
    "pilot": "есть пилот с заказчиком",
}
#: Признаки «да/нет»: подпись говорит, что признак ДАЛ, а не только его имя.
QUERY_BINARY = frozenset({"backed", *QUERY_COMMERCE})


def query_features(pool: list[dict[str, float | None]]) -> list[dict[str, float]]:
    """Признаки `QUERY_FEATURES` для каждого кандидата ОДНОГО запроса.

    Вход — по кандидату: `core_last_year_share` (доля работ ядра за последний год или
    `None`), `core_works`, `players` (независимых компаний), `money_stream` (1.0 — из
    денежного потока) и чтения свидетельств `COMMERCE_SOURCE` (нет — 0). Один код для обучения (`scripts/train_growth_model.py --in-query`,
    пул — кандидаты `emerging` одного домена одного среза) и для выдачи (`weak.ask`, пул —
    кандидаты `emerging` запроса): разойдись он — модель применялась бы к другим числам.
    """
    shares = sorted(
        float(r["core_last_year_share"])
        for r in pool
        if r.get("core_last_year_share") is not None
        and not math.isnan(float(r["core_last_year_share"]))
    )
    prior = 0.0
    if shares:
        mid = len(shares) // 2
        prior = shares[mid] if len(shares) % 2 else (shares[mid - 1] + shares[mid]) / 2
    shrunk = []
    for r in pool:
        s = r.get("core_last_year_share")
        works = float(r.get("core_works") or 0.0)
        if s is None or math.isnan(float(s)):
            shrunk.append(-1.0)  # измерить нечем — в хвост, как в прежней формуле
        else:
            m = SHARE_PRIOR_WEIGHT
            shrunk.append((float(s) * works + m * prior) / (works + m))
    out = []
    for r, v in zip(pool, shrunk):
        players = float(r.get("players") or 0.0)
        out.append(
            {
                "query_share": v,
                "query_share_pct": sum(x <= v for x in shrunk) / len(shrunk),
                "backed": 1.0 if players > 0 or float(r.get("money_stream") or 0.0) > 0 else 0.0,
                "players_cap": min(players, PLAYERS_CAP),
                **{
                    name: 1.0 if float(r.get(src) or 0.0) > 0 else 0.0
                    for name, src in COMMERCE_SOURCE.items()
                },
            }
        )
    return out


def phrase(name: str, value: float | None, part: float) -> str:
    """Вклад признака словами. У двоичного чтения свидетельств («это стандарт») сначала
    говорится, что оно ДАЛО — «нет: это стандарт — за», иначе отсутствие признака
    читалось как его наличие («это стандарт: за»)."""
    label = FEATURE_LABELS.get(name) or QUERY_LABELS.get(name) or MOMENTUM_LABELS.get(name, name)
    binary = name in JUDGMENT_FEATURES or name in QUERY_BINARY
    if binary and value is not None and value in (0.0, 1.0):
        label = f"{'да' if value else 'нет'}: {label}"
    elif name == "query_share" and value is not None and value >= 0:
        label = f"{label} {value:.0%}"
    elif name == "query_share_pct" and value is not None:
        label = f"публикации свежее, чем у {value:.0%} кандидатов запроса"
    return f"{label} — {'за' if part > 0 else 'против'} ({part:+.2f})"


@dataclass(frozen=True, slots=True)
class Prediction:
    probability: float
    #: (признак, вклад в отступ) по убыванию модуля.
    contributions: list[tuple[str, float]] = field(default_factory=list)
    #: Сырые значения признаков, какими их видела модель (до импутации); для подписи.
    values: dict[str, float | None] = field(default_factory=dict)

    @property
    def predictors(self) -> list[str]:
        """Три главных вклада словами, для карточки."""
        return [
            phrase(name, self.values.get(name), part) for name, part in self.contributions[:TOP]
        ]


def load(path: Path = PARAMS) -> dict | None:
    """Параметры модели или `None`, если её нет, правило решения провалено или не грузится.

    Победитель зоопарка (`scripts/model_zoo.py`) может быть не регрессией: тогда рядом с
    JSON лежит pickle (`estimator: "pickle"`), и нужен sklearn. ⚠️ Нет sklearn или файла —
    `None`, как без модели: выдача без вероятности лучше выдачи, упавшей на импорте.
    """
    if not path.exists():
        return None
    params = json.loads(path.read_text(encoding="utf-8"))
    if not params.get("gate", {}).get("passed"):
        return None
    if params.get("estimator") == "pickle":
        pkl = path.with_name(params["pickle"])
        try:
            import joblib

            params["_model"] = joblib.load(pkl)
        except Exception:  # noqa: BLE001 — нет sklearn/joblib, нет файла, чужая версия
            return None
    return params


def _values(features: dict[str, float | None], params: dict) -> list[float]:
    """Признаки в порядке модели: пропуск → медиана обучения, счётчики → log1p."""
    logged = set(params.get("log1p", ()))
    out = []
    for i, name in enumerate(params["features"]):
        v = features.get(name)
        if v is None or (isinstance(v, float) and math.isnan(v)):
            # ⚠️ Медиана обучения уже в log-пространстве для счётных признаков.
            v = params["impute_median"][i]
        elif name in logged:
            v = math.log1p(max(v, 0.0))
        out.append(float(v))
    return out


def probability(features: dict[str, float | None], params: dict) -> Prediction:
    """Вероятность и вклады признаков.

    Регрессия: p = σ(a·s + b), s — отступ на стандартизованных признаках, вклад = coef × z.
    Pickle-модель: `predict_proba`; вклады — не разложение, а глобальная permutation
    importance × знак отклонения признака от среднего обучения (грубая подсказка, и
    подписывается так же).
    """
    if "_model" in params:
        import numpy as np

        raw = np.array([_values(features, params)])
        p = float(params["_model"].predict_proba(raw)[0, 1])
        contributions = []
        for i, name in enumerate(params["features"]):
            std = params["scale_std"][i] or 1.0
            z = (raw[0, i] - params["scale_mean"][i]) / std
            contributions.append((name, params["permutation"].get(name, 0.0) * z))
        contributions.sort(key=lambda t: -abs(t[1]))
        return Prediction(probability=p, contributions=contributions, values=dict(features))
    names = params["features"]
    logged = set(params.get("log1p", ()))
    s = params["intercept"]
    contributions = []
    for i, name in enumerate(names):
        v = features.get(name)
        if v is None or (isinstance(v, float) and math.isnan(v)):
            # ⚠️ Медиана обучения уже в log-пространстве для счётных признаков.
            v = params["impute_median"][i]
        elif name in logged:
            v = math.log1p(max(v, 0.0))
        std = params["scale_std"][i] or 1.0
        z = (v - params["scale_mean"][i]) / std
        part = params["coef"][i] * z
        s += part
        contributions.append((name, part))
    a, b = params["platt"]["a"], params["platt"]["b"]
    p = 1 / (1 + math.exp(-(a * s + b)))
    contributions.sort(key=lambda t: -abs(t[1]))
    return Prediction(probability=p, contributions=contributions, values=dict(features))
