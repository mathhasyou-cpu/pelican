"""Источники шире IT (`weak.extra`): разбор реальных ответов, срез времени, три исхода."""

from __future__ import annotations

import json
from datetime import date

from conftest import fixture

from pelican.weak import asof, extra, layers
from pelican.weak.ask import AskResult, Signal, attach_extra
from pelican.weak.kinds import EMERGING
from pelican.weak.live import LiveDoc
from pelican.weak.trust import HIGH, PREPRINT, classify


def test_nih_grants_parse_with_amount_and_org() -> None:
    docs = extra.parse_nih(json.loads(fixture("nih_reporter.json")))
    assert len(docs) == 3
    d = docs[0]
    assert d.source == extra.NIH and d.url.startswith("https://reporter.nih.gov/project-details/")
    assert d.published[:4] == "2026" and d.publisher.startswith("грант NIH")
    assert d.snippet.startswith("грант NIH $") and d.trust.level == HIGH


def test_preprints_parse_and_are_trusted_as_preprints() -> None:
    docs = extra.parse_europepmc(json.loads(fixture("europepmc_preprints.json")))
    assert docs and all(d.source == extra.PREPRINT for d in docs)
    square = next(d for d in docs if "Research Square" in d.publisher)
    assert square.url.startswith("https://doi.org/10.21203/")
    assert square.trust.kind == PREPRINT
    assert classify("www.biorxiv.org").kind == PREPRINT


def test_trials_parse_titles_and_sponsor() -> None:
    docs = extra.parse_trials(json.loads(fixture("clinicaltrials_studies.json")))
    assert len(docs) == 3 and all("rganoid" in d.title for d in docs)
    assert docs[0].url.startswith("https://clinicaltrials.gov/study/NCT")
    assert docs[0].trust.level == HIGH


def test_yc_search_needs_all_words_and_respects_the_cut() -> None:
    companies = json.loads(fixture("yc_companies.json"))
    # «Drug discovery» стоит в описании подряд; «screening drugs against organoids» — нет.
    found = extra.search_yc(companies, "drug discoveries", date(2026, 10, 1))
    assert [d.title.split(":")[0] for d in found] == ["Rosebud Biosciences"]
    assert found[0].publisher == "Y Combinator Winter 2022"
    assert extra.search_yc(companies, "drug organoid", date(2026, 10, 1)) == []
    # Запущенная после среза компания срезу не видна.
    assert extra.search_yc(companies, "drug discovery", date(2020, 1, 1)) == []
    # Одно общее слово — не поиск.
    assert extra.search_yc(companies, "organoid", date(2026, 10, 1)) == []


def test_window_follows_the_cut() -> None:
    asof.AS_OF = date(2018, 1, 1)
    try:
        start, end = extra._window()
    finally:
        asof.AS_OF = None
    assert end == date(2018, 1, 1) and (end - start).days == extra.WINDOW_DAYS


def _sig(label: str) -> Signal:
    return Signal(label=label, title=label, kind=EMERGING, kind_label="", stage="",
                  confidence=0.5, checks=[], why="", papers=1)


def test_attach_extra_counts_sources_and_reports_silence(monkeypatch) -> None:
    grant = LiveDoc(extra.NIH, "Organoid chip grant", "https://reporter.nih.gov/project-details/1",
                    "2026-05-01", "грант NIH, MIT", "en", domain="reporter.nih.gov")
    trial = LiveDoc(extra.TRIALS, "Organoid trial", "https://clinicaltrials.gov/study/NCT1",
                    "2026-03-01", "Mayo", "en", domain="clinicaltrials.gov")

    def fake(phrases):
        return [extra.Extra({extra.NIH: [grant], extra.PREPRINT: [], extra.TRIALS: [trial],
                             extra.YC: None}) for _ in phrases]

    monkeypatch.setattr(extra, "gather", fake)
    sig, ev, errors = _sig("organ-on-chip"), [], {}
    attach_extra([(sig, ev)], errors, lambda _m: None)
    assert sig.extra == {extra.NIH: 1, extra.PREPRINT: 0, extra.TRIALS: 1, extra.YC: 0}
    assert [s.n for s in sig.sources] == [1, 2] and len(ev) == 2
    assert errors == {extra.YC: "не ответил по 1 из 1 кандидатов"}
    # Грант и испытание — два независимых пути: это уже слабый сигнал, а не «наблюдать».
    lay = layers.classify([], {}, sig.papers, None, sig.extra)
    assert lay.name == layers.WEAK and "грантов: 1" in lay.reason
    assert isinstance(AskResult("q", "q", [], "d").watch, list)
