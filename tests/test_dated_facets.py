"""Направления на дату среза (`flags.DATED_FACETS`): живой запрос не меняется, срез — меняется."""

from __future__ import annotations

from datetime import date

from pelican.weak import ask, asof, flags
from pelican.weak.core import Footprint


def _capture(monkeypatch) -> list[str]:
    seen: list[str] = []

    async def one(system, user, schema, name, max_tokens):
        seen.append(system)
        return {"facets": ["ф"], "facets_en": ["f"], "query_en": "q", "area_en": "a"}

    monkeypatch.setattr(ask, "_one", one)
    return seen


def test_live_query_prompt_is_unchanged_even_with_flag(monkeypatch):
    seen = _capture(monkeypatch)
    monkeypatch.setattr(flags, "DATED_FACETS", True)
    monkeypatch.setattr(asof, "AS_OF", None)
    ask.expand("технологии в ИИ")
    assert seen == [ask.EXPAND_PROMPT]


def test_cut_without_flag_keeps_old_prompt(monkeypatch):
    seen = _capture(monkeypatch)
    monkeypatch.setattr(flags, "DATED_FACETS", False)
    monkeypatch.setattr(asof, "AS_OF", date(2021, 6, 1))
    ask.expand("технологии в ИИ")
    assert seen == [ask.EXPAND_PROMPT]


def test_cut_with_flag_names_the_date(monkeypatch):
    seen = _capture(monkeypatch)
    monkeypatch.setattr(flags, "DATED_FACETS", True)
    monkeypatch.setattr(asof, "AS_OF", date(2021, 6, 1))
    ask.expand("технологии в ИИ")
    assert seen[0].startswith(ask.EXPAND_PROMPT) and "TODAY IS 2021-06-01" in seen[0]


def test_facet_without_works_on_the_date_is_replaced_by_burst(monkeypatch):
    prints = {"diffusion models": Footprint("diffusion models", 243, 2015, 71),
              "long-context multimodal llms": Footprint("long-context multimodal llms", 0, None, 0)}
    monkeypatch.setattr(ask, "footprints", lambda store, names, loose=False: {
        n: prints[n] for n in names if n in prints})
    got = ask.dated_facets(None, ["long-context multimodal llms", "diffusion models"],
                           ["graph convolutional", "diffusion models", "point cloud"], n=3)
    assert got == ["diffusion models", "graph convolutional", "point cloud"]
