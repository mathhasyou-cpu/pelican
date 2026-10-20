"""Очередь запросов и готовые отчёты в Postgres.

Стенд держит одну локальную модель, поэтому запросы выполняются строго по одному. Новый
запрос не отвергается, а встаёт в очередь (FIFO) и стартует сам, когда закончится
предыдущий. Забирает задание один исполнитель через `FOR UPDATE SKIP LOCKED` — приём
«очередь на таблице Postgres»: двойная выдача одного задания исключена блокировкой строки,
а не соглашением между процессами.
"""

from __future__ import annotations

import json
import os
import re
import statistics
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

SCHEMA = Path(__file__).with_name("schema.sql")

#: Длительность запроса, пока своей истории нет: холодный запрос на стенде — 15–20 минут.
#: ⚠️ Догадка из прошлых замеров, не из этой базы; как только в `jobs` набираются готовые
#: задания, берётся их медиана.
DEFAULT_RUN_S = 17 * 60
#: Сколько последних готовых заданий берётся в медиану. ⚠️ Догадка, не замер (docs/todo.md).
HISTORY = 20
#: Сколько заданий (ожидающих и идущее вместе) может держать один IP. За одним адресом
#: бывает несколько человек (офис, NAT), а интерфейс и так не даёт одной вкладке второй
#: запрос. ⚠️ Выбор пользователя, не замер.
MAX_ACTIVE_PER_CLIENT = 3
#: Имя отчёта по BR-45: время старта прогона или служебный отчёт замера
#: `tz-<запрос>-<время>` (`measure_ask.py --tz`). Управляющих символов и слэшей в имени нет.
REPORT_NAME = re.compile(r"(tz-[^\x00-\x1f/\\]+-)?\d{8}-\d{6}")


class Busy(Exception):
    """У клиента уже `MAX_ACTIVE_PER_CLIENT` заданий в очереди и в работе."""


class NotAuthor(Exception):
    """Задание поставлено с другого адреса — отменить его нельзя."""


class Cancelled(BaseException):
    """Автор отменил идущее задание; исполнитель останавливает прогон на границе стадии.

    ⚠️ BaseException, а не Exception: конвейер ловит `Exception` вокруг источников, и
    отмена не должна там тонуть, как отказ одного источника.
    """


@dataclass(frozen=True, slots=True)
class Claimed:
    id: int
    query: str


def connect(url: str) -> psycopg.Connection:
    return psycopg.connect(url, autocommit=True, row_factory=dict_row)


def apply_schema(conn: psycopg.Connection) -> None:
    """Таблицы без индексов корпуса: GIN строит перелив, а не старт приложения."""
    tables, _, _ = SCHEMA.read_text(encoding="utf-8").partition("-- @indexes")
    conn.execute(tables)


def enqueue(conn: psycopg.Connection, query: str, client: str = "") -> int:
    """Новое задание в хвост очереди; `Busy`, если у `client` их уже предел.

    Подсчёт и вставка идут под транзакционной advisory-блокировкой на ключ клиента
    (Postgres «Advisory Locks», `pg_advisory_xact_lock`): два одновременных запроса с одного
    адреса сериализуются и не проскочат лимит оба. Пустой `client` не ограничен.

    Тот же запрос в очереди или в работе — его id, без вставки. Проверка — под блокировкой
    на текст запроса и в той же транзакции: проверка до транзакции (как была в `api.ask`)
    пропускала два одинаковых запроса в одну секунду, то есть два прогона по 20 минут.
    """
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(hashtext('q:' || lower(%s)))", [query])
        if (same := active_job(conn, query)) is not None:
            return same
        if client:
            conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", [client])
            n = conn.execute(
                "SELECT count(*) AS n FROM jobs "
                "WHERE client = %s AND status IN ('queued', 'running')",
                [client],
            ).fetchone()["n"]
            if n >= MAX_ACTIVE_PER_CLIENT:
                raise Busy(client)
        row = conn.execute(
            "INSERT INTO jobs (query, client) VALUES (%s, %s) RETURNING id", [query, client]
        ).fetchone()
    return int(row["id"])


#: Условие отсечки истории — общее для истории, кэша повтора и прошлых прогонов запроса.
_SINCE_SQL = "started >= COALESCE(%s::timestamptz, '-infinity')"


def since() -> str | None:
    """Отсечка истории стенда: отчёты раньше `REPORTS_SINCE` (ISO-время) не показываются в
    истории, не отдаются из кэша повтора и не идут в «держится N из K».

    Отчёты не удаляются: прямая ссылка `/api/reports/<имя>` их по-прежнему отдаёт. Нужна,
    когда выдача поменялась так, что прежние отчёты вводят в заблуждение (собраны до
    исправлений, до включения флагов или это тестовые прогоны). Пусто — без отсечки.
    """
    return os.getenv("REPORTS_SINCE", "").strip() or None


def cached_report(conn: psycopg.Connection, query: str, min_version: int = 0) -> str | None:
    """Имя последнего готового отчёта по тому же запросу (без учёта регистра); None — нет.

    Повтор запроса из истории — это кэш, а не новый прогон на 15–20 минут: быстрые кнопки
    интерфейса жмут одно и то же. Запрос приходит уже со схлопнутыми пробелами (`api.ask`).
    Отчёт версии ниже `min_version` (`ask.REPORT_VERSION`) не отдаётся: он собран до
    исправлений выдачи, и повтор должен пересчитать, а не показать старое.
    """
    row = conn.execute(
        "SELECT name FROM reports WHERE lower(query) = lower(%s) "
        f"AND COALESCE((result->>'version')::int, 0) >= %s AND {_SINCE_SQL} "
        "ORDER BY started DESC LIMIT 1",
        [query, min_version, since()],
    ).fetchone()
    return row["name"] if row else None


def active_job(conn: psycopg.Connection, query: str) -> int | None:
    """Задание с тем же запросом в очереди или в работе — к нему подключаются, а не ставят дубль."""
    row = conn.execute(
        "SELECT id FROM jobs WHERE status IN ('queued', 'running') AND lower(query) = lower(%s) "
        "ORDER BY id LIMIT 1",
        [query],
    ).fetchone()
    return int(row["id"]) if row else None


def claim(conn: psycopg.Connection) -> Claimed | None:
    """Старейшее ожидающее задание → `running`; None — очередь пуста."""
    row = conn.execute(
        "UPDATE jobs SET status = 'running', started = now(), touched = now() WHERE id = ("
        " SELECT id FROM jobs WHERE status = 'queued' ORDER BY id"
        " LIMIT 1 FOR UPDATE SKIP LOCKED) RETURNING id, query"
    ).fetchone()
    return Claimed(int(row["id"]), row["query"]) if row else None


def requeue_stale(conn: psycopg.Connection) -> int:
    """Задания, оставшиеся `running` от упавшего процесса, — обратно в голову очереди.

    Исполнитель один, поэтому на старте `running` может быть только брошенным. Брошенное с
    просьбой об отмене не возвращается, а снимается.
    """
    with conn.transaction():
        conn.execute(
            "UPDATE jobs SET status = 'cancelled', finished = now() WHERE status = 'running' AND cancel"
        )
        conn.execute(
            "DELETE FROM job_events WHERE job_id IN (SELECT id FROM jobs WHERE status = 'running')"
        )
        cur = conn.execute(
            "UPDATE jobs SET status = 'queued', started = NULL, progress = '[]' "
            "WHERE status = 'running'"
        )
    return cur.rowcount


def say(conn: psycopg.Connection, job_id: int, line: str) -> None:
    conn.execute(
        "UPDATE jobs SET progress = progress || %s, touched = now() WHERE id = %s",
        [Jsonb([line]), job_id],
    )


def emit(conn: psycopg.Connection, job_id: int, at: float, kind: str, data: dict[str, Any]) -> None:
    """Структурное событие прогона (контракт — `pelican.events`, docs/techdoc.md «Ход прогона»)."""
    conn.execute(
        "INSERT INTO job_events (job_id, at, kind, data) VALUES (%s, %s, %s, %s)",
        [job_id, round(at, 1), kind, Jsonb(data)],
    )


def events(conn: psycopg.Connection, job_id: int, after: int = 0) -> list[dict[str, Any]]:
    """События задания после курсора `after` (номер `seq`) по порядку.

    Опрос с курсором, а не SSE: двадцатиминутному прогону хватает раза в 2–3 с, а Funnel и
    прокси не нужно учить держать долгое соединение.
    """
    return conn.execute(
        "SELECT seq, at, kind, data FROM job_events WHERE job_id = %s AND seq > %s ORDER BY seq",
        [job_id, after],
    ).fetchall()


def stage_typical(conn: psycopg.Connection) -> dict[str, float]:
    """Стадия → медиана секунд её начала по последним `HISTORY` готовым заданиям.

    Пусто, пока заданий с событиями нет: тогда полоса ожидания делится по константам
    фронта (`web/src/lib/timeline.js`), а подпись под ней честно говорит «не прогноз».
    """
    rows = conn.execute(
        "SELECT e.data->>'id' AS id, e.at FROM job_events e JOIN ("
        " SELECT id FROM jobs WHERE status = 'done' ORDER BY finished DESC LIMIT %s"
        ") j ON e.job_id = j.id WHERE e.kind = 'stage'",
        [HISTORY],
    ).fetchall()
    by: dict[str, list[float]] = {}
    for r in rows:
        by.setdefault(r["id"], []).append(float(r["at"]))
    return {k: round(statistics.median(v), 1) for k, v in by.items()}


def prune_events(conn: psycopg.Connection, keep: int = HISTORY) -> int:
    """События законченных заданий, кроме последних `keep` готовых, — вон; сколько строк.

    Законченному прогону события нужны только медиане этапов (`stage_typical` читает те же
    последние `HISTORY` готовых); экран открывает его уже отчётом. Без подрезки таблица
    росла бы на сотню строк с каждым прогоном.
    """
    return conn.execute(
        "DELETE FROM job_events WHERE job_id IN ("
        " SELECT id FROM jobs WHERE status IN ('done', 'failed', 'cancelled') AND id NOT IN ("
        "  SELECT id FROM jobs WHERE status = 'done' ORDER BY finished DESC LIMIT %s))",
        [keep],
    ).rowcount


def fail_stuck(conn: psycopg.Connection, idle_s: float) -> list[int]:
    """Задания `running`, чей прогресс не двигался дольше `idle_s`, → `failed`; их id.

    Поток, повисший на сетевом вызове, этим не освобождается — но пользователь видит отказ,
    а не вечное «идёт», и `/api/health` показывает, что исполнитель занят слишком долго.
    """
    rows = conn.execute(
        "UPDATE jobs SET status = 'failed', finished = now(), "
        "error = 'прогресс не двигался ' || round(%s) || ' с — задание снято сторожем' "
        "WHERE status = 'running' AND COALESCE(touched, started) < now() - make_interval(secs => %s) "
        "RETURNING id",
        [idle_s, idle_s],
    ).fetchall()
    return [int(r["id"]) for r in rows]


def finish(conn: psycopg.Connection, job_id: int, report: str) -> None:
    conn.execute(
        "UPDATE jobs SET status = 'done', finished = now(), report = %s WHERE id = %s",
        [report, job_id],
    )


def fail(conn: psycopg.Connection, job_id: int, error: str, detail: str = "") -> None:
    conn.execute(
        "UPDATE jobs SET status = 'failed', finished = now(), error = %s, error_detail = %s "
        "WHERE id = %s",
        [error, detail, job_id],
    )


def cancel(conn: psycopg.Connection, job_id: int, client: str) -> str | None:
    """Отмена задания его автором → статус после отмены; None — задания нет.

    Ожидающее снимается сразу (`cancelled`). Идущее получает флаг `cancel` и остаётся
    `running`, пока исполнитель не дойдёт до границы стадии (`api.Worker._run`): поток в это
    время держит вызов модели, и оборвать его посреди значило бы оставить модель занятой.
    Готовое и упавшее не меняются. Автор — адрес, с которого задание поставлено
    (аутентификации нет; тот же признак держит лимит `MAX_ACTIVE_PER_CLIENT`); чужому —
    `NotAuthor`.
    """
    with conn.transaction():
        job = conn.execute(
            "SELECT status, client FROM jobs WHERE id = %s FOR UPDATE", [job_id]
        ).fetchone()
        if job is None:
            return None
        if job["client"] != client:
            raise NotAuthor(job_id)
        if job["status"] == "queued":
            conn.execute(
                "UPDATE jobs SET status = 'cancelled', finished = now() WHERE id = %s", [job_id]
            )
            return "cancelled"
        if job["status"] == "running":
            conn.execute("UPDATE jobs SET cancel = true WHERE id = %s", [job_id])
        return job["status"]


def cancel_requested(conn: psycopg.Connection, job_id: int) -> bool:
    row = conn.execute("SELECT cancel FROM jobs WHERE id = %s", [job_id]).fetchone()
    return bool(row and row["cancel"])


def cancelled(conn: psycopg.Connection, job_id: int) -> None:
    conn.execute(
        "UPDATE jobs SET status = 'cancelled', finished = now() WHERE id = %s", [job_id]
    )


def typical_run_s(conn: psycopg.Connection) -> float:
    rows = conn.execute(
        "SELECT extract(epoch FROM finished - started) AS s FROM jobs "
        "WHERE status = 'done' ORDER BY finished DESC LIMIT %s",
        [HISTORY],
    ).fetchall()
    return float(statistics.median(r["s"] for r in rows)) if rows else float(DEFAULT_RUN_S)


def snapshot(conn: psycopg.Connection, job_id: int) -> dict[str, Any] | None:
    """Задание глазами пользователя: статус, место в очереди, ожидаемое начало, стадии.

    `position` — сколько запросов впереди (идущий считается); `eta_start_s` — остаток
    идущего плюс типичная длительность на каждый ожидающий впереди. Оценка грубая
    намеренно: её дело — показать порядок величины, а не обещать минуту.
    """
    job = conn.execute("SELECT * FROM jobs WHERE id = %s", [job_id]).fetchone()
    if job is None:
        return None
    out = {
        "id": job["id"],
        "query": job["query"],
        "status": job["status"],
        "created": _iso(job["created"]),
        "started": _iso(job["started"]),
        "finished": _iso(job["finished"]),
        "progress": job["progress"],
        "error": job["error"],
        "error_detail": job["error_detail"],
        "report": job["report"],
        "cancel": job["cancel"],
        "position": 0,
        "eta_start_s": 0.0,
    }
    if job["status"] not in ("queued", "running"):
        return out
    typical = typical_run_s(conn)
    out["typical_run_s"] = round(typical, 1)
    if job["status"] == "running":
        return out
    ahead = conn.execute(
        "SELECT count(*) AS n FROM jobs WHERE status = 'queued' AND id < %s", [job_id]
    ).fetchone()["n"]
    running = conn.execute(
        "SELECT extract(epoch FROM now() - started) AS s FROM jobs WHERE status = 'running'"
    ).fetchone()
    left = max(typical - float(running["s"]), 60.0) if running else 0.0
    out["position"] = int(ahead) + (1 if running else 0)
    # Входит ли в `position` идущий: без него экран не отличит «впереди один ожидающий» от
    # «впереди идущий» (UC-02 шаг 5).
    out["running_ahead"] = running is not None
    out["eta_start_s"] = round(left + typical * int(ahead), 1)
    return out


def queue(conn: psycopg.Connection, client: str = "") -> list[dict[str, Any]]:
    """Идущее и ожидающие по порядку глазами `client`.

    `position` и `eta_start_s` — как в `snapshot` (сколько впереди, ожидаемое начало). Текст
    запроса, id и текущая стадия — только у своих (`mine`): аутентификации нет (NFR-52), и
    чужие запросы не перечисляются любому, кто открыл главную. Чужие видны строкой «чужой
    запрос» — ради порядка очереди (QP-02).
    """
    rows = conn.execute(
        "SELECT id, query, status, created, started, client, "
        "extract(epoch FROM now() - started) AS s FROM jobs "
        "WHERE status IN ('running', 'queued') ORDER BY status = 'queued', id"
    ).fetchall()
    typical = typical_run_s(conn) if rows else 0.0
    running = next((r for r in rows if r["status"] == "running"), None)
    left = max(typical - float(running["s"]), 60.0) if running else 0.0
    out = []
    for at, r in enumerate(rows):
        ahead = at - (1 if running else 0)
        item: dict[str, Any] = {
            "status": r["status"], "position": at, "mine": bool(client) and r["client"] == client,
            "created": _iso(r["created"]), "started": _iso(r["started"]),
            "eta_start_s": 0.0 if r["status"] == "running" else round(left + typical * ahead, 1),
        }
        if item["mine"]:
            stage = conn.execute(
                "SELECT data FROM job_events WHERE job_id = %s AND kind = 'stage' "
                "ORDER BY seq DESC LIMIT 1",
                [r["id"]],
            ).fetchone()
            item |= {"id": r["id"], "query": r["query"], "stage": stage["data"] if stage else None}
        out.append(item)
    return out


# ---------------------------------------------------------------- отчёты


def save_report(
    conn: psycopg.Connection, name: str, query: str, started: datetime, result: str, html: str
) -> None:
    """`result` — JSON-строка результата (`page.to_json`), хранится как JSONB; рядом — сводка
    для истории (`summary_of`)."""
    data = json.loads(result)
    conn.execute(
        "INSERT INTO reports (name, query, started, result, html, summary) "
        "VALUES (%s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (name) DO UPDATE SET query = excluded.query, started = excluded.started, "
        "result = excluded.result, html = excluded.html, summary = excluded.summary",
        [name, query, started, Jsonb(data), html, Jsonb(summary_of(data))],
    )


def reports(conn: psycopg.Connection, limit: int = 30) -> list[dict[str, Any]]:
    return conn.execute(
        "SELECT name, query, started, result FROM reports ORDER BY started DESC LIMIT %s",
        [limit],
    ).fetchall()


def summary_of(result: dict[str, Any]) -> dict[str, Any]:
    """Сводка отчёта для истории: десяток чисел вместо JSON на сотни килобайт.

    Пишется в `reports.summary` при сохранении. ⚠️ Не считать её из `result` при каждом
    чтении: ни в Python (мегабайты по сети ради чисел — ловушка PEL-I-05), ни в SQL
    (Postgres распаковывает большой JSONB заново на каждое `result->…`, страница истории
    выходила медленнее, чем с выгрузкой).
    Уверенность — вероятность модели роста (`ask.score`, BR-09); «уверенных» — выше 0.75.
    """

    def items(key: str) -> list:
        v = result.get(key)
        return v if isinstance(v, list) else []

    signals = items("signals")
    return {
        "signals": len(signals),
        "confident": sum(1 for s in signals if isinstance(s, dict) and (s.get("confidence") or 0) > 0.75),
        "candidates": result.get("candidates") or 0,
        "sources_processed": result.get("sources_processed") or 0,
        "seconds": float(result.get("seconds") or 0),
        "excluded": len(items("excluded")),
        "thin": len(items("thin")),
        "facets": len(items("facets")),
    }


def summaries(
    conn: psycopg.Connection, limit: int = 50, offset: int = 0, q: str = ""
) -> tuple[int, list[dict[str, Any]]]:
    """(всего отчётов под поиском, страница сводок) — история интерфейсов, новые сверху.

    Поиск — подстрока запроса без учёта регистра (`ILIKE`); `%` и `_` экранируются, иначе
    запрос «100%» искал бы что угодно.
    """
    like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    total = conn.execute(
        f"SELECT count(*) AS n FROM reports WHERE query ILIKE %s AND {_SINCE_SQL}",
        [like, since()],
    ).fetchone()["n"]
    rows = conn.execute(
        f"SELECT name, query, started, summary FROM reports WHERE query ILIKE %s AND {_SINCE_SQL} "
        "ORDER BY started DESC LIMIT %s OFFSET %s",
        [like, since(), limit, offset],
    ).fetchall()
    out = []
    for r in rows:
        summary = r["summary"]
        if summary is None:
            # Отчёт записан без сводки (старой версией или до колонки) — дописать один раз.
            result = conn.execute("SELECT result FROM reports WHERE name = %s", [r["name"]]).fetchone()
            summary = summary_of(result["result"])
            conn.execute("UPDATE reports SET summary = %s WHERE name = %s", [Jsonb(summary), r["name"]])
        out.append({"name": r["name"], "query": r["query"], "started": r["started"].isoformat(), **summary})
    return int(total), out


def history(conn: psycopg.Connection, query: str, limit: int = 100) -> list[dict[str, Any]]:
    """Прошлые прогоны того же запроса в форме, которую читает `weak.history.prior_runs`:
    `query`, `started` и только `signals[].label`.

    ⚠️ Не `reports(limit=500)`: `result` — JSONB на сотни килобайт (карточки, источники),
    и полные отчёты тянулись по сети ради пяти наборов меток (PEL-I-05). `limit` — с
    запасом на одинаковые ТОП подряд (`prior_runs` схлопывает их в одно наблюдение).
    """
    rows = conn.execute(
        "SELECT result->>'query' AS query, result->>'started' AS started, "
        "jsonb_path_query_array(result, '$.signals[*].label') AS labels FROM reports "
        "WHERE lower(regexp_replace(query, '\\s+', ' ', 'g')) = lower(%s) "
        f"AND {_SINCE_SQL} ORDER BY started DESC LIMIT %s",
        [" ".join(query.split()), since(), limit],
    ).fetchall()
    return [
        {"query": r["query"], "started": r["started"], "signals": [{"label": x} for x in r["labels"]]}
        for r in rows
    ]


def report(conn: psycopg.Connection, name: str) -> dict[str, Any] | None:
    return conn.execute("SELECT * FROM reports WHERE name = %s", [name]).fetchone()


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None
