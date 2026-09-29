"""Модель этапа 1 ТЗ: обучение победителя зоопарка, важность признаков, калибровка, установка.

    python scripts/measure_signal_kinds.py --evidence corpus --features-out --embed-features
    python scripts/model_zoo.py
    python scripts/train_signal_model.py --from-zoo            # победитель по правилу парсимонии
    python scripts/train_signal_model.py                       # опорная: LR L1 на N+J
    python scripts/train_signal_model.py --from-zoo --install  # → src/pelican/weak/model.json
    python scripts/train_signal_model.py --extra reports/weak-features-corpus-weak_controls_hard.tsv

## Что это и чем не является

ТЗ (схема 1, этап 1) просит ОБУЧЕННУЮ модель: «обосновать критерии отбора признаков,
математически отделять ранние индикаторы от шума и зрелых технологий, feature importance».
Здесь она и обучается — на признаках, которые конвейер считает по каждому кандидату
(`weak/dataset.py`): числа `N`, атомарные чтения свидетельств `J` (модель читает
свидетельства, решение принимает классификатор — `weak/assess.py`) и соседство в
векторах `E`. ⚠️ Вердикт LLM (`kind`) в признаки НЕ входит: с ним классификатор
выучивает any-yes, а важность признаков говорит только «важен голос»
(`reports/weak-model-zoo.md`).

Какое семейство и какой набор — решает `scripts/model_zoo.py` по правилу парсимонии
(`--from-zoo` читает его вердикт); без него — опорная LR L1 на `N+J`.

Приёмы названы: логистическая регрессия как интерпретируемая модель, **Platt scaling**
(Platt 1999) на out-of-fold отступах, Brier score (Brier 1950) и таблица надёжности,
**permutation importance** (Breiman 2001) на отложенных фолдах, **nested CV** для выбора
гиперпараметра (Cawley & Talbot 2010).

## Правило годности — записано ДО прогона

В выдачу (`weak/model.json`, `--install`) модель идёт при **accuracy ≥ 0.80, balanced
accuracy ≥ 0.80 и AUC ≥ 0.90** по повторной кросс-валидации (порог ТЗ — 75–80%). Иначе —
только отчёт, и страница остаётся с долей проверок.

## Пределы, которые обязаны стоять рядом с числами

- Контроль написан НАМИ, и он лёгкий (docs/weak-measurements.md): specificity и accuracy
  меряют отделимость от нашего представления о зрелом, не от закрытой разметки; порог на
  таком контроле проходится легче, чем на трудном (docs/todo.md §61).
- ⚠️ Разброс по повторам CV — изменчивость ОЦЕНКИ на этой выборке, не доверительный
  интервал на генеральную совокупность; интервал Уилсона печатается для сводных счётчиков.
- ⚠️ `None` в признаках — «измерить нечем»: импутация медианой считается ВНУТРИ фолда.
  Признак, пустой у всех строк (E без покрытия векторами), выбрасывается до обучения и
  называется вслух — молча выпасть в импутации он не может.
- ⚠️ Признак, постоянный внутри положительных (`standard_or_regulation` = 0 у всех ста),
  НЕ выбрасывается: это не условие отбора (правило docs/weak-audit.md про проверки внутри
  ТОП), а класс исключения ТЗ, который делит именно так. Он называется в отчёте вслух.
"""

from __future__ import annotations

import argparse
import importlib.util
import io as _io
import json
import math
import shutil
import sys
from datetime import date
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.stdout = _io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from sklearn.base import clone  # noqa: E402
from sklearn.impute import SimpleImputer  # noqa: E402
from sklearn.inspection import permutation_importance  # noqa: E402
from sklearn.linear_model import LogisticRegression  # noqa: E402
from sklearn.metrics import (  # noqa: E402
    brier_score_loss,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.model_selection import RepeatedStratifiedKFold, StratifiedKFold  # noqa: E402
from sklearn.tree import DecisionTreeClassifier, export_text  # noqa: E402

from pelican.weak.dataset import FEATURE_LABELS, LOG_FEATURES  # noqa: E402
from pelican.weak.kinds import EMERGING  # noqa: E402
from pelican.weak.model import phrase  # noqa: E402
from pelican.weak.stats import span  # noqa: E402

_ZOO = Path(__file__).with_name("model_zoo.py")
_spec = importlib.util.spec_from_file_location("model_zoo", _ZOO)
zoo = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(zoo)

FEATURES = Path("reports/weak-features-corpus.tsv")
OUT_MD = Path("reports/weak-model.md")
OUT_JSON = Path("reports/weak-model.json")
OUT_PKL = Path("reports/weak-model.pkl")
INSTALL_JSON = Path(__file__).resolve().parents[1] / "src" / "pelican" / "weak" / "model.json"

#: Правило годности (докстринг) — то же, что `model_zoo.STAGE1_*`.
GATE_ACC = zoo.STAGE1_ACC
GATE_AUC = zoo.STAGE1_AUC
FOLDS = 5
REPEATS = 10
SEED = 20260917


def read_tsv(path: Path) -> list[dict]:
    import csv

    return list(csv.DictReader(path.read_text(encoding="utf-8").splitlines(), delimiter="\t"))


def matrix(rows: list[dict], names: tuple[str, ...]) -> np.ndarray:
    return zoo.matrix(rows, names)


def select_features(rows: list[dict], names: tuple[str, ...], y: np.ndarray) -> tuple[list, list]:
    """Признаки, годные в обучение, и причины отсева остальных (вслух, не молча).

    Постоянный внутри положительных признак остаётся (докстринг модуля) и печатается.
    """
    kept, dropped = [], []
    for n in names:
        col = zoo.column(rows, n)
        if np.isnan(col).all():
            dropped.append((n, "пуст у всех строк — измерить нечем"))
            continue
        pos = col[y == 1]
        pos = pos[~np.isnan(pos)]
        if pos.size and np.nanmin(pos) == np.nanmax(pos):
            print(f"  признак `{n}` постоянен внутри положительных ({pos[0]:g}) — класс исключения")
        kept.append(n)
    return kept, dropped


def cross_validate(est: object, grid: dict, x: np.ndarray, y: np.ndarray) -> dict:
    """Повторная стратифицированная CV с nested-подбором гиперпараметра; метрики по повторам."""
    outer = RepeatedStratifiedKFold(n_splits=FOLDS, n_repeats=REPEATS, random_state=SEED)
    per_repeat: list[dict[str, float]] = []
    pooled = {"tp": 0, "fp": 0, "fn": 0, "tn": 0}
    oof_margin = np.zeros((REPEATS, len(y)))
    fold_pred = np.zeros(len(y), dtype=int)
    fold_prob = np.zeros(len(y))
    for i, (tr, te) in enumerate(outer.split(x, y)):
        repeat = i // FOLDS
        model = zoo.fit_best(clone(est), grid, x[tr], y[tr])
        prob = model.predict_proba(x[te])[:, 1]
        fold_prob[te] = prob
        fold_pred[te] = (prob >= 0.5).astype(int)
        if hasattr(model, "decision_function"):
            oof_margin[repeat, te] = model.decision_function(x[te])
        else:
            p = np.clip(prob, 1e-6, 1 - 1e-6)
            oof_margin[repeat, te] = np.log(p / (1 - p))
        if (i + 1) % FOLDS == 0:
            rec = recall_score(y, fold_pred, zero_division=0)
            spec = float(((fold_pred == 0) & (y == 0)).sum() / max((y == 0).sum(), 1))
            per_repeat.append(
                {
                    "precision": precision_score(y, fold_pred, zero_division=0),
                    "recall": rec,
                    "specificity": spec,
                    "f1": f1_score(y, fold_pred, zero_division=0),
                    "accuracy": float((fold_pred == y).mean()),
                    "balanced_accuracy": (rec + spec) / 2,
                    "auc": roc_auc_score(y, fold_prob),
                    "brier": brier_score_loss(y, fold_prob),
                }
            )
            pooled["tp"] += int(((fold_pred == 1) & (y == 1)).sum())
            pooled["fp"] += int(((fold_pred == 1) & (y == 0)).sum())
            pooled["fn"] += int(((fold_pred == 0) & (y == 1)).sum())
            pooled["tn"] += int(((fold_pred == 0) & (y == 0)).sum())
    keys = list(per_repeat[0])
    summary = {
        k: {
            "mean": float(np.mean([r[k] for r in per_repeat])),
            "lo": float(np.percentile([r[k] for r in per_repeat], 2.5)),
            "hi": float(np.percentile([r[k] for r in per_repeat], 97.5)),
        }
        for k in keys
    }
    return {"summary": summary, "pooled": pooled, "oof_margin": oof_margin.mean(axis=0)}


def platt(margin: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    """Сигмоида Платта на out-of-fold отступах: p = σ(a·s + b)."""
    lr = LogisticRegression(C=1e6, max_iter=5000).fit(margin.reshape(-1, 1), y)
    return float(lr.coef_[0][0]), float(lr.intercept_[0])


def reliability(prob: np.ndarray, y: np.ndarray, bins: int = 5) -> list[dict]:
    edges = np.linspace(0, 1, bins + 1)
    out = []
    for lo, hi in zip(edges[:-1], edges[1:], strict=True):
        mask = (prob >= lo) & (prob < hi if hi < 1 else prob <= hi)
        if not mask.any():
            continue
        out.append(
            {
                "bin": f"{lo:.1f}–{hi:.1f}",
                "n": int(mask.sum()),
                "predicted": float(prob[mask].mean()),
                "observed": float(y[mask].mean()),
            }
        )
    return out


def importance(final: object, x: np.ndarray, y: np.ndarray, names: list[str]) -> dict:
    """Permutation importance на отложенных фолдах; коэффициенты — если модель линейная."""
    est = final.named_steps["est"]
    coef = None
    if hasattr(est, "coef_"):
        coef = {n: float(v) for n, v in zip(names, est.coef_[0], strict=True)}
    perm = np.zeros(len(names))
    skf = StratifiedKFold(n_splits=FOLDS, shuffle=True, random_state=SEED)
    for tr, te in skf.split(x, y):
        m = clone(final).fit(x[tr], y[tr])
        r = permutation_importance(
            m, x[te], y[te], scoring="roc_auc", n_repeats=20, random_state=SEED
        )
        perm += r.importances_mean
    perm /= FOLDS
    return {
        "coef": coef,
        "intercept": float(est.intercept_[0]) if hasattr(est, "intercept_") else None,
        "permutation": {n: float(v) for n, v in zip(names, perm, strict=True)},
    }


def tree_rules(x: np.ndarray, y: np.ndarray, names: list[str]) -> str:
    imp = SimpleImputer(strategy="median").fit(x)
    tree = DecisionTreeClassifier(max_depth=3, class_weight="balanced", random_state=SEED)
    tree.fit(imp.transform(x), y)
    return export_text(tree, feature_names=[FEATURE_LABELS.get(n, n) for n in names], decimals=2)


def direction(name: str, coef: float) -> str:
    label = FEATURE_LABELS.get(name, name)
    if coef == 0:
        return "не участвует: L1 обнулила коэффициент"
    return f"чем больше «{label}», тем {'выше' if coef > 0 else 'ниже'} вероятность сигнала"


def fmt(m: dict) -> str:
    return f"{m['mean']:.2f} [{m['lo']:.2f}; {m['hi']:.2f}]"


def contributions(final: object, x: np.ndarray, names: list[str], params: dict) -> list[list]:
    """Разложение отступа по признакам для строки — те же слова, что в карточке
    (`weak.model.phrase`), только у линейной модели: (признак, вклад, сырое значение)."""
    if params.get("coef") is None:
        return []
    imp = final.named_steps["impute"].transform(x)
    std = np.array([v or 1.0 for v in params["scale_std"]])
    z = (imp - np.array(params["scale_mean"])) / std
    out = []
    for raw, row in zip(x, z, strict=True):
        parts = [
            (n, float(c * zi), None if math.isnan(v) else float(v))
            for n, c, zi, v in zip(names, params["coef"], row, raw, strict=True)
        ]
        out.append(sorted(parts, key=lambda t: -abs(t[1])))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="модель этапа 1: обучение победителя зоопарка")
    ap.add_argument("--features", default=str(FEATURES), help="TSV признаков (weak.dataset)")
    ap.add_argument(
        "--extra",
        action="append",
        default=[],
        help="дополнительные TSV признаков (трудный контроль); можно несколько раз",
    )
    ap.add_argument(
        "--from-zoo",
        action="store_true",
        help=f"взять семейство и набор из вердикта этапа 1 в {zoo.OUT_JSON}",
    )
    ap.add_argument(
        "--install",
        action="store_true",
        help=f"при выполненном правиле скопировать модель в {INSTALL_JSON}",
    )
    ap.add_argument(
        "--score",
        action="append",
        default=[],
        help="TSV признаков, которые НЕ идут в обучение, а только оцениваются готовой моделью "
        "(непроверенные кандидаты трудного контроля) — таблицей в отчёт",
    )
    args = ap.parse_args()

    rows = read_tsv(Path(args.features))
    seen = {(r["set"], r["n"]) for r in rows}
    for extra in args.extra:
        for r in read_tsv(Path(extra)):
            if (r["set"], r["n"]) not in seen:
                rows.append(r)
                seen.add((r["set"], r["n"]))
    y = np.array([1 if r["set"] == "pos" else 0 for r in rows])
    n_pos, n_ctl = int(y.sum()), int((y == 0).sum())
    if n_ctl < 10:
        print(f"контрольных строк {n_ctl}: обучать не на чем")
        raise SystemExit(1)
    print(f"строк {len(rows)}: положительных {n_pos}, контрольных {n_ctl}")
    const_f1 = 2 * n_pos / (2 * n_pos + n_ctl)
    print(
        f"⚠️ константа «всё — сигнал»: accuracy {n_pos / len(rows):.2f}, F1 {const_f1:.2f}, AUC 0.50"
    )

    # ------------------------------------------------------------ кто обучается
    key = zoo.REFERENCE
    if args.from_zoo:
        if not zoo.OUT_JSON.exists():
            print(f"нет {zoo.OUT_JSON}: сначала scripts/model_zoo.py")
            raise SystemExit(1)
        verdict = json.loads(zoo.OUT_JSON.read_text(encoding="utf-8")).get("stage1") or {}
        key = verdict.get("winner") or key
        print(f"вердикт зоопарка (этап 1): «{key}»")
    est_name, set_name = key.split(" / ", 1)
    est, grid = zoo.estimators(with_tabpfn=est_name == "tabpfn")[est_name]
    names, dropped = select_features(rows, zoo.SETS[set_name], y)
    for n, why in dropped:
        print(f"⚠️ признак `{n}` выброшен: {why}")
    print(f"семейство {est_name}, набор {set_name}: {len(names)} признаков")

    # ------------------------------------------------------- сравнительные линии
    lines_cv: dict[str, dict] = {}
    compare = {key: (est, grid, names)}
    lr_est, lr_grid = zoo.estimators(False)["lr_l1"]
    for other in ("N", "J"):
        if other != set_name:
            kept, _ = select_features(rows, zoo.SETS[other], y)
            compare[f"lr_l1 / {other}"] = (lr_est, lr_grid, kept)
    for title, (e, g, nm) in compare.items():
        x = matrix(rows, tuple(nm))
        print(f"\n=== {title} ({len(nm)} признаков) ===")
        cv = cross_validate(e, g, x, y)
        s = cv["summary"]
        for k in (
            "precision",
            "recall",
            "specificity",
            "f1",
            "accuracy",
            "balanced_accuracy",
            "auc",
        ):
            print(f"  {k:18s} {fmt(s[k])}")
        lines_cv[title] = cv

    # LLM в одиночку на тех же строках — рядом, без правила «должен превзойти».
    llm = np.array([r.get("llm_corpus", "") for r in rows])
    llm_line = None
    if all(v not in ("", None) for v in llm):
        pred = llm.astype(float).astype(int)
        rec = recall_score(y, pred, zero_division=0)
        spec = float(((pred == 0) & (y == 0)).sum() / max((y == 0).sum(), 1))
        llm_line = {
            "f1": f1_score(y, pred, zero_division=0),
            "accuracy": float((pred == y).mean()),
            "balanced_accuracy": (rec + spec) / 2,
            "recall": rec,
            "specificity": spec,
        }
        print(
            f"\ngemma `{EMERGING}` в одиночку на тех же строках: F1 {llm_line['f1']:.2f}, "
            f"accuracy {llm_line['accuracy']:.2f}, balanced {llm_line['balanced_accuracy']:.2f}"
        )

    # ------------------------------------------------------------ итоговая модель
    cv = lines_cv[key]
    x = matrix(rows, tuple(names))
    final = zoo.fit_best(clone(est), grid, x, y)
    imp = importance(final, x, y, names)
    a, b = platt(cv["oof_margin"], y)
    prob_cal = 1 / (1 + np.exp(-(a * cv["oof_margin"] + b)))
    prob_raw = 1 / (1 + np.exp(-cv["oof_margin"]))
    brier_raw = brier_score_loss(y, prob_raw)
    brier_cal = brier_score_loss(y, prob_cal)
    table = reliability(prob_cal, y)
    s = cv["summary"]
    gate = (
        s["accuracy"]["mean"] >= GATE_ACC
        and s["balanced_accuracy"]["mean"] >= GATE_ACC
        and s["auc"]["mean"] >= GATE_AUC
    )
    ranked = sorted(
        names,
        key=lambda n: -abs(imp["coef"][n]) if imp["coef"] else -imp["permutation"][n],
    )
    print("\n=== важность признаков ===")
    for n in ranked:
        c = f"coef {imp['coef'][n]:+.2f}  " if imp["coef"] else ""
        d = f"  — {direction(n, imp['coef'][n])}" if imp["coef"] else ""
        print(f"  {n:24s} {c}perm ΔAUC {imp['permutation'][n]:+.3f}{d}")
    print(f"\nкалибровка (Platt): a={a:.3f} b={b:.3f}; Brier {brier_raw:.3f} → {brier_cal:.3f}")
    print(
        f"\nправило годности accuracy ≥ {GATE_ACC}, balanced ≥ {GATE_ACC}, AUC ≥ {GATE_AUC}: "
        f"{'ВЫПОЛНЕНО' if gate else 'НЕ выполнено'}"
    )

    pooled = cv["pooled"]
    spec_pooled = pooled["tn"] / max(pooled["tn"] + pooled["fp"], 1)
    params: dict = {
        "trained": date.today().isoformat(),
        "source": "train_signal_model" + (" --from-zoo" if args.from_zoo else ""),
        "winner": key,
        "feature_set": set_name,
        "rows": {"positive": n_pos, "control": n_ctl},
        "features": list(names),
        "dropped": dropped,
        "impute_median": [float(v) for v in final.named_steps["impute"].statistics_],
        "scale_mean": [float(v) for v in final.named_steps["scale"].mean_],
        "scale_std": [float(v) for v in final.named_steps["scale"].scale_],
        "platt": {"a": a, "b": b},
        "metrics": {k: s[k] for k in s},
        "pooled": pooled,
        "permutation": imp["permutation"],
        "gate": {"accuracy": GATE_ACC, "auc": GATE_AUC, "passed": bool(gate)},
        "constant": {"accuracy": n_pos / len(rows), "f1": const_f1},
        "log1p": sorted(LOG_FEATURES & set(names)),
        "compare": {k: v["summary"] for k, v in lines_cv.items() if k != key},
        "llm_alone": llm_line,
        "reliability": table,
        "brier": {"raw": float(brier_raw), "platt": float(brier_cal)},
    }
    if imp["coef"] is not None:
        params["coef"] = [imp["coef"][n] for n in names]
        params["intercept"] = imp["intercept"]
    else:
        import joblib

        joblib.dump(final, OUT_PKL)
        params["estimator"] = "pickle"
        params["pickle"] = OUT_PKL.name
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(params, ensure_ascii=False, indent=2), encoding="utf-8")
    rows_expl = contributions(final, x, names, params)

    # Строки только на оценку (в обучение не идут): вероятность и главный вклад.
    scored: list[tuple[dict, float, str]] = []
    for path in args.score:
        extra_rows = [r for r in read_tsv(Path(path)) if (r["set"], r["n"]) not in seen]
        if not extra_rows:
            continue
        xs = matrix(extra_rows, tuple(names))
        if hasattr(final, "decision_function"):
            margin = final.decision_function(xs)
        else:
            p_ = np.clip(final.predict_proba(xs)[:, 1], 1e-6, 1 - 1e-6)
            margin = np.log(p_ / (1 - p_))
        probs = 1 / (1 + np.exp(-(a * margin + b)))
        expl = contributions(final, xs, names, params)
        for r, pr, ex in zip(extra_rows, probs, expl, strict=True):
            top = ", ".join(phrase(n, v, part) for n, part, v in ex[:2])
            scored.append((r, float(pr), top))

    # ---------------------------------------------------------------------- отчёт
    lines = [
        "# Модель этапа 1: обученный классификатор слабого сигнала",
        "",
        f"Сгенерировано `scripts/train_signal_model.py` {date.today():%d.%m.%Y} из "
        f"`{args.features}`" + (f" + {', '.join(args.extra)}" if args.extra else "") + ".",
        "",
        f"Строк {len(rows)}: положительных {n_pos}, контрольных {n_ctl}. Модель — "
        f"`{key}`" + (" (вердикт зоопарка по правилу парсимонии)" if args.from_zoo else "") + ": "
        f"{len(names)} признаков из `weak/dataset.py`; импутация медианой и стандартизация "
        f"внутри фолда, гиперпараметр — nested CV; валидация — стратифицированная "
        f"{FOLDS}-кратная CV × {REPEATS} повторов.",
        "",
        "⚠️ **Контроль написан нами и лёгкий**: метрики меряют отделимость от нашего "
        "представления о зрелом и шумном, не от закрытой разметки организаторов "
        "(docs/todo.md §61). ⚠️ Интервал у метрик — разброс по повторам CV (2.5–97.5 "
        "перцентили), не доверительный интервал; у specificity — Уилсон по сводным счётчикам.",
        "",
    ]
    if dropped:
        lines += ["Выброшено до обучения:", ""]
        lines += [f"- `{n}` — {why}" for n, why in dropped]
        lines.append("")
    lines += [
        "## Метрики",
        "",
        "| конфигурация | precision | recall | spec | F1 | accuracy | balanced | AUC |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for title, res in lines_cv.items():
        s2 = res["summary"]
        mark = " **← модель**" if title == key else ""
        lines.append(
            f"| `{title}`{mark} | {fmt(s2['precision'])} | {fmt(s2['recall'])} | "
            f"{fmt(s2['specificity'])} | {fmt(s2['f1'])} | {fmt(s2['accuracy'])} | "
            f"{fmt(s2['balanced_accuracy'])} | {fmt(s2['auc'])} |"
        )
    if llm_line:
        lines.append(
            f"| gemma `{EMERGING}` в одиночку | — | {llm_line['recall']:.2f} | "
            f"{llm_line['specificity']:.2f} | {llm_line['f1']:.2f} | {llm_line['accuracy']:.2f} | "
            f"{llm_line['balanced_accuracy']:.2f} | — |"
        )
    lines += [
        "",
        f"⚠️ Константа «всё — сигнал» на этой доле положительных: accuracy {n_pos / len(rows):.2f}, "
        f"F1 {const_f1:.2f}, AUC 0.50 — метрика ниже неё результатом не является. Строка gemma — "
        "для сравнения, без правила «должен превзойти»: вопрос этапа 1 — годна ли модель.",
        "",
        f"**Правило годности** (записано до прогона): accuracy ≥ {GATE_ACC}, balanced accuracy ≥ "
        f"{GATE_ACC}, AUC ≥ {GATE_AUC} → **{'выполнено' if gate else 'НЕ выполнено'}**"
        + (
            ": модель идёт в `weak/model.json`, вероятность и предикторы — в карточку."
            if gate
            else ": вероятность в выдачу не идёт, страница остаётся с долей проверок."
        ),
        "",
        f"Сводно по {REPEATS} повторам: TP {pooled['tp']} · FP {pooled['fp']} · "
        f"FN {pooled['fn']} · TN {pooled['tn']}; specificity {spec_pooled:.2f} "
        f"{span(pooled['tn'], pooled['tn'] + pooled['fp'])}.",
        "",
        "## Важность признаков",
        "",
    ]
    if imp["coef"] is not None:
        lines += [
            "Коэффициент — на стандартизованном признаке (сравнимы между собой); permutation "
            "importance — падение AUC на отложенном фолде при перемешивании признака "
            f"(среднее по {FOLDS} фолдам × 20 перестановок). Вклад строки = коэффициент × z — "
            "это и есть «ключевые предикторы» карточки (`weak/model.py`).",
            "",
            "| признак | коэффициент | ΔAUC при перестановке | читается |",
            "|---|---|---|---|",
        ]
        for n in ranked:
            lines.append(
                f"| `{n}` — {FEATURE_LABELS.get(n, n)} | {imp['coef'][n]:+.2f} | "
                f"{imp['permutation'][n]:+.3f} | {direction(n, imp['coef'][n])} |"
            )
    else:
        lines += [
            "Модель нелинейная: важность — permutation importance (падение AUC на отложенном "
            f"фолде, среднее по {FOLDS} фолдам × 20 перестановок). ⚠️ Вклад по строке у неё — "
            "грубая подсказка (`weak/model.py`), не разложение.",
            "",
            "| признак | ΔAUC при перестановке |",
            "|---|---|",
        ]
        for n in ranked:
            lines.append(f"| `{n}` — {FEATURE_LABELS.get(n, n)} | {imp['permutation'][n]:+.3f} |")
    if rows_expl:
        lines += ["", "## Пять строк с разложением «за / против»", ""]
        picks = list(range(0, len(rows), max(len(rows) // 5, 1)))[:5]
        for i in picks:
            top = ", ".join(
                phrase(n, v, part)
                for n, part, v in rows_expl[i][:3]
            )
            lines.append(
                f"- **{rows[i]['tech']}** ({'сигнал' if y[i] else 'контроль'}, "
                f"p={prob_cal[i]:.2f}): {top}"
            )
    if scored:
        lines += [
            "",
            "## Непроверенные кандидаты трудного контроля — только оценка, не обучение",
            "",
            "⚠️ Метки этих строк машинные (судья / след ядра) и рукой не подтверждены "
            "(docs/todo.md §61): в обучение и в порог они не идут. Таблица — подсказка для "
            "ручной разметки: где модель и ожидание расходятся, там и смотреть.",
            "",
            "| строка | ожидание | p модели | главные вклады |",
            "|---|---|---|---|",
        ]
        for r, pr, top in scored:
            lines.append(f"| {r['tech']} | {r.get('expected_kind', '')} | {pr:.2f} | {top} |")
    lines += [
        "",
        "## Калибровка",
        "",
        f"Platt scaling на out-of-fold отступах: `p = σ({a:.3f}·s + {b:.3f})`. "
        f"Brier score: сырая сигмоида {brier_raw:.3f} → после Платта {brier_cal:.3f} "
        "(0 — идеально, 0.25 — монета).",
        "",
        "| вероятность | строк | предсказано | наблюдено |",
        "|---|---|---|---|",
    ]
    for t in table:
        lines.append(f"| {t['bin']} | {t['n']} | {t['predicted']:.2f} | {t['observed']:.2f} |")
    lines += [
        "",
        f"⚠️ Калибровка на {len(rows)} строках с долей положительных {n_pos / len(rows):.2f}: "
        "вероятность откалибрована под ЭТУ долю, и на потоке с другой долей зрелого она смещена "
        "— свойство любой калибровки (prior shift), не дефект модели.",
        "",
        "## Дерево глубины 3 — читаемое правило (не модель выдачи)",
        "",
        "```",
        tree_rules(x, y, names).rstrip(),
        "```",
        "",
        f"Параметры модели для применения без sklearn — `{OUT_JSON}` (`weak/model.py`).",
    ]
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nотчёт: {OUT_MD}\nмодель: {OUT_JSON}")

    if args.install:
        if not gate:
            print(f"⚠️ правило не выполнено — в {INSTALL_JSON} не копирую")
            return
        shutil.copyfile(OUT_JSON, INSTALL_JSON)
        if params.get("estimator") == "pickle":
            shutil.copyfile(OUT_PKL, INSTALL_JSON.with_name(OUT_PKL.name))
        print(f"установлено: {INSTALL_JSON}")


if __name__ == "__main__":
    main()
