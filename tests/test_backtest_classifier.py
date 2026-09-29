"""Бэктест модели (`scripts/backtest_classifier.py`): исходы по домену и momentum.

⚠️ Исход «вырос ≥2×» на корпусе про ИИ истинен почти у всех, поэтому исход считается
ОТНОСИТЕЛЬНО домена; тест держит, что медиана и верхняя треть берутся внутри домена, а
momentum из двух следов — `None`, когда меряет нечем.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from pelican.weak.core import Footprint

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "backtest_classifier.py"
spec = importlib.util.spec_from_file_location("backtest_classifier", SCRIPT)
bt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bt)


def test_outcomes_are_relative_to_domain() -> None:
    rows = [
        {"домен": "A", "во сколько раз": 2.0, "money_after_n": 0, "money_last_n": "1"},
        {"домен": "A", "во сколько раз": 4.0, "money_after_n": 0, "money_last_n": ""},
        {"домен": "A", "во сколько раз": 8.0, "money_after_n": 3, "money_last_n": "0"},
        {"домен": "B", "во сколько раз": 1.0, "money_after_n": 2, "money_last_n": "0"},
        {"домен": "B", "во сколько раз": 1.5, "money_after_n": 5, "money_last_n": "0"},
        {"домен": "B", "во сколько раз": 1.1, "money_after_n": 9, "money_last_n": "0"},
    ]
    bt.outcomes(rows)
    # В домене A всё выросло ≥2×, но избыточно — только верх домена.
    assert [r["grew"] for r in rows] == [1, 1, 1, 0, 0, 0]
    assert [r["grew_excess"] for r in rows] == [0, 0, 1, 0, 1, 0]
    assert [r["top_tercile"] for r in rows] == [0, 0, 1, 0, 1, 0]
    assert rows[1]["excess"] == 0.0 and rows[2]["excess"] > 0 > rows[0]["excess"]
    # Деньги — тем же приёмом; медиана домена A — 0, и «выше медианы» = «нашлись вовсе».
    assert [r["money_grew"] for r in rows] == [0, 0, 1, 0, 0, 1]
    assert [r["money_top"] for r in rows] == [0, 0, 1, 0, 0, 1]
    assert rows[2]["money_excess"] > 0 and rows[3]["money_excess"] < 0
    # Отношение «после/до» — справка: у строки с деньгами до среза и без после — ниже нуля.
    assert rows[0]["money_ratio"] < 0 < rows[2]["money_ratio"]


def test_money_features_from_two_traces() -> None:
    from datetime import date

    from pelican.weak.rounds import MoneyTrace

    last = MoneyTrace("x", 4, 25e6, 3, "2024-08-01")
    prev = MoneyTrace("x", 1, 0.0, 1, "2023-05-05")
    got = bt.money_features(last, prev, date(2024, 9, 15), 0.12)
    assert got["money_momentum"] == 2.5 and got["money_recency_days"] == 45.0
    assert got["money_last_usd"] == 25e6 and got["money_publishers"] == 3.0
    assert got["industry_share"] == 0.12
    empty = MoneyTrace("x", 0, 0.0, 0, "")
    assert bt.money_features(empty, empty, date(2024, 9, 15), None)["money_recency_days"] is None


def test_momentum_is_none_when_unmeasured_and_ratio_otherwise() -> None:
    assert bt.momentum(None, Footprint("x", 1, 2020, 1)) is None
    assert bt.momentum(Footprint("x", 5, 2020, -1), Footprint("x", 1, 2020, 1)) is None
    assert bt.momentum(Footprint("x", 5, 2020, 0), Footprint("x", 1, 2020, 0)) is None
    assert bt.momentum(Footprint("x", 9, 2020, 3), Footprint("x", 5, 2020, 1)) == 2.0


def test_backtest_signals_measures_both_ends_on_one_slice_set(tmp_path, monkeypatch) -> None:
    """`backtest_signals`: срезы openalex с краем не позже среза; след на срезе и на
    горизонте — оба `weak.core`, новости — явным окном после среза."""
    import json
    import sys
    from datetime import date

    spec2 = importlib.util.spec_from_file_location(
        "backtest_signals", SCRIPT.with_name("backtest_signals.py")
    )
    bs = importlib.util.module_from_spec(spec2)
    spec2.loader.exec_module(bs)

    class FakeStore:
        def __init__(self, *_a, **_k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

        def query(self, _sql, _params=None):
            return [("Energy", "2020-01-01"), ("Computer Science", "2023-01-02")]

    assert bs.slices_at(FakeStore(), date(2022, 9, 15)) == {"Energy"}
    assert bs.slices_at(FakeStore(), date(2024, 9, 15)) == {"Energy", "Computer Science"}

    folder = tmp_path / "bt-2022-09-15"
    folder.mkdir()
    (folder / "ask-cards-X.json").write_text(
        json.dumps(
            {
                "signals": [{"label": "a", "core": "a", "core_works": 999}],
                "excluded": [{"label": "b", "core": "b", "core_works": 999}],
            }
        ),
        encoding="utf-8",
    )
    seen: dict = {"as_of": [], "window": None}

    def fake_footprints(_store, names):
        seen["as_of"].append(bs.asof.AS_OF)
        n = 1 if date(2022, 9, 15) == bs.asof.AS_OF else 4
        return {x: Footprint(x, n, 2020, 0) for x in names}

    def fake_news(labels, _lang, window=None):
        seen["window"] = window
        return [[] for _ in labels]

    monkeypatch.setattr(bs, "Store", FakeStore)
    monkeypatch.setattr(bs, "footprints", fake_footprints)
    monkeypatch.setattr(bs, "news_many", fake_news)
    monkeypatch.setattr(bs, "REPO", tmp_path)
    monkeypatch.setattr(bs.core_mod, "SLICES", None)  # состояние процесса — вернуть
    monkeypatch.setattr(sys, "argv", ["x", "--dir", "bt-2022-09-15"])
    bs.main()
    assert seen["as_of"] == [date(2022, 9, 15), date(2024, 9, 15)]
    assert seen["window"] == "after:2022-09-15 before:2024-09-15"
    assert {"Energy"} == bs.core_mod.SLICES
    rows = (folder / "backtest.tsv").read_text(encoding="utf-8").splitlines()
    assert "работ на срезе (карточка)" in rows[0]
    assert "\t1\t999\t4\t" in rows[1]  # на срезе — из корпуса, не 999 из карточки
    meta = json.loads((folder / "backtest.json").read_text(encoding="utf-8"))
    assert meta["горизонт"] == "2024-09-15" and meta["срезы openalex"] == ["Energy"]
