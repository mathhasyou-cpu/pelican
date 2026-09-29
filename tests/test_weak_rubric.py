"""Рубрика слабых сигналов: разбор стадии и тренда, сумма, и сам замер 98/100.

⚠️ Последний тест — это **закреплённый замер**, а не проверка кода. Он держит
число, которое предъявляется жюри, и держит его по всем трём составляющим сразу:
правка разбора, которая «чинит» строку вне словаря ценой расхождения где-то ещё,
обязана быть замечена здесь, а не в презентации.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from pelican.weak.rubric import (
    CONCEPT,
    EARLY,
    FAST,
    GROWING,
    PILOT,
    PROTOTYPE,
    STAGES,
    explain,
    parse_stage,
    parse_trend,
    score,
    stage_floor,
)


def test_stage_floor_follows_what_the_evidence_reads() -> None:
    """«Концепция» при прочитанных продажах — противоречие, решается в пользу факта."""
    assert stage_floor(CONCEPT, {"commercial_deployment": 1.0}) == EARLY
    assert stage_floor(PROTOTYPE, {"pilot_mentioned": 1.0}) == PILOT
    assert stage_floor(EARLY, {"pilot_mentioned": 1.0}) == EARLY  # не понижает
    assert stage_floor(CONCEPT, {"funding_mentioned": 1.0}) == CONCEPT  # раунд — не внедрение

DATASET = Path(__file__).resolve().parents[1] / "scripts" / "weak_signals_100.tsv"


def test_stage_scale_is_ordinal_and_closed() -> None:
    assert STAGES == (CONCEPT, PROTOTYPE, PILOT, EARLY)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Концепция/Исследование", CONCEPT),
        ("Прототип/PoC", PROTOTYPE),
        ("Пилот", PILOT),
        ("Раннее внедрение", EARLY),
        # ⚠️ Гибрид считается по ПОЗДНЕЙ ступени: наивное «берём первую» промахнулось
        # на 35 строках из 99.
        ("Прототип/PoC → Пилот", PILOT),
        ("Пилот → Раннее внедрение", EARLY),
        ("Исследование → Прототип/PoC", PROTOTYPE),
        # ⚠️ Проза ступень не повышает — иначе промах ровно на две единицы.
        ("Прототип/PoC → первые поставки", PROTOTYPE),
        ("Прототип → ранняя серия", PROTOTYPE),
        ("Прототип/PoC → первые полисы (4+ андеррайтера к апрелю 2026)", PROTOTYPE),
    ],
)
def test_parse_stage(text: str, expected: str) -> None:
    assert parse_stage(text) == expected


def test_parse_stage_unknown() -> None:
    assert parse_stage("Ранние внедрения (ограниченный доступ)") is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Растёт", GROWING),
        ("Растет — без буквы ё", GROWING),
        ("Растёт быстро — почти пятикратный рост", FAST),
        ("Стабильный/растёт — устойчивый поток пилотов", GROWING),
        ("Стабильный с ускорением — новое поколение HASEL", None),
    ],
)
def test_parse_trend(text: str, expected: str | None) -> None:
    assert parse_trend(text) == expected


def test_score_is_additive() -> None:
    assert score(CONCEPT, GROWING) == 3
    assert score(EARLY, FAST) == 7
    assert score(PILOT, GROWING) == 5


def test_score_rejects_unknown() -> None:
    with pytest.raises(ValueError):
        score("зрелость", GROWING)
    with pytest.raises(ValueError):
        score(PILOT, "падает")


def test_explain_shows_the_arithmetic() -> None:
    assert explain(PILOT, FAST) == "Пилот (3) + Растёт быстро (3) = 6"


def test_rubric_reproduces_98_of_100() -> None:
    """Закреплённый замер на датасете организаторов.

    Расхождений нет ни одного: две оставшиеся строки не выражаются закрытым
    словарём вовсе (прозаическая «стадия» и третья формулировка тренда).

    ⚠️ Эти две НЕ подгоняются: правило, дотянутое до сотни из ста на двух
    наблюдениях, — это запоминание выборки, а не правило.
    """
    rows = list(csv.DictReader(DATASET.read_text(encoding="utf-8").splitlines(), delimiter="\t"))
    assert len(rows) == 100

    agreed = disagreed = outside = 0
    for row in rows:
        stage, trend = parse_stage(row["stage"]), parse_trend(row["trend"])
        if stage is None or trend is None:
            outside += 1
        elif score(stage, trend) == int(row["score"]):
            agreed += 1
        else:
            disagreed += 1

    assert (agreed, disagreed, outside) == (98, 0, 2), (
        f"сходимость рубрики изменилась: сходится {agreed}, расходится {disagreed}, "
        f"вне словаря {outside}"
    )
