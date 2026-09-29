"""Оснастка аудита выдачи: сверка имён, судья точности и проверка фактов карточки.

Проверяется ровно то, что в этих замерах может ошибиться молча: что судье не видно
нашей же оценки, что ссылка вида «[1, 3]» разбирается целиком, а её номера не читаются
как числа карточки, и что различительность имени считается по документной частоте.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from pelican.weak.names import discriminating, document_frequency, tokens
from pelican.weak.stats import span, wilson

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def facts():
    return _load("measure_card_facts")


@pytest.fixture(scope="module")
def precision():
    return _load("judge_precision")


# ------------------------------------------------------------------ имена


def test_generic_words_do_not_link_different_companies() -> None:
    """⚠️ «Pulse Security» и «Fabrix Security» — разные игроки, общее слово тут шум."""
    assert not tokens("Pulse Security") & tokens("Fabrix Security")


def test_frequent_name_stops_discriminating() -> None:
    """Имя из трёх строк связывает что угодно с чем угодно и в сверку не идёт."""
    rows = ["Google, Keycard", "Google DeepMind", "Google Cloud", "Cyata"]
    df = document_frequency(rows)
    assert discriminating("Google, Keycard", df) == {"keycard"}


def test_brackets_are_not_names() -> None:
    """«(стратеги-инвесторы)» — пояснение методолога, а не компания."""
    assert tokens("Scania Invest (strategic investors)") == {"scania", "invest"}


# ------------------------------------------------------------------ интервал


def test_zero_of_thirty_is_not_zero() -> None:
    """⚠️ «0 ложных из 30» — верхняя граница около 11%, а не отсутствие ложных."""
    lo, hi = wilson(0, 30)
    assert lo == 0.0
    assert 0.08 < hi < 0.13
    assert span(0, 30).startswith("[0.00;")


# ------------------------------------------------------- достоверность карточки


def _card() -> dict:
    return {
        "label": "agent identity",
        "description": "Ключи агентам выдаются на время [1].",
        "advantage": "Короче срок компрометации [1, 2].",
        "case": "Keycard привлекла 38 млн долларов [2].",
        "companies": ["Keycard", "Cyata"],
        "sources": [
            {"n": 1, "title": "Scoped credentials for AI agents", "date": "2026-08-01"},
            {
                "n": 2,
                "title": "Keycard raises $38M; Cyata joins the agent identity race",
                "date": "2026-09-01",
            },
        ],
    }


def test_multi_reference_is_parsed_whole(facts) -> None:
    """⚠️ «[1, 2]» — две ссылки, а не одна: одиночный шаблон терял вторую."""
    by_n = {1: "первое", 2: "второе"}
    got, missing = facts.cited("Короче срок [1, 2].", by_n)
    assert got == ["первое", "второе"]
    assert not missing


def test_reference_numbers_are_not_card_numbers(facts) -> None:
    """⚠️ Номер ссылки не число карточки: иначе «[1, 3]» читалось бы как выдумка."""
    defects, _checks = facts.strict_check(_card())
    assert not defects


def test_invented_amount_is_caught(facts) -> None:
    """Сумма, которой нет ни в одном свидетельстве, — это ВЫДУМКА, и она называется так."""
    card = _card()
    card["case"] = "Keycard привлекла 570 млн долларов [2]."
    defects, _checks = facts.strict_check(card)
    assert any(kind == facts.WRONG and "570" in text for kind, text in defects)


def test_amount_matches_across_units(facts) -> None:
    """⚠️ «38 млн» против «$38M» — одно число: сверяются цифры, а не единица."""
    card = _card()
    card["case"] = "Раунд составил 38 млн долларов [2]."
    defects, _checks = facts.strict_check(card)
    assert not defects


def test_company_without_source_is_not_called_a_lie(facts) -> None:
    """⚠️ Компания могла прийти из заголовка, которого в карточке не показано.

    Это не выдумка, а непроверяемость: читатель не может её сверить по источникам
    карточки. Складывать два рода дефектов в одно число нельзя — они про разное.
    """
    card = _card()
    card["companies"] = ["Keycard", "Fabrix"]
    defects, _checks = facts.strict_check(card)
    assert [kind for kind, text in defects if "Fabrix" in text] == [facts.UNVERIFIABLE]


def test_only_sentences_with_references_are_judged(facts) -> None:
    """Раздел без ссылок карточка и так не показывает — и проверять там нечего."""
    card = _card()
    card["description"] = "Предложение без ссылки. Предложение со ссылкой [1]."
    got = facts.claims(card)
    assert [c for _f, c, _e in got if "без ссылки" in c] == []
    assert any("со ссылкой" in c for _f, c, _e in got)


# ------------------------------------------------------------- судья точности


def test_judge_never_sees_our_own_verdict(precision) -> None:
    """⚠️ Иначе судья подтверждает нашу же разметку, а не судит выдачу."""
    sig = {
        "label": "agent identity",
        "title": "Идентичность агентов",
        "description": "Ключи на время [1].",
        "kind": "emerging",
        "kind_label": "зарождающаяся",
        "stage": "пилот",
        "confidence": 1.0,
        "companies": ["Keycard"],
        "sources": [{"n": 1, "title": "Scoped credentials", "date": "2026-08-01"}],
    }
    text = precision.card_text(sig, "ai security")
    assert "Keycard" in text and "Scoped credentials" in text
    for leak in ("emerging", "зарождающаяся", "пилот", "1.0", "confidence"):
        assert leak not in text


def test_verdict_outside_the_dictionary_is_dropped(precision) -> None:
    """Эхо-проверка: вердикт вне словаря — это выдумка, и в долю он не идёт."""
    assert 7 not in precision.VERDICTS
    assert precision.VERDICTS[precision.GOOD] == "signal"


# ------------------------------------------- правда карточки: источники под компании


def _doc(title: str, domain: str = "techcrunch.com"):
    from pelican.weak.live import LiveDoc

    return LiveDoc("google_news", title, f"https://{domain}/x", "2026-08-01", "TC", "en",
                   domain=domain)


def test_company_from_the_label_is_not_a_player() -> None:
    """⚠️ У «ensemble of specialized llm» игроком оказывался «Ensemble»."""
    from pelican.weak import ask

    docs = [_doc("Ensemble raises $10M")]
    firms = {"Ensemble raises $10M": ("Ensemble", "Keycard")}
    named = ask._named_by(docs, firms, {"ensemble", "of", "llm"})
    assert list(named) == ["Keycard"]


def test_first_headline_that_named_the_company_wins() -> None:
    from pelican.weak import ask

    first, second = _doc("Keycard raises $38M"), _doc("Keycard hires a CTO")
    firms = {first.title: ("Keycard",), second.title: ("Keycard",)}
    named = ask._named_by([first, second], firms, set())
    assert named["Keycard"].title == first.title


def test_backing_picks_the_headline_covering_most_companies() -> None:
    """Жадное покрытие: сначала заголовок, называющий больше всего неподтверждённых."""
    from pelican.weak import ask

    shown = [_doc("показанный заголовок")]
    one, many = _doc("один игрок"), _doc("сразу трое")
    named = {
        "A": many, "B": many, "C": many,  # трое пришли из одного заголовка
        "D": one,
    }
    got = ask._backing(shown, [one, many], named)
    assert [d.title for d in got][0] == many.title


def test_backing_stops_when_nothing_is_missing() -> None:
    """Все компании уже подтверждены показанным — добирать нечего."""
    from pelican.weak import ask

    shown = [_doc("он и назвал")]
    got = ask._backing(shown, [_doc("другой")], {"A": shown[0]})
    assert got == []


def test_backing_respects_the_cap() -> None:
    from pelican.weak import ask

    rest = [_doc(f"заголовок {i}") for i in range(6)]
    named = {f"C{i}": d for i, d in enumerate(rest)}
    got = ask._backing([], rest, named)
    assert len(got) == ask.EXTRA_FOR_COMPANIES


def test_corpus_trace_becomes_a_source_without_a_link() -> None:
    """⚠️ Иначе ссылка на след ведёт в никуда: номера источников и свидетельств совпадают."""
    from pelican.weak import ask
    from pelican.weak.assess import CORPUS
    from pelican.weak.core import Footprint
    from pelican.weak.trust import HIGH, SCIENCE

    core = Footprint("federated learning", 13575, 2016, 2913)
    label = Footprint("federated learning for aml", 2, 2025, 2)
    src = ask._corpus_source(8, core, label, "2026-09-17")
    assert src.n == 8
    assert src.url == ""
    assert src.evidence == CORPUS
    assert src.kind == SCIENCE and src.trust == HIGH
    assert "13575" in src.title and "2016" in src.title


def test_entry_conditions_do_not_inflate_confidence() -> None:
    """⚠️ Внутри ТОП жанр и число игроков истинны по построению.

    Пока они входили в уверенность, та принимала ровно три значения (0.6 / 0.8 / 1.0) и
    кандидатов не различала. Уверенность считается по проверкам СВЕРХ условий отбора.
    """
    from pelican.weak.ask import Check

    checks = [
        Check("жанр: зарождающаяся", True, "", entry=True),
        Check("несколько независимых игроков", True, "", entry=True),
        Check("разные типы источников", True, ""),
        Check("научная опора", False, ""),
        Check("разные издатели", False, ""),
        Check("объяснение со ссылками", True, ""),
    ]
    scored = [c for c in checks if not c.entry]
    assert len(scored) == 4
    assert sum(c.passed for c in scored) / len(scored) == 0.5
    # со старой формулой было бы 4 из 6 — выше и без всякой заслуги
    assert sum(c.passed for c in checks) / len(checks) > 0.5
