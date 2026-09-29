"""Ветка слабых сигналов без сети: доверенность, разбор ответов модели, живые парсеры, страница."""

from __future__ import annotations

import json

import pytest

from pelican.weak import card as card_mod
from pelican.weak import deepen
from pelican.weak.ask import AskResult, Check, Signal, Source, _Cand, _median, _merge_group
from pelican.weak.assess import NEWS, PAPER, Candidate, Evidence, build_prompt, parse
from pelican.weak.kinds import EMERGING, KINDS, MATURE
from pelican.weak.live import (
    LiveDoc,
    parse_cyberleninka,
    parse_github,
    parse_google_news,
    parse_habr,
)
from pelican.weak.page import from_json, render, to_json
from pelican.weak.rubric import PILOT, STAGES
from pelican.weak.trust import HIGH, LOW, MEDIUM, classify, corroborated

# ------------------------------------------------------------------ доверенность


@pytest.mark.parametrize(
    ("url", "level"),
    [
        ("https://arxiv.org/abs/2601.00001", HIGH),
        ("https://www.nature.com/articles/x", HIGH),
        ("https://cyberleninka.ru/article/n/x", HIGH),
        ("https://www.nist.gov/ai", HIGH),
        ("https://cs.stanford.edu/paper", HIGH),
        ("https://techcrunch.com/2026/01/01/x", MEDIUM),
        ("https://www.prnewswire.com/news/x", LOW),
        ("https://habr.com/ru/articles/1/", LOW),
        ("https://someone.substack.com/p/x", LOW),
        # ⚠️ Неизвестный домен — средняя, а не высокая: иначе правило ТЗ обходится
        # любым новым доменом.
        ("https://unknown-industry-outlet.example/x", MEDIUM),
    ],
)
def test_trust_levels(url: str, level: str) -> None:
    assert classify(url).level == level


def test_corroboration_needs_something_above_low() -> None:
    """Правило ТЗ: пресс-релизы и блоги не могут быть единственным основанием."""
    levels = [classify(u).level for u in ("prnewswire.com", "habr.com")]
    assert not corroborated(levels)
    assert corroborated(levels + [classify("arxiv.org").level])


# ------------------------------------------------------------------ оценка моделью


def _cand() -> Candidate:
    return Candidate(
        "k",
        "memory poisoning defense",
        [
            Evidence(1, "2026-06-01", "SMSR: Certified Defence Against Memory Poisoning", PAPER),
            Evidence(
                -1,
                "2026-07-01",
                "Startup raises $10M for agent memory firewall",
                NEWS,
                publisher="TechCrunch",
            ),
        ],
    )


def test_assess_prompt_marks_evidence_type_and_publisher() -> None:
    prompt = build_prompt(_cand())
    assert "[1] (2026-06-01, PAPER)" in prompt
    assert "[2] (2026-07-01, NEWS, TechCrunch)" in prompt


def test_assess_parse_keeps_cited_why() -> None:
    a = parse(
        {"kind": EMERGING, "stage": PILOT, "why": "Работа [1] и новость [2] о пилоте."}, _cand()
    )
    assert (a.kind, a.stage, a.cited) == (EMERGING, PILOT, [1, 2])
    assert a.shown


def test_assess_parse_drops_uncited_why() -> None:
    """Вывод без ссылок неотличим от знаний модели — ТЗ такое основанием не принимает."""
    a = parse(
        {"kind": MATURE, "stage": STAGES[3], "why": "Это давно известная технология."}, _cand()
    )
    assert a.why == "" and a.cited == []
    assert not a.shown


def test_assess_parse_ignores_citation_outside_evidence() -> None:
    a = parse({"kind": EMERGING, "stage": STAGES[0], "why": "См. [7]."}, _cand())
    assert a.why == ""


def test_assess_parse_reads_judgments_as_numbers_and_skips_absent() -> None:
    payload = {
        "kind": EMERGING,
        "stage": STAGES[0],
        "why": "см. [1]",
        "players_named": 3,
        "funding_mentioned": True,
        "routine_tool": "false",
        "novelty_claimed": None,
    }
    a = parse(payload, _cand())
    assert a.judgments["players_named"] == 3.0
    assert a.judgments["funding_mentioned"] == 1.0
    assert a.judgments["routine_tool"] == 0.0
    assert "novelty_claimed" not in a.judgments and "pilot_mentioned" not in a.judgments
    # Старый формат ответа — без атомарных чтений, а не с нулями.
    assert parse({"kind": EMERGING, "stage": STAGES[0], "why": ""}, _cand()).judgments == {}


def test_assess_schema_requires_every_judgment() -> None:
    from pelican.weak.assess import JUDGMENT_NAMES, schema

    sch = schema()
    assert set(JUDGMENT_NAMES) <= set(sch["required"])
    assert sch["properties"]["players_named"]["type"] == "integer"
    assert sch["properties"]["routine_tool"]["type"] == "boolean"


def test_assess_parse_rejects_off_vocabulary() -> None:
    with pytest.raises(ValueError):
        parse({"kind": "trendy", "stage": STAGES[0], "why": ""}, _cand())
    with pytest.raises(ValueError):
        parse({"kind": KINDS[0], "stage": "зрелость", "why": ""}, _cand())


# ------------------------------------------------------------------ карточка


CARD_EV = [
    Evidence(-1, "2026-05-01", "Memory poisoning defence for agents", PAPER),
    Evidence(-1, "2026-07-08", "Keycard raises $10M for agent memory", NEWS, publisher="TechCrunch"),
]


def test_card_blanks_uncited_sections() -> None:
    c = card_mod.parse(
        {
            "title": "Защита памяти агентов",
            "description": "Проверка записей памяти перед использованием [1].",
            "advantage": "Работает лучше всего на свете.",
            "case": "Стартап Keycard привлёк $10M [2].",
            "facts": [],
            "summaries": [{"n": 1, "text": "Статья о защите."}, {"n": 9, "text": "лишнее"}],
        },
        "x",
        CARD_EV,
    )
    assert c.description and c.case
    assert c.advantage == ""
    assert c.summaries == {1: "Статья о защите."}


def test_card_drops_sentences_without_facts_or_with_invented_numbers() -> None:
    c = card_mod.parse(
        {
            "title": "t",
            "description": "Метод проверяет память [1]. Точность выросла до 97% [1].",
            "advantage": "Технология обладает большим потенциалом [1]. Keycard снизил утечки [2].",
            "case": "Keycard привлёк $25M [2]. Keycard привлёк $10M в 2026 [2].",
            "facts": [
                {"date": "2026-07-08", "text": "Keycard привлёк $10M [2]."},
                {"date": "2025-01", "text": "Keycard основан [2]."},  # даты нет в свидетельстве
                {"date": "июль", "text": "Keycard [2]."},
            ],
            "summaries": [],
        },
        "memory defence",
        CARD_EV,
    )
    assert c.description == "Метод проверяет память [1]."
    assert c.advantage == "Keycard снизил утечки [2]."
    assert c.case == "Keycard привлёк $10M в 2026 [2]."
    assert c.facts == [("2026-07", "Keycard привлёк $10M [2].")]


def test_card_drops_foreign_script_and_falls_back_to_the_label() -> None:
    c = card_mod.parse(
        {
            "title": "Смартфон-आधारные инструменты диагностики",
            "description": "Метод проверяет память [1]. Метод μ-проверки आधार памяти [1].",
            "advantage": "",
            "case": "",
            "facts": [],
            "summaries": [{"n": 1, "text": "Статья о защите."}, {"n": 2, "text": "新闻 о раунде."}],
        },
        "smartphone-based medical diagnostic tools",
        CARD_EV,
    )
    assert c.title == "smartphone-based medical diagnostic tools"
    assert c.description == "Метод проверяет память [1]."
    assert c.summaries == {1: "Статья о защите."}
    assert not card_mod.foreign_script("Проверка μ-сенсора в LLM (α = 0.5)")


def test_excluded_get_russian_titles(monkeypatch) -> None:
    """ТЗ: выдача на русском — исключённые получают русское название; чужое письмо не берётся."""
    import pelican.weak.ask as ask_mod

    async def fake(_system, user, _schema, _name, _tokens):
        assert "1. edge ai chips" in user
        return {"items": ["Чипы для периферийного ИИ", "आधार"]}

    monkeypatch.setattr(ask_mod, "_one", fake)
    a = Signal(label="edge ai chips", title="edge ai chips", kind=MATURE, kind_label="",
               stage=PILOT, confidence=0.0, checks=[], why="")
    b = Signal(label="other", title="other", kind=MATURE, kind_label="", stage=PILOT,
               confidence=0.0, checks=[], why="")
    ask_mod.russian_titles([a, b])
    assert (a.title, b.title) == ("Чипы для периферийного ИИ", "other")


def test_card_hypothesis_needs_citation() -> None:
    """Гипотеза (схема 1 ТЗ) чистится как описание: без ссылки на свидетельство — пусто."""
    base = {"title": "Т", "description": "", "advantage": "", "case": "", "facts": [],
            "summaries": []}
    kept = card_mod.parse(
        base | {"hypothesis": "Если память проверяют, то атак станет меньше [1]."}, "x", CARD_EV
    )
    assert kept.hypothesis == "Если память проверяют, то атак станет меньше [1]."
    assert card_mod.parse(base | {"hypothesis": "Если так, то так."}, "x", CARD_EV).hypothesis == ""
    # Точка сокращения внутри не режет гипотезу (финтех: «…Custodia Inc. Bank) начинают…»).
    cut = "Если банки (напр. Custodia Inc. Bank) начнут выпуск, то спрос вырастет [1]."
    assert card_mod.parse(base | {"hypothesis": cut}, "x", CARD_EV).hypothesis == cut


def test_stored_report_drops_foreign_script_on_render() -> None:
    """Отчёт, собранный до проверки письма («необанк» #5): чистится при показе."""
    d = {
        "query": "необанк", "query_en": "neobank", "facets": [], "started": "2026-09-27",
        "signals": [{
            "label": "blockchain identity", "title": "Блокчейн-идентификация",
            "kind": EMERGING, "kind_label": "", "stage": PILOT, "confidence": 0.5,
            "description": "Банк проверяет личность [1]. Строит блокчейн-आधारные структуры [1].",
            "why": "Новые работы [1].", "checks": [],
            "sources": [{"n": 1, "title": "t", "url": "u", "date": "", "kind": "", "lang": "en",
                         "evidence": "PAPER", "trust": "high", "trust_label": "",
                         "summary_ru": "Резюме आधार."}],
        }],
    }
    r = from_json(d)
    assert r.signals[0].description == "Банк проверяет личность [1]."
    assert r.signals[0].sources[0].summary_ru == ""
    assert "आ" not in render(r)
    assert "Гипотеза для проверки" not in render(r)  # отчёт старше поля — блока нет
    r.signals[0].hypothesis = "Если банки внедрят, то проверка ускорится [1]."
    assert "Гипотеза для проверки" in render(r)
    assert r.signals[0].corroborated and "пониженная доверенность:" not in render(r)
    r.signals[0].sources[0].trust = LOW  # один пресс-релиз — отметка по правилу ТЗ
    assert not r.signals[0].corroborated and "пониженная доверенность:" in render(r)


def test_card_sees_abstract_in_evidence() -> None:
    ev = Evidence(-1, "2026-01-01", "Title", PAPER, detail="We reach 3.2x speed-up.")
    assert "3.2x speed-up" in card_mod.build_prompt("x", [ev])


def test_deepen_parses_arxiv_and_openalex() -> None:
    xml = (
        '<feed xmlns="http://www.w3.org/2005/Atom"><entry>'
        "<id>http://arxiv.org/abs/2309.04269v1</id><summary> Dense\n summaries. </summary>"
        "</entry></feed>"
    )
    assert deepen.parse_arxiv(xml) == {"arxiv:2309.04269": "Dense summaries."}
    payload = {"results": [{"id": "https://openalex.org/W1", "abstract_inverted_index":
                            {"world": [1], "models": [2], "Learned": [0]}}]}
    assert deepen.parse_openalex(payload) == {"openalex:W1": "Learned world models"}
    assert deepen.paper_key("https://arxiv.org/abs/1805.07777v3") == "arxiv:1805.07777"
    assert deepen.paper_key("https://news.google.com/rss/articles/x") is None


def test_deepen_keeps_only_headlines_about_the_core() -> None:
    assert deepen._about("Manifold AI raises for World Models", "world models")
    assert not deepen._about("Galaxea raises $290M", "world models")


# ------------------------------------------------------------------ живые парсеры

GNEWS = """<rss><channel><item>
<title>Keycard raises $38M for agent identity - SiliconANGLE</title>
<link>https://news.google.com/rss/articles/abc</link>
<pubDate>Wed, 08 Jul 2026 12:41:30 GMT</pubDate>
<source url="https://siliconangle.com">SiliconANGLE</source>
</item></channel></rss>"""

HABR = """<rss><channel><item>
<title><![CDATA[LLM Firewall для агентов]]></title>
<guid isPermaLink="true">https://habr.com/ru/articles/100/</guid>
<link>https://habr.com/ru/articles/100/?utm_source=rss</link>
<description><![CDATA[<p>Как защитить&nbsp;агентов</p>]]></description>
<pubDate>Tue, 30 Jun 2026 10:00:00 GMT</pubDate>
</item></channel></rss>"""


def test_google_news_strips_publisher_and_takes_its_domain() -> None:
    [d] = parse_google_news(GNEWS, "en")
    assert d.title == "Keycard raises $38M for agent identity"
    assert d.publisher == "SiliconANGLE"
    # ⚠️ Доверенность — по домену издателя, а не по ссылке-переадресации Google.
    assert d.domain == "siliconangle.com"
    assert d.trust.level == MEDIUM
    assert d.published == "2026-07-08"


def test_habr_prefers_guid_and_cleans_html() -> None:
    [d] = parse_habr(HABR)
    assert d.url == "https://habr.com/ru/articles/100/"
    assert d.title == "LLM Firewall для агентов"
    assert d.snippet == "Как защитить агентов"
    assert d.lang == "ru" and d.trust.level == LOW


def test_cyberleninka_and_github_parsers() -> None:
    [c] = parse_cyberleninka(
        {
            "articles": [
                {
                    "name": "Роевые <b>алгоритмы</b>",
                    "link": "/article/n/x",
                    "year": 2022,
                    "journal": "Журнал",
                    "annotation": "Текст",
                }
            ]
        }
    )
    assert c.title == "Роевые алгоритмы" and c.url == "https://cyberleninka.ru/article/n/x"
    assert c.trust.level == HIGH
    [g] = parse_github(
        {
            "items": [
                {
                    "full_name": "a/b",
                    "html_url": "https://github.com/a/b",
                    "created_at": "2026-01-02T00:00:00Z",
                    "stargazers_count": 5,
                    "description": "desc",
                }
            ]
        }
    )
    assert g.published == "2026-01-02" and g.publisher == "★ 5"


# ------------------------------------------------------------------ страница


def _result() -> AskResult:
    src = [
        Source(
            1,
            "Keycard raises $38M",
            "https://news.google.com/x",
            "2026-07-08",
            "отраслевое медиа",
            NEWS,
            "en",
            MEDIUM,
            "средняя",
            "SiliconANGLE",
            "Стартап привлёк раунд.",
            domain="siliconangle.com",
            machine_summary=True,
        ),
        Source(
            2,
            "Статья <script>",
            "https://arxiv.org/abs/1",
            "2026-06-01",
            "препринт",
            PAPER,
            "en",
            HIGH,
            "высокая",
            "arxiv",
        ),
    ]
    sig = Signal(
        label="agent identity",
        title="Идентификация ИИ-агентов",
        kind=EMERGING,
        kind_label="зарождающаяся технология",
        stage=PILOT,
        confidence=0.8,
        checks=[
            Check("жанр: зарождающаяся", True, "ok"),
            Check("рыночное подтверждение", False, "0"),
        ],
        why="Новость [1] о раунде.",
        description="Описание [2].",
        sources=src,
        companies=["Keycard", "Cyata"],
        core="agent identity",
        core_works=12,
        core_since=2025,
        label_works=3,
        low_visibility=True,
    )
    excluded = Signal(
        label="tls",
        title="tls",
        kind="standard",
        kind_label="стандарт",
        stage=STAGES[3],
        confidence=0.2,
        checks=[],
        why="Спецификация [1].",
    )
    return AskResult(
        "кибербезопасность",
        "cybersecurity",
        ["агенты"],
        "2026-09-15T20:00:00",
        candidates=2,
        sources_processed=10,
        signals=[sig],
        excluded=[excluded],
    )


def test_page_has_everything_the_spec_lists_and_escapes() -> None:
    page = render(_result())
    for must in (
        "кибербезопасность",  # поисковый запрос
        "Идентификация ИИ-агентов",  # название технологии
        "80%",  # уверенность
        "Ключевые предикторы",
        "Описание технологии",
        "Потенциальное преимущество",
        "Кейс-пример",
        "Доверенность",
        "Язык",
        "Тип",
        "Дата",
        "машинное резюме",  # отметка у иностранного источника
        "Исключено из выдачи",  # логика исключения
        "уверенность выше 75%",
        "источников обработано",
        "Кто это делает",  # колонка «Компании» у заказчика
        "Keycard",
        "ниже медианы выдачи",  # видимость
        "за пределами ТОП",  # отсев считается вслух
    ):
        assert must in page, must
    # Данные экранированы: в содержимом страницы скриптов нет (свой — после `</main>`).
    assert "<script>" not in page.split("<main>", 1)[1].split("</main>", 1)[0]
    assert page.count("<script>") == 1
    assert "https://" not in page.split("<style>", 1)[1].split("</style>", 1)[0]


def test_json_roundtrip_is_utf8() -> None:
    assert "Идентификация ИИ-агентов" in to_json(_result())


def test_saved_report_reads_back_old_and_new() -> None:
    """Старый отчёт без `facts` и с лишними полями читается; хронология рисуется."""
    raw = json.loads(to_json(_result()))
    del raw["signals"][0]["facts"]
    raw["signals"][0]["gone_field"] = 1
    back = from_json(raw)
    assert back.signals[0].facts == [] and back.signals[0].sources[0].n == 1
    assert "Хронология" not in render(back)
    back.signals[0].facts = [{"date": "2026-07", "text": "Keycard привлёк $38M [1]."}]
    page = render(back)
    assert "Хронология" in page and "2026-07" in page
    assert "table-layout:fixed" in page


def test_page_tells_about_spread_or_history() -> None:
    """Без истории — подпись о разбросе; с историей — «держится N из K» и плитка."""
    result = _result()
    page = render(result)
    assert "Прошлых прогонов по этому запросу нет" in page
    assert "прошлых прогонов</div>" not in page
    result.history_runs = 3
    result.signals[0].held = 2
    result.stable = 1
    result.submarkets_en = ["security certification services"]
    page = render(result)
    assert "держится в 2 из 3 прошлых прогонов" in page
    assert "<b>1</b>держатся в ≥ половине из 3 прошлых прогонов" in page
    assert "security certification services" in page


def test_on_topic_stops_at_top_and_moves_off_topic_to_excluded(monkeypatch) -> None:
    """Проверяется по порядку ТОП до набора `TOP` тематичных; хвост не спрашивается."""
    import dataclasses

    from pelican.weak import ask as ask_mod

    asked: list[str] = []

    async def fake_one(system, user, schema, name, max_tokens):
        label = user.splitlines()[1].removeprefix("TECHNOLOGY: ")
        asked.append(label)
        if label == "boom":
            raise ask_mod.LLMError("сбой")
        return {"score": 0 if label.startswith("off") else 3, "why": "Работы [1] — не о том"}

    monkeypatch.setattr(ask_mod, "_one", fake_one)
    monkeypatch.setattr(ask_mod, "TOP", 2)
    base = _result().signals[0]
    ranked = [
        (dataclasses.replace(base, label=name, checks=[Check("жанр", True, "", entry=True)]), [])
        for name in ("off-1", "on-1", "boom", "on-2", "on-3")
    ]
    result = _result()
    result.excluded = []
    kept = ask_mod._on_topic(ranked, "art brut", result)
    assert [s.label for s, _ in kept] == ["on-1", "boom", "on-2", "on-3"]
    assert asked == ["off-1", "on-1", "boom"]
    assert [s.label for s in result.excluded] == ["off-1"]
    assert result.excluded[0].kind == "noise" and not result.excluded[0].checks[0].passed
    assert result.off_topic == 1
    # Оценка остаётся подписью в выдаче; сбой проверки — «не оценивалось», а не балл.
    assert [s.relevance for s, _ in kept] == [3, None, None, None]


def test_page_explains_short_or_empty_top() -> None:
    """Пустой ТОП без слов читается как сбой: страница говорит, куда ушли кандидаты."""
    result = _result()
    result.area_en = "art brut and prison art"
    result.off_topic = 1
    assert "набралось 1 из 15" in render(result)
    result.signals = []
    page = render(result)
    assert "не найдено" in page
    assert "Все кандидаты исключены" in page
    assert "не о предмете запроса «art brut and prison art» — 1" in page
    result.signals = [_result().signals[0]] * 15
    assert "не найдено" not in render(result)


def test_same_direction_is_exact_or_by_significant_words() -> None:
    from pelican.weak.ask import same_direction

    assert same_direction("agent identity", "Agent Identity ")
    assert same_direction(
        "security and governance for agentic ai", "security infrastructure for agentic ai"
    )
    assert not same_direction("memory poisoning defense", "agent identity")


def _pool_cand(label: str, core: str, stream: str, firms: list[str], heads: list[str]) -> _Cand:
    docs = [LiveDoc("google_news", h, "https://x", "2026-07-01", "TechCrunch", "en") for h in heads]
    return _Cand(label, [label], stream, core=core, headlines=docs, companies=firms)


def test_merge_group_folds_spellings_of_one_direction() -> None:
    got = _merge_group(
        [
            _pool_cand("ai cybersecurity", "AI cybersecurity", "наука", ["Kai"], ["a"]),
            _pool_cand("ai cyber", "AI cybersecurity", "деньги", ["Kai", "Onyx"], ["b"]),
        ]
    )
    # Ярлыком остался тот, за кем больше игроков; варианты и заголовки слиты.
    assert got.label == "ai cyber"
    assert got.stream == "деньги"
    assert got.companies == ["Kai", "Onyx"]
    assert sorted(got.members) == ["ai cyber", "ai cybersecurity"]
    assert len(got.headlines) == 2


def test_merge_twins_folds_what_the_model_grouped(monkeypatch) -> None:
    from pelican.weak import ask as ask_mod

    cands = [
        _pool_cand("automated kyc and kyb compliance", "automated compliance", "деньги", ["Sinpex"], ["a"]),
        _pool_cand("automated kyc and aml workflows", "RPA and NLP", "деньги", [], ["b"]),
        _pool_cand("decentralized identity", "decentralized identity", "наука", [], []),
    ]
    monkeypatch.setattr(ask_mod, "vectors_for", lambda texts: None)
    seen: list[list[str]] = []
    monkeypatch.setattr(ask_mod.twins, "groups", lambda _l, _v, keys: seen.append(keys) or [0, 0, 2])
    got = ask_mod._merge_twins(cands)
    assert [c.label for c in got] == ["automated kyc and kyb compliance", "decentralized identity"]
    assert got[0].core == "automated compliance"
    assert got[0].cores == ["RPA and NLP"]
    assert len(got[0].headlines) == 2
    # Ядра уходят модели как повод спросить, а не сводят сами.
    assert seen == [["automated compliance", "RPA and NLP", "decentralized identity"]]


def test_traced_core_replaces_only_a_core_without_footprint() -> None:
    from pelican.weak.ask import _traced_core

    works = {"automated KYC/KYB": 0, "automated compliance": 181, "machine learning": 42224}.get
    c = _pool_cand("kyc", "automated KYC/KYB", "деньги", [], [])
    c.cores = ["automated compliance"]
    assert _traced_core(c, lambda k: works(k, 0)) == "automated compliance"
    # След у своего ядра есть — остаётся своё, даже если у варианта след больше.
    c.core, c.cores = "automated compliance", ["machine learning"]
    assert _traced_core(c, lambda k: works(k, 0)) == "automated compliance"
    # Следа нет ни у кого — остаётся своё.
    c.core, c.cores = "automated KYC/KYB", ["RPA and NLP"]
    assert _traced_core(c, lambda k: works(k, 0)) == "automated KYC/KYB"


def test_only_emerging_goes_to_top() -> None:
    """Фильтр мейнстрима схемы 1 ТЗ: зрелое, стандарт, хайп, шум и общая категория в ТОП
    не идут — ТЗ запрещает включать их в выдачу. Игроки в отбор не входят."""
    sig = Signal(
        label="x",
        title="x",
        kind=EMERGING,
        kind_label="",
        stage=PILOT,
        confidence=1.0,
        checks=[],
        why="",
        companies=[],
    )
    assert sig.shown
    for kind in ("mature", "standard", "hype", "noise", "too_broad"):
        sig.kind = kind
        assert not sig.shown, kind


def test_score_orders_by_model_probability_with_its_predictors() -> None:
    """Скоринг пишет уверенность модели и три предиктора; свежее ядро с игроками — выше
    старого ядра без игроков. Без модели — `False`, уверенность не трогается."""
    from pelican.weak import ask as ask_mod
    from pelican.weak import model as model_mod

    if model_mod.load(model_mod.GROWTH_PARAMS) is None:
        pytest.skip("weak/growth.json не обучена")

    def make(label: str, share: float, works: int, companies: list[str]) -> Signal:
        return Signal(
            label=label, title=label, kind=EMERGING, kind_label="", stage=PILOT,
            confidence=0.25, checks=[], why="", companies=companies,
            core_works=works, core_recent_share=share,
        )

    fresh = make("fresh", 0.9, 40, ["A", "B"])
    stale = make("stale", 0.1, 40, [])
    mid = make("mid", 0.5, 40, ["A"])
    assert ask_mod.score([stale, mid, fresh])
    assert fresh.confidence > mid.confidence > stale.confidence
    assert len(fresh.predictors) == 3
    assert not ask_mod.score([])


def test_top_is_ordered_by_core_recent_share() -> None:
    """Ключ порядка — доля работ ядра за последний год; у кого её измерить нечем — в хвост."""
    from pelican.weak.ask import top_key

    def sig(label: str, companies: list[str], share: float | None) -> Signal:
        return Signal(
            label=label, title=label, kind=EMERGING, kind_label="", stage=PILOT,
            confidence=1.0, checks=[], why="", companies=companies, core_recent_share=share,
            core_works=100,
        )  # fmt: skip

    a, b, c = sig("a", ["K", "C"], 0.2), sig("b", ["K"], 0.7), sig("c", ["K", "C", "D"], None)
    got = sorted([a, b, c], key=lambda s: top_key(s, 0.4))
    assert [s.label for s in got] == ["b", "a", "c"]


def test_zero_key_puts_the_unbacked_after_everyone() -> None:
    """Нулевой ключ: научный поток без игроков идёт после всех, каким бы высоким ни был
    шанс попасть в список — это конфигурация одной статьи, а не направление."""
    from pelican.weak.ask import MONEY_STREAM, top_key

    def sig(label: str, listed: float, companies: list[str], stream: str = "наука") -> Signal:
        return Signal(
            label=label, title=label, kind=EMERGING, kind_label="", stage=PILOT,
            confidence=1.0, checks=[], why="", listed=listed, companies=companies,
            core_recent_share=0.5, core_works=100, stream=stream,
        )  # fmt: skip

    alone = sig("alone", 0.9, [])
    backed = sig("backed", 0.2, ["K"])
    money = sig("money", 0.1, [], stream=MONEY_STREAM)
    got = sorted([alone, backed, money], key=lambda s: top_key(s, 0.5))
    # Порядок двух «за кем стоят» решает уже уверенность и видимость — здесь они равны, и
    # важно только то, что одиночная статья стоит последней.
    assert got[-1].label == "alone"
    assert {s.label for s in got[:2]} == {"backed", "money"}


def test_listed_probability_does_not_touch_the_order() -> None:
    """Выключенная (`LISTED_IN_ASK`), модель списка — подпись: порядок зависит только от
    доли свежих работ ядра, чем бы ни была `listed`."""

    from pelican.weak.ask import top_key

    def sig(label: str, listed: float | None, share: float) -> Signal:
        return Signal(
            label=label, title=label, kind=EMERGING, kind_label="", stage=PILOT,
            confidence=1.0, checks=[], why="", listed=listed, core_recent_share=share,
            companies=["K"], core_works=100,
        )  # fmt: skip

    pool = [sig("a", 0.8, 0.1), sig("b", 0.3, 0.9), sig("c", None, 0.5)]
    assert [s.label for s in sorted(pool, key=lambda s: top_key(s, 0.5))] == ["b", "c", "a"]
    # Та же выдача с перевёрнутыми вероятностями — тот же порядок.
    flipped = [sig("a", 0.1, 0.1), sig("b", 0.9, 0.9), sig("c", 0.5, 0.5)]
    assert [s.label for s in sorted(flipped, key=lambda s: top_key(s, 0.5))] == ["b", "c", "a"]


def test_listed_probability_leads_the_order_when_measured(monkeypatch) -> None:
    """Включённая замером, модель списка — первый ключ после «кто-то стоит»; без
    вероятности кандидат уходит за посчитанных, дальше — доля свежих работ."""
    from pelican.weak import ask as ask_mod

    monkeypatch.setattr(ask_mod, "LISTED_IN_ASK", True)

    def sig(label: str, listed: float | None, share: float, companies: list[str]) -> Signal:
        return Signal(
            label=label, title=label, kind=EMERGING, kind_label="", stage=PILOT,
            confidence=1.0, checks=[], why="", listed=listed, core_recent_share=share,
            companies=companies, core_works=100,
        )  # fmt: skip

    pool = [
        sig("a", 0.8, 0.1, ["K"]),
        sig("b", 0.3, 0.9, ["K"]),
        sig("c", None, 0.5, ["K"]),
        sig("lonely", 0.99, 0.9, []),
    ]
    got = sorted(pool, key=lambda s: ask_mod.top_key(s, 0.5))
    assert [s.label for s in got] == ["a", "b", "c", "lonely"]


def test_rank_share_shrinks_tiny_cores_to_the_pool_prior() -> None:
    """Ядро с 1 работой и долей 1.0 не обгоняет ядро с 776 работами и долей 0.97."""
    from pelican.weak.ask import rank_share

    def sig(share: float | None, works: int) -> Signal:
        return Signal(
            label="x", title="x", kind=EMERGING, kind_label="", stage=PILOT, confidence=1.0,
            checks=[], why="", core_recent_share=share, core_works=works,
        )  # fmt: skip

    prior = 0.4
    assert rank_share(sig(1.0, 1), prior) < rank_share(sig(0.97, 776), prior)
    assert rank_share(sig(None, 0), prior) == -1.0
    assert abs(rank_share(sig(0.4, 5), prior) - 0.4) < 1e-9  # на уровне априорной — не сдвигается


def test_median_of_even_and_odd() -> None:
    assert _median([1, 5, 9]) == 5
    assert _median([1, 3, 5, 9]) == 4
    assert _median([]) == 0


# ------------------------------------------------------------------ команда CLI


def test_runner_returns_report_with_models_and_summary_marks(monkeypatch, pg_url) -> None:
    """Прогон очереди: отчёт несёт модели ответа, у КАЖДОГО резюме от модели — отметка
    (и у русского источника), и отметка доезжает до JSON, а не живёт только в HTML."""
    import contextlib
    import json

    from pelican import jobs, runner
    from pelican.config import settings
    from pelican.weak import ask as ask_mod

    with jobs.connect(pg_url) as c:
        jobs.apply_schema(c)
    monkeypatch.setattr(settings, "database_url", pg_url)
    monkeypatch.setattr(runner.locks, "hold", lambda _name: contextlib.nullcontext())

    def fake_run(_store, _q, on_progress=None):
        result = _result()
        result.models = ask_mod.answer_models()
        for s in result.signals[0].sources:
            s.machine_summary = bool(s.summary_ru)
        result.signals[0].sources[1].lang = "ru"
        result.signals[0].sources[1].summary_ru = "Резюме."
        result.signals[0].sources[1].machine_summary = True
        return result

    monkeypatch.setattr(ask_mod, "run", fake_run)
    name, payload, page = runner.run_query("кибербезопасность", lambda _m: None)
    data = json.loads(payload)
    srcs = data["signals"][0]["sources"]
    assert [s["machine_summary"] for s in srcs] == [True, True]
    assert {m["model"] for m in data["models"]} >= {settings.llm_model, settings.embedding_model}
    assert "машинное резюме на русском" in page and "резюме сгенерировано моделью" in page
    assert "Модели ответа" in page and "автоматического выбора модели на ответ нет" in page
    assert name[:8].isdigit()


def test_installed_listed_model_gets_every_feature_it_was_trained_on() -> None:
    """Обучение и выдача сходятся ПО ИМЕНАМ: установленная модель списка требует ровно те
    ключи, которые `_score_by_model` кладёт в словарь признаков.

    ⚠️ Расхождение имени тихое: `model.probability` подставит медиану импутации и посчитает
    вероятность по пустому признаку, ничего не сломав, — а ключ порядка ТОП станет шумом.
    Живой прогон это не показывает: он тоже не падает.
    """
    from datetime import date

    from pelican.weak import model as model_mod
    from pelican.weak.ask import emergence_features
    from pelican.weak.dataset import (
        EMBED_FEATURES,
        JUDGMENT_FEATURES,
        Row,
        embed_features,
        features,
        llm_features,
    )
    from pelican.weak.rounds import MONEY_FEATURES

    params = model_mod.load(model_mod.LISTED_PARAMS)
    if params is None:
        pytest.skip("модель списка не установлена (или правило решения провалено)")
    today = date(2026, 9, 22)
    row = Row("top", "x", "x")
    sig = Signal(
        label="x", title="x", kind=EMERGING, kind_label="", stage=PILOT,
        confidence=1.0, checks=[], why="",
    )  # fmt: skip
    built = (
        set(features(row, today))
        | set(llm_features({"corpus": (EMERGING, PILOT)}))
        | set(JUDGMENT_FEATURES)
        | set(embed_features(row))
        | set(EMBED_FEATURES)
        | set(emergence_features(sig, None, today))
        | set(MONEY_FEATURES)
        | {"industry_share"}
    )
    assert not set(params["features"]) - built
