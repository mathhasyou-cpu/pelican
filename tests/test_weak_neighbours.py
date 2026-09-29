"""Соседство в векторах (`weak.neighbours`): окна по маскам id, покрытие, `None` без него.

⚠️ Шарды синтетические, дат в них нет: окно решает маска «после даты» из
`future_ids`, и тест держит, что работа из последнего окна не считается «прежним
соседом» для новизны, частичное покрытие признак не гасит, а пустое окно — `None`, не ноль.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import pytest

from pelican.weak import neighbours as nb

MODEL = "google/embedding-gemma-300m"
TODAY = date(2026, 9, 18)


def _unit(*coords: float) -> np.ndarray:
    v = np.zeros(4, dtype=np.float32)
    for i, c in enumerate(coords):
        v[i] = c
    return v / np.linalg.norm(v)


def _write(path: Path, ids: list[int], vectors: list[np.ndarray]) -> None:
    np.savez(
        path,
        model=np.array(MODEL),
        ids=np.array(ids, dtype=np.int64),
        vectors=np.stack(vectors).astype(np.float16),
    )


@pytest.fixture
def corpus(tmp_path: Path, monkeypatch) -> dict:
    """Пять работ: 1–2 старые, 3 в предыдущем окне, 4–5 в последнем; 5 — хаб."""
    _write(tmp_path / "shard-000000.npz", [1, 2, 3], [_unit(1, 1), _unit(0, 1), _unit(1, 0.2)])
    _write(tmp_path / "shard-000001.npz", [4, 5], [_unit(1, 0), _unit(1, 0)])
    after_asof = np.array([4, 5], dtype=np.int64)
    after_since = np.array([3, 4, 5], dtype=np.int64)
    state = {"total": 5}
    monkeypatch.setattr(nb, "EMB_DIR", tmp_path)
    monkeypatch.setattr(nb.settings, "embedding_model", MODEL)
    monkeypatch.setattr(nb, "junk_ids", lambda store: np.array([5], dtype=np.int64))
    # ⚠️ `TODAY` — в прошлом относительно календаря, и `neighbourhoods` берёт маску среза
    # `future_ids(store, TODAY)`: она обязана быть пустой, иначе тест ломается датой.
    masks = {TODAY: np.zeros(0, dtype=np.int64), date(2024, 9, 18): after_asof}
    monkeypatch.setattr(nb, "future_ids", lambda store, day: masks.get(day, after_since))
    monkeypatch.setattr(nb, "science_count", lambda store: state["total"])
    monkeypatch.setattr(nb, "TAU", 0.5)
    return state


def test_windows_come_from_masks_and_hub_is_ignored(corpus: dict) -> None:
    query = _unit(1, 0)[None, :]
    [n], cov, vec = nb.neighbourhoods(None, ["x"], TODAY, vectors=query)
    assert cov.last == 1.0 and cov.prev == 1.0 and cov.before == 1.0
    assert n.nn_sim_now == pytest.approx(1.0)  # работа 4 (5 — хаб, снят)
    # Прежний сосед — среди работ ДО последнего окна: 3 (cos ≈ 0.98), не 4.
    assert n.nn_sim_asof == pytest.approx(float(_unit(1, 0.2)[0]), abs=1e-3)
    assert n.nn_sim_gain is not None and n.nn_sim_gain > 0
    # cos ≥ 0.5: работы 1 (0.71), 3 (0.98), 4 (1.0); 2 (0) и хаб 5 — нет.
    assert n.dense_last == 1 and n.dense_prev == 1
    assert n.dense_ratio == 1.0
    assert n.nbr_coherence is not None and -1.0 <= n.nbr_coherence <= 1.0
    assert vec.shape == (1, 4)


def test_partial_coverage_still_measures(corpus: dict) -> None:
    # База знает о пятидесяти работах, в шардах пять: считаем по тем, что есть.
    corpus["total"] = 50
    query = _unit(1, 0)[None, :]
    [n], cov, _ = nb.neighbourhoods(None, ["x"], TODAY, vectors=query)
    assert cov.before < 0.1
    assert n.nn_sim_asof == pytest.approx(float(_unit(1, 0.2)[0]), abs=1e-3)
    assert n.dense_last == 1 and n.dense_prev == 1


def test_empty_window_yields_none_not_zero(corpus: dict, monkeypatch) -> None:
    # Все работы с векторами — в последнем окне: до asof сравнивать не с чем.
    everything = np.array([1, 2, 3, 4, 5], dtype=np.int64)
    masks = {TODAY: np.zeros(0, dtype=np.int64)}
    monkeypatch.setattr(nb, "future_ids", lambda store, day: masks.get(day, everything))
    query = _unit(1, 0)[None, :]
    [n], _, _ = nb.neighbourhoods(None, ["x"], TODAY, vectors=query)
    assert n.nn_sim_asof is None and n.nn_sim_gain is None
    assert n.dense_prev is None
    assert n.nn_sim_now == pytest.approx(1.0)


def test_past_today_hides_works_after_the_cut(corpus: dict) -> None:
    """Под срезом (`today` в прошлом) работы после него — не соседи и не покрытие."""
    query = _unit(1, 0)[None, :]
    # today = 2024-09-18 → «после today» = [4, 5]; последнее окно (2022-09..2024-09) = [3].
    [n], cov, _ = nb.neighbourhoods(None, ["x"], date(2024, 9, 18), vectors=query)
    assert n.nn_sim_now == pytest.approx(float(_unit(1, 0.2)[0]), abs=1e-3)  # работа 3
    assert n.nn_sim_asof == pytest.approx(float(_unit(1, 1)[0]), abs=1e-3)  # работа 1
    assert cov.last == 1.0 and n.dense_last == 1


def test_empty_labels_measure_nothing() -> None:
    out, cov, vec = nb.neighbourhoods(None, [], TODAY)
    assert out == [] and vec.size == 0 and cov.last == 0.0
