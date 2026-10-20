"""Кэш векторов ярлыков: битый файл — промах, а не отказ каждого запроса; старое уходит."""

from __future__ import annotations

import time

import numpy as np

from pelican.weak import group


def test_broken_cache_reads_as_empty(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(group, "CACHE", tmp_path / "emb-tech.npz")
    group.CACHE.write_bytes(b"PK\x03\x04 half a zip")
    assert group._load_cache("m") == ({}, {})


def test_cache_round_trip_drops_stale_labels(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(group, "CACHE", tmp_path / "emb-tech.npz")
    now = time.time()
    vec = np.ones(4, dtype=np.float32)
    seen = {"fresh": now, "stale": now - (group.CACHE_TTL_DAYS + 1) * 86400}
    group._save_cache("m", {"fresh": vec, "stale": vec}, seen)
    cache, got_seen = group._load_cache("m")
    assert list(cache) == ["fresh"] and got_seen["fresh"] == now
    assert not (tmp_path / "emb-tech.npz.tmp").exists()
    # Чужая модель — промах, а не чужие векторы.
    assert group._load_cache("другая") == ({}, {})
