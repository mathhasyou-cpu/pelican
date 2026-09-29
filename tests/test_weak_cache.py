"""Суточный кэш живого поиска: попадание, промах по сроку, выключатель."""

from __future__ import annotations

import asyncio
import time

from pelican.weak import cache
from pelican.weak.live import LiveDoc


def _doc(title: str) -> LiveDoc:
    return LiveDoc("google_news", title, "https://x/1", "2026-07-01", "TechCrunch", "en")


def _use(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(cache, "DIR", tmp_path / "live-cache")
    monkeypatch.setattr(cache, "ENABLED", True)
    monkeypatch.setattr(cache, "_swept", True)


def test_saved_docs_come_back(tmp_path, monkeypatch) -> None:
    _use(tmp_path, monkeypatch)
    cache.save("google_news:en", "ai agent identity", [_doc("Keycard raises $38M")])
    got = cache.load("google_news:en", "ai agent identity")
    assert got is not None
    assert [d.title for d in got] == ["Keycard raises $38M"]
    # Ключ включает вид источника: тот же текст к другому источнику — промах.
    assert cache.load("habr", "ai agent identity") is None


def test_stale_file_is_a_miss(tmp_path, monkeypatch) -> None:
    _use(tmp_path, monkeypatch)
    cache.save("habr", "q", [_doc("t")])
    path = cache.DIR / f"{cache.key_of('habr', 'q')}.json"
    old = time.time() - cache.TTL_S - 10
    import os

    os.utime(path, (old, old))
    assert cache.load("habr", "q") is None


def test_disabled_cache_neither_reads_nor_writes(tmp_path, monkeypatch) -> None:
    _use(tmp_path, monkeypatch)
    cache.save("habr", "q", [_doc("t")])
    monkeypatch.setattr(cache, "ENABLED", False)
    assert cache.load("habr", "q") is None
    calls = []

    async def fetch():
        calls.append(1)
        return [_doc("fresh")]

    got = asyncio.run(cache.through("habr", "q", fetch))
    assert [d.title for d in got] == ["fresh"]
    assert len(calls) == 1


def test_through_calls_the_source_once(tmp_path, monkeypatch) -> None:
    _use(tmp_path, monkeypatch)
    calls = []

    async def fetch():
        calls.append(1)
        return [_doc("fresh")]

    first = asyncio.run(cache.through("github", "q", fetch))
    second = asyncio.run(cache.through("github", "q", fetch))
    assert [d.title for d in first] == [d.title for d in second] == ["fresh"]
    assert len(calls) == 1
