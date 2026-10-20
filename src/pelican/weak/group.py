"""Векторы названий приёмов для сведения вариантов написания (`ask`, `twins`).

Модель называет приём в каждой работе по-своему: «speculative decoding with
flash offloading» и «flash-offloaded speculative decoding» — один приём без
единого общего порядка слов. Ключом строка быть не может, сводит эмбеддинг:
группировка — `embed.build_groups` внутри запроса (`ask.run`).

## Векторы — файлом, а не таблицей

Кэш `<DATA_DIR>/emb-tech.npz`: текст → вектор, со знаком модели. Таблица под
него потребовала бы правки схемы и перезапуска сервера (DDL по Quack клиент не
шлёт), а живёт кэш столько же, сколько ветка. Правило то же, что у научных
векторов: смешивать векторы разных моделей нельзя, и знак сверяется при чтении.
"""

from __future__ import annotations

import os
import time
import zipfile
from collections.abc import Callable

import httpx
import numpy as np

from pelican.config import DATA_DIR, settings
from pelican.embed import embed

CACHE = DATA_DIR / "emb-tech.npz"
#: Вектор ярлыка, не встречавшийся столько дней, из кэша уходит: иначе кэш копит каждый
#: ярлык каждого запроса и переписывается целиком на любой промах. ⚠️ Догадка: ярлык
#: повторяется в основном внутри недели прогонов одной темы.
CACHE_TTL_DAYS = 30


def _load_cache(model: str) -> tuple[dict[str, np.ndarray], dict[str, float]]:
    """(текст → вектор, текст → когда последний раз нужен). Битый файл — пустой кэш.

    ⚠️ Кэш — производная: недописанный zip (процесс убит посреди записи) или чужая модель
    читаются как промах, а не роняют каждый следующий запрос до ручного удаления файла.
    """
    if not CACHE.exists():
        return {}, {}
    try:
        with np.load(CACHE, allow_pickle=False) as blob:
            if str(blob["model"]) != model:
                return {}, {}
            texts = blob["texts"].tolist()
            vectors = dict(zip(texts, blob["vectors"], strict=True))
            # Файл без отметок времени (старый формат) — всем «нужен сейчас».
            now = time.time()
            seen = blob["seen"].tolist() if "seen" in blob.files else [now] * len(texts)
            return vectors, dict(zip(texts, seen, strict=True))
    except (OSError, ValueError, KeyError, zipfile.BadZipFile):
        return {}, {}


def _save_cache(model: str, cache: dict[str, np.ndarray], seen: dict[str, float]) -> None:
    """Запись через временный файл и `os.replace`: читатель видит старый файл или новый."""
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    horizon = time.time() - CACHE_TTL_DAYS * 86400
    texts = [t for t in cache if seen.get(t, 0.0) >= horizon]
    tmp = CACHE.with_name(CACHE.name + ".tmp")
    with tmp.open("wb") as fh:
        np.savez(
            fh,
            model=np.array(model),
            texts=np.array(texts),
            vectors=np.asarray([cache[t] for t in texts], dtype=np.float32),
            seen=np.asarray([seen[t] for t in texts], dtype=np.float64),
        )
    os.replace(tmp, CACHE)


def vectors_for(
    texts: list[str], on_progress: Callable[[int, int], None] | None = None
) -> np.ndarray:
    """Нормированные векторы текстов, с кэшем на диске."""
    model = settings.embedding_model
    cache, seen = _load_cache(model)
    now = time.time()
    # Отметка «нужен» обновляется на диске только вместе с промахом: переписывать файл
    # ради одних отметок на каждый запрос — та же цена, от которой TTL и спасает.
    seen.update(dict.fromkeys(texts, now))
    missing = [t for t in texts if t not in cache]
    if missing:
        step = settings.embedding_batch_size * 8
        with httpx.Client(timeout=httpx.Timeout(settings.llm_timeout_s)) as http:
            for start in range(0, len(missing), step):
                chunk = missing[start : start + step]
                got = embed(http, chunk, model, settings.embedding_batch_size)
                cache.update(
                    zip(chunk, (np.asarray(v, dtype=np.float32) for v in got), strict=True)
                )
                if on_progress:
                    on_progress(min(start + step, len(missing)), len(missing))
        _save_cache(model, cache, seen)
    return np.asarray([cache[t] for t in texts], dtype=np.float32)
