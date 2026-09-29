"""Применение модели без sklearn (`weak.model`): формула, импутация, гейт.

⚠️ Последний тест — регресс между numpy-применением и sklearn-обучением: если в
`weak/model.json` лежат параметры, вероятность на строке из `weak-features-corpus.tsv`
обязана сходиться с тем, что дал бы sklearn на тех же параметрах. Без файла тест
пропускается, а не проходит.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from pelican.weak import model
from pelican.weak.dataset import (
    EMBED_FEATURES,
    FEATURE_NAMES,
    JUDGMENT_FEATURES,
    LLM_FEATURES,
)

PARAMS = {
    "features": ["a", "b"],
    "impute_median": [1.0, 5.0],
    "scale_mean": [0.0, 5.0],
    "scale_std": [2.0, 1.0],
    "coef": [1.0, -0.5],
    "intercept": 0.25,
    "platt": {"a": 1.0, "b": 0.0},
    "gate": {"passed": True},
}


def _sigmoid(x: float) -> float:
    return 1 / (1 + math.exp(-x))


def test_probability_is_platt_of_linear_margin() -> None:
    p = model.probability({"a": 2.0, "b": 7.0}, PARAMS)
    # z = (1.0, 2.0); s = 0.25 + 1.0·1.0 − 0.5·2.0 = 0.25
    assert abs(p.probability - _sigmoid(0.25)) < 1e-12
    assert p.contributions[0] == ("a", 1.0) and p.contributions[1] == ("b", -1.0)
    assert p.predictors[0].startswith("a — за")


def test_binary_judgment_phrase_names_absence_before_direction() -> None:
    # «нет: это стандарт — за», а не «это стандарт: за»: отсутствие признака не его наличие.
    assert model.phrase("standard_or_regulation", 0.0, 0.4).startswith("нет: ")
    assert model.phrase("standard_or_regulation", 1.0, -0.4).startswith("да: ")
    assert model.phrase("players_named", 3.0, 0.1).startswith("компаний")


def test_missing_feature_is_imputed_with_training_median() -> None:
    p = model.probability({"a": None}, PARAMS)
    # a → медиана 1.0 → z 0.5 → +0.5; b → медиана 5.0 → z 0 → 0
    assert abs(p.probability - _sigmoid(0.75)) < 1e-12


def test_load_refuses_model_that_failed_the_gate(tmp_path: Path) -> None:
    bad = dict(PARAMS, gate={"passed": False})
    path = tmp_path / "model.json"
    path.write_text(json.dumps(bad), encoding="utf-8")
    assert model.load(path) is None
    assert model.load(tmp_path / "absent.json") is None
    path.write_text(json.dumps(PARAMS), encoding="utf-8")
    assert model.load(path) is not None


def test_pickle_model_unloadable_means_no_model(tmp_path: Path, monkeypatch) -> None:
    params = dict(PARAMS, estimator="pickle", pickle="absent.pkl")
    path = tmp_path / "model.json"
    path.write_text(json.dumps(params), encoding="utf-8")
    assert model.load(path) is None  # файла нет → без модели, а не падение


def test_pickle_model_predicts_and_ranks_by_permutation(tmp_path: Path) -> None:
    sklearn = pytest.importorskip("sklearn")  # noqa: F841
    import joblib
    import numpy as np
    from sklearn.dummy import DummyClassifier

    est = DummyClassifier(strategy="prior").fit(np.zeros((4, 2)), [0, 1, 1, 1])
    joblib.dump(est, tmp_path / "m.pkl")
    params = dict(
        PARAMS,
        estimator="pickle",
        pickle="m.pkl",
        permutation={"a": 0.2, "b": 0.05},
    )
    (tmp_path / "model.json").write_text(json.dumps(params), encoding="utf-8")
    loaded = model.load(tmp_path / "model.json")
    assert loaded is not None
    p = model.probability({"a": 4.0, "b": 5.0}, loaded)
    assert abs(p.probability - 0.75) < 1e-9
    assert p.contributions[0][0] == "a"


def test_shipped_params_match_feature_contract() -> None:
    if not model.PARAMS.exists():
        pytest.skip("weak/model.json не обучена")
    params = json.loads(model.PARAMS.read_text(encoding="utf-8"))
    names = tuple(params["features"])
    known = set(FEATURE_NAMES) | set(LLM_FEATURES) | set(JUDGMENT_FEATURES) | set(EMBED_FEATURES)
    assert set(names) <= known
    # ⚠️ Вердикт LLM в модели этапа 1 быть не может (docs/weak-measurements.md).
    assert not set(names) & set(LLM_FEATURES)
    assert params["gate"]["passed"]
    for key in ("impute_median", "scale_mean", "scale_std"):
        assert len(params[key]) == len(names)


def test_shipped_growth_params_match_feature_contract() -> None:
    if not model.GROWTH_PARAMS.exists():
        pytest.skip("weak/growth.json не обучена")
    params = json.loads(model.GROWTH_PARAMS.read_text(encoding="utf-8"))
    names = tuple(params["features"])
    # Скоринг выдачи (`ask.score`) принимает только признаки внутри запроса: другой набор
    # означал бы, что модель в стенде молча не применяется.
    assert set(names) <= set(model.QUERY_FEATURES)
    assert params["gate"]["passed"]
    for key in ("impute_median", "scale_mean", "scale_std", "coef"):
        assert len(params[key]) == len(names)
    feats = model.query_features(
        [{"core_last_year_share": 0.8, "core_works": 30, "players": 3, "money_stream": 0}]
    )[0]
    got = model.probability(feats, params)
    assert 0.0 < got.probability < 1.0 and len(got.predictors) == 3


def test_query_features_are_relative_to_the_query() -> None:
    pool = [
        {"core_last_year_share": 0.9, "core_works": 100, "players": 6, "money_stream": 0},
        {"core_last_year_share": 1.0, "core_works": 1, "players": 0, "money_stream": 1},
        {"core_last_year_share": 0.2, "core_works": 50, "players": 0, "money_stream": 0},
        {"core_last_year_share": None, "core_works": 0, "players": 1, "money_stream": 0},
    ]
    f = model.query_features(pool)
    # Крошечное ядро со 100% свежих сжато к медиане пула (0.9), большое — почти не сдвинуто.
    assert 0.9 <= f[1]["query_share"] < 0.92
    assert abs(f[0]["query_share"] - 0.9) < 1e-9
    # Нечем измерить — в хвост; перцентиль внутри пула.
    assert f[3]["query_share"] == -1.0 and f[3]["query_share_pct"] == 0.25
    assert f[1]["query_share_pct"] == 1.0 and f[2]["query_share_pct"] == 0.5
    # Деньги или хотя бы один игрок; премия за игроков с потолком.
    assert [x["backed"] for x in f] == [1.0, 1.0, 0.0, 1.0]
    assert f[0]["players_cap"] == model.PLAYERS_CAP


def test_numpy_application_matches_sklearn() -> None:
    sklearn = pytest.importorskip("sklearn")  # noqa: F841
    if not model.PARAMS.exists():
        pytest.skip("weak/model.json не обучена")
    import numpy as np
    from sklearn.linear_model import LogisticRegression

    params = json.loads(model.PARAMS.read_text(encoding="utf-8"))
    if "coef" not in params:
        pytest.skip("победитель зоопарка — не регрессия")
    names = tuple(params["features"])
    lr = LogisticRegression()
    lr.coef_ = np.array([params["coef"]])
    lr.intercept_ = np.array([params["intercept"]])
    lr.classes_ = np.array([0, 1])
    x = np.array([[0.7 * (i + 1) for i in range(len(names))]])
    logged = np.array([n in set(params.get("log1p", ())) for n in names])
    xt = np.where(logged, np.log1p(x), x)
    z = (xt - np.array(params["scale_mean"])) / np.array(params["scale_std"])
    margin = float(lr.decision_function(z)[0])
    expected = _sigmoid(params["platt"]["a"] * margin + params["platt"]["b"])
    got = model.probability(dict(zip(names, x[0].tolist(), strict=True)), params)
    assert abs(got.probability - expected) < 1e-9
