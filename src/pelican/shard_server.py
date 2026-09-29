"""Поиск по шардам векторов нативно на хосте, для `api` в контейнере.

    python -m pelican.shard_server        # 127.0.0.1:8020, из контейнера — host.docker.internal

⚠️ **Зачем отдельный процесс.** Проход по шардам — это чтение всех 23.5 ГБ `shard-*.npz`
на каждый вызов `weak.ground.ground` (три вызова на запрос `ask`). Через bind mount он идёт
втрое медленнее, чем нативно (~165 с против ~60 с, docs/weak-demo.md §3): файлы пересекают
границу VM Docker. Поэтому шарды читает этот процесс на хосте, а `api` в контейнере шлёт
ему только матрицу запросов и получает id и баллы — тот же приём, что с LM Studio.

Маски (`junk_ids`, `future_ids` под срезом) строятся здесь же, по той же базе: гонять по
HTTP 300 тысяч id на каждый вызов незачем. Логика привязки одна — `weak.ground.nearest`.

Слушает только loopback: Docker Desktop пробрасывает `host.docker.internal` на 127.0.0.1
хоста (проверено), наружу порт не открывается.
"""

from __future__ import annotations

import base64
import logging
import os
import threading
import time
from datetime import date

import numpy as np
from fastapi import FastAPI
from pydantic import BaseModel

from pelican.config import settings
from pelican.store import Store
from pelican.weak.ground import nearest

log = logging.getLogger("pelican.shard_server")

PORT = int(os.environ.get("SHARD_SERVER_PORT", "8020"))


class TopKIn(BaseModel):
    #: float32 `[m, dim]` построчно, base64 — точные биты, а не десятичная запись.
    vectors: str
    dim: int
    k: int
    #: Дата среза `weak.asof`, если он активен у вызывающего; иначе `None`.
    as_of: date | None = None


class TopKOut(BaseModel):
    ids: list[list[int]]
    scores: list[list[float]]


app = FastAPI(title="pelican shards", docs_url=None, redoc_url=None)
#: Один проход за раз: параллельные проходы делят диск и идут оба медленнее.
_lock = threading.Lock()


@app.post("/top_k")
def top_k(req: TopKIn) -> TopKOut:
    matrix = np.frombuffer(base64.b64decode(req.vectors), dtype=np.float32).reshape(-1, req.dim)
    # Соединение на проход, а не на процесс: сервер живёт неделями, а перезапуск
    # postgres обрывает долгое соединение молча — до первого промаха кэша масок.
    with _lock, Store(settings.database_url) as store:
        started = time.monotonic()
        ids, scores = nearest(store, matrix, req.k, as_of=req.as_of)
    log.info("%d запросов, k=%d: %.0f с", matrix.shape[0], req.k, time.monotonic() - started)
    return TopKOut(ids=ids.tolist(), scores=scores.astype(float).tolist())


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    from pelican.config import DATA_DIR

    # ⚠️ На стенде процесс поднимает планировщик через `pythonw` (docs/weak-demo.md §1): у
    # него нет ни stdout, ни stderr, и штатный лог uvicorn падает на первой же записи.
    # Поэтому лог — только файл, а конфиг логов uvicorn выключен.
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(message)s",
        filename=DATA_DIR / "shard-server.log",
        encoding="utf-8",
    )
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_config=None)
