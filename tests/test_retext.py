"""Дописывание причин и поиск латинских названий у сохранённых отчётов (без базы и модели)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "retext.py"
spec = importlib.util.spec_from_file_location("retext", SCRIPT)
rt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rt)


def test_fill_why_only_empty_reasons_from_judgments():
    result = {"excluded": [
        {"label": "a", "title": "а", "kind": "mature", "why": "", "judgments": {"routine_tool": 1.0}},
        {"label": "b", "title": "б", "kind": "noise", "why": "Свой текст [1]."},
    ]}
    assert rt.fill_why(result) == 1
    assert result["excluded"][0]["why"].startswith("По признакам оценки: упоминается как рутинный инструмент.")
    assert result["excluded"][1]["why"] == "Свой текст [1]."


def test_latin_titles_across_sections():
    result = {"signals": [{"label": "x", "title": "Latent Space Probing"}, {"label": "y", "title": "Зонд"}],
              "excluded": [{"label": "z", "title": "z"}], "thin": [], "watch": None}
    assert [x["label"] for x in rt.latin_titles(result)] == ["x", "z"]


def test_fix_refs_drops_numbers_beyond_sources():
    result = {"signals": [{"label": "x", "sources": [{}, {}], "description": "Раунд [2, 25]. Пилот [1].",
                           "facts": [{"date": "2026-01", "text": "Сделка [3]."}], "why": "Есть [1]."}]}
    assert rt.fix_refs(result) == 2
    s = result["signals"][0]
    assert s["description"] == "Раунд [2]. Пилот [1]." and s["facts"][0]["text"] == "Сделка."
    assert s["why"] == "Есть [1]."
