"""Сборщики научного корпуса: arxiv и openalex.

Оба отдают факт публикации работы в дату. Суточный сбор и загрузка истории — один и тот
же адаптер с разным окном: отбор в истории обязан совпадать с суточным, иначе свежие дни
окажутся плотнее исторических, и рост читался бы там, где его нет.
"""

from __future__ import annotations

from datetime import timedelta

from pelican.sources.arxiv import ArXiv
from pelican.sources.base import Source
from pelican.sources.openalex import BACKFILL_RETRY, OpenAlex

REGISTRY: dict[str, type] = {"arxiv": ArXiv, "openalex": OpenAlex}


def build(name: str) -> Source:
    try:
        return REGISTRY[name]()
    except KeyError:
        raise KeyError(f"unknown source {name!r}; known: {', '.join(sorted(REGISTRY))}") from None


def build_backfill(name: str, days: int, skip_days: int = 0) -> Source:
    """Источник с окном в `days` суток назад для загрузки истории.

    ⚠️ `skip_days` отодвигает СВЕЖИЙ край окна, чтобы срез начинался там, где кончился
    предыдущий: `-d 260` и следом `-d 520 --skip-days 260` покрывают 520 суток без нахлёста
    и без дыры. Перезапуск после падения тогда не качает заново уже собранное (записи и так
    не дублируются — id вычисляется из данных, — но сеть и бюджет ключа тратились бы).
    """
    if skip_days >= days:
        raise KeyError(f"--skip-days ({skip_days}) обязан быть меньше --days ({days})")
    if name == "arxiv":
        # `submittedDate:[A TO B]` историчен, а дата подачи v1 неизменна: бэкфилл пишет
        # тот же факт, что записал бы суточный прогон того дня.
        return ArXiv(lookback=timedelta(days=days), skip=timedelta(days=skip_days))
    if name == "openalex":
        # `from/to_publication_date` историчны. ⚠️ Ретраи сети у бэкфилла — на час, а не
        # на три попытки: отказ посреди окна уносит всё окно.
        return OpenAlex(
            lookback=timedelta(days=days), skip=timedelta(days=skip_days), retry=BACKFILL_RETRY
        )
    raise KeyError(f"unknown source {name!r}; known: {', '.join(sorted(REGISTRY))}")
