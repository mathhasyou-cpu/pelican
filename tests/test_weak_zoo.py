"""Статистика зоопарка (`scripts/model_zoo.py`): corrected t, McNemar, Холм, прибавка.

⚠️ Правило решения считает строки, а не проценты: тест держит, что «чинит / ломает»
сходится с ручным счётом и что базовые линии self-consistency собираются верно.
"""

from __future__ import annotations

import importlib.util
import math
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("sklearn")
pytest.importorskip("scipy")

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "model_zoo.py"
spec = importlib.util.spec_from_file_location("model_zoo", SCRIPT)
zoo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(zoo)


def test_corrected_t_inflates_variance_against_naive() -> None:
    diffs = np.array([0.02, 0.03, 0.01, 0.04, 0.02, 0.03, 0.02, 0.01, 0.03, 0.02] * 10)
    t, p = zoo.corrected_t(diffs, n_train=117, n_test=13)
    naive_t = diffs.mean() / (diffs.std(ddof=1) / math.sqrt(len(diffs)))
    assert 0 < t < naive_t  # поправка Надо–Бенжио всегда уменьшает t
    assert 0 < p < 1
    assert zoo.corrected_t(np.zeros(10), 90, 10) == (0.0, 1.0)


def test_mcnemar_counts_only_discordant_rows() -> None:
    y = np.array([1, 1, 1, 0, 0, 1])
    a = np.array([1, 1, 0, 0, 1, 1])  # чинит третью? нет: a ошибается на 3 и 5
    b = np.array([0, 1, 1, 0, 0, 1])
    fixed, broke, p = zoo.mcnemar_exact(y, a, b)
    assert (fixed, broke) == (1, 2)  # a права там, где b нет: строка 1; b права: 3 и 5
    assert p == pytest.approx(1.0)


def test_holm_is_monotone_and_capped() -> None:
    out = zoo.holm({"a": 0.01, "b": 0.04, "c": 0.5})
    assert out["a"] == pytest.approx(0.03)
    assert out["b"] == pytest.approx(0.08)
    assert out["c"] == pytest.approx(0.5)
    assert out["a"] <= out["b"] <= out["c"]


def _cand(f1_folds: list[float], f1: float, acc: float = 0.9, auc: float = 0.95) -> dict:
    return {
        "f1_folds": f1_folds,
        "f1": f1,
        "accuracy": acc,
        "balanced_accuracy": acc,
        "auc": auc,
    }


def test_stage1_keeps_reference_unless_beaten_significantly() -> None:
    """Парсимония: опорная LR остаётся, пока нелинейное семейство не бьёт её по Холму."""
    ref = _cand([0.90] * 20, 0.90)
    same = _cand([0.90] * 20, 0.90)  # ничем не лучше
    noisy = _cand([0.95, 0.85] * 10, 0.90)  # выше в среднем — нет, шум
    results = {
        zoo.REFERENCE: ref,
        "rf / N+J": same,
        "hgb / N+J+E": noisy,
        "rf / N+L3": _cand([0.99] * 20, 0.99),
    }
    v = zoo.stage1_verdict(results, n_train=117, n_test=13)
    assert v["winner"] == zoo.REFERENCE and v["passed"]
    assert "rf / N+L3" not in v["p_vs_reference"]  # набор с голосами — не кандидат этапа 1
    # Устойчиво лучше на каждом фолде — бьёт.
    results["rf / N+J"] = _cand([0.98] * 20, 0.98)
    v = zoo.stage1_verdict(results, n_train=117, n_test=13)
    assert v["winner"] == "rf / N+J" and v["beats_reference"] == ["rf / N+J"]


def test_stage1_gate_fails_below_threshold_and_without_reference() -> None:
    weak = {zoo.REFERENCE: _cand([0.7] * 20, 0.7, acc=0.7, auc=0.8)}
    assert not zoo.stage1_verdict(weak, 117, 13)["passed"]
    assert zoo.stage1_verdict({}, 117, 13)["winner"] is None


def test_usable_drops_only_all_empty_columns() -> None:
    rows = [{"a": "1", "b": ""}, {"a": "", "b": ""}]
    assert zoo.usable(rows, ("a", "b")) == ("a",)


def test_summarize_gain_is_fixed_minus_broken() -> None:
    y = np.array([1] * 6 + [0] * 4)
    llm = np.array([1, 1, 1, 1, 0, 0, 0, 0, 0, 0])  # FN 2, FP 0
    pred = np.array([1, 1, 1, 1, 1, 0, 0, 0, 0, 1])  # чинит одну FN, ломает одну TN
    prob = pred.astype(float)
    splits = [(np.arange(5), np.arange(5, 10)), (np.arange(5, 10), np.arange(5))]
    f1_folds = [zoo.fold_f1(y, pred, te) for _, te in splits]
    llm_f1 = [zoo.fold_f1(y, llm, te) for _, te in splits]
    r = zoo.summarize(y, llm, [pred, pred], [prob, prob], f1_folds, llm_f1, 5, 5)
    assert r["gain_median"] == 0
    assert r["fixed_rows"] == [4] and r["broke_rows"] == [9]
    assert r["mcnemar"]["fixed"] == 1 and r["mcnemar"]["broke"] == 1
