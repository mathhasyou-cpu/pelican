"""Денежный след по окну (`weak.rounds`): суммы из заголовков, дедуп, окно, снятие среза."""

from __future__ import annotations

from datetime import date

import pytest

from pelican.weak import asof, rounds
from pelican.weak.live import LiveDoc


@pytest.mark.parametrize(
    ("title", "usd"),
    [
        ("Positron AI Secures $51.6 Million in Oversubscribed Series A", 51.6e6),
        ("Clockwork raises $20.5M to synchronize GPU clusters", 20.5e6),
        ("Startup lands $1.2bn valuation after Series C", 1.2e9),
        ("Seed round of $500K closed", 500e3),
        ("Company raises €30 million", None),
        ("AI chip firm raises funding, amount undisclosed", None),
    ],
)
def test_parse_usd(title: str, usd: float | None) -> None:
    got = rounds.parse_usd(title)
    assert got == pytest.approx(usd) if usd is not None else got is None


def _doc(title: str, url: str, published: str, domain: str = "x.com") -> LiveDoc:
    return LiveDoc("google_news", title, url, published, "", "en", domain=domain)


def test_summarize_counts_funding_headlines_once() -> None:
    docs = [
        _doc("Acme raises $10M Series A", "u1", "2025-01-10", "a.com"),
        _doc("Acme raises $10M Series A", "u1", "2025-01-10", "a.com"),  # тот же URL
        _doc("ACME RAISES $10M SERIES A", "u2", "2025-01-11", "b.com"),  # тот же заголовок
        _doc("Beta secures $5 million seed funding", "u3", "2025-03-01", "b.com"),
        _doc("Gamma launches new product", "u4", "2025-04-01", "c.com"),  # не про деньги
    ]
    t = rounds.summarize("acme tech", docs)
    assert (t.n, t.usd, t.publishers, t.last_date) == (2, 15e6, 2, "2025-03-01")
    assert not t.empty


def test_window_is_half_open_operators() -> None:
    assert rounds.window(date(2024, 9, 15), date(2026, 9, 15)) == (
        "after:2024-09-15 before:2026-09-15"
    )


def test_trace_queries_templates_per_label_and_lifts_asof(monkeypatch) -> None:
    seen: dict = {}

    def fake_news_many(queries, lang="en", *, window=None, limit=10):
        seen.update(queries=queries, window=window, limit=limit, asof=asof.AS_OF)
        return [[_doc(f"X{i} raises $1M", f"u{i}", "2025-01-01")] for i, _q in enumerate(queries)]

    monkeypatch.setattr(rounds, "news_many", fake_news_many)
    monkeypatch.setattr(asof, "AS_OF", date(2024, 9, 15))
    got = rounds.trace(["a", "b"], date(2024, 9, 15), date(2026, 9, 15))
    assert seen["asof"] is None  # срез снят на время запроса...
    assert asof.AS_OF == date(2024, 9, 15)  # ...и возвращён
    assert seen["limit"] == rounds.LIMIT
    assert seen["window"] == "after:2024-09-15 before:2026-09-15"
    assert len(seen["queries"]) == 2 * len(rounds.TEMPLATES)
    assert [t.label for t in got] == ["a", "b"]
    assert got[0].n == len(rounds.TEMPLATES) and got[0].usd == len(rounds.TEMPLATES) * 1e6


def test_features_take_two_year_windows_and_skip_prev_on_request(monkeypatch) -> None:
    """Признаки `$` живого `ask` — те же окна, что у бэктеста: год до даты и год до того;
    без `with_prev` второе окно не спрашивается, а его признаки — `None`, не ноль."""
    windows: list[str] = []

    def fake_news_many(queries, lang="en", *, window=None, limit=10):
        windows.append(window)
        return [[_doc(f"Acme{i} raises $2M", f"u{i}{window}", "2025-06-01")] for i, _q in enumerate(queries)]  # noqa: E501

    monkeypatch.setattr(rounds, "news_many", fake_news_many)
    cut = date(2026, 9, 21)
    got = rounds.features(["a"], cut)
    assert windows == [
        "after:2025-09-21 before:2026-09-21",
        "after:2024-09-21 before:2025-09-21",
    ]
    assert set(got[0]) == set(rounds.MONEY_FEATURES)
    assert got[0]["money_momentum"] == 1.0 and got[0]["money_recency_days"] == 477.0
    windows.clear()
    got = rounds.features(["a"], cut, with_prev=False)
    assert windows == ["after:2025-09-21 before:2026-09-21"]
    assert got[0]["money_prev_n"] is None and got[0]["money_momentum"] is None
    assert got[0]["money_last_n"] == len(rounds.TEMPLATES)
