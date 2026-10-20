"""Живой поиск: «не ответил» и «пусто» — разные исходы (`live.news_many_measured`)."""

from __future__ import annotations

import httpx

from pelican.weak import live


def _doc(title: str) -> live.LiveDoc:
    return live.LiveDoc("google_news", title, "https://x", "2026-01-01", "P", "en")


def test_failed_query_is_retried_then_marked_unmeasured(monkeypatch) -> None:
    calls: dict[str, int] = {}

    async def fake(_http, query, _lang, _window=None, _limit=live.PER_SOURCE):
        calls[query] = calls.get(query, 0) + 1
        if query == "flaky" and calls[query] == 1:
            raise httpx.ConnectError("boom")
        if query == "down":
            raise httpx.ReadTimeout("still down")
        return [] if query == "quiet" else [_doc(query)]

    monkeypatch.setattr(live, "_google_news", fake)
    monkeypatch.setattr(live, "RETRY_PAUSE_S", 0.0)

    got = live.news_many_measured(["ok", "flaky", "quiet", "down"])

    assert [d.title for d in got[0]] == ["ok"]
    assert [d.title for d in got[1]] == ["flaky"]  # сбой снят повтором
    assert got[2] == []  # источник ответил: новостей нет — это измерение
    assert got[3] is None  # не ответил и после повтора — «не проверено»
    assert calls == {"ok": 1, "flaky": 2, "quiet": 1, "down": 2}
    # Старый интерфейс для денег и раундов: отказ — пустой список.
    assert live.news_many(["down"]) == [[]]


def test_onpoint_drops_companies_from_headlines_about_something_else(monkeypatch) -> None:
    """«Adaptive adapter routing»: ASUS из заметки про роутеры — не игрок технологии."""
    import pelican.weak.ask as ask_mod
    from pelican.weak import onpoint

    on = _doc("Startup Lora Labs ships adaptive LoRA adapter routing for LLMs")
    off = _doc("ASUS unveils a new Wi-Fi 7 router with adaptive routing")
    named = {"Lora Labs": on, "ASUS": off}

    async def fake(_system, user, _schema, name, _tokens):
        assert name == "onpoint" and "[1]" in user and "[2]" in user
        return {"about": [1, 7]}  # чужой номер отбрасывается

    monkeypatch.setattr(ask_mod, "_one", fake)
    assert ask_mod._onpoint("adaptive adapter routing", "AI", named) == {"Lora Labs": on}

    async def broken(*_a):
        raise ValueError("schema")

    monkeypatch.setattr(ask_mod, "_one", broken)
    assert ask_mod._onpoint("adaptive adapter routing", "AI", named) == named  # сбой ничего не снимает
    assert onpoint.parse({"about": []}, 2) == set()


def test_html_200_is_unmeasured_and_not_cached(monkeypatch) -> None:
    """Страница согласия кодом 200 — «не ответил», а не «новостей нет» на сутки (PEL-H-01)."""
    import asyncio

    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url)
        return httpx.Response(200, text="<html><body>Before you continue to Google</body></html>")

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            for _ in range(2):
                live._block_file().unlink(missing_ok=True)  # предохранитель — свой тест
                try:
                    await live._google_news(http, "ai agent identity startup", "en")
                except live.NotRSS:
                    pass
                else:
                    raise AssertionError("HTML прочитан как лента")

    asyncio.run(go())
    assert len(calls) == 2  # отказ в кэш не лёг: второй вызов снова пошёл в сеть
    rss = '<?xml version="1.0"?><rss><channel></channel></rss>'
    assert live._rss_text(httpx.Response(200, text=rss)) == rss


def test_rate_limit_trips_breaker_for_every_next_query() -> None:
    """Первый 503 «Sorry…» размыкает предохранитель: следующие запросы в сеть не идут."""
    import asyncio

    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url)
        return httpx.Response(503, text="<html><title>Sorry...</title></html>")

    async def go() -> list:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            out = []
            for q in ("a", "b", "c"):
                try:
                    await live._fetch_google_news(http, q, "en", "")
                except live.GoogleBlocked as exc:
                    out.append(exc)
            return out

    assert len(asyncio.run(go())) == 3
    assert len(calls) == 1
    assert live._blocked_until() > 0


def test_unanswered_money_trace_is_an_error_not_zero(monkeypatch) -> None:
    """Отказ Google в денежном следе — `MoneyUnchecked`, а не «денег нет»."""
    from pelican.weak import rounds

    monkeypatch.setattr(rounds, "news_many_measured", lambda q, *a, **k: [None for _ in q])
    import datetime as dt

    try:
        rounds.trace(["robot insurance"], dt.date(2020, 1, 1), dt.date(2021, 1, 1))
    except rounds.MoneyUnchecked:
        return
    raise AssertionError("отказ прочитан как нулевой денежный след")
