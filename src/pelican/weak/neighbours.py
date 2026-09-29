"""Признаки из векторов корпуса: соседство ярлыка технологии в `emb-science/`.

Сверх привязки (`weak.ground`: k ближайших работ и их косинусы) из шардов берутся три
семейства чисел, у каждого — имя в литературе:

- **новизна на дату** — сходство с ближайшей работой ДО даты T против сегодняшнего.
  Это first story detection из Topic Detection and Tracking (Allan et al. 1998): история
  нова, если её ближайший прежний сосед далёк. `nn_sim_asof`, `nn_sim_gain`;
- **плотность соседей по окнам** — сколько работ с cos ≥ `TAU` вышло за последние
  `WINDOW_YEARS` лет и за предыдущие столько же. Это след ядра (`weak.core`), но по
  вектору, а не по точной фразе: ярлык из 2–8 слов дословно не пишет никто
  (docs/weak-signals.md). `dense_last`, `dense_prev`, `dense_ratio`;
- **связность соседства** — средняя попарная близость top-`K_COHERENCE` соседей:
  coherence у Rotolo, Hicks & Martin (2015). У зонтика соседи разбросаны, у технологии —
  кучны. `nbr_coherence`.

Всё считается ОДНИМ проходом по шардам (`weak.shards`: blocked matmul со скользящим
top-k), даты работ не читаются вовсе: окно определяется по маскам id «после даты»
(`weak.corpus.future_ids`), которые и так есть у бэктеста.

**Считается по тем векторам, что есть.** Доля работ окна с вектором печатается в прогрессе
и признак не гасит. `None` — только когда в окне нет ни одного вектора: тогда «до T ничего
похожего не было» — артефакт пустоты, а не новизна.

⚠️ `TAU`, `K_DENSE` — догадки (docs/todo.md): первым замером идёт таблица
«τ → сколько соседей у медианной строки».
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date

import numpy as np

from pelican.config import settings
from pelican.store import Store
from pelican.weak.corpus import future_ids, junk_ids, science_count
from pelican.weak.ground import EMB_DIR, embed_texts
from pelican.weak.shards import WORK_DTYPE, ShardCorpus

#: Ширина окна для новизны и плотности, лет. ⚠️ Догадка.
WINDOW_YEARS = 2
#: Порог «работа — сосед ярлыка». ⚠️ Догадка; косинус приговором не служит
#: (docs/weak-signals.md), здесь он только счётчик плотности.
TAU = 0.60
#: Сколько ближайших держать для счёта плотности: счёт `dense_*` — это min(число, K_DENSE).
K_DENSE = 2000
#: Сколько ближайших идёт в связность.
K_COHERENCE = 20

Progress = Callable[[str], None]


@dataclass(frozen=True, slots=True)
class Neighbourhood:
    """Соседство одного ярлыка. `None` — измерить нечем (нет покрытия или соседей)."""

    nn_sim_now: float | None
    nn_sim_asof: float | None
    dense_last: int | None
    dense_prev: int | None
    nbr_coherence: float | None

    @property
    def nn_sim_gain(self) -> float | None:
        if self.nn_sim_now is None or self.nn_sim_asof is None:
            return None
        return self.nn_sim_now - self.nn_sim_asof

    @property
    def dense_ratio(self) -> float | None:
        if self.dense_last is None or self.dense_prev is None:
            return None
        return (self.dense_last + 1) / (self.dense_prev + 1)


@dataclass(frozen=True, slots=True)
class Coverage:
    """Доля работ окна, у которых есть вектор в шардах."""

    last: float
    prev: float
    #: Всё, что до `asof`, — окно новизны: ближайший прежний сосед может быть сколь
    #: угодно старым.
    before: float
    asof: date
    since: date


def _members(sorted_ids: np.ndarray, ids: np.ndarray) -> np.ndarray:
    """Булева маска: какие `ids` есть в отсортированном `sorted_ids`."""
    if sorted_ids.size == 0:
        return np.zeros(ids.shape, dtype=bool)
    slot = np.searchsorted(sorted_ids, ids)
    slot[slot >= sorted_ids.size] = 0
    return sorted_ids[slot] == ids


def _years_back(day: date, years: int) -> date:
    try:
        return day.replace(year=day.year - years)
    except ValueError:  # 29 февраля
        return day.replace(year=day.year - years, day=28)


def neighbourhoods(
    store: Store,
    labels: Sequence[str],
    today: date | None = None,
    say: Progress = lambda _m: None,
    vectors: np.ndarray | None = None,
) -> tuple[list[Neighbourhood], Coverage, np.ndarray]:
    """Соседство каждого ярлыка одним проходом по шардам.

    Возвращает соседства, покрытие окон векторами и сами векторы ярлыков `[m, dim]`
    (они же нужны контрольной линии «embedding probe» в зоопарке).
    """
    today = today or date.today()
    asof = _years_back(today, WINDOW_YEARS)
    since = _years_back(asof, WINDOW_YEARS)
    if not labels:
        return [], Coverage(0.0, 0.0, 0.0, asof, since), np.zeros((0, 0), dtype=WORK_DTYPE)

    q = embed_texts(labels) if vectors is None else np.asarray(vectors, dtype=WORK_DTYPE)
    m = q.shape[0]
    corpus = ShardCorpus(EMB_DIR, settings.embedding_model)
    junk = junk_ids(store)
    cut = np.zeros(0, dtype=np.int64)
    if today < date.today():
        # Под срезом работы после «сегодня» не существуют (look-ahead, `weak.asof`):
        # они уходят в ту же маску, что хабы, и ни соседями, ни покрытием не считаются.
        cut = future_ids(store, today)
        junk = np.union1d(junk, cut)
    say(f"маски окон: работы после {asof} и после {since}...")
    after_asof = future_ids(store, asof)  # окно «последние»: (asof, today]
    after_since = future_ids(store, since)  # (since, today] = prev ∪ last
    if cut.size:
        after_asof = np.setdiff1d(after_asof, cut, assume_unique=True)
        after_since = np.setdiff1d(after_since, cut, assume_unique=True)

    best_ids = np.full((m, K_DENSE), -1, dtype=np.int64)
    best_scores = np.full((m, K_DENSE), -np.inf, dtype=WORK_DTYPE)
    asof_best = np.full(m, -np.inf, dtype=WORK_DTYPE)
    coh_scores = np.full((m, K_COHERENCE), -np.inf, dtype=WORK_DTYPE)
    coh_vectors = np.zeros((m, K_COHERENCE, q.shape[1]), dtype=WORK_DTYPE)
    total = science_count(store) - int(cut.size)
    seen_last = 0
    seen_prev = 0
    seen_all = 0

    for index, shard in enumerate(corpus.iter_shards()):
        vectors_ = shard.vectors.astype(WORK_DTYPE, copy=False)
        sims = q @ vectors_.T
        drop = _members(junk, shard.ids)
        if drop.any():
            sims[:, drop] = -np.inf
        in_last = _members(after_asof, shard.ids)
        in_prev = _members(after_since, shard.ids) & ~in_last
        seen_last += int(in_last.sum())
        seen_prev += int(in_prev.sum())
        seen_all += int(shard.ids.shape[0] - _members(cut, shard.ids).sum())

        # Новизна на дату: лучший сосед среди работ ДО asof (не in_last).
        old = sims.copy()
        old[:, in_last] = -np.inf
        asof_best = np.maximum(asof_best, old.max(axis=1))

        take = min(K_DENSE, sims.shape[1])
        part = np.argpartition(-sims, take - 1, axis=1)[:, :take]
        part_scores = np.take_along_axis(sims, part, axis=1)
        part_ids = shard.ids[part]
        merged_scores = np.concatenate([best_scores, part_scores], axis=1)
        merged_ids = np.concatenate([best_ids, part_ids], axis=1)
        order = np.argsort(-merged_scores, axis=1, kind="stable")[:, :K_DENSE]
        best_scores = np.take_along_axis(merged_scores, order, axis=1)
        best_ids = np.take_along_axis(merged_ids, order, axis=1)

        # Связность: держим векторы K_COHERENCE лучших.
        take_c = min(K_COHERENCE, sims.shape[1])
        part_c = np.argpartition(-sims, take_c - 1, axis=1)[:, :take_c]
        part_c_scores = np.take_along_axis(sims, part_c, axis=1)
        merged_c = np.concatenate([coh_scores, part_c_scores], axis=1)
        merged_v = np.concatenate([coh_vectors, vectors_[part_c]], axis=1)
        order_c = np.argsort(-merged_c, axis=1, kind="stable")[:, :K_COHERENCE]
        coh_scores = np.take_along_axis(merged_c, order_c, axis=1)
        coh_vectors = np.take_along_axis(merged_v, order_c[:, :, None], axis=1)
        if (index + 1) % 50 == 0:
            say(f"шардов {index + 1}/{len(corpus)}")

    n_last = int(after_asof.size)
    n_prev = int(after_since.size - after_asof.size)
    n_before = max(total - n_last, 0)
    coverage = Coverage(
        last=seen_last / n_last if n_last else 0.0,
        prev=seen_prev / n_prev if n_prev else 0.0,
        before=(seen_all - seen_last) / n_before if n_before else 0.0,
        asof=asof,
        since=since,
    )
    say(
        f"покрытие векторами: последние {WINDOW_YEARS} г. {coverage.last:.0%} "
        f"({seen_last} из {n_last}), предыдущие {coverage.prev:.0%} ({seen_prev} из {n_prev}), "
        f"всё до {asof} {coverage.before:.0%} ({seen_all - seen_last} из {n_before})"
    )

    out: list[Neighbourhood] = []
    for i in range(m):
        ids_i = best_ids[i]
        sc_i = best_scores[i]
        valid = ids_i >= 0
        close = valid & (sc_i >= TAU)
        last_mask = close & _members(after_asof, ids_i)
        prev_mask = close & _members(after_since, ids_i) & ~last_mask
        now = float(sc_i[0]) if valid.any() and np.isfinite(sc_i[0]) else None
        asof_sim = float(asof_best[i]) if np.isfinite(asof_best[i]) else None
        coh_valid = np.isfinite(coh_scores[i])
        coherence = None
        if coh_valid.sum() >= 2:
            v = coh_vectors[i][coh_valid]
            g = v @ v.T
            k = g.shape[0]
            coherence = float((g.sum() - np.trace(g)) / (k * (k - 1)))
        out.append(
            Neighbourhood(
                nn_sim_now=now,
                nn_sim_asof=asof_sim if seen_all > seen_last else None,
                dense_last=int(last_mask.sum()) if seen_last else None,
                dense_prev=int(prev_mask.sum()) if seen_prev else None,
                nbr_coherence=coherence,
            )
        )
    return out, coverage, q
