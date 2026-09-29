"""Прогон одного открытого запроса для исполнителя очереди (`pelican.api.Worker`).

Замок `model` держится на время прогона: локальная модель одна, и любой другой её
потребитель (докачка векторов, разметка очереди `tech`) ждёт, а не делит GPU пополам.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime

from pelican import jobs, locks
from pelican.config import settings
from pelican.store import Store

log = logging.getLogger("pelican.runner")
MODEL_LOCK = "model"


def run_query(query: str, say: Callable[[str], None]) -> tuple[str, str, str]:
    """Запрос → (имя отчёта, JSON результата, HTML страницы-отчёта)."""
    from pelican.weak import history, page
    from pelican.weak.ask import run as run_ask

    started = datetime.now()
    with locks.hold(MODEL_LOCK), Store(settings.database_url) as store:
        result = run_ask(store, query, on_progress=say)
    with jobs.connect(settings.database_url) as conn:
        past = [r["result"] for r in jobs.reports(conn, limit=500)]
    history.annotate(result, history.prior_runs(past, query, result.started))
    log.info(
        "ответ «%s»: %d сигналов, %d кандидатов, %d источников; модели: %s",
        query, len(result.signals), result.candidates, result.sources_processed,
        ", ".join(f"{m['role']}={m['model']}" for m in result.models) or result.model,
    )
    return f"{started:%Y%m%d-%H%M%S}", page.to_json(result), page.render(result)
