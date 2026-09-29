"""Привязка: текст запроса → ближайшие работы научного корпуса.

Одна дорога на два потребителя — часть 1 ТЗ (название технологии из датасета) и
открытый запрос (`trends ask`, фасеты). Порознь они разошлись бы в мелочах, и
замер привязки перестал бы описывать то, что работает в выдаче.

Приём и три правила из замера (`scripts/measure_grounding.py`,
docs/weak-signals.md):

- **dense retrieval** потоковым top-k по шардам (`weak.shards`), без индекса;
- ⚠️ **запрос идёт как есть, без перевода.** Лучшее плечо замера — сырое
  русское название (hit@8 = 28/30); перевод моделью сокращает запрос и тянет
  короткие документы (length bias, корреляция длин +0.59);
- ⚠️ **строки без содержания в выдачу не пускаются** (`weak.corpus.junk_ids`):
  иначе верх занимают словарные статьи вроде «Machine Learning»;
- ⚠️ **без query-префикса EmbeddingGemma** — корпус записан без него.

⚠️ Балл косинуса наружу отдаётся, но порогом не служит: на замере бессмыслица
нулевого контроля набрала 0.614 там, где настоящий запрос — 0.490.
"""

from __future__ import annotations

import base64
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date

import httpx
import numpy as np

from pelican.config import EMB_DIR, settings
from pelican.store import Store
from pelican.embed import embed
from pelican.weak import asof
from pelican.weak.corpus import future_ids, junk_ids
from pelican.weak.shards import ShardCorpus, top_k

log = logging.getLogger("pelican.ground")

#: Потолок одного прохода на сервере шардов. Нативный проход — ~60 с (docs/weak-demo.md
#: §3); запас на холодный диск и на соседний проход, ждущий замка. ⚠️ Догадка.
REMOTE_TIMEOUT_S = 600.0


@dataclass(frozen=True, slots=True)
class Hit:
    signal_id: int
    score: float


def embed_texts(texts: Sequence[str]) -> np.ndarray:
    """Нормированные векторы запросов `[m, dim]` в пространстве корпуса (без префикса)."""
    with httpx.Client(timeout=httpx.Timeout(settings.llm_timeout_s)) as http:
        vectors = embed(http, list(texts), settings.embedding_model, settings.embedding_batch_size)
    return np.asarray(vectors, dtype=np.float32)


def ground(
    store: Store,
    queries: Sequence[str],
    k: int = 20,
    on_shard: Callable[[int, int], None] | None = None,
) -> list[list[Hit]]:
    """k ближайших работ на каждый запрос, в порядке убывания сходства."""
    if not queries:
        return []
    matrix = embed_texts(queries)
    as_of = asof.today() if asof.active() else None
    found = _remote(matrix, k, as_of) if settings.shards_url else None
    if found is None:
        found = nearest(store, matrix, k, on_shard=on_shard, as_of=as_of)
    ids, scores = found
    return [
        [Hit(int(ids[q, r]), float(scores[q, r])) for r in range(k) if ids[q, r] >= 0]
        for q in range(len(queries))
    ]


def nearest(
    store: Store,
    matrix: np.ndarray,
    k: int,
    on_shard: Callable[[int, int], None] | None = None,
    as_of: date | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Проход по шардам этого процесса: (ids `[m, k]`, scores `[m, k]`)."""
    corpus = ShardCorpus(EMB_DIR, settings.embedding_model)
    blocked = junk_ids(store)
    if as_of is not None:
        # Под срезом из поиска убираются работы, которых на ту дату ещё не было.
        blocked = np.union1d(blocked, future_ids(store, as_of))
    return top_k(corpus, matrix, k=k, on_shard=on_shard, exclude=blocked)


def _remote(matrix: np.ndarray, k: int, as_of: date | None) -> tuple[np.ndarray, np.ndarray] | None:
    """Тот же проход в нативном `pelican.shard_server` (docs/weak-demo.md §3).

    ⚠️ Не запущен сервер — `None`, и проход идёт здесь же, через bind mount: втрое
    медленнее, но запрос не падает. Предупреждение в логе — чтобы это было видно.
    Любой другой отказ (таймаут, 5xx) — исключение: повторный проход здесь удвоил бы
    время запроса молча.
    """
    q = np.ascontiguousarray(matrix, dtype=np.float32)
    body = {
        "vectors": base64.b64encode(q.tobytes()).decode(),
        "dim": int(q.shape[1]),
        "k": k,
        "as_of": as_of.isoformat() if as_of else None,
    }
    try:
        r = httpx.post(f"{settings.shards_url.rstrip('/')}/top_k", json=body, timeout=REMOTE_TIMEOUT_S)
    except httpx.ConnectError:
        log.warning("сервер шардов %s недоступен — прохожу шарды сам", settings.shards_url)
        return None
    r.raise_for_status()
    out = r.json()
    return np.asarray(out["ids"], dtype=np.int64), np.asarray(out["scores"], dtype=np.float32)
