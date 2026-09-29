"""Эмбеддинги и сведение формулировок в группы.

`embed` — векторы с того же OpenAI-совместимого сервера, что и генерация
(`/v1/embeddings`), нормированные: косинус тогда — скалярное произведение.

`build_groups` — средняя связь (UPGMA), жадно от самых «центральных» формулировок, плюс
пограничные точки как в DBSCAN (core/border): формулировка у порога от члена ядра
приписывается к его группе уликой, но группу не расширяет и с другими не сливает.
Одиночная связь отвергнута — она цепляет цепочки «A≈B≈C» в одну группу при далёких A и C.
Полная матрица сходств не строится никогда: косинусы считаются блоками, наружу выходят
только пары выше порога.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field

import httpx
import numpy as np

from pelican.config import settings

#: Порог сходства для сведения формулировок: середина полосы, где группы ещё связные.
THRESHOLD = 0.78
#: Группа из одной формулировки — это не группа, а сама формулировка.
MIN_MEMBERS = 2
EMBEDDINGS_PATH = "/embeddings"
#: Потолок одного блока косинусов: число строк блока подбирается под него, а не задаётся
#: константой — иначе блок растёт вместе с числом формулировок.
CHUNK_BYTES = 64 * 1024 * 1024
DTYPE = np.float32


@dataclass(slots=True)
class Group:
    label: str
    members: list[str]
    border: list[str] = field(default_factory=list)
    count: int = 0


def normalise(vector: Sequence[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vector))
    if norm < 1e-12:
        return list(vector)
    return [x / norm for x in vector]


def embed(
    client: httpx.Client, texts: Sequence[str], model: str, batch_size: int
) -> list[list[float]]:
    """Эмбеддинги пакетами. Возвращает нормированные векторы в порядке входа."""
    out: list[list[float]] = []
    for start in range(0, len(texts), batch_size):
        chunk = texts[start : start + batch_size]
        response = client.post(
            settings.llm_base_url.rstrip("/") + EMBEDDINGS_PATH,
            json={"model": model, "input": list(chunk)},
            headers={"Authorization": f"Bearer {settings.llm_api_key}"},
        )
        response.raise_for_status()
        # Порядок ответа контрактом не гарантирован, зато есть index.
        rows = sorted(response.json()["data"], key=lambda d: d.get("index", 0))
        out.extend(normalise(row["embedding"]) for row in rows)
    return out


def neighbours(vectors: Sequence[Sequence[float]] | np.ndarray, threshold: float) -> list[dict[int, float]]:
    """Для каждой формулировки — её соседи выше порога (блочное умножение матриц)."""
    a = np.asarray(vectors, dtype=DTYPE)
    n = len(a)
    out: list[dict[int, float]] = [{} for _ in range(n)]
    if n == 0:
        return out
    rows_per_chunk = max(1, CHUNK_BYTES // (n * DTYPE().itemsize))
    for start in range(0, n, rows_per_chunk):
        block = a[start : start + rows_per_chunk] @ a.T
        for offset in range(len(block)):
            i = start + offset
            row = block[offset]
            row[i] = 0.0  # сама с собой строка сходится всегда — это не сосед
            for j in np.flatnonzero(row >= threshold):
                out[i][int(j)] = float(row[j])
    return out


def build_groups(
    texts: Sequence[str],
    vectors: Sequence[Sequence[float]],
    counts: dict[str, tuple[int, int]],
    threshold: float = THRESHOLD,
) -> list[Group]:
    """Средняя связь, жадно, плюс подбор пограничных. Без сети — тестируемо.

    Порядок обхода — от самых центральных формулировок к периферии: у жадного алгоритма
    результат зависит от того, с чего начать. Два прохода: сначала ядра (их состав и
    ярлыки от второго прохода не зависят), потом пограничные — к группе лучшего соседа.
    Ярлык группы — медоид: самая типичная реальная формулировка, а не сочинённая моделью.
    """
    n = len(texts)
    if n == 0:
        return []
    sim = neighbours(vectors, threshold)
    matrix = np.asarray(vectors, dtype=DTYPE)
    centrality = [sum(row.values()) for row in sim]
    order = sorted(range(n), key=lambda i: (-centrality[i], texts[i]))
    rank = [0] * n
    for position, index in enumerate(order):
        rank[index] = position

    placed = [False] * n
    cores: list[list[int]] = []
    core_of = [-1] * n
    for seed in order:
        if placed[seed]:
            continue
        members = [seed]
        placed[seed] = True
        # Кандидаты — только соседи затравки: это и есть предохранитель от цепочек,
        # расти группа может только вокруг своей затравки. Векторы единичные, поэтому
        # среднее сходство с членами — скалярное произведение с их суммой.
        total = matrix[seed].astype(DTYPE, copy=True)
        for candidate in sorted(sim[seed], key=lambda c: rank[c]):
            if placed[candidate]:
                continue
            if float(matrix[candidate] @ total) >= threshold * len(members):
                members.append(candidate)
                placed[candidate] = True
                total += matrix[candidate]
        if len(members) < MIN_MEMBERS:
            continue
        for index in members:
            core_of[index] = len(cores)
        cores.append(members)

    borders: list[list[int]] = [[] for _ in cores]
    for index in range(n):
        if core_of[index] >= 0:
            continue
        best: tuple[float, int] | None = None
        for other, score in sim[index].items():
            if core_of[other] < 0:
                continue
            key = (-score, rank[other])  # тай-брейк по месту в обходе — детерминизм
            if best is None or key < (-best[0], rank[best[1]]):
                best = (score, other)
        if best is not None:
            borders[core_of[best[1]]].append(index)

    groups: list[Group] = []
    for members, extra in zip(cores, borders, strict=True):
        member_texts = [texts[i] for i in members]
        evidence = member_texts + [texts[i] for i in extra]
        label = member_texts[
            max(
                range(len(members)),
                key=lambda k: sum(sim[members[k]].get(o, 0.0) for o in members if o != members[k]),
            )
        ]
        groups.append(
            Group(
                label=label,
                members=member_texts,
                border=[texts[i] for i in extra],
                count=sum(counts.get(t, (0, 0))[0] for t in evidence),
            )
        )
    groups.sort(key=lambda g: (-g.count, g.label))
    return groups
