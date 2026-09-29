"""Признаки строки датасета (`weak.dataset`): `None` — «измерить нечем», порядок закреплён.

⚠️ Порядок и имена признаков — контракт между TSV замера, обучением и `weak/model.json`:
переставить признак значит молча применить чужой коэффициент.
"""

from __future__ import annotations

from datetime import date

from pelican.weak.assess import CORPUS, NEWS, PAPER, Assessment, Evidence
from pelican.weak.core import Footprint
from pelican.weak.dataset import (
    ARM_EVIDENCE,
    EMBED_FEATURES,
    FEATURE_LABELS,
    FEATURE_NAMES,
    JUDGMENT_FEATURES,
    LLM_FEATURES,
    LOG_FEATURES,
    MIXED_NEWS,
    MIXED_PAPERS,
    NEWS_KEPT,
    Row,
    age_share,
    candidate,
    embed_features,
    features,
    judgment_features,
    llm_features,
)
from pelican.weak.ground import Hit
from pelican.weak.live import LiveDoc
from pelican.weak.neighbours import Neighbourhood

TODAY = date(2026, 9, 17)


def _doc(title: str, url: str, published: str, publisher: str) -> LiveDoc:
    return LiveDoc("google_news", title, url, published, publisher, "en", domain=publisher)


def test_feature_names_are_stable_and_labelled() -> None:
    sets = (FEATURE_NAMES, LLM_FEATURES, JUDGMENT_FEATURES, EMBED_FEATURES)
    everything = [n for names in sets for n in names]
    assert len(everything) == len(set(everything))  # наборы не пересекаются
    assert set(FEATURE_LABELS) == set(everything)
    assert FEATURE_NAMES[0] == "papers_found"
    # ⚠️ Вердикт модели в признаки не входит: голос как признак выучивает any-yes.
    assert "kind" not in JUDGMENT_FEATURES and "llm_kind" not in JUDGMENT_FEATURES
    assert set(everything) >= LOG_FEATURES


def test_judgment_features_come_from_assessment_or_are_none() -> None:
    a = Assessment("k", "emerging", "пилот", "", [], {"players_named": 3.0, "routine_tool": 0.0})
    j = judgment_features(a)
    assert list(j) == list(JUDGMENT_FEATURES)
    assert j["players_named"] == 3.0 and j["routine_tool"] == 0.0
    assert j["funding_mentioned"] is None  # поля нет — не ноль
    assert all(v is None for v in judgment_features(None).values())


def test_embed_features_are_none_without_neighbourhood_and_derive_gain_and_ratio() -> None:
    assert all(v is None for v in embed_features(Row("pos", "1", "x")).values())
    row = Row("pos", "1", "x", nbr=Neighbourhood(0.8, 0.5, 12, 3, 0.4))
    e = embed_features(row)
    assert list(e) == list(EMBED_FEATURES)
    assert abs(e["nn_sim_gain"] - 0.3) < 1e-9
    assert e["dense_ratio"] == 13 / 4
    # Окно без покрытия → нет и производных.
    e2 = embed_features(Row("pos", "1", "x", nbr=Neighbourhood(0.8, None, 12, None, 0.4)))
    assert e2["nn_sim_gain"] is None and e2["dense_ratio"] is None and e2["dense_last"] == 12.0


def test_empty_row_measures_nothing_rather_than_zero() -> None:
    f = features(Row("pos", "1", "x"), TODAY)
    assert f["papers_found"] == 0.0
    assert f["news_found"] == 0.0
    assert f["cos_max"] is None
    assert f["core_works"] is None
    assert f["core_age_years"] is None
    # След не считался вовсе — это третье состояние, не «ядро не встречается».
    assert f["core_unseen"] is None


def test_core_unseen_is_flag_when_scanned_and_absent() -> None:
    row = Row(
        "pos",
        "1",
        "x",
        core_print=Footprint("x", 0, None, 0),
        label_print=Footprint("x", 0, None, 0),
    )
    f = features(row, TODAY)
    assert f["core_unseen"] == 1.0
    assert f["core_works"] == 0.0
    assert f["core_age_years"] is None
    assert f["core_last_year_share"] is None
    assert f["label_to_core_ratio"] is None


def test_features_from_full_row() -> None:
    row = Row(
        "pos",
        "7",
        "tech",
        hits=[Hit(1, 0.9), Hit(2, 0.7)],
        papers=[Evidence(1, "2026-05-01", "a", PAPER), Evidence(2, "2024-01-01", "b", PAPER)],
        news=[
            _doc("n1", "https://arxiv.org/abs/1", "2026-08-01", "arxiv.org"),
            _doc("n2", "https://techcrunch.com/x", "2026-07-01", "techcrunch.com"),
            _doc("n3", "https://techcrunch.com/y", "2025-01-01", "techcrunch.com"),
        ],
        core_print=Footprint("core", 1000, 2016, 100),
        label_print=Footprint("label", 10, 2025, 8),
    )
    f = features(row, TODAY)
    assert f["papers_found"] == 2.0
    assert f["cos_max"] == 0.9
    assert abs(f["cos_mean"] - 0.8) < 1e-9
    assert f["news_found"] == 3.0
    assert f["publishers_distinct"] == 2.0
    assert f["trust_high_count"] == 1.0  # arxiv — высокая, techcrunch — нет
    assert f["source_kinds_distinct"] >= 2.0
    assert f["recent_share"] == 3 / 5  # три даты из пяти не старше года
    assert f["core_works"] == 1000.0
    assert f["core_age_years"] == 10.0
    assert f["core_unseen"] == 0.0
    assert f["core_last_year_share"] == 0.1
    assert f["label_works"] == 10.0
    assert f["label_last_year_share"] == 0.8
    assert f["label_to_core_ratio"] == 0.01
    # v2: даты и деньги
    assert f["news_first_age_days"] == (TODAY - date(2025, 1, 1)).days
    assert f["news_span_days"] == (date(2026, 8, 1) - date(2025, 1, 1)).days
    assert f["funding_share"] == 0.0
    assert f["paper_first_age_days"] == (TODAY - date(2024, 1, 1)).days
    assert f["paper_recent_share"] == 0.5
    assert list(f) == list(FEATURE_NAMES)


def test_news_are_not_capped_at_five_and_funding_words_count() -> None:
    assert NEWS_KEPT >= 10
    news = [
        _doc(t, f"https://x.com/{i}", "2026-01-01", f"p{i}")
        for i, t in enumerate(
            ["Acme raises $10M seed round", "Acme launches product", "Beta Series A funding",
             "Gamma acquires Delta", "plain headline", "another one", "third", "fourth",
             "fifth", "sixth"]
        )
    ]  # fmt: skip
    f = features(Row("pos", "1", "t", news=news), TODAY)
    assert f["news_found"] == 10.0
    assert f["publishers_distinct"] == 10.0
    assert f["funding_share"] == 0.3
    assert f["news_span_days"] == 0.0


def test_llm_votes_missing_arm_is_none_not_zero() -> None:
    f = llm_features({"corpus": ("emerging", "пилот"), "papers": ("mature", "раннее внедрение")})
    assert f["llm_corpus"] == 1.0 and f["llm_papers"] == 0.0
    assert f["llm_mixed"] is None and f["llm_stage_mixed"] is None
    assert f["llm_stage_corpus"] == 3.0
    assert f["llm_votes"] == 1.0
    assert list(f) == list(LLM_FEATURES)
    assert llm_features({})["llm_votes"] is None


def test_age_share_accepts_bare_year() -> None:
    assert age_share(["2026", "2020-01-01", "мусор"], TODAY) == 0.5
    assert age_share([], TODAY) == 0.0


def test_candidate_arms_differ_in_composition_not_size() -> None:
    papers = [Evidence(i, "2026-01-01", f"p{i}", PAPER) for i in range(1, 7)]
    news = [_doc(f"n{i}", f"https://x.com/{i}", "2026-01-01", "x.com") for i in range(5)]
    row = Row(
        "pos",
        "1",
        "t",
        papers=papers,
        news=news,
        core_print=Footprint("c", 5, 2024, 5),
        label_print=Footprint("l", 1, 2026, 1),
    )
    only_papers = candidate(row, "papers").evidence
    mixed = candidate(row, "mixed").evidence
    corpus = candidate(row, "corpus").evidence
    assert len(only_papers) == ARM_EVIDENCE
    assert len(mixed) == MIXED_NEWS + MIXED_PAPERS == ARM_EVIDENCE
    assert [e.kind for e in mixed] == [NEWS] * MIXED_NEWS + [PAPER] * MIXED_PAPERS
    assert corpus[-1].kind == CORPUS and len(corpus) == ARM_EVIDENCE + 1
