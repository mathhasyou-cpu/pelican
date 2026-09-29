"""API радара слабых сигналов: очередь запросов, статус, отчёты.

    uvicorn pelican.api:app --host 0.0.0.0 --port 8010      # Swagger: /api/docs

Интерфейс (Streamlit, `ui/app.py`) ходит только сюда; считает всё `pelican.weak.ask.run`
в потоке-исполнителе этого же процесса. Разделение слоёв по ТЗ: интерфейс — сбор и
инференс — хранение.

## ⚠️ Запросы идут по одному, остальные ждут в очереди

Локальная модель выполняет вызовы строго по одному, и два параллельных запроса не
ускорились бы, а растянули бы оба вдвое, пробив потолок заказчика в 20 минут. Поэтому
исполнитель один, а новый запрос встаёт в очередь (`pelican.jobs`) и получает место и
ожидаемое время начала; стартует он сам, как только закончится предыдущий. Очередь в
Postgres, поэтому перезапуск контейнера её не теряет.

## ⚠️ Отказ запроса виден в статусе, а не только в логе

Упавший запрос сохраняет текст ошибки в задании, и интерфейс его показывает: молча
зависший прогресс неотличим от долгого запроса.

## ⚠️ Не больше трёх запросов с одного IP

Один адрес держит не больше `jobs.MAX_ACTIVE_PER_CLIENT` заданий в очереди и в работе
вместе, лишний получает 429. Лимит держит `jobs.enqueue` под advisory-блокировкой, а не
проверка здесь. Адрес клиента берётся из заголовка `X-Client-IP`, который ставит интерфейс
(сам API видит только контейнер ui); заголовку можно верить, пока порт API опубликован
только на 127.0.0.1 и снаружи до него доходит лишь интерфейс.
"""

from __future__ import annotations

import logging
import threading
import time
import traceback
from collections.abc import Callable
from datetime import datetime

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from pelican import jobs

log = logging.getLogger("pelican.api")

#: Прогон одного запроса: (запрос, хук прогресса) → (имя отчёта, JSON результата, HTML).
Runner = Callable[[str, Callable[[str], None]], tuple[str, str, str]]

#: Как часто простаивающий исполнитель заглядывает в очередь, если его не разбудили.
IDLE_POLL_S = 5.0


class AskIn(BaseModel):
    query: str


class Worker:
    """Единственный исполнитель очереди."""

    def __init__(self, url: str, runner: Runner) -> None:
        self.url = url
        self.runner = runner
        self.wake = threading.Event()
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._loop, name="pelican-worker", daemon=True)

    def start(self) -> None:
        with jobs.connect(self.url) as conn:
            if n := jobs.requeue_stale(conn):
                log.warning("вернул в очередь %d брошенных заданий", n)
        self.thread.start()

    def _loop(self) -> None:
        conn = jobs.connect(self.url)
        while not self.stop.is_set():
            job = jobs.claim(conn)
            if job is None:
                self.wake.wait(IDLE_POLL_S)
                self.wake.clear()
                continue
            self._run(conn, job)

    def _run(self, conn, job: jobs.Claimed) -> None:
        started = time.monotonic()
        log.info("задание %d: старт «%s»", job.id, job.query)

        def say(msg: str) -> None:
            jobs.say(conn, job.id, f"{time.monotonic() - started:>4.0f} с · {msg}")

        try:
            name, result, page = self.runner(job.query, say)
            jobs.save_report(conn, name, job.query, datetime.now().astimezone(), result, page)
            jobs.finish(conn, job.id, name)
            log.info("задание %d: готово за %.0f с → %s", job.id, time.monotonic() - started, name)
        except Exception as exc:  # noqa: BLE001 — отказ обязан дойти до статуса
            jobs.fail(conn, job.id, f"{type(exc).__name__}: {exc}")
            jobs.say(conn, job.id, traceback.format_exc(limit=3))
            log.exception("задание %d: отказ", job.id)


def create_app(url: str, runner: Runner | None, start_worker: bool = True) -> FastAPI:
    app = FastAPI(title="Радар слабых сигналов", docs_url="/api/docs", redoc_url=None)
    with jobs.connect(url) as conn:
        jobs.apply_schema(conn)
    worker = Worker(url, runner) if runner is not None else None
    if worker is not None and start_worker:
        worker.start()
    app.state.worker = worker

    def conn():
        return jobs.connect(url)

    @app.post("/api/ask", status_code=202, response_model=None)
    def ask(body: AskIn, request: Request) -> dict | JSONResponse:
        """Поставить запрос в очередь. Ответ — место в очереди и ожидаемое начало.

        Запрос, по которому уже есть отчёт, в очередь не встаёт: ответ 200 с именем
        готового отчёта (`cached`). Такой же запрос в очереди или в работе — ответ о нём.
        """
        query = " ".join(body.query.split())
        if len(query) < 3:
            raise HTTPException(422, "запрос короче трёх символов")
        from pelican.weak.ask import REPORT_VERSION

        with conn() as c:
            if name := jobs.cached_report(c, query, REPORT_VERSION):
                return JSONResponse(
                    {"status": "done", "query": query, "report": name, "cached": True}
                )
            if (same := jobs.active_job(c, query)) is not None:
                return jobs.snapshot(c, same)
            try:
                job_id = jobs.enqueue(c, query, _client(request))
            except jobs.Busy:
                raise HTTPException(
                    429,
                    f"с вашего адреса уже {jobs.MAX_ACTIVE_PER_CLIENT} запроса в очереди и в "
                    "работе — дождитесь, пока один из них закончится",
                ) from None
            snap = jobs.snapshot(c, job_id)
        if worker is not None:
            worker.wake.set()
        return snap

    @app.get("/api/jobs/{job_id}")
    def job(job_id: int) -> dict:
        with conn() as c:
            snap = jobs.snapshot(c, job_id)
        if snap is None:
            raise HTTPException(404, "нет такого задания")
        return snap

    @app.get("/api/queue")
    def queue() -> list[dict]:
        with conn() as c:
            return jobs.queue(c)

    @app.get("/api/reports")
    def reports() -> list[dict]:
        with conn() as c:
            rows = jobs.reports(c)
        return [_summary(r) for r in rows]

    @app.get("/api/reports/{name}")
    def report(name: str) -> dict:
        with conn() as c:
            row = jobs.report(c, name)
        if row is None:
            raise HTTPException(404, "нет такого отчёта")
        return row["result"]

    @app.get("/r/{name}", response_class=HTMLResponse)
    def page(name: str) -> str:
        with conn() as c:
            row = jobs.report(c, name)
        if row is None:
            raise HTTPException(404, "нет такого отчёта")
        # Страница собирается из сохранённого результата ТЕКУЩИМ `weak.page`: отчёты истории
        # получают тот же вид, что новые. Не собралась (отчёт старого формата) — сохранённая.
        from pelican.weak import page as page_mod

        try:
            return page_mod.render(page_mod.from_json(row["result"]))
        except Exception:  # noqa: BLE001 — лучше старый вид, чем 500 на отчёте истории
            log.exception("отчёт %s не пересобрался, отдаю сохранённую страницу", name)
            return row["html"]

    return app


def _client(request: Request) -> str:
    """IP пользователя: от интерфейса заголовком, иначе адрес соединения."""
    ip = request.headers.get("x-client-ip", "").strip()
    return ip or (request.client.host if request.client else "")


def _summary(row: dict) -> dict:
    signals = row["result"].get("signals", [])
    return {
        "name": row["name"],
        "query": row["query"],
        "started": row["started"].isoformat(),
        "signals": len(signals),
        # Уверенность — доля проверок, не вероятность модели (та насыщена).
        "confident": sum(1 for s in signals if s.get("confidence", 0) > 0.75),
        "candidates": row["result"].get("candidates", 0),
        "sources_processed": row["result"].get("sources_processed", 0),
    }


def _default_app() -> FastAPI:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    # Строка на каждый вызов модели заглушала бы строки ответов и моделей.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    from pelican.config import settings
    from pelican.runner import run_query  # тяжёлый импорт — только в боевом процессе

    return create_app(settings.database_url, run_query)


def __getattr__(name: str):
    # `uvicorn pelican.api:app` — приложение собирается при первом обращении, а не при
    # импорте модуля: тесты импортируют `create_app` без базы и без модели.
    if name == "app":
        globals()["app"] = _default_app()
        return globals()["app"]
    raise AttributeError(name)
