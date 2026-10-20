"""Три слоя выдачи (`weak.layers`) и их место в конвейере и на странице."""

from __future__ import annotations

from pelican.weak import layers
from pelican.weak.ask import AskResult, Signal, _drop_trends, _split_layers
from pelican.weak.kinds import EMERGING, MATURE
from pelican.weak.page import from_json, render, to_json


def test_big_player_in_research_is_not_a_trend() -> None:
    """«Attention is all you need» — Google Brain в 2017-м: исследование, не продажа."""
    lay = layers.classify(["Google"], {"novelty_claimed": 1.0}, works=3, patent_growth=None)
    assert lay.name != layers.TREND


def test_big_player_selling_or_named_leaders_is_a_trend() -> None:
    selling = layers.classify(["NVIDIA", "IBM"], {"commercial_deployment": 1.0}, 5, 2.0)
    assert selling.name == layers.TREND and "NVIDIA" in selling.reason
    leaders = layers.classify([], {"market_leaders_named": 1.0}, 0, None)
    assert leaders.name == layers.TREND


def test_one_giant_among_startups_with_a_round_is_not_a_trend() -> None:
    """FHE-ускорители: раунды Niobium и Zama, крупный игрок — один из многих."""
    companies = ["Niobium", "Zama", "Lattica", "DataKrypto", "Intel"]
    judgments = {"funding_mentioned": 1.0, "commercial_deployment": 1.0}
    assert layers.classify(companies, judgments, 28, None).name != layers.TREND
    assert layers.classify(["NVIDIA"], {"funding_mentioned": 1.0}, 0, None).name != layers.TREND


def test_weak_needs_two_independent_confirmations() -> None:
    assert layers.classify(["Lora Labs"], {}, works=2, patent_growth=None).name == layers.WEAK
    assert layers.classify([], {}, works=3, patent_growth=2.0, growth_floor=1.5).name == layers.WEAK
    lone = layers.classify([], {}, works=1, patent_growth=None)
    assert lone.name == layers.WATCH and "0 из нужных 2" in lone.reason
    # Патенты, растущие не быстрее пула, путь не дают: объём меряет общее ядро.
    slow = layers.classify([], {}, works=2, patent_growth=1.2, growth_floor=1.5)
    assert slow.name == layers.WATCH and "1 из нужных 2" in slow.reason


def _sig(label: str, companies=(), judgments=None, papers=0) -> Signal:
    return Signal(label=label, title=label, kind=EMERGING, kind_label="", stage="",
                  confidence=0.5, checks=[], why="", companies=list(companies),
                  judgments=judgments or {}, label_works=papers)


def test_pipeline_splits_trend_weak_and_watch(monkeypatch) -> None:
    import pelican.weak.ask as ask_mod

    seen: dict = {}
    monkeypatch.setattr(ask_mod, "attach_patents", lambda pool, *_a, **kw: seen.update(kw))
    monkeypatch.setattr(ask_mod, "TOP", 1)  # слабых хватает на ТОП — добора нет
    result = AskResult("q", "q", [], "2026-10-16")
    pool = [
        (_sig("agentic ai", ["Amazon", "NVIDIA"], {"commercial_deployment": 1.0}, 9), []),
        (_sig("memory poisoning defence", ["Lora Labs"], {}, 3), []),
        (_sig("training-free diffusion experts", [], {}, 1), []),
    ]
    kept = _drop_trends(pool, result)
    assert [s.label for s, _ in kept] == ["memory poisoning defence", "training-free diffusion experts"]
    assert result.excluded[0].kind == MATURE and result.excluded[0].kind_label == "уже тренд"
    ordered = _split_layers(kept, result, lambda _m: None, deadline=123.0)
    assert seen == {"deadline": 123.0}  # BR-33: срок патентов и в режиме слоёв
    assert [s.label for s, _ in ordered] == ["memory poisoning defence"]
    assert [s.label for s in result.watch] == ["training-free diffusion experts"]

    html = render(result)
    assert "Зарождающееся — наблюдать" in html and "training-free diffusion experts" in html
    import json

    again = from_json(json.loads(to_json(result)))
    assert [s.label for s in again.watch] == ["training-free diffusion experts"]


def test_short_top_is_filled_from_watch_with_a_mark(monkeypatch) -> None:
    import pelican.weak.ask as ask_mod

    monkeypatch.setattr(ask_mod, "attach_patents", lambda pool, *_a, **_k: None)
    monkeypatch.setattr(ask_mod, "TOP", 2)
    result = AskResult("q", "q", [], "2026-10-19")
    pool = [
        (_sig("weak one", ["Lora Labs"], {}, 3), []),
        (_sig("emerging a", [], {}, 1), []),
        (_sig("emerging b", [], {}, 0), []),
    ]
    ordered = _split_layers(pool, result, lambda _m: None)
    assert [s.label for s, _ in ordered] == ["weak one", "emerging a"]
    assert ordered[1][0].layer == layers.WATCH
    assert ordered[1][0].layer_reason.startswith("добор до пятнадцати позиций")
    assert [s.label for s in result.watch] == ["emerging b"]


def test_technique_drops_bare_categories_and_needs_a_citation(monkeypatch) -> None:
    import pelican.weak.ask as ask_mod
    from pelican.weak import technique
    from pelican.weak.assess import NEWS, PAPER, Evidence
    from pelican.weak.kinds import TOO_BROAD

    ev = [Evidence(-1, "2026-01-01", "On-chain FX between BRL1 and MXN stablecoins", NEWS),
          Evidence(-1, "2026-02-01", "Stablecoins grow", PAPER)]
    answers = {
        "local-currency stablecoin fx": {"category": "stablecoins", "specific": True, "cite": 1,
                                          "technique": "on-chain FX between non-USD stablecoins"},
        "defi": {"category": "decentralized finance", "specific": False, "cite": 0, "technique": ""},
        "uncited": {"category": "x", "specific": True, "cite": 9, "technique": "memory"},
    }

    async def fake(_system, user, _schema, name, _tokens):
        assert name == "technique"
        return answers[user.splitlines()[0].removeprefix("TECHNOLOGY: ")]

    monkeypatch.setattr(ask_mod, "_one", fake)
    result = AskResult("q", "q", [], "2026-10-16")
    kept = ask_mod._techniques([(_sig(k), ev) for k in answers], result)
    assert [s.label for s, _ in kept] == ["local-currency stablecoin fx"]
    assert kept[0][0].technique == "on-chain FX between non-USD stablecoins"
    assert {s.label for s in result.excluded} == {"defi", "uncited"}
    assert all(s.kind == TOO_BROAD for s in result.excluded)
    assert "decentralized finance" in next(s.why for s in result.excluded if s.label == "defi")
    assert technique.parse({"specific": True, "technique": "t", "cite": 1, "category": "c"}, []) == ("c", "")

    async def broken(*_a):
        raise ValueError("schema")

    monkeypatch.setattr(ask_mod, "_one", broken)
    assert len(ask_mod._techniques([(_sig("defi"), ev)], AskResult("q", "q", [], "d"))) == 1
