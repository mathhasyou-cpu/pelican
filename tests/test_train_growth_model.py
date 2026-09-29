"""Модель роста (`scripts/train_growth_model.py`): temporal holdout.

Набор признаков выбран по train ДО взгляда на test, столбец, пустой на одном из срезов,
выбрасывается на обоих, а вердикт считается по правилу на test.
"""

from __future__ import annotations

import importlib.util
import json
import random
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "train_growth_model.py"
spec = importlib.util.spec_from_file_location("train_growth_model", SCRIPT)
tg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tg)


def _rows(n: int, seed: int, with_label: bool) -> list[dict]:
    rnd = random.Random(seed)
    out = []
    for i in range(n):
        signal = rnd.random()
        grew = int(signal > 0.5)
        out.append(
            {
                "домен": "A" if i % 2 else "B",
                "где": "ТОП" if i % 3 == 0 else "контроль",
                "направление": f"d{i}",
                "grew_excess": str(grew),
                "excess": f"{signal - 0.5:.3f}",
                "во сколько раз": f"{1 + 3 * signal:.2f}",
                "p модели": f"{rnd.random():.3f}",
                "gemma на срезе": "emerging" if rnd.random() > 0.5 else "noise",
                "players": f"{signal * 5 + rnd.random():.2f}",
                "core_momentum": f"{1 + signal * 2 + rnd.random() * 0.3:.2f}",
                "low_visibility": str(int(rnd.random() > 0.5)),
                "money_stream": str(int(rnd.random() > 0.7)),
                "core_age_at_cut": f"{rnd.randint(0, 10)}",
                "label_momentum": f"{1 + signal + rnd.random() * 0.3:.2f}" if with_label else "",
            }
        )
    return out


def test_holdout_trains_on_one_cut_and_scores_the_other(tmp_path) -> None:
    train, test = _rows(60, 1, True), _rows(40, 2, False)
    sets = {"M": tg.MOMENTUM}
    got = tg.holdout(
        train, test, "grew_excess", sets, "lr_l2 / M",
        tmp_path / "h.md", tmp_path / "h.json", ("bt-2022", "bt-2024"),
    )  # fmt: skip
    model = got["results"]["lr_l2 / M"]
    # Пустой на test столбец выброшен на обоих срезах; сигнал на синтетике ловится.
    assert "label_momentum" not in model["features"] and "core_momentum" in model["features"]
    assert model["auc"] > 0.8 > model["floor"]
    assert got["chosen"] == "lr_l2 / M" and got["train_dir"] == "bt-2022"
    assert "ТОП конвейера" in got["results"]
    assert json.loads((tmp_path / "h.json").read_text(encoding="utf-8"))["passed"] == got["passed"]
    assert "**←**" in (tmp_path / "h.md").read_text(encoding="utf-8")


def test_best_c_picks_from_grid() -> None:
    import numpy as np

    rnd = np.random.default_rng(0)
    x = rnd.normal(size=(60, 2))
    y = (x[:, 0] > 0).astype(int)
    assert tg.best_c(x, y, "l2") in tg.C_GRID


def test_stable_features_drop_sign_flips_and_keep_consistent_ones(capsys) -> None:
    """Устойчивый набор: признак с перевёрнутым знаком между срезами выброшен, устойчивый
    и достаточно сильный — оставлен, слабый на обоих — нет, пустой — нет."""
    rnd = random.Random(3)
    cut_a, cut_b = [], []
    for i in range(80):
        y = i % 2
        for rows, flip in ((cut_a, 1.0), (cut_b, -1.0)):
            rows.append(
                {
                    "grew_excess": str(y),
                    "stable": f"{y + rnd.random() * 0.5:.3f}",
                    "flipping": f"{flip * y + rnd.random() * 0.5:.3f}",
                    "weak": f"{rnd.random():.3f}",
                    "empty": "",
                }
            )
    got = tg.stable_features(
        {"a": cut_a, "b": cut_b}, ("stable", "flipping", "weak", "empty"), "grew_excess"
    )
    assert got == ("stable",)
    out = capsys.readouterr().out
    assert "flipping" in out and "← S" in out


def test_pick_best_prefers_l1_within_margin() -> None:
    results = {
        "lr_l2 / A": {"auc": 0.72},
        "lr_l1 / A": {"auc": 0.71},  # в пределах L1_MARGIN — берётся L1
        "lr_l2 / B": {"auc": 0.70},
        "lr_l1 / B": {"auc": 0.60},  # хуже на 0.10 — остаётся L2
    }
    assert tg.pick_best(results, {"A": ("x",), "B": ("x",)}) == "lr_l1 / A"
    results["lr_l2 / B"]["auc"] = 0.80
    assert tg.pick_best(results, {"A": ("x",), "B": ("x",)}) == "lr_l2 / B"
