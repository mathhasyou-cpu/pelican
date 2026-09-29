"""Чтение научных векторов из шардов `<DATA_DIR>/emb-science/`.

⚠️ **До этого модуля шарды не читал никто.** `scripts/embed_science.py` их
пишет, `scripts/science_progress.py` считает файлы, а `scripts/search_corpus.py`
ищет совсем по другому корпусу — по `need_embeddings` и своему кэшу
`.search-raw.npz`. То есть 3.2 ГБ векторов лежали без дороги наружу, и эта
дорога — здесь.

## Формат шарда

`shard-NNNNNN.npz`: `ids` (int64), `vectors` (float16, `[n, 768]`), `model`
(строка). Пишутся по `SHARD_ROWS = 20 000` строк, обход корпуса идёт от нового
к старому.

⚠️ **Модель сверяется по содержимому шарда, а не по имени файла.** Смешать
векторы двух моделей — значит получить осмысленные на вид числа без единого
осмысленного соседа, и заметить это будет нечем.

## Приём: потоковый top-k, а не индекс

Корпус не помещается в памяти целиком (2.16 млн × 768 float16 = 3.2 ГБ, и он
растёт), но и индекс под один замер строить рано. Здесь **blocked matrix
multiply со скользящим top-k**: шард читается, умножается на матрицу запросов
разом, из результата наружу выходят только k лучших, шард забывается. Тот же
приём, что в `cluster._pairs_above`, только там порог, а тут k.

Векторы нормированы при записи (`cluster.normalise`), поэтому косинус — это
скалярное произведение, а оно и есть матричное умножение.

⚠️ **Префикса EmbeddingGemma в корпусе нет** — `embed_science.py` пишет через
`cluster.embed`, намеренно, ради сравнимости с `need_embeddings`
(`docs/science-vectors.md`). Значит и запрос обязан идти БЕЗ префикса: иначе
сравниваются два разных пространства.

Потолок приёма — линейный проход по всему корпусу на каждый набор запросов
(~3.2 ГБ чтения). Для замера и для десятка фасетов это секунды; когда запросы
пойдут потоком, следующий шаг — ANN (hnswlib/FAISS), а не более хитрый проход.
"""

from __future__ import annotations

import time
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np

#: Векторы в шардах лежат float16, считаем во float32: matmul во float16 на CPU
#: и медленнее, и теряет точность на длинных суммах.
WORK_DTYPE = np.float32


@dataclass(frozen=True, slots=True)
class Shard:
    """Один файл шарда. `since`/`until` — границы `observed_at` внутри него.

    ⚠️ `None` значит «в файле их нет», а НЕ «шард пустой по времени»: поля
    появились позже первых двух сотен шардов, и решать по ним что-либо можно
    только когда они не `None`. Судить о датах по номеру шарда нельзя вовсе —
    номер это счётчик имён (`scripts/embed_science.py:flush`).
    """

    path: Path
    ids: np.ndarray
    vectors: np.ndarray
    since: date | None = None
    until: date | None = None


def _day(blob: object, key: str) -> date | None:
    """Дата из шарда, если она там есть. Старые шарды этих полей не имеют."""
    if key not in getattr(blob, "files", ()):
        return None
    return date.fromisoformat(str(blob[key]))


class ShardCorpus:
    """Каталог шардов одной модели."""

    def __init__(self, directory: Path, model: str) -> None:
        self.directory = directory
        self.model = model
        self.paths = sorted(directory.glob("shard-*.npz"))
        if not self.paths:
            raise SystemExit(
                f"в {directory} нет ни одного shard-*.npz — сначала scripts/embed_science.py"
            )

    def __len__(self) -> int:
        return len(self.paths)

    def iter_shards(self) -> Iterator[Shard]:
        for path in self.paths:
            with self._open(path) as blob:
                got = str(blob["model"])
                if got != self.model:
                    raise SystemExit(
                        f"{path.name}: векторы модели {got!r}, а ищем моделью {self.model!r} — "
                        "смешивать пространства нельзя"
                    )
                yield Shard(
                    path=path,
                    ids=blob["ids"],
                    vectors=blob["vectors"],
                    since=_day(blob, "since"),
                    until=_day(blob, "until"),
                )

    @staticmethod
    def _open(path: Path):
        """Открыть шард; недописанный (`embed_science.py` пишет `np.savez` прямо в
        целевое имя, и догон идёт параллельно с чтением) — подождать и открыть снова.
        ⚠️ Секунда — догадка: сам `savez` шарда в 20 000 строк короче."""
        try:
            return np.load(path, allow_pickle=False)
        except (zipfile.BadZipFile, ValueError, OSError):
            time.sleep(1.0)
            return np.load(path, allow_pickle=False)

    def rows(self) -> int:
        return sum(int(s.ids.shape[0]) for s in self.iter_shards())


def top_k(
    corpus: ShardCorpus,
    queries: np.ndarray,
    k: int = 20,
    on_shard: object = None,
    exclude: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """(ids `[m, k]`, scores `[m, k]`) — k ближайших документов на каждый запрос.

    `queries` — `[m, dim]`, уже нормированные (`cluster.normalise`).

    Скользящий top-k: держим текущие k лучших на запрос и после каждого шарда
    сливаем их с k лучшими шарда. Полная матрица сходств `m × N` не
    материализуется никогда — ровно по той же причине, что в `cluster`.

    `exclude` — ОТСОРТИРОВАННЫЕ id, которые в выдачу не пускаются
    (`weak.corpus.junk_ids`). Ищутся `searchsorted` по шарду, а не множеством на
    каждую строку: 303 тысячи id против 20 тысяч строк шарда.
    """
    if queries.ndim != 2:
        raise ValueError("queries должен быть [m, dim]")
    q = np.ascontiguousarray(queries, dtype=WORK_DTYPE)
    m = q.shape[0]

    best_ids = np.full((m, k), -1, dtype=np.int64)
    best_scores = np.full((m, k), -np.inf, dtype=WORK_DTYPE)

    for index, shard in enumerate(corpus.iter_shards()):
        vectors = shard.vectors.astype(WORK_DTYPE, copy=False)
        if vectors.shape[1] != q.shape[1]:
            raise SystemExit(
                f"{shard.path.name}: размерность {vectors.shape[1]}, а у запроса {q.shape[1]}"
            )
        sims = q @ vectors.T  # [m, n_shard]
        if exclude is not None and exclude.size:
            slot = np.searchsorted(exclude, shard.ids)
            slot[slot >= exclude.size] = 0
            drop = exclude[slot] == shard.ids
            if drop.any():
                # -inf, а не удаление столбцов: ширина блока остаётся прежней, и
                # argpartition ниже работает без перекладывания матрицы.
                sims[:, drop] = -np.inf
        take = min(k, sims.shape[1])
        part = np.argpartition(-sims, take - 1, axis=1)[:, :take]
        part_scores = np.take_along_axis(sims, part, axis=1)
        part_ids = shard.ids[part]

        merged_scores = np.concatenate([best_scores, part_scores], axis=1)
        merged_ids = np.concatenate([best_ids, part_ids], axis=1)
        order = np.argsort(-merged_scores, axis=1, kind="stable")[:, :k]
        best_scores = np.take_along_axis(merged_scores, order, axis=1)
        best_ids = np.take_along_axis(merged_ids, order, axis=1)

        if callable(on_shard):
            on_shard(index + 1, len(corpus))

    return best_ids, best_scores
