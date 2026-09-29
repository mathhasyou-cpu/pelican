"""Стадия `tech-group`: свести варианты написания одного приёма в группу.

Модель называет приём в каждой работе по-своему: «speculative decoding with
flash offloading» и «flash-offloaded speculative decoding» — один приём без
единого общего порядка слов. Ключом строка быть не может, сводит эмбеддинг.

## Ядро — то же, что у ветки болей, и не копируется

`pipeline/cluster.build_groups` целиком: разрежённое соединение по сходству
(матрица не материализуется), **средняя связь (UPGMA)**, второй проход по
пограничным точкам (DBSCAN core/border), ярлык — медоид. Замеры и причины — в
[cluster](../../docs/cluster.md). Здесь только другая единица и другие счётчики.

⚠️ `build_groups` знает про два счётчика (`stuck`, `shipping`), а у приёма их
три. Ядру отдаются `proposes`/`uses` — ему они нужны лишь для отчёта, на
связывание не влияют, — а все три счётчика группы пересчитываются здесь по
членам заново. Иначе `reviews` — признак зрелости — терялся бы молча.

⚠️ **Порог 0.78 здесь — ДОГАДКА.** Он замерен на формулировках работ, а названия
приёмов короче и гуще аббревиатурами ([todo](../../docs/todo.md) §60).

## Векторы — файлом, а не таблицей

Кэш `<DATA_DIR>/emb-tech.npz`: текст → вектор, со знаком модели. Таблица под
него потребовала бы правки схемы и перезапуска сервера (DDL по Quack клиент не
шлёт), а живёт кэш столько же, сколько ветка. Правило то же, что у научных
векторов: смешивать векторы разных моделей нельзя, и знак сверяется при чтении.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date

import httpx
import numpy as np

from pelican.config import DATA_DIR, settings
from pelican.store import Store
from pelican.embed import THRESHOLD, build_groups, embed

CACHE = DATA_DIR / "emb-tech.npz"


@dataclass(slots=True)
class TechGroup:
    label: str
    members: list[str]
    border: list[str] = field(default_factory=list)
    proposes: int = 0
    uses: int = 0
    reviews: int = 0
    first_seen: date | None = None
    last_seen: date | None = None

    @property
    def works(self) -> int:
        return self.proposes + self.uses + self.reviews


@dataclass(slots=True)
class GroupStats:
    techs: int = 0
    embedded: int = 0
    groups: int = 0
    grouped: int = 0
    border: int = 0


def _load_cache(model: str) -> dict[str, np.ndarray]:
    if not CACHE.exists():
        return {}
    with np.load(CACHE, allow_pickle=False) as blob:
        if str(blob["model"]) != model:
            # Не падаем: кэш — производная, и пересчитать его дешевле, чем
            # объяснять пользователю, что делать с чужими векторами.
            return {}
        return dict(zip(blob["texts"].tolist(), blob["vectors"], strict=True))


def _save_cache(model: str, cache: dict[str, np.ndarray]) -> None:
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    texts = list(cache)
    np.savez(
        CACHE,
        model=np.array(model),
        texts=np.array(texts),
        vectors=np.asarray([cache[t] for t in texts], dtype=np.float32),
    )


def vectors_for(
    texts: list[str], on_progress: Callable[[int, int], None] | None = None
) -> np.ndarray:
    """Нормированные векторы текстов, с кэшем на диске."""
    model = settings.embedding_model
    cache = _load_cache(model)
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
        _save_cache(model, cache)
    return np.asarray([cache[t] for t in texts], dtype=np.float32)


def group_techs(
    store: Store,
    threshold: float = THRESHOLD,
    on_progress: Callable[[int, int], None] | None = None,
) -> tuple[list[TechGroup], GroupStats]:
    model = settings.llm_model
    stats = GroupStats()
    rows = store.distinct_techs(model)
    stats.techs = len(rows)
    if not rows:
        store.replace_tech_groups(settings.embedding_model, threshold, [])
        return [], stats

    # Сводим без учёта регистра: «Speculative Decoding» и «speculative decoding»
    # — одна строка, и платить за неё двумя векторами незачем.
    by_text: dict[str, tuple[int, int, int]] = {}
    for tech, proposes, uses, reviews in rows:
        key = tech.strip().lower()
        p, u, r = by_text.get(key, (0, 0, 0))
        by_text[key] = (p + proposes, u + uses, r + reviews)
    texts = sorted(by_text)

    vectors = vectors_for(texts, on_progress)
    stats.embedded = len(texts)

    core = build_groups(
        texts,
        vectors,
        counts={t: (by_text[t][0], by_text[t][1]) for t in texts},
        threshold=threshold,
    )

    spans_raw = store.tech_spans(model)
    spans: dict[str, tuple[date, date]] = {}
    for tech, (lo, hi) in spans_raw.items():
        key = tech.strip().lower()
        if key in spans:
            spans[key] = (min(spans[key][0], lo), max(spans[key][1], hi))
        else:
            spans[key] = (lo, hi)

    groups: list[TechGroup] = []
    for g in core:
        everyone = [*g.members, *g.border]
        firsts = [spans[t][0] for t in everyone if t in spans]
        lasts = [spans[t][1] for t in everyone if t in spans]
        groups.append(
            TechGroup(
                label=g.label,
                members=list(g.members),
                border=list(g.border),
                proposes=sum(by_text[t][0] for t in everyone),
                uses=sum(by_text[t][1] for t in everyone),
                reviews=sum(by_text[t][2] for t in everyone),
                first_seen=min(firsts) if firsts else None,
                last_seen=max(lasts) if lasts else None,
            )
        )

    store.replace_tech_groups(settings.embedding_model, threshold, groups)
    stats.groups = len(groups)
    stats.grouped = sum(len(g.members) for g in groups)
    stats.border = sum(len(g.border) for g in groups)
    return groups, stats
