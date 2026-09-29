from __future__ import annotations

from datetime import UTC, datetime

from pelican.store import work_id


def test_work_id_matches_source():
    """Id совпадает с тем, что посчитало исходное хранилище (строки взяты оттуда):
    иначе новый сбор задублировал бы перелитые работы."""
    assert work_id("arxiv", "cond-mat/0210014v1", "submissions",
                   datetime(2002, 10, 1, 8, 32, 12)) == 3566413626119188785
    assert work_id("openalex", "W4210564341", "works",
                   datetime(2021, 12, 31)) == 1389550030532087225


def test_work_id_aware_equals_naive_utc():
    naive = datetime(2026, 9, 1, 12, 0)
    assert work_id("arxiv", "x", "submissions", naive) == work_id(
        "arxiv", "x", "submissions", naive.replace(tzinfo=UTC)
    )
