"""Ретро-тест «Attention в 2018»: разбор набора и место технологии в отчёте (без базы)."""

from __future__ import annotations

import importlib.util
import sys
from datetime import date
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "retro_hits.py"
spec = importlib.util.spec_from_file_location("retro_hits", SCRIPT)
retro = importlib.util.module_from_spec(spec)
# ⚠️ dataclass со слотами ищет свой модуль в sys.modules.
sys.modules["retro_hits"] = retro
spec.loader.exec_module(retro)


def test_dataset_parses_and_every_cut_is_before_today() -> None:
    hits = retro.load()
    assert len(hits) >= 20
    assert all(h.aliases and h.query and h.cut < date(2026, 1, 1) for h in hits)
    attention = next(h for h in hits if "self-attention" in h.aliases)
    assert attention.cut.year == 2017


def test_alias_matches_whole_words_only() -> None:
    assert retro.matches({"label": "Vision Transformer for remote sensing"}, ["vit", "vision transformer"])
    assert not retro.matches({"label": "human activity recognition"}, ["vit"])
    assert retro.matches({"label": "x", "variants": ["sparse mixture-of-experts routing"]},
                         ["mixture-of-experts"])


def test_locate_takes_the_best_place() -> None:
    report = {
        "signals": [{"label": "graph kernels"}, {"label": "self-attention for translation"}],
        "watch": [],
        "thin": [{"label": "transformer pruning"}],
        "excluded": [{"label": "attention LSTM", "kind_label": "зрелая"}],
    }
    place = retro.locate(report, ["self-attention", "transformer"])
    assert (place.where, place.rank, place.pool) == ("top", 2, 4)
    assert retro.locate(report, ["attention lstm"]).detail == "зрелая"
    assert retro.locate(report, ["crispr"]).where == "missing"


def test_table_reports_random_floor() -> None:
    hit = retro.load()[0]
    md = retro.table([(hit, retro.Place("top", 3, "transformer", pool=30), 12),
                      (hit, retro.Place("missing", pool=30), 0)])
    assert "в ТОП-15**: 1 из 2" in md
    assert "ожидалось бы 0.5 из 1" in md
