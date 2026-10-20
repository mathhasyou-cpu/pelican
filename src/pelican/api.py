"""API радара слабых сигналов: очередь запросов, статус, отчёты.

    uvicorn pelican.api:app --host 0.0.0.0 --port 8010      # Swagger: /api/docs

Интерфейс — веб-интерфейс `web/`, раздаётся этим же приложением с `/` и ходит только
сюда. Считает всё `pelican.weak.ask.run` в
потоке-исполнителе этого же процесса. Разделение слоёв по ТЗ: интерфейс — сбор и
инференс — хранение.

## ⚠️ Запросы идут по одному, остальные ждут в очереди

Локальная модель выполняет вызовы строго по одному, и два параллельных запроса не
ускорились бы, а растянули бы оба вдвое, пробив потолок заказчика в 20 минут. Поэтому
исполнитель один, а новый запрос встаёт в очередь (`pelican.jobs`) и получает место и
ожидаемое время начала; стартует он сам, как только закончится предыдущий. Очередь в
Postgres, поэтому перезапуск контейнера её не теряет.

## ⚠️ Отказ запроса виден в статусе, а не только в логе

Упавший запрос сохраняет ошибку в задании, и интерфейс её показывает: молча
зависший прогресс неотличим от долгого запроса. `error` — фраза для пользователя
(`failure.explain`), `error_detail` — сырой текст исключения.

## ⚠️ Не больше трёх запросов с одного IP

Один адрес держит не больше `jobs.MAX_ACTIVE_PER_CLIENT` заданий в очереди и в работе
вместе, лишний получает 429. Лимит держит `jobs.enqueue` под advisory-блокировкой, а не
проверка здесь. Адрес клиента (`_client`): левый адрес `X-Forwarded-For`, иначе адрес
соединения. API открыт наружу через Funnel, а Funnel перезаписывает `X-Forwarded-For`
адресом источника (`Header.Set` в `ipn/ipnlocal/serve.go`), прочие заголовки пропускает
как есть — поэтому других заголовков с адресом не читать. Заголовку можно верить, пока
порт API опубликован только на 127.0.0.1.
"""

from __future__ import annotations

import contextlib
import logging
import os
import threading
import time
import traceback
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from pydantic import BaseModel
from starlette.middleware.gzip import GZipMiddleware

from pelican import events, failure, jobs

log = logging.getLogger("pelican.api")

#: Прогон одного запроса: (запрос, хук прогресса, emit=хук событий) → (имя отчёта, JSON
#: результата, HTML). Контракт событий — `pelican.events`.
Runner = Callable[..., tuple[str, str, str]]

#: Каталог собранного веб-интерфейса (`npm run build` в `web/` → `web/dist`): `WEB_DIR`, иначе
#: `web/dist` в рабочем каталоге (образ: /app/web/dist), иначе рядом с исходниками
#: (`pelican/web/dist` в репозитории). Нет сборки — API без интерфейса.
#: ⚠️ Не `web/` без `dist`: там исходный `index.html` Vite, браузер получил бы пустую страницу.
WEB_CANDIDATES = (
    os.environ.get("WEB_DIR", ""),
    str(Path.cwd() / "web" / "dist"),
    str(Path(__file__).resolve().parents[2] / "web" / "dist"),
)

#: Как часто простаивающий исполнитель заглядывает в очередь, если его не разбудили.
#: ⚠️ Догадка, не замер (docs/todo.md).
IDLE_POLL_S = 5.0
#: Пауза перед переподключением к Postgres после обрыва: перезапуск контейнера базы
#: занимает секунды, и долбить его чаще незачем. ⚠️ Догадка.
RECONNECT_S = 5.0
#: Как часто сторож проверяет поток исполнителя и зависшее задание.
#: ⚠️ Догадка, не замер (docs/todo.md).
WATCHDOG_S = 60.0
#: Задание `running` без новой строки прогресса дольше стольких типичных прогонов
#: считается зависшим и помечается `failed`. Стадии `ask.run` пишут прогресс каждые
#: секунды–минуты, самая длинная молчащая (проход по шардам) — ~3 мин при прогоне ~17 мин.
#: ⚠️ Догадка, не замер (docs/todo.md).
STUCK_RUNS = 2.0
#: Соединений в пуле HTTP-маршрутов: запросы короткие, вкладок — единицы. ⚠️ Догадка.
POOL_MAX = 8


class AskIn(BaseModel):
    query: str


class Worker:
    """Единственный исполнитель очереди и его сторож.

    ⚠️ Поток исполнителя не должен умирать ни от чего, кроме `stop`: снаружи мёртвый
    исполнитель неотличим от занятого, API продолжает принимать задания, которые никто не
    возьмёт. Поэтому цикл ловит любой отказ (в том числе обрыв Postgres) и переподключается,
    а сторож перезапускает поток, если тот всё же погиб, и снимает задание, у которого
    прогресс давно не двигался.
    """

    def __init__(self, url: str, runner: Runner) -> None:
        self.url = url
        self.runner = runner
        self.wake = threading.Event()
        self.stop = threading.Event()
        #: `time.time()` последнего обращения к очереди — для `/api/health`.
        self.beat = 0.0
        self.restarts = 0
        self.thread = self._spawn()
        self.watchdog = threading.Thread(target=self._watch, name="pelican-watchdog", daemon=True)

    def _spawn(self) -> threading.Thread:
        return threading.Thread(target=self._loop, name="pelican-worker", daemon=True)

    def start(self) -> None:
        with jobs.connect(self.url) as conn:
            if n := jobs.requeue_stale(conn):
                log.warning("вернул в очередь %d брошенных заданий", n)
        self.thread.start()
        self.watchdog.start()

    def alive(self) -> bool:
        return self.thread.is_alive()

    def _loop(self) -> None:
        conn = None
        while not self.stop.is_set():
            try:
                if conn is None or conn.closed:
                    conn = jobs.connect(self.url)
                self.beat = time.time()
                job = jobs.claim(conn)
                if job is None:
                    self.wake.wait(IDLE_POLL_S)
                    self.wake.clear()
                    continue
                self._run(conn, job)
            except Exception:  # noqa: BLE001 — поток живёт, пока не попросят остановиться
                log.exception("исполнитель: отказ очереди, переподключаюсь через %.0f с", RECONNECT_S)
                _close(conn)
                conn = None
                self.stop.wait(RECONNECT_S)
        _close(conn)

    def _run(self, conn, job: jobs.Claimed) -> None:
        started = time.monotonic()
        began = datetime.now().astimezone()
        log.info("задание %d: старт «%s»", job.id, job.query)

        def say(msg: str) -> None:
            # Прогресс — не условие результата: оборванное соединение не должно ронять
            # прогон посреди 15 минут работы. Строка теряется, прогон идёт дальше.
            try:
                jobs.say(conn, job.id, f"{time.monotonic() - started:>4.0f} с · {msg}")
            except Exception:  # noqa: BLE001
                log.warning("задание %d: прогресс не записан: %s", job.id, msg)

        def emit(kind: str, data: dict) -> None:
            # ⚠️ Событие — витрина, а не прогон: сбой записи не должен ронять 20 минут счёта.
            try:
                jobs.emit(conn, job.id, time.monotonic() - started, kind, data)
            except Exception:  # noqa: BLE001
                log.exception("задание %d: событие %s не записалось", job.id, kind)
            # Граница стадии — место отмены: посреди стадии поток держит вызов модели.
            # `save` не прерывается — отчёт уже посчитан.
            if kind == "stage" and data.get("id") != events.SAVE and self._cancel_requested(conn, job.id):
                raise jobs.Cancelled(job.id)

        try:
            name, result, page = self.runner(job.query, say, emit=emit)
            emit("stage", events.stage(events.SAVE))
            # Время СТАРТА прогона: колонка `started` — «когда запущен», а не когда записан.
            self._with_conn(conn, lambda c: (
                jobs.save_report(c, name, job.query, began, result, page),
                jobs.finish(c, job.id, name),
            ))
            emit("done", {"report": name})
            log.info("задание %d: готово за %.0f с → %s", job.id, time.monotonic() - started, name)
        except jobs.Cancelled:
            log.info("задание %d: отменено автором через %.0f с", job.id, time.monotonic() - started)
            self._with_conn(conn, lambda c: jobs.cancelled(c, job.id))
        except (Exception, SystemExit) as exc:  # noqa: BLE001 — отказ обязан дойти до статуса
            log.exception("задание %d: отказ", job.id)
            error, detail = failure.explain(exc), f"{type(exc).__name__}: {exc}"
            trace = traceback.format_exc(limit=3)
            self._with_conn(conn, lambda c: (jobs.fail(c, job.id, error, detail), jobs.say(c, job.id, trace)))
            emit("failed", {"error": error, "error_detail": detail})

    def _cancel_requested(self, conn, job_id: int) -> bool:
        # Сбой чтения флага — не отмена: двадцать минут счёта не бросаются из-за обрыва.
        try:
            return jobs.cancel_requested(conn, job_id)
        except Exception:  # noqa: BLE001
            log.warning("задание %d: флаг отмены не прочитан", job_id)
            return False

    def _with_conn(self, conn, write: Callable) -> None:
        """Запись итога задания; соединение задания оборвалось — новым соединением."""
        try:
            write(conn)
        except Exception:  # noqa: BLE001
            log.warning("соединение задания оборвалось, пишу итог новым")
            with jobs.connect(self.url) as fresh:
                write(fresh)

    def _watch(self) -> None:
        while not self.stop.wait(WATCHDOG_S):
            try:
                self.check()
            except Exception:  # noqa: BLE001 — сторож не должен падать от недоступной базы
                log.exception("сторож: проверка не удалась")

    def check(self) -> None:
        """Один обход сторожа: перезапустить мёртвый поток, снять зависшее задание."""
        if not self.thread.is_alive() and not self.stop.is_set():
            self.restarts += 1
            log.error("исполнитель умер — перезапускаю (%d-й раз)", self.restarts)
            self.thread = self._spawn()
            self.thread.start()
        with jobs.connect(self.url) as conn:
            limit = STUCK_RUNS * jobs.typical_run_s(conn)
            for job_id in jobs.fail_stuck(conn, limit):
                log.error("задание %d: прогресс стоит дольше %.0f с — снято", job_id, limit)
            jobs.prune_events(conn)


def create_app(url: str, runner: Runner | None, start_worker: bool = True) -> FastAPI:
    app = FastAPI(title="Радар слабых сигналов", docs_url="/api/docs", redoc_url=None)
    # Сжатие всего текстового — JSON отчёта, HTML/JS/CSS интерфейса; бинарное (картинки,
    # шрифты, архивы) Starlette исключает сам. Уровень 6 — дефолт zlib: у 9 выигрыш на
    # крупном JSON копеечный при заметно большем CPU. Меньше 1000 байт сжимать незачем.
    app.add_middleware(GZipMiddleware, minimum_size=1000, compresslevel=6)

    @app.middleware("http")
    async def same_origin_only(request: Request, call_next):
        """Изменяющий запрос — только со своей страницы (PQA-020), и заголовки безопасности (QP-07).

        Межсайтовая подделка запроса: чужая страница не должна ставить и снимать задания из
        браузера пользователя (его адрес — его квота и его право отмены). Приём — OWASP CSRF
        Prevention Cheat Sheet, «Fetch Metadata headers» с запасной проверкой `Origin` для
        браузеров без `Sec-Fetch-Site` (web.dev/articles/fetch-metadata). Токен не нужен:
        интерфейс и API — один источник. Запрос без обоих заголовков — не браузер (curl,
        скрипты) и пропускается.
        """
        if request.method not in SAFE_METHODS and _cross_site(request):
            return JSONResponse({"detail": "Запрос с чужого сайта отклонён."}, status_code=403)
        response = await call_next(request)
        response.headers.update(SECURITY_HEADERS)
        return response

    with jobs.connect(url) as conn:
        jobs.apply_schema(conn)
    worker = Worker(url, runner) if runner is not None else None
    if worker is not None and start_worker:
        worker.start()
    app.state.worker = worker
    # Пул на процесс, а не соединение на HTTP-запрос: интерфейс опрашивает статус постоянно,
    # и каждое открытие — рукопожатие и аутентификация против `max_connections` (PEL-I-04).
    # Исполнитель держит свои соединения: его прогон идёт 20 минут и пул занимать не должен.
    pool = ConnectionPool(
        url, min_size=1, max_size=POOL_MAX,
        kwargs={"autocommit": True, "row_factory": dict_row}, open=True,
    )  # fmt: skip
    app.state.pool = pool

    def conn():
        return pool.connection()

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

    @app.get("/api/health", response_model=None)
    def health() -> dict | JSONResponse:
        """Жив ли исполнитель: 503, если поток мёртв или к очереди давно не обращался.

        Простаивающий исполнитель обращается к очереди каждые `IDLE_POLL_S`; занятый — нет,
        пока идёт прогон, поэтому возраст обращения сравнивается с потолком прогона.
        """
        if worker is None:
            return {"status": "ok", "worker": None}
        age = time.time() - worker.beat if worker.beat else None
        with conn() as c:
            limit = STUCK_RUNS * jobs.typical_run_s(c)
        body = {
            "status": "ok",
            "worker": worker.alive(),
            "last_claim_s": round(age, 1) if age is not None else None,
            "restarts": worker.restarts,
        }
        if not worker.alive() or (age is not None and age > limit):
            body["status"] = "down"
            return JSONResponse(body, status_code=503)
        return body

    @app.get("/api/jobs/{job_id}")
    def job(job_id: int) -> dict:
        with conn() as c:
            snap = jobs.snapshot(c, job_id)
            if snap is not None and snap["status"] in ("queued", "running"):
                # Медиана начала каждой стадии — по ней делится полоса ожидания.
                snap["stage_typical"] = jobs.stage_typical(c)
        if snap is None:
            raise HTTPException(404, "нет такого задания")
        return snap

    @app.get("/api/jobs/{job_id}/events")
    def job_events(job_id: int, after: int = Query(0, ge=0)) -> dict:
        """События прогона после курсора `after`; `next` — курсор следующего опроса,
        `elapsed` — секунды от старта задания по часам сервера (None — ещё не стартовало)."""
        with conn() as c:
            row = c.execute(
                "SELECT status, report, error, error_detail, "
                "extract(epoch FROM now() - started) AS s "
                "FROM jobs WHERE id = %s",
                [job_id],
            ).fetchone()
            if row is None:
                raise HTTPException(404, "нет такого задания")
            got = jobs.events(c, job_id, after)
        return {
            "status": row["status"],
            "report": row["report"],
            "error": row["error"],
            "error_detail": row["error_detail"],
            "elapsed": round(float(row["s"]), 1) if row["s"] is not None else None,
            "next": got[-1]["seq"] if got else after,
            "events": [
                # Вид события — в `t`: у вердикта есть своё поле `kind` (жанр кандидата).
                {**e["data"], "seq": e["seq"], "at": e["at"], "t": e["kind"]}
                for e in got
            ],
        }

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel(job_id: int, request: Request) -> dict:
        """Отмена своего задания: ожидающее — сразу, идущее — на границе стадии."""
        with conn() as c:
            try:
                status = jobs.cancel(c, job_id, _client(request))
            except jobs.NotAuthor:
                raise HTTPException(403, "Задание поставлено с другого адреса — отменить его может только автор.") from None
        if status is None:
            raise HTTPException(404, "нет такого задания")
        return {"id": job_id, "status": status}

    @app.get("/api/queue")
    def queue(request: Request) -> list[dict]:
        with conn() as c:
            return jobs.queue(c, _client(request))

    @app.get("/api/history")
    def history(
        limit: int = Query(50, ge=1, le=1000), offset: int = Query(0, ge=0), q: str = ""
    ) -> dict:
        """Вся история с поиском по запросу и страницами.

        `typical_run_s` — то же число, что у экрана ожидания (`snapshot`): главная, палитра и
        руководство обещают ту же длительность. Медиана по `jobs`, а не по показанным отчётам:
        отсечка `REPORTS_SINCE` урезает историю до пары отчётов, и обещания расходились."""
        with conn() as c:
            total, items = jobs.summaries(c, limit, offset, " ".join(q.split()))
            typical = jobs.typical_run_s(c)
        return {"total": total, "items": items, "typical_run_s": round(typical, 1)}

    @app.get("/api/glossary")
    def glossary() -> dict:
        """Тексты подсказок ⓘ и русские имена источников — те же, что у отчёта-документа
        (`weak.page`): интерфейс не держит своих копий формулировок."""
        from pelican.weak import page as page_mod  # тяжёлый импорт — только по запросу

        return {"hints": page_mod.HINTS, "live_names": page_mod.LIVE_NAMES}

    @app.get("/api/reports/{name}")
    def report(name: str) -> dict:
        # Имя — по BR-45 до обращения к базе: иное (нулевой байт, обход пути) — просто «нет
        # такого», а не 500 с трассировкой в логе (PQA-005).
        if not jobs.REPORT_NAME.fullmatch(name):
            raise HTTPException(404, "нет такого отчёта")
        with conn() as c:
            row = jobs.report(c, name)
        if row is None:
            raise HTTPException(404, "нет такого отчёта")
        return _laddered(row["result"])

    # ⚠️ Интерфейс монтируется ПОСЛЕДНИМ: смонтированный на «/» раньше маршрутов, он
    # перехватил бы /api.
    web = next((Path(p) for p in WEB_CANDIDATES if p and (Path(p) / "index.html").is_file()), None)
    if web is not None:
        app.mount("/", _SiteStatic(directory=web, html=True), name="web")
    return app


class _SiteStatic(StaticFiles):
    """Статика по схеме «хэшированные ресурсы навсегда, HTML с перепроверкой» (Vite,
    «Static Asset Handling»; web.dev, «Love your cache»). Ресурсы `assets/` Vite называет по
    хэшу содержимого: изменённый файл приходит под новым именем, поэтому старый кэшируется
    на год без вопросов к серверу. Точки входа (`index.html`, `print.html`) хэша не имеют и
    идут с `no-cache`: браузер перепроверяет их по ETag (304, если не менялись), иначе после
    выкладки открывалась бы старая вёрстка вперемешку с новым API.

    ⚠️ В `assets/` должны лежать только файлы с хэшем в имени — файл без хэша застрял бы у
    пользователя на год."""

    def file_response(self, full_path, *args, **kwargs):
        response = super().file_response(full_path, *args, **kwargs)
        hashed = Path(full_path).parent.name == "assets"
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable" if hashed else "no-cache"
        return response


def _laddered(result: dict) -> dict:
    """Стадия по лестнице вех (BR-15) у всех списков сохранённого отчёта.

    Отчёты, собранные до того, как лестницу применили ко всем, хранят у исключённых и
    «наблюдать» ступень модели; HTML (`page.from_json`) их выправляет, и экран обязан
    показывать то же. `evidence_stage` идемпотентна — свежим отчётам ничего не делает.
    """
    from pelican.weak.rubric import evidence_stage

    for key in ("signals", "excluded", "thin", "watch"):
        for sig in result.get(key) or []:
            if sig.get("stage"):
                sig["stage"] = evidence_stage(sig["stage"], sig.get("judgments") or {})
    return result


def _close(conn) -> None:
    if conn is not None:
        # Закрываем уже мёртвое соединение: отказ здесь ничего не значит.
        with contextlib.suppress(Exception):
            conn.close()


SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

#: Заголовки безопасности на каждый ответ (OWASP HTTP Headers Cheat Sheet): без угадывания
#: типа, без встраивания во фрейм (кликджекинг; встраивать интерфейсу нечего), адрес страницы
#: — только своему источнику.
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "frame-ancestors 'none'",
    "Referrer-Policy": "same-origin",
}


def _cross_site(request: Request) -> bool:
    """Пришёл ли запрос с чужой страницы: `Sec-Fetch-Site`, а без него — `Origin` против хоста."""
    site = request.headers.get("sec-fetch-site")
    if site is not None:
        # `none` — пользователь сам (адресная строка, закладка); `same-site` — соседний
        # поддомен, тоже чужой источник.
        return site not in ("same-origin", "none")
    origin = request.headers.get("origin")
    if not origin:
        return False
    hosts = {request.headers.get("host", ""), request.headers.get("x-forwarded-host", "").split(",")[0].strip()}
    return origin == "null" or origin.split("://", 1)[-1] not in hosts - {""}


def _client(request: Request) -> str:
    """IP пользователя: адрес Funnel, иначе соединение."""
    ip = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    return ip or (request.client.host if request.client else "")


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
