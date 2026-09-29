"""Сколько стоит проход по шардам векторов: нативно против контейнера.

    python scripts/measure_shard_scan.py                          # на хосте
    docker compose exec api python scripts/measure_shard_scan.py  # в контейнере

Поиск ближайших работ (`weak.ground`) — полный проход по шардам на каждый запрос. В
контейнере шарды приходят через bind mount с диска Windows, и у Docker Desktop это
медленный путь (файлы идут через границу VM). Здесь меряется только сам проход: векторы
запроса случайные, модель не вызывается, поэтому числа сравнимы между запусками.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from pelican.config import EMB_DIR, settings  # noqa: E402
from pelican.weak.shards import ShardCorpus, top_k  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--queries", type=int, default=6, help="векторов запроса (фасетов)")
    ap.add_argument("--k", type=int, default=30)
    args = ap.parse_args()
    rng = np.random.default_rng(0)
    matrix = rng.standard_normal((args.queries, 768)).astype(np.float32)
    matrix /= np.linalg.norm(matrix, axis=1, keepdims=True)
    corpus = ShardCorpus(EMB_DIR, settings.embedding_model)
    shards = {"n": 0}

    def on_shard(done: int, total: int) -> None:
        shards["n"] = total

    t = time.perf_counter()
    top_k(corpus, matrix, k=args.k, on_shard=on_shard, exclude=np.empty(0, dtype=np.int64))
    print(f"{EMB_DIR}: {shards['n']} шардов, {time.perf_counter() - t:.1f} с")
    return 0


if __name__ == "__main__":
    sys.exit(main())
