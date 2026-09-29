"""Проверка тематичности без сети: сборка промпта и разбор ответа."""

from __future__ import annotations

import pytest

from pelican.weak import topic
from pelican.weak.assess import CORPUS, NEWS, PAPER, Evidence


def test_prompt_puts_subject_at_both_ends_and_drops_corpus() -> None:
    ev = [
        Evidence(1, "2026-01-01", "Image steganography with contourlet transform", PAPER),
        Evidence(-1, "2026-02-01", "Startup raises seed for stego tools", NEWS),
        Evidence(-1, "2026-09-25", "Локальный научный корпус: ядро «steganography»", CORPUS),
    ]
    got = topic.build_prompt("art brut and prison art", "steganographic fusion", ev)
    lines = got.splitlines()
    assert lines[0] == "SUBJECT: art brut and prison art"
    assert "art brut and prison art" in lines[-1]
    assert "[2] (NEWS)" in got
    assert "корпус" not in got


def test_parse_keeps_why_only_with_reference() -> None:
    assert topic.parse({"score": 0, "why": "Работы [1] и [2] — о криптографии"}) == (
        0,
        "Работы [1] и [2] — о криптографии",
    )
    assert topic.parse({"score": 3, "why": "про искусство"}) == (3, "")


def test_parse_rejects_score_out_of_scale() -> None:
    with pytest.raises(ValueError):
        topic.parse({"score": 5, "why": ""})
