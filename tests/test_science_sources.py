"""Парсинг научных источников — на живых ответах, без сети.

`docs/sources-science.md`. Проверяется главное, чем эти двое отличаются от
остальных: датой берётся ФАКТ подачи/публикации, а не момент сбора, и ни у
одного не появляется `value` — величины «на сейчас» в ряд по датам не идут.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
from tenacity import wait_none

from tests.conftest import fixture
from pelican.sources import build_backfill
from pelican.sources.arxiv import (
    MAX_WINDOW_DAYS,
    ArXiv,
    ArXivThrottled,
    parse_entries,
    total_results,
    window_days,
)
from pelican.sources.base import Chunk
from pelican.sources.openalex import (
    BACKFILL_RETRY,
    DAILY_RETRY,
    MAX_PAGES_PER_WINDOW,
    PER_PAGE,
    SPLIT_AT,
    WINDOW_DAYS,
    OpenAlex,
    Tally,
    abstract_of,
    forecast,
    parse_works,
)

# ------------------------------------------------------------------- arxiv


def test_parse_entries_extracts_signals() -> None:
    signals = parse_entries(fixture("arxiv_query.xml"))

    assert signals
    for s in signals:
        assert s.source == "arxiv"
        assert s.metric == "submissions"
        # ⚠️ Ноль означал бы «измерили и вышло ничего»; веса у препринта нет.
        assert s.value is None
        assert s.observed_at.tzinfo is not None
        assert s.url == f"https://arxiv.org/abs/{s.external_id}"
        assert s.term_raw
        assert set(s.payload) == {
            "summary",
            "primary_category",
            "categories",
            "authors",
            "updated",
            "comment",
            "journal_ref",
            "doi",
            "affiliations",
        }


def test_external_id_keeps_the_version() -> None:
    """v1 и v2 — разные подачи с разными датами, и схлопывать их нельзя."""
    ids = [s.external_id for s in parse_entries(fixture("arxiv_query.xml"))]

    assert all("/" not in i for i in ids)
    assert any(i[-2:].startswith("v") for i in ids)


def test_old_style_ids_keep_their_archive() -> None:
    """⚠️ До апреля 2007 идентификатор — `архив/номер`, и архив обязан уцелеть.

    Без него `cs/0607001` и `cond-mat/0607001` сходятся в один ключ, второй
    молча теряется на `ON CONFLICT DO NOTHING`, а ссылка перестаёт открываться.
    """
    xml = """<?xml version='1.0' encoding='UTF-8'?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry><id>http://arxiv.org/abs/cs/0607001v1</id><title>old cs</title>
        <summary>b</summary><published>2006-07-01T00:00:00Z</published></entry>
      <entry><id>http://arxiv.org/abs/cond-mat/0607001v1</id><title>old cond-mat</title>
        <summary>b</summary><published>2006-07-01T00:00:00Z</published></entry>
      <entry><id>http://arxiv.org/abs/2609.06873v1</id><title>new</title>
        <summary>b</summary><published>2026-09-06T00:00:00Z</published></entry>
    </feed>"""

    signals = parse_entries(xml)

    assert [s.external_id for s in signals] == [
        "cs/0607001v1",
        "cond-mat/0607001v1",
        "2609.06873v1",
    ]
    # Ключ различает их, значит вторая работа не пропадёт на вставке.
    assert len({s.external_id for s in signals}) == 3
    assert signals[0].url == "https://arxiv.org/abs/cs/0607001v1"


def test_date_comes_from_published_not_updated() -> None:
    """`updated` переписывается новой версией — ряд по нему мерил бы правки."""
    signal = parse_entries(fixture("arxiv_query.xml"))[0]

    assert signal.payload["updated"]
    assert signal.observed_at <= datetime.now(UTC)


def test_parse_entries_skips_incomplete_records() -> None:
    xml = """<?xml version='1.0' encoding='UTF-8'?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry><id>http://arxiv.org/abs/2609.1v1</id><title>ok</title>
        <summary>body</summary><published>2026-09-06T23:31:55Z</published></entry>
      <entry><id>http://arxiv.org/abs/2609.2v1</id><published>2026-09-06T23:31:55Z</published></entry>
      <entry><title>no id</title><published>2026-09-06T23:31:55Z</published></entry>
      <entry><id>http://arxiv.org/abs/2609.4v1</id><title>no date</title></entry>
    </feed>"""

    assert [s.external_id for s in parse_entries(xml)] == ["2609.1v1"]


def test_total_results_is_readable() -> None:
    """По нему видно, обрезано ли окно потолком среза, а не только пусто ли оно."""
    assert total_results(fixture("arxiv_query.xml")) is not None


def test_query_head_wraps_categories_in_parentheses() -> None:
    """⚠️ Без скобок окно по дате связалось бы только с последней категорией."""
    head = ArXiv(categories=["cs.*", "eess.*"]).query_head

    assert head == "(cat:cs.* OR cat:eess.*)"


# ---------------------------------------------------------------- openalex


def test_abstract_of_restores_word_order() -> None:
    assert abstract_of({"solid": [1], "a": [0], "state": [2]}) == "a solid state"
    assert abstract_of({"the": [0, 2], "cat": [1]}) == "the cat the"
    assert abstract_of(None) == ""
    assert abstract_of({}) == ""


def test_parse_works_extracts_signals() -> None:
    signals = parse_works(json.loads(fixture("openalex_works.json")))

    assert signals
    for s in signals:
        assert s.source == "openalex"
        assert s.metric == "works"
        # ⚠️ Главное правило источника: цитирования — величина «на сейчас».
        assert s.value is None
        assert s.payload["cited_by_count"] is not None
        assert s.observed_at.tzinfo is not None
        assert s.observed_at.hour == 0  # дата публикации — сутки, не момент
        assert s.url.startswith("https://openalex.org/")
        assert s.term_raw


def test_parse_works_puts_abstract_where_the_card_looks_for_it() -> None:
    """`description` входит в `Store.BODY_KEYS` — иначе карточка покажет обрубок."""
    signals = parse_works(json.loads(fixture("openalex_works.json")))

    assert any(s.payload["description"] for s in signals)


def test_parse_works_skips_incomplete_records() -> None:
    payload = {
        "results": [
            {"id": "https://openalex.org/W1", "title": "ok", "publication_date": "2026-09-01"},
            {"id": "https://openalex.org/W2", "publication_date": "2026-09-01"},
            {"title": "no id", "publication_date": "2026-09-01"},
            {"id": "https://openalex.org/W4", "title": "no date"},
        ]
    }

    assert [s.external_id for s in parse_works(payload)] == ["W1"]


def test_openalex_params_carry_the_window_and_the_scope() -> None:
    source = OpenAlex(filters=["primary_topic.field.id:fields/25"])
    params = source._params(
        "primary_topic.field.id:fields/25", date(2026, 9, 1), date(2026, 9, 30), "*"
    )

    assert params["filter"] == (
        "from_publication_date:2026-09-01,to_publication_date:2026-09-30,"
        "primary_topic.field.id:fields/25"
    )
    assert params["cursor"] == "*"


def test_openalex_splits_a_window_that_does_not_fit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Окно, не влезшее в ёмкость, делится пополам — как у arxiv."""
    capacity = PER_PAGE * MAX_PAGES_PER_WINDOW
    asked: list[tuple[date, date]] = []

    async def page(self, http, scope, lo, hi, cursor):  # noqa: ANN001, ANN202
        asked.append((lo, hi))
        # Не влезает только целиком; любая половина влезает и отдаёт пустоту.
        count = capacity + 1 if (hi - lo).days > 15 else 0
        return {"meta": {"count": count, "next_cursor": None}, "results": []}

    monkeypatch.setattr(OpenAlex, "_fetch_page", page)
    asyncio.run(
        OpenAlex()._fetch_window(
            None, "primary_topic.field.id:fields/25", date(2026, 1, 1), date(2026, 1, 31)
        )
    )

    halves = asked[1:]
    assert len(halves) == 2, "окно обязано делиться, а не обрезаться"
    # Половины идут встык и покрывают исходное окно целиком.
    assert halves[0][0] == date(2026, 1, 1)
    assert halves[1][1] == date(2026, 1, 31)
    assert (halves[1][0] - halves[0][1]).days == 1


def test_openalex_refuses_to_truncate_a_day(monkeypatch: pytest.MonkeyPatch) -> None:
    """⚠️ Окно, которому не хватило бюджета страниц, обязано падать, а не обрезаться.

    Молчаливая обрезка уже стоила пяти января подряд: 1 января у OpenAlex —
    работы без точной даты, и год читался бы как спад, а не как поломка сбора.
    Улика обрезки — живой курсор при кончившемся бюджете страниц.
    """
    work = {"id": "https://openalex.org/W1", "title": "t", "publication_date": "2026-01-01"}

    async def page(self, http, scope, lo, hi, cursor):  # noqa: ANN001, ANN202
        return {"meta": {"count": 10**9, "next_cursor": "c1"}, "results": [work]}

    monkeypatch.setattr(OpenAlex, "_fetch_page", page)

    with pytest.raises(RuntimeError, match="обрезано"):
        asyncio.run(
            OpenAlex()._fetch_window(
                None, "primary_topic.field.id:fields/25", date(2026, 1, 1), date(2026, 1, 1)
            )
        )


def test_openalex_takes_a_dense_day_whole(monkeypatch: pytest.MonkeyPatch) -> None:
    """⚠️ Плотные сутки берутся одним куском, а не роняют прогон.

    Замер: 1 января у `fields/22` — 323 737 работ, у `fields/17` — 161 556, то
    есть вчетверо выше порога деления. Делить сутки нечем, и отказ здесь означал
    бы, что бэкфилл этих срезов не доходит до первого же января.
    """
    asked: list[str] = []

    async def page(self, http, scope, lo, hi, cursor):  # noqa: ANN001, ANN202
        asked.append(cursor)
        work = {"id": "https://openalex.org/W1", "title": "t", "publication_date": "2026-01-01"}
        return {"meta": {"count": SPLIT_AT * 4, "next_cursor": None}, "results": [work]}

    monkeypatch.setattr(OpenAlex, "_fetch_page", page)
    out = asyncio.run(
        OpenAlex()._fetch_window(
            None, "primary_topic.field.id:fields/22", date(2026, 1, 1), date(2026, 1, 1)
        )
    )

    assert len(out) == 1
    assert asked == ["*"], "сутки делиться не должны — второго захода нет"


def test_openalex_follows_the_cursor_past_the_old_ceiling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Курсор глубину не ограничивает — потолок здесь только предохранитель."""
    total = 65  # больше прежних 60 страниц и заведомо меньше нынешней ёмкости

    async def page(self, http, scope, lo, hi, cursor):  # noqa: ANN001, ANN202
        n = 0 if cursor == "*" else int(cursor)
        works = [
            {
                "id": f"https://openalex.org/W{n}",
                "title": f"работа {n}",
                "publication_date": "2026-01-01",
            }
        ]
        return {
            "meta": {"count": total, "next_cursor": str(n + 1) if n + 1 < total else None},
            "results": works,
        }

    monkeypatch.setattr(OpenAlex, "_fetch_page", page)
    signals = asyncio.run(
        OpenAlex()._fetch_window(
            None, "primary_topic.field.id:fields/25", date(2026, 1, 1), date(2026, 1, 1)
        )
    )

    assert len(signals) == total


# ------------------------------------------------- срезы истории (--skip-days)


def test_arxiv_windows_cover_the_whole_lookback() -> None:
    now = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)
    windows = ArXiv(lookback=timedelta(days=30)).windows(now)

    assert windows[0][1] == now  # свежий край — сейчас
    assert windows[-1][0] == now - timedelta(days=30)  # старый край — граница


def test_arxiv_windows_are_contiguous() -> None:
    """Дыра между окнами — это молча потерянный кусок истории."""
    windows = ArXiv(lookback=timedelta(days=400)).windows(datetime(2026, 9, 10, tzinfo=UTC))

    for newer, older in zip(windows, windows[1:], strict=False):
        assert newer[0] == older[1]


def test_window_width_follows_measured_density() -> None:
    """Цена бэкфилла у arXiv — в запросах, и на редких годах окно обязано расти.

    Иначе 24 года это 8 766 запросов по три секунды, большей частью за днями,
    где работ десяток.
    """
    assert window_days(datetime(2026, 6, 1, tzinfo=UTC)) == 3
    assert window_days(datetime(2015, 6, 1, tzinfo=UTC)) == 24
    assert window_days(datetime(2001, 6, 1, tzinfo=UTC)) == MAX_WINDOW_DAYS

    # Ширина не убывает вглубь: плотность arXiv монотонно падала. ⚠️ Последнее
    # окно из проверки исключено — оно обрезано границей глубины и потому уже
    # своего года (115 суток против 64 на границе 2002 года).
    now = datetime(2026, 9, 10, tzinfo=UTC)
    widths = [
        (end - start).days for start, end in ArXiv(lookback=timedelta(days=8766)).windows(now)
    ][:-1]
    assert widths == sorted(widths)


def test_deep_backfill_costs_hundreds_of_requests_not_thousands() -> None:
    """Прямая проверка того, ради чего окно стало переменным."""
    now = datetime(2026, 9, 10, tzinfo=UTC)

    assert len(ArXiv(lookback=timedelta(days=8766)).windows(now)) < 1000


def test_arxiv_skip_moves_the_fresh_edge_back() -> None:
    now = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)
    windows = ArXiv(lookback=timedelta(days=30), skip=timedelta(days=10)).windows(now)

    assert windows[0][1] == now - timedelta(days=10)
    assert windows[-1][0] == now - timedelta(days=30)


def test_arxiv_slices_join_without_gap_or_overlap() -> None:
    """⚠️ Ради этого `skip` и заведён: срез встык к предыдущему.

    `-d 4` и следом `-d 9 --skip-days 4` обязаны покрыть ровно то же, что `-d 9`
    одним куском, — иначе перезапуск после падения либо теряет дни, либо
    перекачивает их.
    """
    now = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)
    first = ArXiv(lookback=timedelta(days=40)).windows(now)
    second = ArXiv(lookback=timedelta(days=900), skip=timedelta(days=40)).windows(now)
    whole = ArXiv(lookback=timedelta(days=900)).windows(now)

    # ⚠️ Стык не обязан совпасть с границей окна — ширина зависит от даты,
    # поэтому крайние окна срезов обрезаются по границе. Сходиться обязано
    # ПОКРЫТИЕ: те же сутки, ни одних дважды, ни одних пропущенных.
    assert first[-1][0] == second[0][1]
    assert first[0][1] == whole[0][1]
    assert second[-1][0] == whole[-1][0]
    assert sum((e - s).total_seconds() for s, e in first + second) == pytest.approx(
        sum((e - s).total_seconds() for s, e in whole)
    )


def _covered(windows: list[tuple[date, date]]) -> set[date]:
    return {lo + timedelta(days=n) for lo, hi in windows for n in range((hi - lo).days + 1)}


def test_openalex_windows_and_slices() -> None:
    today = date(2026, 9, 10)

    # ⚠️ Сегодняшний день входит: `publication_date` ставит издатель.
    assert OpenAlex(lookback=timedelta(days=3)).windows(today)[0][1] == today

    first = OpenAlex(lookback=timedelta(days=40)).windows(today)
    second = OpenAlex(lookback=timedelta(days=90), skip=timedelta(days=40)).windows(today)
    whole = OpenAlex(lookback=timedelta(days=90)).windows(today)

    # Срез встык покрывает ровно то же, что целый прогон, и ни одних суток дважды.
    assert _covered(first) | _covered(second) == _covered(whole)
    assert not _covered(first) & _covered(second)
    assert len(_covered(whole)) == 90


def test_openalex_windows_are_contiguous_and_wide() -> None:
    """⚠️ Дыра между окнами — молча потерянный кусок истории; узкое окно —
    сожжённый бюджет (каждое окно стоит минимум двух запросов)."""
    windows = OpenAlex(lookback=timedelta(days=365)).windows(date(2026, 9, 10))

    for newer, older in zip(windows, windows[1:], strict=False):
        assert (newer[0] - older[1]).days == 1
    assert all((hi - lo).days + 1 <= WINDOW_DAYS for lo, hi in windows)
    assert len(windows) <= 365 // WINDOW_DAYS + 1


def test_skip_is_refused_where_it_would_lie() -> None:
    """Смещение не меньше самого окна дало бы пустой срез, который выглядит собранным."""
    with pytest.raises(KeyError, match="unknown source"):
        build_backfill("hackernews", days=100, skip_days=10)

    with pytest.raises(KeyError, match="меньше"):
        build_backfill("arxiv", days=100, skip_days=100)


def test_arxiv_does_not_knock_again_after_a_refusal() -> None:
    """⚠️ 429 у arXiv НЕ ретраится: отказ держится минутами, а то и сутками.

    Ретраи перекрыть его не могут (до 10 с по построению) и только превращают
    один стук в три — а стук по закрытой двери у лимитера со скользящим окном
    продлевает само окно. Проверяется именно ЧИСЛО запросов.
    """

    class Refusing:
        def __init__(self) -> None:
            self.calls = 0

        async def get(self, url, params=None):  # noqa: ANN001, ANN202
            self.calls += 1
            return httpx.Response(
                429,
                text="Rate exceeded.",
                headers={"retry-after": "600"},
                request=httpx.Request("GET", url),
            )

    http = Refusing()
    with pytest.raises(ArXivThrottled) as exc:
        asyncio.run(ArXiv(delay_s=0)._fetch_page(http, "cat:cs.AI", 0))

    assert http.calls == 1, "отказ по частоте не повторяют"
    assert "600" in str(exc.value), "в сообщении обязана быть просьба хоста подождать"


def test_openalex_walks_windows_outside_and_scopes_together(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """⚠️ Окно снаружи, срезы внутри и одновременно.

    Порядок решает, что останется от прерванного бэкфилла: со срезом снаружи
    остановка оставляет одно поле полным, а второе пустым. С окном снаружи —
    ровный временной срез по всем полям сразу.
    """
    seen: list[tuple[str, date]] = []
    live = 0
    together = 0

    async def window(self, http, scope, lo, hi):  # noqa: ANN001, ANN202
        nonlocal live, together
        live += 1
        together = max(together, live)
        seen.append((scope, hi))
        await asyncio.sleep(0)
        live -= 1
        return []

    monkeypatch.setattr(OpenAlex, "_fetch_window", window)

    async def drain_stream() -> list[Chunk]:
        source = OpenAlex(lookback=timedelta(days=60), filters=["a", "b"])
        return [chunk async for chunk in source.fetch_stream()]

    chunks = asyncio.run(drain_stream())

    assert together == 2, "срезы одного окна обязаны идти параллельно"
    assert len(chunks) == len(seen), "кусок на каждую пару (окно, срез)"
    # Оба среза закрываются на первом окне и только потом начинается второе.
    assert [s for s, _ in seen[:2]] == ["a", "b"]
    assert seen[0][1] == seen[1][1] != seen[2][1]


# ------------------------------------------------- бэкфилл: сеть и прогресс


class _FlakyNet:
    """Сеть, которая `fails` раз не резолвит хост, а потом отвечает."""

    def __init__(self, fails: int) -> None:
        self.fails = fails
        self.calls = 0

    async def get(self, url, params=None):  # noqa: ANN001, ANN202
        self.calls += 1
        if self.calls <= self.fails:
            raise httpx.ConnectError("[Errno 11001] getaddrinfo failed")
        return httpx.Response(
            200,
            json={"meta": {"count": 0, "next_cursor": None}, "results": []},
            headers={"x-ratelimit-remaining": "2662", "x-ratelimit-limit": "10000"},
            request=httpx.Request("GET", url),
        )


def _page(source: OpenAlex, http: _FlakyNet) -> dict:
    return asyncio.run(source._fetch_page(http, "s", date(2026, 1, 1), date(2026, 1, 31), "*"))


def test_backfill_waits_out_a_network_outage() -> None:
    """⚠️ Отказ сети посреди окна уносил всё окно (~1000 оплаченных запросов).

    Бэкфилл обязан переждать его, а не упасть на третьей попытке, — и в счёт
    бюджета идут только ответы: запрос, не дошедший до хоста, не оплачен.
    """
    source = OpenAlex(delay_s=0, retry={**BACKFILL_RETRY, "wait": wait_none()})
    http = _FlakyNet(fails=8)

    _page(source, http)

    assert http.calls == 9
    assert source.tally.requests == 1
    assert source.tally.budget_left == 2662


def test_daily_run_does_not_wait_for_one_source() -> None:
    """Суточный прогон терпение бэкфилла не наследует: он ждёт не одного источника."""
    source = OpenAlex(delay_s=0, retry={**DAILY_RETRY, "wait": wait_none()})
    http = _FlakyNet(fails=8)

    with pytest.raises(httpx.ConnectError):
        _page(source, http)
    assert http.calls == 3


def test_backfill_gets_the_patient_retry() -> None:
    assert build_backfill("openalex", days=100).retry is BACKFILL_RETRY
    assert OpenAlex().retry is DAILY_RETRY


def test_forecast_uses_the_rate_of_this_run() -> None:
    today = date(2026, 9, 14)
    windows = OpenAlex(lookback=timedelta(days=310)).windows(today)
    t = Tally(windows=windows, done=2, planned=1000, works=200, requests=4)
    t.budget_left, t.budget_limit = 10, 100

    f = forecast(t, now=t.started + 20)

    assert f is not None
    assert f.works_left == 800
    assert f.requests_left == 16  # 50 работ на запрос — замер прогона, а не PER_PAGE
    assert f.budgets_left == pytest.approx(0.16)
    assert f.seconds_left == pytest.approx(80)
    # Остаток бюджета — 500 работ, по 100 на окно: ещё пять окон после двух закрытых.
    assert f.works_today == 500
    assert f.today_reaches == windows[6][0]


def test_forecast_waits_for_the_first_window() -> None:
    """До первого закрытого окна скорость не на чем мерить: там только план."""
    assert forecast(Tally(planned=1000, requests=2), now=0) is None


def test_openalex_reports_plan_and_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[tuple[str, int]] = []

    async def count(self, http, scope, lo, hi):  # noqa: ANN001, ANN202
        return 7

    async def window(self, http, scope, lo, hi):  # noqa: ANN001, ANN202
        return []

    monkeypatch.setattr(OpenAlex, "_count", count)
    monkeypatch.setattr(OpenAlex, "_fetch_window", window)

    async def drain_stream() -> None:
        source = OpenAlex(lookback=timedelta(days=60), filters=["a", "b"])
        source.on_progress = lambda event, t, note: events.append((event, t.done))
        async for _ in source.fetch_stream():
            pass
        assert source.tally.planned == 14, "план — по всем срезам сразу"

    asyncio.run(drain_stream())

    assert events == [("plan", 0), ("window", 1), ("window", 2)]
