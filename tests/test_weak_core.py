"""След технологии в корпусе: две меры совпадения, экранирование, строка свидетельства."""

from __future__ import annotations

from pelican.weak.core import Footprint, _match, corpus_line, significant


def test_core_is_matched_by_the_whole_phrase() -> None:
    # Токены подряд, последний префиксом: «learning» ловит и «learnings».
    assert _match("federated learning") == "federated <-> learning:*"
    # Дефис — разделитель токенов: «cnn-lstm» и «CNN LSTM» — одно имя.
    assert _match("cnn-lstm") == "cnn <-> lstm:*"


def test_short_core_is_matched_as_a_token() -> None:
    # ⚠️ `rag` подстрокой находится внутри `fragment` и `leverage`; токен — нет.
    assert _match("rag") == "rag | rags"


def test_long_label_is_matched_by_a_set_of_words() -> None:
    got = _match("security and defense for large language models", loose=True)
    # Служебные слова выброшены, осталось не больше четырёх самых длинных.
    assert got == "defense:* & language:* & models:* & security:*"


def test_single_word_label_keeps_the_phrase_rule() -> None:
    # Одно значимое слово — это не набор, правило набора слов не включается.
    assert _match("neuromorphic", loose=True) == "neuromorphic:*"


def test_footprints_count_in_postgres(store, tmp_path, monkeypatch) -> None:
    """След на живом Postgres: фраза, дефис как пробел, короткое ядро токеном, заглушка
    1 января не даёт год появления."""
    from datetime import datetime

    from pelican.weak import core

    monkeypatch.setattr(core, "CACHE", tmp_path / "footprints.json")
    monkeypatch.setattr(core, "CACHE_STAMP", tmp_path / "footprints.count")
    monkeypatch.setattr(core, "_cache", None)
    monkeypatch.setattr(core, "_checked", False)
    works = [
        (1, "arxiv", "Federated learning at the edge", datetime(2019, 5, 1)),
        (2, "openalex", "Personalised federated learnings for health", datetime(2021, 3, 1)),
        (3, "openalex", "Survey of federated learning", datetime(2016, 1, 1)),  # заглушка
        (4, "arxiv", "A CNN LSTM hybrid", datetime(2018, 2, 2)),
        (5, "arxiv", "Leverage a fragment of RAG pipelines", datetime(2024, 6, 1)),
    ]
    store.insert_works((i, s, t, d, None, None, False) for i, s, t, d in works)
    got = core.footprints(store, ["federated learning", "cnn-lstm", "rag"])
    assert (got["federated learning"].works, got["federated learning"].first_year) == (3, 2019)
    assert got["cnn-lstm"].works == 1
    assert got["rag"].works == 1  # «leverage» и «fragment» не в счёт


def test_significant_drops_service_words_and_short_ones() -> None:
    assert significant("identity and access management for ai agents") == [
        "identity",
        "access",
        "management",
        "agents",
    ]


def test_corpus_line_shows_both_traces() -> None:
    core = Footprint("federated learning", 13575, 2016, 900)
    label = Footprint("federated learning for aml", 2, 2026, 2)
    line = corpus_line(core, label)
    assert line.startswith("CORPUS: the core technology")
    assert "13575 indexed papers since 2016" in line
    assert "federated learning for aml" in line


def test_corpus_line_collapses_when_label_is_the_core() -> None:
    core = Footprint("memory poisoning", 104, 2026, 104)
    line = corpus_line(core, Footprint("Memory Poisoning", 104, 2026, 104))
    assert line.count("CORPUS") == 1
    assert "core technology" not in line


def test_corpus_line_says_when_nothing_is_found() -> None:
    line = corpus_line(
        Footprint("AI", 138536, 2002, 9000), Footprint("ai risk intelligence", 0, None, 0)
    )
    assert "appears in no indexed paper" in line


def test_footprint_cache_resets_only_when_corpus_grows_past_drift(tmp_path, monkeypatch) -> None:
    """⚠️ Кэш следа обязан сам замечать бэкфилл: рост корпуса сверх `CACHE_DRIFT` сбрасывает
    его, суточный сбор — нет (иначе каждый запрос платил бы за след заново)."""
    from pelican.weak import core, corpus

    cache = tmp_path / "footprints.json"
    stamp = tmp_path / "footprints.count"
    monkeypatch.setattr(core, "CACHE", cache)
    monkeypatch.setattr(core, "CACHE_STAMP", stamp)
    monkeypatch.setattr(core, "INDUSTRY_CACHE", tmp_path / "industry.json")
    cache.write_text('{"x": [1, 2020, 1, 9e12]}', encoding="utf-8")
    counts = {"n": 1_000_000}
    monkeypatch.setattr(corpus, "science_count", lambda store: counts["n"])

    core._cache = None
    core._cache_check(None)  # штампа нет — сброс один раз, штамп записан
    assert not cache.exists() and stamp.read_text(encoding="utf-8") == "1000000"

    cache.write_text('{"x": [1, 2020, 1, 9e12]}', encoding="utf-8")
    core._cache = None
    counts["n"] = 1_005_000  # +0.5% — суточный сбор, кэш живёт
    core._cache_check(None)
    assert cache.exists()

    counts["n"] = 1_020_000  # +2% — бэкфилл, кэш сброшен и штамп обновлён
    core._cache_check(None)
    assert not cache.exists() and stamp.read_text(encoding="utf-8") == "1020000"


def test_industry_share_fails_loudly_without_institutions(store, monkeypatch) -> None:
    """Институты авторов в `works` не перелиты: признак падает, а не отдаёт тихий `None`
    (модель, которая его берёт, иначе стала бы молча другой)."""
    import pytest

    from pelican.weak import core

    monkeypatch.setattr(core, "_companies", frozenset({"Google (United States)"}))
    monkeypatch.setattr(core, "_industry_load", dict)
    with pytest.raises(RuntimeError, match="институты"):
        core.industry_share(store, ["federated learning"])


def test_slices_narrow_the_scan_and_the_cache_key(monkeypatch) -> None:
    """Набор срезов корпуса — состояние процесса: без него условие пусто, с ним в проход
    идут arxiv и названные срезы, а ключ кэша различает наборы для тех же слов."""
    from pelican.weak import asof, core

    monkeypatch.setattr(core, "SLICES", None)
    assert core._slice() == ""
    assert core._key("m") == asof.key("m")
    monkeypatch.setattr(core, "SLICES", frozenset({"Energy", "Chemistry"}))
    got = core._slice()
    assert "source = 'arxiv'" in got and "'Chemistry', 'Energy'" in got
    assert core._key("m") != asof.key("m")
    monkeypatch.setattr(core, "SLICES", frozenset({"Energy"}))
    assert core._key("m") != core._key("n")
