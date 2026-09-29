"""Формат шарда: границы времени внутри файла и терпимость к старым файлам.

⚠️ Номер шарда о времени не говорит ничего: `flush` берёт следующее свободное
имя, поэтому `shard-000000` держит свежак ПЕРВОГО прогона, а свежак догона
лежит на две сотни номеров дальше. Отсюда и поля `since`/`until` в файле.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import pytest

from pelican.weak.shards import ShardCorpus, top_k

MODEL = "google/embedding-gemma-300m"


def _write(path: Path, ids: list[int], dates: tuple[str, str] | None) -> None:
    vectors = np.zeros((len(ids), 4), dtype=np.float16)
    vectors[:, 0] = 1.0
    extra = {"since": np.array(dates[0]), "until": np.array(dates[1])} if dates else {}
    np.savez(
        path,
        model=np.array(MODEL),
        ids=np.array(ids, dtype=np.int64),
        vectors=vectors,
        **extra,
    )


def test_dates_survive_the_round_trip(tmp_path: Path) -> None:
    _write(tmp_path / "shard-000000.npz", [1, 2], ("2026-08-28", "2026-09-08"))
    [shard] = ShardCorpus(tmp_path, MODEL).iter_shards()
    assert shard.since == date(2026, 8, 28)
    assert shard.until == date(2026, 9, 8)


def test_old_shard_has_no_dates_and_that_is_not_an_error(tmp_path: Path) -> None:
    """⚠️ `None` — «в файле их нет», а не «шард пуст по времени».

    Двести с лишним шардов написаны до появления полей, и читаться они обязаны
    по-прежнему: иначе правка формата стоила бы пересчёта всего корпуса.
    """
    _write(tmp_path / "shard-000000.npz", [1, 2], None)
    [shard] = ShardCorpus(tmp_path, MODEL).iter_shards()
    assert shard.since is None and shard.until is None


def test_search_works_across_both_shapes(tmp_path: Path) -> None:
    _write(tmp_path / "shard-000000.npz", [10, 11], None)
    _write(tmp_path / "shard-000001.npz", [20, 21], ("2026-01-01", "2026-01-31"))
    query = np.zeros((1, 4), dtype=np.float32)
    query[0, 0] = 1.0
    ids, scores = top_k(ShardCorpus(tmp_path, MODEL), query, k=4)
    assert sorted(int(i) for i in ids[0]) == [10, 11, 20, 21]
    assert scores[0][0] == pytest.approx(1.0)
