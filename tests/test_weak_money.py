"""Денежный поток без сети: повторы заголовков, эхо-проверки пачки, имена компаний."""

from __future__ import annotations

from pelican.weak import money
from pelican.weak.live import LiveDoc


def _doc(title: str) -> LiveDoc:
    return LiveDoc(
        "google_news",
        title,
        "https://news.google.com/x",
        "2026-07-01",
        "TechCrunch",
        "en",
        domain="techcrunch.com",
    )


def test_queries_mix_area_and_facets() -> None:
    got = money.queries("cybersecurity", ["AI agent identity", "MCP server security"])
    assert "cybersecurity startup raises" in got
    assert "AI agent identity startup" in got
    assert "MCP server security funding" in got
    # Повторов нет: один и тот же запрос из шаблона и из фасета считается один раз.
    assert len(got) == len(set(q.lower() for q in got))


def test_queries_add_submarkets_after_facets() -> None:
    """Подрынки идут теми же приставками, что фасеты, и без них список прежний."""
    base = money.queries("robotics", ["dexterous hands"])
    got = money.queries("robotics", ["dexterous hands"], ["robot insurance", "robot insurance"])
    assert got[: len(base)] == base
    assert got[len(base) :] == ["robot insurance startup", "robot insurance funding"]


def test_headlines_dedupe_across_queries(monkeypatch) -> None:
    same = _doc("Keycard raises $38M to secure AI agent identities")
    monkeypatch.setattr(
        money,
        "news_many",
        lambda queries, lang: [[same, _doc("Other startup raises $5M")], [same]],
    )
    got = money.headlines(["a", "b"])
    assert [d.title for d in got] == [same.title, "Other startup raises $5M"]


def test_named_in_keeps_only_names_written_in_the_text() -> None:
    title = "Keycard raises $38M to secure AI agent identities"
    got = money.named_in(title, ["Keycard", "ai", "Cyata", "AI agent"])
    # «ai» отсеяно регистром, «Cyata» — отсутствием в тексте, «AI agent» — словарём.
    assert got == ["Keycard"]


def test_parse_group_checks_numbers_and_names() -> None:
    chunk = [
        _doc("Keycard raises $38M to secure AI agent identities"),
        _doc("Cyata emerges from stealth with agent identity platform"),
        _doc("Glow raises $100M"),
    ]
    payload = {
        "items": [
            {
                "tech": "identity and access management for AI agents",
                "companies": ["Keycard", "Cyata", "Fabrix"],
                "headlines": [1, 2],
            },
            # Номер вне пачки — направление выдумано и не берётся.
            {"tech": "quantum key distribution", "companies": ["Glow"], "headlines": [9]},
            # Без единого номера — тоже.
            {"tech": "secure enclaves for agents", "companies": [], "headlines": []},
        ]
    }
    got = money.parse_group(payload, chunk)
    assert len(got) == 1
    assert got[0].label == "identity and access management for ai agents"
    assert len(got[0].headlines) == 2
    # «Fabrix» в заголовках не стоит — выдумка модели отброшена.
    assert got[0].companies == ("Keycard", "Cyata")


def test_term_must_stand_in_the_headlines() -> None:
    chunk = [_doc("Invariant Labs ships MCP scanner against tool poisoning")]
    got = money.parse_terms({"terms": ["MCP", "tool poisoning", "HTTP 402"]}, chunk)
    # «HTTP 402» в заголовках не стоит — это знание модели, а не прочитанное.
    assert got == ["MCP", "tool poisoning"]


def test_topic_words_are_not_terms() -> None:
    chunk = [_doc("AI security startup raises $20M for agents platform")]
    assert money.parse_terms({"terms": ["AI", "security", "platform", "agents"]}, chunk) == []


def test_follow_up_skips_already_asked_queries() -> None:
    asked = {"mcp startup"}
    assert money.follow_up(["MCP"], asked) == ["MCP funding"]
    # Тот же набор второй раз запросов уже не даёт.
    assert money.follow_up(["MCP"], asked) == []
