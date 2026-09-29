"""Зоопарк моделей: какая обученная модель идёт в этап 1 ТЗ, и помогает ли связка LLM.

    python scripts/measure_signal_kinds.py --evidence corpus --features-out --embed-features
    python scripts/model_zoo.py
    python scripts/model_zoo.py --repeats 3 --no-tabpfn      # быстрая отладка

## Два вопроса, два правила

**Вопрос этапа 1 ТЗ** («обучить модель, обосновать признаки, показать важность»): какая
обученная модель на признаках без вердикта LLM идёт в выдачу. Наборы `N` (числа конвейера),
`J` (атомарные чтения свидетельств моделью — `weak/assess.py`, приём «LLM как аннотатор
признаков»: Snorkel, Ratner et al. 2017; FeatLLM, Han et al. 2024), `E` (соседство в
векторах — `weak/neighbours.py`) и их суммы. Правило — **парсимония**: `REFERENCE`
(LR L1 на `N+J`) по умолчанию, нелинейное семейство заменяет его только если бьёт его по
corrected resampled t-test с поправкой Холма (p < `ALPHA`). Это one-standard-error rule
(Breiman et al. 1984; Hastie, Tibshirani & Friedman, ESL §7.10): из кандидатов в пределах
погрешности от лучшего берётся простейший. Причина — ТЗ: «по каким именно признакам» у LR
отвечает разложение coef × z (`weak/model.py`), у дерева — permutation importance × знак,
там же названная «грубой подсказкой». Порог годности победителя — `STAGE1_ACC`
(accuracy и balanced accuracy, порог ТЗ) и `STAGE1_AUC`. ⚠️ Сравнение с gemma
печатается рядом без правила «должен превзойти»: вопрос этапа 1 — годна ли модель.

⚠️ Контрольная линия `probe` — LR на сырых 768 координатах вектора названия
(`reports/weak-label-vectors-<плечо>.npz`). Кандидатом она не является: не интерпретируема по
признаку, а на 130 строках выучивает словарь, не зрелость. Стоит в таблице, чтобы было
видно, что проверено и почему отвергнуто.

**Второй вопрос** — помогает ли связка LLM (ниже).

## Что здесь меряется и против чего

Промпт-класс LLM (плечо `corpus`) на 100 + 30 строках: FN 8, FP 0. Чинить есть что только
на восьми строках — это потолок «помощи», и он назван до прогона. Каждый кандидат
(оценщик × набор признаков) сравнивается **на одних и тех же фолдах** с LLM в одиночку и
с базовыми линиями без обучения (self-consistency по трём плечам: any-yes, большинство,
persistence — Wang et al. 2022).

Наборы признаков: `N` — числа v2 (`weak.dataset.FEATURE_NAMES`), `N+L1` — плюс голос
`corpus`, `L3` — три голоса LLM и стадии (`LLM_FEATURES`), `N+L3` — всё. Голоса LLM как
признаки — stacking (Wolpert 1992); они не обучаются, утечки в CV нет.

## Как сравнивается — названные приёмы

- **Corrected resampled t-test** (Nadeau & Bengio 2003) на разности F1 по фолдам 10×10:
  фолды делят обучающие данные, наивный парный t занижает дисперсию. Поправка Холма на
  число кандидатов — их ~40, и лучший по внешней CV лучший отчасти случайно.
- **Вложенный выбор** (Cawley & Talbot 2010): «что даёт зоопарк» — это оценка ПРОЦЕДУРЫ,
  которая на внутренних фолдах каждого внешнего выбирает лучшего кандидата; лучшая клетка
  таблицы — не она.
- **Точный McNemar** (Dietterich 1998) на одном разбиении — вторая проверка.

## Правило решения — до прогона

Кандидат «помогает», если против LLM `corpus` в одиночку: медиана по повторам чистой
прибавки (исправлено FN − добавлено FP) ≥ `GAIN_MIN`; p corrected t-test после Холма
< 0.05; AUC не ниже, чем у LLM. Иначе — «никто не помогает», и это тоже результат.

⚠️ Контроль лёгкий и наш: специфичность 1.0 у LLM означает, что любая связка может
только терять точность, а видно это будет лишь на трудном контроле (docs/todo.md §90).
"""

from __future__ import annotations

import argparse
import io as _io
import json
import math
import os
import sys
import warnings
from collections import Counter
from datetime import date
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from scipy import stats  # noqa: E402
from sklearn.base import clone  # noqa: E402
from sklearn.ensemble import (  # noqa: E402
    ExtraTreesClassifier,
    HistGradientBoostingClassifier,
    RandomForestClassifier,
)
from sklearn.impute import SimpleImputer  # noqa: E402
from sklearn.linear_model import LogisticRegression  # noqa: E402
from sklearn.metrics import f1_score, roc_auc_score  # noqa: E402
from sklearn.model_selection import (  # noqa: E402
    GridSearchCV,
    RepeatedStratifiedKFold,
    StratifiedKFold,
    cross_val_predict,
)
from sklearn.naive_bayes import GaussianNB  # noqa: E402
from sklearn.neighbors import KNeighborsClassifier  # noqa: E402
from sklearn.pipeline import Pipeline  # noqa: E402
from sklearn.preprocessing import StandardScaler  # noqa: E402
from sklearn.svm import SVC  # noqa: E402
from sklearn.tree import DecisionTreeClassifier  # noqa: E402

from pelican.weak.dataset import (  # noqa: E402
    EMBED_FEATURES,
    FEATURE_LABELS,
    FEATURE_NAMES,
    JUDGMENT_FEATURES,
    LLM_FEATURES,
    LOG_FEATURES,
)

warnings.filterwarnings("ignore")

FEATURES = Path("reports/weak-features-corpus.tsv")
OUT_MD = Path("reports/weak-model-zoo.md")
OUT_JSON = Path("reports/weak-model-zoo.json")

SEED = 20260917
FOLDS = 10
#: Правило решения второго вопроса (докстринг).
GAIN_MIN = 3
ALPHA = 0.05
#: Порог AUC — у LLM по бинарному ответу (recall 0.92 + specificity 1.00) / 2.
AUC_MIN = 0.96
#: Правило этапа 1: модель по умолчанию и пороги годности (ТЗ: 75–80%).
REFERENCE = "lr_l1 / N+J"
STAGE1_ACC = 0.80
STAGE1_AUC = 0.90

#: Наборы признаков без вердикта LLM — кандидаты этапа 1.
STAGE1_SETS: dict[str, tuple[str, ...]] = {
    "N": FEATURE_NAMES,
    "J": JUDGMENT_FEATURES,
    "N+J": (*FEATURE_NAMES, *JUDGMENT_FEATURES),
    "N+J+E": (*FEATURE_NAMES, *JUDGMENT_FEATURES, *EMBED_FEATURES),
}
#: Наборы с голосами-вердиктами — второй вопрос (связка).
SETS: dict[str, tuple[str, ...]] = {
    **STAGE1_SETS,
    "N+L1": (*FEATURE_NAMES, "llm_corpus"),
    "L3": LLM_FEATURES,
    "N+L3": (*FEATURE_NAMES, *LLM_FEATURES),
}
VECTORS = Path("reports/weak-label-vectors-corpus.npz")


def read_tsv(path: Path) -> list[dict]:
    import csv

    return list(csv.DictReader(path.read_text(encoding="utf-8").splitlines(), delimiter="\t"))


def column(rows: list[dict], name: str) -> np.ndarray:
    out = []
    for r in rows:
        v = r.get(name, "")
        if v in ("", None):
            out.append(math.nan)
        else:
            x = float(v)
            out.append(math.log1p(x) if name in LOG_FEATURES else x)
    return np.array(out, dtype=float)


def matrix(rows: list[dict], names: tuple[str, ...]) -> np.ndarray:
    return np.column_stack([column(rows, n) for n in names])


def usable(rows: list[dict], names: tuple[str, ...]) -> tuple[str, ...]:
    """Признаки, у которых есть хоть одно значение: пустой столбец (E без покрытия,
    J у оценок старого формата) молча выпал бы в импутации, а тут он назван."""
    return tuple(n for n in names if not np.isnan(column(rows, n)).all())


def vectors_path(features: Path) -> Path:
    """`weak-features-<плечо>.tsv` → `weak-label-vectors-<плечо>.npz` рядом."""
    name = features.name.replace("weak-features", "weak-label-vectors")
    return features.with_name(name).with_suffix(".npz")


def label_vectors(rows: list[dict], path: Path = VECTORS) -> np.ndarray | None:
    """Сырые векторы названий для контрольной линии `probe`, в порядке строк."""
    if not path.exists():
        return None
    blob = np.load(path, allow_pickle=False)
    by_key = {str(k): i for i, k in enumerate(blob["keys"])}
    idx = [by_key.get(f"{r['set']}:{r['n']}") for r in rows]
    if any(i is None for i in idx):
        return None
    return blob["vectors"][idx].astype(float)


# ------------------------------------------------------------------- оценщики


def estimators(with_tabpfn: bool) -> dict[str, tuple[object, dict]]:
    """Имя → (оценщик, сетка). Сетки узкие нарочно: 130 строк."""
    zoo: dict[str, tuple[object, dict]] = {
        "lr_l2": (
            LogisticRegression(class_weight="balanced", max_iter=3000),
            {"est__C": [0.03, 0.1, 0.3, 1.0, 3.0]},
        ),
        "lr_l1": (
            LogisticRegression(
                penalty="l1", solver="liblinear", class_weight="balanced", max_iter=3000
            ),
            {"est__C": [0.1, 0.3, 1.0, 3.0]},
        ),
        "svm_rbf": (
            SVC(kernel="rbf", class_weight="balanced", probability=True, random_state=SEED),
            {"est__C": [0.3, 1.0, 3.0, 10.0]},
        ),
        "knn": (KNeighborsClassifier(), {"est__n_neighbors": [3, 5, 9, 15]}),
        "nb": (GaussianNB(), {}),
        "tree": (
            DecisionTreeClassifier(class_weight="balanced", random_state=SEED),
            {"est__max_depth": [2, 3, 4]},
        ),
        "rf": (
            RandomForestClassifier(
                n_estimators=300, class_weight="balanced_subsample", random_state=SEED
            ),
            {"est__max_depth": [3, None]},
        ),
        "et": (
            ExtraTreesClassifier(
                n_estimators=300, class_weight="balanced_subsample", random_state=SEED
            ),
            {"est__max_depth": [3, None]},
        ),
        "hgb": (
            HistGradientBoostingClassifier(
                max_depth=3, max_iter=200, class_weight="balanced", random_state=SEED
            ),
            {"est__learning_rate": [0.05, 0.1]},
        ),
    }
    if with_tabpfn:
        # ⚠️ TabPFN ≥ 9 качает веса только после принятия лицензии: без `TABPFN_TOKEN`
        # `.fit()` открывает браузер и ЖДЁТ ввода — под nohup это вечный сон, а не отказ.
        # Ключ — https://ux.priorlabs.ai/account (регистрация, лицензия, API key).
        if not os.environ.get("TABPFN_TOKEN"):
            os.environ["TABPFN_TOKEN"] = _env_value("TABPFN_TOKEN")
        if not os.environ.get("TABPFN_TOKEN"):
            print("⚠️ tabpfn не измерен: нет TABPFN_TOKEN (лицензия Prior Labs), см. docstring")
        else:
            try:
                from tabpfn import TabPFNClassifier

                zoo["tabpfn"] = (TabPFNClassifier(device="cpu", random_state=SEED), {})
            except Exception as exc:  # noqa: BLE001 — нет torch, нет весов, чужая версия
                print(f"⚠️ tabpfn не измерен: {type(exc).__name__}: {str(exc)[:120]}")
    return zoo


def _env_value(key: str) -> str:
    """Значение из `.env` рядом с репозиторием: pydantic-settings в `os.environ` не пишет."""
    env = Path(__file__).resolve().parents[1] / ".env"
    if not env.exists():
        return ""
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def pipeline(est: object) -> Pipeline:
    return Pipeline(
        [
            ("impute", SimpleImputer(strategy="median")),
            ("scale", StandardScaler()),
            ("est", est),
        ]
    )


def fit_best(est: object, grid: dict, x: np.ndarray, y: np.ndarray) -> Pipeline:
    """Внутренний подбор гиперпараметров (nested CV), если сетка не пуста."""
    pipe = pipeline(est)
    if not grid:
        return pipe.fit(x, y)
    inner = StratifiedKFold(n_splits=5, shuffle=True, random_state=SEED)
    search = GridSearchCV(pipe, grid, scoring="roc_auc", cv=inner, n_jobs=1)
    return search.fit(x, y).best_estimator_


def proba(model: object, x: np.ndarray) -> np.ndarray:
    return model.predict_proba(x)[:, 1]


# --------------------------------------------------------------------- метрики


def fold_f1(y: np.ndarray, pred: np.ndarray, idx: np.ndarray) -> float:
    return f1_score(y[idx], pred[idx], zero_division=0)


def corrected_t(diffs: np.ndarray, n_train: int, n_test: int) -> tuple[float, float]:
    """Nadeau–Bengio: дисперсия × (1/k + n_test/n_train), df = k − 1. Возвращает (t, p)."""
    k = len(diffs)
    mean = diffs.mean()
    var = diffs.var(ddof=1)
    if var == 0:
        return (math.inf if mean > 0 else -math.inf if mean < 0 else 0.0), (0.0 if mean else 1.0)
    t = mean / math.sqrt(var * (1 / k + n_test / n_train))
    p = 2 * stats.t.sf(abs(t), df=k - 1)
    return float(t), float(p)


def mcnemar_exact(y: np.ndarray, a: np.ndarray, b: np.ndarray) -> tuple[int, int, float]:
    """Сколько строк чинит A против B и наоборот, и точный биномиальный p."""
    a_ok, b_ok = a == y, b == y
    only_a = int((a_ok & ~b_ok).sum())
    only_b = int((~a_ok & b_ok).sum())
    n = only_a + only_b
    p = 1.0 if n == 0 else float(stats.binomtest(only_a, n, 0.5).pvalue)
    return only_a, only_b, p


def holm(pvals: dict[str, float]) -> dict[str, float]:
    items = sorted(pvals.items(), key=lambda kv: kv[1])
    m = len(items)
    out, running = {}, 0.0
    for i, (k, p) in enumerate(items):
        running = max(running, min(1.0, (m - i) * p))
        out[k] = running
    return out


def summarize(
    y: np.ndarray,
    llm: np.ndarray,
    pred_by_repeat: list[np.ndarray],
    prob_by_repeat: list[np.ndarray],
    f1_folds: list[float],
    llm_f1_folds: list[float],
    n_train: int,
    n_test: int,
) -> dict:
    gains, f1s, aucs, spec, rec = [], [], [], [], []
    fixed_all, broke_all = Counter(), Counter()
    for pred, prob in zip(pred_by_repeat, prob_by_repeat, strict=True):
        fixed = (llm != y) & (pred == y)
        broke = (llm == y) & (pred != y)
        gains.append(int(fixed.sum()) - int(broke.sum()))
        fixed_all.update(np.flatnonzero(fixed).tolist())
        broke_all.update(np.flatnonzero(broke).tolist())
        f1s.append(f1_score(y, pred, zero_division=0))
        aucs.append(roc_auc_score(y, prob))
        spec.append(float(((pred == 0) & (y == 0)).sum() / max((y == 0).sum(), 1)))
        rec.append(float(((pred == 1) & (y == 1)).sum() / max((y == 1).sum(), 1)))
    diffs = np.array(f1_folds) - np.array(llm_f1_folds)
    t, p = corrected_t(diffs, n_train, n_test)
    only_a, only_b, p_mc = mcnemar_exact(y, pred_by_repeat[0], llm)
    acc = [float((pred == y).mean()) for pred in pred_by_repeat]
    return {
        "f1": float(np.mean(f1s)),
        "f1_lo": float(np.percentile(f1s, 2.5)),
        "f1_hi": float(np.percentile(f1s, 97.5)),
        "auc": float(np.mean(aucs)),
        "recall": float(np.mean(rec)),
        "specificity": float(np.mean(spec)),
        "accuracy": float(np.mean(acc)),
        "balanced_accuracy": float((np.mean(rec) + np.mean(spec)) / 2),
        "f1_folds": [float(v) for v in f1_folds],
        "gain_median": float(np.median(gains)),
        "gain_min": int(min(gains)),
        "gain_max": int(max(gains)),
        "t": t,
        "p_raw": p,
        "mcnemar": {"fixed": only_a, "broke": only_b, "p": p_mc},
        "fixed_rows": [i for i, c in fixed_all.most_common() if c >= len(pred_by_repeat) / 2],
        "broke_rows": [i for i, c in broke_all.most_common() if c >= len(pred_by_repeat) / 2],
    }


# ------------------------------------------------------------------------ main


def main() -> None:
    # ⚠️ Здесь, а не на уровне модуля: тесты импортируют скрипт, и подмена stdout при
    # импорте ломает захват вывода pytest.
    sys.stdout = _io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="зоопарк моделей против LLM")
    ap.add_argument("--features", default=str(FEATURES))
    ap.add_argument("--repeats", type=int, default=10)
    ap.add_argument("--no-tabpfn", action="store_true")
    ap.add_argument("--skip", default="", help="оценщики через запятую, которые не гонять")
    ap.add_argument(
        "--only",
        default="",
        help="только эти оценщики; результат вливается в готовый weak-model-zoo.json "
        "(те же фолды по seed), вложенный выбор и «спасение» не пересчитываются",
    )
    args = ap.parse_args()

    rows = read_tsv(Path(args.features))
    y = np.array([1 if r["set"] == "pos" else 0 for r in rows])
    n = len(y)
    votes = {a: column(rows, f"llm_{a}") for a in ("papers", "mixed", "corpus")}
    if any(np.isnan(v).any() for v in votes.values()):
        print("⚠️ не у всех строк есть три голоса LLM — прогнать все три плеча замера")
        raise SystemExit(1)
    llm = votes["corpus"].astype(int)
    stacked = np.column_stack([votes[a] for a in ("papers", "mixed", "corpus")])
    baselines = {
        "LLM corpus (одна)": llm,
        "LLM papers (одна)": votes["papers"].astype(int),
        "LLM mixed (одна)": votes["mixed"].astype(int),
        "any-yes трёх плеч": (stacked.sum(axis=1) >= 1).astype(int),
        "большинство трёх плеч": (stacked.sum(axis=1) >= 2).astype(int),
        "persistence (все три)": (stacked.sum(axis=1) == 3).astype(int),
    }
    print(f"строк {n}: положительных {int(y.sum())}, контрольных {int((y == 0).sum())}")
    for name, pred in baselines.items():
        fn = int(((pred == 0) & (y == 1)).sum())
        fp = int(((pred == 1) & (y == 0)).sum())
        print(f"  {name:26s} FN {fn:2d} FP {fp:2d} F1 {f1_score(y, pred):.3f}")

    outer = RepeatedStratifiedKFold(n_splits=FOLDS, n_repeats=args.repeats, random_state=SEED)
    splits = list(outer.split(np.zeros(n), y))
    n_test = n // FOLDS
    n_train = n - n_test
    llm_f1_folds = [fold_f1(y, llm, te) for _, te in splits]

    zoo = estimators(with_tabpfn=not args.no_tabpfn)
    for name in filter(None, args.skip.split(",")):
        zoo.pop(name, None)
    if args.only:
        keep = set(args.only.split(","))
        zoo = {k: v for k, v in zoo.items() if k in keep}
        if not zoo:
            print(f"нечего гонять: {args.only}")
            raise SystemExit(1)
    results: dict[str, dict] = {}
    oof: dict[str, tuple[list[np.ndarray], list[np.ndarray]]] = {}
    live_sets = {k: usable(rows, v) for k, v in SETS.items()}
    for k, v in SETS.items():
        dropped = sorted(set(v) - set(live_sets[k]))
        if dropped:
            print(f"⚠️ набор {k}: пустые столбцы выброшены до обучения: {', '.join(dropped)}")
    total = len(zoo) * len(SETS)
    done = 0
    for est_name, (est, grid) in zoo.items():
        for set_name, names in live_sets.items():
            x = matrix(rows, names)
            preds = [np.zeros(n, dtype=int) for _ in range(args.repeats)]
            probs = [np.zeros(n) for _ in range(args.repeats)]
            f1_folds = []
            for i, (tr, te) in enumerate(splits):
                rep = i // FOLDS
                model = fit_best(clone(est), grid, x[tr], y[tr])
                pr = proba(model, x[te])
                probs[rep][te] = pr
                preds[rep][te] = (pr >= 0.5).astype(int)
                f1_folds.append(fold_f1(y, preds[rep], te))
            key = f"{est_name} / {set_name}"
            results[key] = summarize(y, llm, preds, probs, f1_folds, llm_f1_folds, n_train, n_test)
            oof[key] = (preds, probs)
            done += 1
            r = results[key]
            print(
                f"[{done:2d}/{total}] {key:22s} F1 {r['f1']:.3f} AUC {r['auc']:.3f} "
                f"прибавка {r['gain_median']:+.0f} [{r['gain_min']:+d};{r['gain_max']:+d}] "
                f"p {r['p_raw']:.3f}",
                flush=True,
            )

    # Контрольная линия «embedding probe»: LR на сырых координатах вектора названия.
    raw = label_vectors(rows, vectors_path(Path(args.features)))
    if raw is not None and not args.only:
        est, grid = zoo.get("lr_l2", estimators(False)["lr_l2"])
        preds = [np.zeros(n, dtype=int) for _ in range(args.repeats)]
        probs = [np.zeros(n) for _ in range(args.repeats)]
        f1_folds = []
        for i, (tr, te) in enumerate(splits):
            rep_ = i // FOLDS
            model = fit_best(clone(est), grid, raw[tr], y[tr])
            pr = proba(model, raw[te])
            probs[rep_][te] = pr
            preds[rep_][te] = (pr >= 0.5).astype(int)
            f1_folds.append(fold_f1(y, preds[rep_], te))
        results["probe / вектор названия"] = summarize(
            y, llm, preds, probs, f1_folds, llm_f1_folds, n_train, n_test
        )
        r = results["probe / вектор названия"]
        print(f"[probe] LR на {raw.shape[1]} координатах: F1 {r['f1']:.3f} AUC {r['auc']:.3f}")
    elif raw is None:
        print(
            f"⚠️ probe не измерен: нет {vectors_path(Path(args.features))} "
            "(measure_signal_kinds.py --embed-features)"
        )

    # Базовые линии теми же фолдами (без обучения — одно предсказание на все повторы).
    base_results = {}
    for name, pred in baselines.items():
        f1_folds = [fold_f1(y, pred, te) for _, te in splits]
        base_results[name] = summarize(
            y, llm, [pred] * args.repeats, [pred.astype(float)] * args.repeats,
            f1_folds, llm_f1_folds, n_train, n_test,
        )  # fmt: skip

    if args.only and OUT_JSON.exists():
        # Долив в готовую таблицу: Холм пересчитывается по ВСЕМ кандидатам, иначе у
        # долитого поправка мягче, чем у остальных.
        prior = json.loads(OUT_JSON.read_text(encoding="utf-8"))
        if prior.get("repeats") != args.repeats:
            print(f"⚠️ в json {prior.get('repeats')} повторов, сейчас {args.repeats} — не сливаю")
            raise SystemExit(1)
        merged = dict(prior["candidates"])
        merged.update(results)
        results = merged
        base_results = prior["baselines"]
        rescue = prior["rescue"]
        nested = prior["nested"]
        chosen = Counter(dict(nested["chosen"]))

    p_holm = holm({k: v["p_raw"] for k, v in results.items()})
    for k, v in results.items():
        v["p_holm"] = p_holm[k]
        v["helps"] = v["gain_median"] >= GAIN_MIN and v["p_holm"] < ALPHA and v["auc"] >= AUC_MIN
    stage1 = stage1_verdict(results, n_train, n_test)
    print(
        f"\nэтап 1: модель — «{stage1['winner']}» (accuracy {stage1['accuracy']:.2f}, "
        f"balanced {stage1['balanced_accuracy']:.2f}, AUC {stage1['auc']:.2f}); порог "
        f"{'ВЫПОЛНЕН' if stage1['passed'] else 'НЕ выполнен'}"
        + (
            f"; бьют опорную: {', '.join(stage1['beats_reference'])}"
            if stage1["beats_reference"]
            else ""
        )
    )
    if args.only and OUT_JSON.exists():
        verdict = (
            prior["verdict"].split(";")[0]
            + "; по правилу помогают: "
            + (", ".join(k for k, v in results.items() if v["helps"]) or "никто")
        )
        print("\n" + verdict)
        prior.update({"candidates": results, "verdict": verdict, "stage1": stage1})
        OUT_JSON.write_text(
            json.dumps(prior, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
        write_report(
            rows, y, baselines, base_results, results, rescue, nested, verdict, stage1, args
        )
        print(f"\nотчёт: {OUT_MD}\nтаблица: {OUT_JSON} (долито: {', '.join(zoo)})")
        return

    # Вложенный выбор: на каждом внешнем фолде кандидат выбирается по внутренней CV.
    print("\nвложенный выбор кандидата (что даёт процедура, а не лучшая клетка)...", flush=True)
    nested_pred = [np.zeros(n, dtype=int) for _ in range(args.repeats)]
    nested_prob = [np.zeros(n) for _ in range(args.repeats)]
    chosen: Counter = Counter()
    nested_f1_folds = []
    inner = StratifiedKFold(n_splits=5, shuffle=True, random_state=SEED)
    quick = {k: v for k, v in zoo.items() if k not in ("tabpfn", "svm_rbf")}
    # Только наборы этапа 1: с голосами процедура выучивает any-yes (замерено), а цена
    # вложенного выбора — 7 × 4 × 5 × 100 обучений — растёт с каждым набором.
    nested_sets = {k: v for k, v in live_sets.items() if k in STAGE1_SETS}
    for i, (tr, te) in enumerate(splits):
        rep = i // FOLDS
        best_key, best_score, best_model, best_x = None, -1.0, None, None
        for est_name, (est, grid) in quick.items():
            for set_name, names in nested_sets.items():
                x = matrix(rows, names)
                pipe = pipeline(clone(est))
                if grid:
                    pipe.set_params(**{k: v[0] for k, v in grid.items()})
                if hasattr(est, "n_estimators"):
                    # Цена вложенного выбора — 28 кандидатов × 5 внутренних × 100 внешних;
                    # леса здесь на 100 деревьях, иначе час против минут.
                    pipe.set_params(est__n_estimators=100)
                inner_prob = cross_val_predict(
                    pipe, x[tr], y[tr], cv=inner, method="predict_proba"
                )[:, 1]
                score = f1_score(y[tr], (inner_prob >= 0.5).astype(int), zero_division=0)
                if score > best_score:
                    best_key, best_score, best_model, best_x = (
                        f"{est_name} / {set_name}",
                        score,
                        pipe,
                        x,
                    )
        chosen[best_key] += 1
        best_model.fit(best_x[tr], y[tr])
        pr = proba(best_model, best_x[te])
        nested_prob[rep][te] = pr
        nested_pred[rep][te] = (pr >= 0.5).astype(int)
        nested_f1_folds.append(fold_f1(y, nested_pred[rep], te))
    nested = summarize(
        y, llm, nested_pred, nested_prob, nested_f1_folds, llm_f1_folds, n_train, n_test
    )
    nested["chosen"] = chosen.most_common(5)
    nested["helps"] = (
        nested["gain_median"] >= GAIN_MIN and nested["p_raw"] < ALPHA and nested["auc"] >= AUC_MIN
    )

    # Режим «спасение»: LLM решает, модель поправляет только «нет» при p ≥ τ (τ — по
    # внутренним фолдам). Для трёх лучших кандидатов по прибавке.
    print("режим «спасение» для трёх лучших...", flush=True)
    top3 = sorted(results, key=lambda k: (-results[k]["gain_median"], -results[k]["auc"]))[:3]
    rescue: dict[str, dict] = {}
    for key in top3:
        est_name, set_name = key.split(" / ")
        est, grid = zoo[est_name]
        x = matrix(rows, SETS[set_name])
        preds = [np.zeros(n, dtype=int) for _ in range(args.repeats)]
        probs = [np.zeros(n) for _ in range(args.repeats)]
        f1_folds = []
        for i, (tr, te) in enumerate(splits):
            rep = i // FOLDS
            model = fit_best(clone(est), grid, x[tr], y[tr])
            inner_prob = cross_val_predict(
                clone(model), x[tr], y[tr], cv=inner, method="predict_proba"
            )[:, 1]
            best_tau, best_f1 = 1.01, f1_score(y[tr], llm[tr], zero_division=0)
            for tau in (0.5, 0.6, 0.7, 0.8, 0.9):
                cand = np.where((llm[tr] == 0) & (inner_prob >= tau), 1, llm[tr])
                f = f1_score(y[tr], cand, zero_division=0)
                if f > best_f1:
                    best_tau, best_f1 = tau, f
            pr = proba(model, x[te])
            probs[rep][te] = np.maximum(pr, llm[te])
            preds[rep][te] = np.where((llm[te] == 0) & (pr >= best_tau), 1, llm[te])
            f1_folds.append(fold_f1(y, preds[rep], te))
        rescue[f"{key} · спасение"] = summarize(
            y, llm, preds, probs, f1_folds, llm_f1_folds, n_train, n_test
        )

    # ------------------------------------------------------------------- вердикты
    helpers = [k for k, v in results.items() if v["helps"]]
    winner_key = chosen.most_common(1)[0][0]
    verdict = f"вложенный выбор чаще всего берёт «{winner_key}»; по правилу помогают: " + (
        ", ".join(helpers) if helpers else "никто"
    )
    print("\n" + verdict)

    OUT_JSON.write_text(
        json.dumps(
            {
                "baselines": base_results,
                "candidates": results,
                "rescue": rescue,
                "nested": nested,
                "verdict": verdict,
                "stage1": stage1,
                "rule": {"gain_min": GAIN_MIN, "alpha": ALPHA, "auc_min": AUC_MIN},
                "stage1_rule": {
                    "reference": REFERENCE,
                    "alpha": ALPHA,
                    "accuracy": STAGE1_ACC,
                    "auc": STAGE1_AUC,
                },
                "sets": {k: list(v) for k, v in live_sets.items()},
                "repeats": args.repeats,
                "folds": FOLDS,
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    write_report(rows, y, baselines, base_results, results, rescue, nested, verdict, stage1, args)
    print(
        f"\nотчёт: {OUT_MD}\nтаблица: {OUT_JSON}\nмодель этапа 1 обучает "
        "scripts/train_signal_model.py --from-zoo"
    )


def stage1_verdict(results: dict[str, dict], n_train: int, n_test: int) -> dict:
    """Правило парсимонии: опорная LR L1 на `N+J`, если её никто не бьёт значимо.

    Кандидаты — только наборы без вердикта LLM (`STAGE1_SETS`) и без контрольной линии
    `probe`. Разность F1 по тем же фолдам, corrected t (Nadeau–Bengio), Холм по числу
    кандидатов. Победитель годен, если accuracy и balanced accuracy ≥ `STAGE1_ACC` и
    AUC ≥ `STAGE1_AUC`.
    """
    ref = results.get(REFERENCE)
    if ref is None:
        return {"winner": None, "passed": False, "beats_reference": [], "note": "нет опорной"}
    ref_folds = np.array(ref["f1_folds"])
    pvals: dict[str, float] = {}
    for key, v in results.items():
        est_name, set_name = key.split(" / ", 1)
        if key == REFERENCE or set_name not in STAGE1_SETS or est_name == "probe":
            continue
        diffs = np.array(v["f1_folds"]) - ref_folds
        _, p = corrected_t(diffs, n_train, n_test)
        pvals[key] = p if diffs.mean() > 0 else 1.0
    p_holm = holm(pvals) if pvals else {}
    beats = sorted(
        (k for k, p in p_holm.items() if p < ALPHA and results[k]["f1"] > ref["f1"]),
        key=lambda k: -results[k]["f1"],
    )
    winner = beats[0] if beats else REFERENCE
    w = results[winner]
    passed = (
        w["accuracy"] >= STAGE1_ACC
        and w["balanced_accuracy"] >= STAGE1_ACC
        and w["auc"] >= STAGE1_AUC
    )
    return {
        "winner": winner,
        "passed": bool(passed),
        "beats_reference": beats,
        "p_vs_reference": p_holm,
        "accuracy": w["accuracy"],
        "balanced_accuracy": w["balanced_accuracy"],
        "auc": w["auc"],
        "f1": w["f1"],
        "reference": {k: ref[k] for k in ("f1", "auc", "accuracy", "balanced_accuracy")},
    }


def write_report(
    rows, y, baselines, base_results, results, rescue, nested, verdict, stage1, args
) -> None:
    def row_name(i: int) -> str:
        r = rows[i]
        return f"{r['set']}:{r['n']}"

    def line(name: str, v: dict, p_col: str = "p_holm") -> str:
        p = v.get(p_col, v["p_raw"])
        mark = " **✓**" if v.get("helps") else ""
        return (
            f"| {name}{mark} | {v['f1']:.3f} [{v['f1_lo']:.2f}; {v['f1_hi']:.2f}] | "
            f"{v['auc']:.3f} | "
            f"{v['recall']:.2f} | {v['specificity']:.2f} | {v['gain_median']:+.0f} "
            f"[{v['gain_min']:+d}; {v['gain_max']:+d}] | {p:.3f} | "
            f"{v['mcnemar']['fixed']} / {v['mcnemar']['broke']} |"
        )

    head = [
        "| кандидат | F1 [разброс] | AUC | recall | spec | прибавка к LLM, строк | p "
        "| чинит / ломает |",
        "|---|---|---|---|---|---|---|---|",
    ]
    n_pos, n_ctl = int(y.sum()), int((y == 0).sum())
    stage1_rows = {
        k: v
        for k, v in results.items()
        if k.split(" / ", 1)[1] in STAGE1_SETS or k.startswith("probe")
    }
    head1 = [
        "| кандидат | F1 [разброс] | AUC | accuracy | balanced | recall | spec "
        "| p против опорной |",
        "|---|---|---|---|---|---|---|---|",
    ]

    def line1(name: str, v: dict) -> str:
        p = stage1.get("p_vs_reference", {}).get(name)
        mark = " **← модель этапа 1**" if name == stage1.get("winner") else ""
        return (
            f"| {name}{mark} | {v['f1']:.3f} [{v['f1_lo']:.2f}; {v['f1_hi']:.2f}] | "
            f"{v['auc']:.3f} | "
            f"{v['accuracy']:.2f} | {v['balanced_accuracy']:.2f} | {v['recall']:.2f} | "
            f"{v['specificity']:.2f} | {'—' if p is None else f'{p:.3f}'} |"
        )

    out = [
        "# Зоопарк моделей: модель этапа 1 и связка с LLM",
        "",
        f"Сгенерировано `scripts/model_zoo.py` {date.today():%d.%m.%Y}: {len(y)} строк "
        f"({n_pos} положительных, {n_ctl} контрольных), стратифицированная CV {FOLDS} × "
        f"{args.repeats}, одни фолды у всех кандидатов. Признаки — `weak/dataset.py`.",
        "",
        "## Этап 1 ТЗ: обученная модель без вердикта LLM",
        "",
        f"**Правило парсимонии** (до прогона): опорная модель — `{REFERENCE}`; нелинейное "
        f"семейство заменяет её, только если бьёт по corrected resampled t-test на разности "
        f"F1 по тем же фолдам с поправкой Холма, p < {ALPHA} (one-standard-error rule, "
        f"Breiman et al. 1984; ESL §7.10). Порог годности: accuracy и balanced accuracy ≥ "
        f"{STAGE1_ACC}, AUC ≥ {STAGE1_AUC}.",
        "",
        f"**Итог этапа 1:** модель — `{stage1.get('winner')}`; порог "
        f"{'выполнен' if stage1.get('passed') else 'НЕ выполнен'}"
        + (
            f"; опорную бьют: {', '.join(stage1['beats_reference'])}"
            if stage1.get("beats_reference")
            else "; опорную значимо не бьёт никто"
        )
        + ".",
        "",
        "⚠️ Контроль лёгкий и наш: specificity и accuracy здесь — на 30 контрольных, "
        "написанных нами (+17 непроверенных кандидатов, если они в файле); порог ТЗ на таком "
        "контроле проходится легче, чем на закрытой разметке. ⚠️ `probe` — контрольная линия "
        "(LR на сырых координатах вектора названия), кандидатом не является: не "
        "интерпретируема по признаку и выучивает словарь.",
        "",
        *head1,
        *(line1(k, v) for k, v in sorted(stage1_rows.items(), key=lambda kv: -kv[1]["f1"])),
        "",
        "## Второй вопрос: помогает ли связка с LLM",
        "",
        f"**Правило** (до прогона): против LLM `corpus` в одиночку — медиана чистой прибавки "
        f"(исправлено − сломано) ≥ {GAIN_MIN} строк, corrected resampled t-test "
        f"(Nadeau–Bengio) по F1 на фолдах с поправкой Холма p < {ALPHA}, AUC ≥ {AUC_MIN}.",
        "",
        "⚠️ Контроль лёгкий и наш (specificity у LLM уже 1.00): связка может здесь только "
        "чинить пропуски, а цена в точности видна лишь на трудном контроле (§90). "
        "⚠️ Разброс F1 — по повторам CV, не доверительный интервал; предел различимости "
        f"выборки ±0.08 на {n_pos} положительных.",
        "",
        "## Базовые линии без обучения (self-consistency по трём плечам)",
        "",
        *head,
        *(line(k, v, "p_raw") for k, v in base_results.items()),
        "",
        "`p` у базовых линий — без поправки Холма (они не участвовали в переборе).",
        "",
        "## Вложенный выбор — что даёт процедура «перебрать и взять лучшего»",
        "",
        *head,
        line("nested selection", nested, "p_raw"),
        "",
        "Кого выбирал на внешних фолдах: "
        + ", ".join(f"«{k}» ×{c}" for k, c in nested["chosen"])
        + ".",
        "",
        f"**Итог:** {verdict}.",
        "",
        "## Все кандидаты (оценщик × набор признаков)",
        "",
        *head,
        *(line(k, v) for k, v in sorted(results.items(), key=lambda kv: -kv[1]["gain_median"])),
        "",
        "## Режим «спасение» (LLM решает, модель поправляет только «нет»)",
        "",
        *head,
        *(line(k, v, "p_raw") for k, v in rescue.items()),
        "",
        "## Что чинится и что ломается (строки, устойчивые в ≥ половине повторов)",
        "",
    ]
    shown = list(base_results.items())[3:] + [("nested selection", nested)]
    shown += sorted(results.items(), key=lambda kv: -kv[1]["gain_median"])[:5]
    for k, v in shown:
        fixed = ", ".join(row_name(i) for i in v["fixed_rows"]) or "—"
        broke = ", ".join(row_name(i) for i in v["broke_rows"]) or "—"
        out.append(f"- **{k}**: чинит {fixed}; ломает {broke}")
    out += [
        "",
        "## Признаки",
        "",
        "Числа `N`: " + ", ".join(f"`{n}`" for n in FEATURE_NAMES) + ".",
        "Чтения `J`: " + ", ".join(f"`{n}` — {FEATURE_LABELS[n]}" for n in JUDGMENT_FEATURES) + ".",
        "Соседство `E`: " + ", ".join(f"`{n}` — {FEATURE_LABELS[n]}" for n in EMBED_FEATURES) + ".",
        "Голоса LLM: " + ", ".join(f"`{n}` — {FEATURE_LABELS[n]}" for n in LLM_FEATURES) + ".",
    ]
    OUT_MD.write_text("\n".join(out) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
