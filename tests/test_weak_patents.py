"""Патентный источник: разбор biblio, ноль против «нечем измерить», срез времени.

Числа фикстуры — из живого ответа OPS 2026-09-17, а не придуманы: ровно та форма,
которая приезжает на самом деле (значение в `$`, одиночная запись без списка,
заявитель в двух написаниях, реферат списком языков).
"""

from __future__ import annotations

import asyncio
from datetime import date

import pytest

from pelican.weak import asof, patents
from pelican.weak.trust import HIGH, PATENT

_EN_ABSTRACT = {"@lang": "en", "p": {"$": "A device."}}


def _payload(docs: list | dict, total: str = "95") -> dict:
    return {
        "ops:world-patent-data": {
            "ops:biblio-search": {
                "@total-result-count": total,
                "ops:search-result": {"exchange-documents": docs},
            }
        }
    }


def _doc(abstract: object = None) -> dict:
    return {
        "exchange-document": {
            "@country": "US",
            "@doc-number": "20260268235",
            "@kind": "A1",
            "bibliographic-data": {
                "publication-reference": {
                    "document-id": [
                        {
                            "@document-id-type": "docdb",
                            "country": {"$": "US"},
                            "doc-number": {"$": "20260268235"},
                            "kind": {"$": "A1"},
                            "date": {"$": "20260910"},
                        },
                        {"@document-id-type": "epodoc", "doc-number": {"$": "US20260268235"}},
                    ]
                },
                "invention-title": {"@lang": "en", "$": "ELECTROPHORETIC OSCILLATORS"},
                "parties": {
                    "applicants": {
                        "applicant": [
                            {
                                "@data-format": "epodoc",
                                "applicant-name": {
                                    "name": {"$": "PURDUE RESEARCH FOUNDATION [US]"}
                                },
                            },
                            {
                                "@data-format": "original",
                                "applicant-name": {"name": {"$": "PURDUE RESEARCH FOUNDATION"}},
                            },
                        ]
                    }
                },
            },
            "abstract": abstract if abstract is not None else _EN_ABSTRACT,
        }
    }


def test_biblio_parses_into_the_same_livedoc() -> None:
    [doc] = patents.parse_biblio(_payload([_doc()]))
    assert doc.source == "patents"
    assert doc.title == "ELECTROPHORETIC OSCILLATORS"
    assert doc.published == "2026-09-10"
    # ⚠️ Заявитель — из ОРИГИНАЛЬНОГО написания: в epodoc к имени приклеен хвост «[US]»,
    # а это имя уходит в карточку как игрок.
    assert doc.publisher == "PURDUE RESEARCH FOUNDATION"
    assert doc.url.endswith("pn%3DUS20260268235A1")
    assert doc.snippet == "A device."


def test_patent_is_a_trusted_source() -> None:
    [doc] = patents.parse_biblio(_payload(_doc()))  # одиночная запись приходит НЕ списком
    assert doc.trust.kind == PATENT
    assert doc.trust.level == HIGH


def test_abstract_may_be_a_list_of_languages() -> None:
    blocks = [
        {"@lang": "de", "p": {"$": "Eine Vorrichtung."}},
        {"@lang": "en", "p": {"$": "A device."}},
    ]
    [doc] = patents.parse_biblio(_payload([_doc(abstract=blocks)]))
    assert doc.snippet == "A device."


def test_broken_payload_is_not_a_crash() -> None:
    assert patents.parse_biblio({"nonsense": 1}) == []
    assert patents.parse_biblio(_payload([{"no-exchange-document": {}}])) == []


def test_cql_strips_quotes_and_dates_the_window() -> None:
    q = patents._cql('agentic "ai" security', date(2024, 9, 17), date(2026, 9, 17))
    assert q == 'ta="agentic  ai  security" and pd within "20240917 20260917"'


def test_windows_follow_the_as_of_slice(monkeypatch) -> None:
    monkeypatch.setattr(asof, "AS_OF", date(2024, 9, 15))
    (mid, end), (start, mid2) = patents.windows()
    assert end == date(2024, 9, 15)
    assert mid == mid2 == date(2022, 9, 16)
    # 730 суток, а не «два года»: 2020-й високосный, и разница в сутки здесь настоящая.
    assert start == date(2020, 9, 16)


class _Resp:
    def __init__(self, status: int, text: str = "", payload: dict | None = None) -> None:
        self.status_code = status
        self.text = text
        self._payload = payload or {}

    def json(self) -> dict:
        return self._payload


def _count(monkeypatch, resp: _Resp) -> int | None:
    async def fake_get(http, path, params):
        return resp

    monkeypatch.setattr(patents, "_get", fake_get)
    monkeypatch.setattr("pelican.weak.cache.ENABLED", False)
    return asyncio.run(patents._count(None, "memory poisoning", date(2024, 1, 1), date(2026, 1, 1)))


def test_empty_result_is_zero_not_unmeasurable(monkeypatch) -> None:
    """⚠️ OPS отвечает 404 `SERVER.EntityNotFound` на пустую выборку.

    Без разбора этой ветки ноль приезжал бы как `None`, то есть «не спросили», —
    и новое направление, у которого база пуста по построению, выпадало бы из оси.
    """
    body = "<fault><code>SERVER.EntityNotFound</code><message>No results found</message></fault>"
    assert _count(monkeypatch, _Resp(404, body)) == 0


def test_refusal_is_unmeasurable_not_zero(monkeypatch) -> None:
    assert _count(monkeypatch, _Resp(403, "quota")) is None
    assert _count(monkeypatch, _Resp(200, "", {"garbage": True})) is None


def test_measured_count_comes_through(monkeypatch) -> None:
    assert _count(monkeypatch, _Resp(200, "", _payload([], total="273"))) == 273


@pytest.mark.parametrize(
    ("recent", "before", "growth"),
    [
        (273, 146, pytest.approx(1.87, abs=0.01)),
        (1, 0, None),  # база пуста — это не бесконечный рост
        (None, 5, None),  # нечем измерить
    ],
)
def test_growth_never_divides_by_an_empty_base(recent, before, growth) -> None:
    a = patents.Activity("x", recent=recent, before=before)
    assert a.growth == growth


def test_line_is_silent_when_nothing_is_measured() -> None:
    assert patents.Activity("x", recent=None, before=None).line() == ""
    assert "273" in patents.Activity("x", recent=273, before=146).line()


def test_disabled_source_returns_nothing(monkeypatch) -> None:
    monkeypatch.setattr(patents.settings, "epo_ops_key", "")
    assert patents.enabled() is False
    assert patents.activity(["anything"]) == {}


def _signal(patents_line: str):
    from pelican.weak.ask import Signal

    return Signal(
        label="neuromorphic edge processors",
        title="Нейроморфные edge-процессоры",
        kind="emerging",
        kind_label="зарождающаяся",
        stage="пилот",
        confidence=0.75,
        checks=[],
        why="",
        patents=patents_line,
    )


def test_patent_line_is_rendered_with_both_numbers() -> None:
    from pelican.weak import page

    html = page._patents(_signal("патентных публикаций за два года — 101, за предыдущие два — 64"))
    assert "101" in html and "64" in html
    # ⚠️ Читатель обязан видеть, что в уверенность это не входит.
    assert "уверенность" in html


def test_nothing_measured_renders_nothing() -> None:
    from pelican.weak import page

    assert page._patents(_signal("")) == ""


# --- маска «работ из будущего»: кэш обязан замечать выросший корпус ---


class _Conn:
    """Store-заглушка: считает, сколько раз у неё спросили полный список id."""

    def __init__(self, ids: list[int]) -> None:
        self.ids = ids
        self.full_queries = 0

    def execute(self, sql: str):
        if sql.lstrip().startswith("SELECT count("):
            return _Row([(len(self.ids),)])
        self.full_queries += 1
        return _Row([(i,) for i in self.ids])


class _Row:
    def __init__(self, rows: list[tuple]) -> None:
        self._rows = rows

    def fetchall(self) -> list[tuple]:
        return self._rows

    def fetchone(self) -> tuple:
        return self._rows[0]


class _Store:
    def __init__(self, ids: list[int]) -> None:
        self.conn = _Conn(ids)


def test_future_mask_rebuilds_when_the_corpus_grew(tmp_path, monkeypatch) -> None:
    """⚠️ Замер 2026-09-17: кэш держал 3.09 млн id при 5.63 млн работ после среза.

    2.5 миллиона работ из будущего были видны детектору, и защита от look-ahead молчала.
    Кэш обязан заметить рост сам — правило «снести рукой» уже один раз не сработало.
    """
    from pelican.weak import corpus

    monkeypatch.setattr(corpus, "DATA_DIR", tmp_path)
    store = _Store([1, 2, 3])
    assert corpus.future_ids(store, date(2024, 9, 15)).tolist() == [1, 2, 3]
    assert store.conn.full_queries == 1

    # Тот же корпус — кэш, полного запроса нет.
    assert corpus.future_ids(store, date(2024, 9, 15)).tolist() == [1, 2, 3]
    assert store.conn.full_queries == 1

    # Корпус вырос — маска пересобирается САМА.
    store.conn.ids = [1, 2, 3, 4]
    assert corpus.future_ids(store, date(2024, 9, 15)).tolist() == [1, 2, 3, 4]
    assert store.conn.full_queries == 2


# --- темп диктует OPS, а не мы ---


def test_throttle_header_sets_the_pace() -> None:
    """Число после цвета — запросов в МИНУТУ по этому сервису, и у search она своя."""
    patents.reset()
    patents._read_throttle(
        {"x-throttling-control": "idle (other=green:1000, retrieval=green:100, search=green:30)"}
    )
    assert patents._per_min == 30  # две секунды между запросами, а не 1000/мин у `other`
    assert patents.closed() == ""


def test_black_search_closes_the_source() -> None:
    """⚠️ Замер 2026-09-17: 198 запросов подряд в фиксированном темпе 4/с — и OPS

    закрыл поиск целиком. Чёрный цвет обязан выключать источник, а не замедлять его.
    """
    patents.reset()
    patents._read_throttle(
        {"x-throttling-control": "busy (other=green:1000, search=black:0)"}
    )
    assert patents._per_min == 0
    assert "закрыл поиск" in patents.closed()


def test_robot_verdict_is_never_retried(monkeypatch) -> None:
    """403 CLIENT.RobotDetected — приговор поведению клиента: повтор его продлевает."""
    patents.reset()
    calls = []

    class _R:
        status_code = 403
        text = "<code>CLIENT.RobotDetected</code>"
        headers: dict[str, str] = {}

    async def fake_token(http):
        return "t"

    class _Http:
        async def get(self, url, params=None, headers=None):
            calls.append(url)
            return _R()

    monkeypatch.setattr(patents, "_access_token", fake_token)
    got = asyncio.run(patents._get(_Http(), "published-data/search", {}))
    assert got is None
    assert len(calls) == 1  # ровно одна попытка
    assert "RobotDetected" in patents.closed()
    # Источник закрыт: следующий запрос даже не уходит.
    assert asyncio.run(patents._get(_Http(), "published-data/search", {})) is None
    assert len(calls) == 1
