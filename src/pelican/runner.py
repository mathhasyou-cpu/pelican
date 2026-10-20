"""Прогон одного открытого запроса для исполнителя очереди (`pelican.api.Worker`).

Замок `model` держится на время прогона: локальная модель одна, и любой другой её
потребитель (докачка векторов, разметка очереди `tech`) ждёт, а не делит GPU пополам.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime

from pelican import jobs, locks
from pelican.config import settings
from pelican.store import Store

log = logging.getLogger("pelican.runner")
MODEL_LOCK = "model"


def run_query(
    query: str,
    say: Callable[[str], None],
    emit: Callable[[str, dict], None] | None = None,
) -> tuple[str, str, str]:
    """Запрос → (имя отчёта, JSON результата, HTML страницы-отчёта).

    `emit` — хук структурных событий для живого экрана ожидания; без него прогон прежний.
    """
    from pelican.weak import history, page
    from pelican.weak.ask import run as run_ask

    started = datetime.now(UTC)
    hooks = {"on_event": emit} if emit is not None else {}
    with locks.hold(MODEL_LOCK), Store(settings.database_url) as store:
        result = run_ask(store, query, on_progress=say, **hooks)
    with jobs.connect(settings.database_url) as conn:
        past = jobs.history(conn, query)
    history.annotate(result, history.prior_runs(past, query, result.started))
    log.info(
        "ответ «%s»: %d сигналов, %d кандидатов, %d источников; модели: %s",
        query, len(result.signals), result.candidates, result.sources_processed,
        ", ".join(f"{m['role']}={m['model']}" for m in result.models) or result.model,
    )
    return jobs.report_stamp(started), page.to_json(result), page.render(result)
