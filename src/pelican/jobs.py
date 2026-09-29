"""Очередь запросов и готовые отчёты в Postgres.

Стенд держит одну локальную модель, поэтому запросы выполняются строго по одному. Новый
запрос не отвергается, а встаёт в очередь (FIFO) и стартует сам, когда закончится
предыдущий. Забирает задание один исполнитель через `FOR UPDATE SKIP LOCKED` — приём
«очередь на таблице Postgres»: двойная выдача одного задания исключена блокировкой строки,
а не соглашением между процессами.
"""

from __future__ import annotations

import json
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
#: Сколько последних готовых заданий берётся в медиану.
HISTORY = 20
#: Сколько заданий (ожидающих и идущее вместе) может держать один IP. За одним адресом
#: бывает несколько человек (офис, NAT), а интерфейс и так не даёт одной вкладке второй
#: запрос. ⚠️ Выбор пользователя, не замер.
MAX_ACTIVE_PER_CLIENT = 3


class Busy(Exception):
    """У клиента уже `MAX_ACTIVE_PER_CLIENT` заданий в очереди и в работе."""


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
    """
    with conn.transaction():
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


def cached_report(conn: psycopg.Connection, query: str, min_version: int = 0) -> str | None:
    """Имя последнего готового отчёта по тому же запросу (без учёта регистра); None — нет.

    Повтор запроса из истории — это кэш, а не новый прогон на 15–20 минут: быстрые кнопки
    интерфейса жмут одно и то же. Запрос приходит уже со схлопнутыми пробелами (`api.ask`).
    Отчёт версии ниже `min_version` (`ask.REPORT_VERSION`) не отдаётся: он собран до
    исправлений выдачи, и повтор должен пересчитать, а не показать старое.
    """
    row = conn.execute(
        "SELECT name FROM reports WHERE lower(query) = lower(%s) "
        "AND COALESCE((result->>'version')::int, 0) >= %s ORDER BY started DESC LIMIT 1",
        [query, min_version],
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
        "UPDATE jobs SET status = 'running', started = now() WHERE id = ("
        " SELECT id FROM jobs WHERE status = 'queued' ORDER BY id"
        " LIMIT 1 FOR UPDATE SKIP LOCKED) RETURNING id, query"
    ).fetchone()
    return Claimed(int(row["id"]), row["query"]) if row else None


def requeue_stale(conn: psycopg.Connection) -> int:
    """Задания, оставшиеся `running` от упавшего процесса, — обратно в голову очереди.

    Исполнитель один, поэтому на старте `running` может быть только брошенным.
    """
    cur = conn.execute(
        "UPDATE jobs SET status = 'queued', started = NULL, progress = '[]' "
        "WHERE status = 'running'"
    )
    return cur.rowcount


def say(conn: psycopg.Connection, job_id: int, line: str) -> None:
    conn.execute(
        "UPDATE jobs SET progress = progress || %s WHERE id = %s", [Jsonb([line]), job_id]
    )


def finish(conn: psycopg.Connection, job_id: int, report: str) -> None:
    conn.execute(
        "UPDATE jobs SET status = 'done', finished = now(), report = %s WHERE id = %s",
        [report, job_id],
    )


def fail(conn: psycopg.Connection, job_id: int, error: str) -> None:
    conn.execute(
        "UPDATE jobs SET status = 'failed', finished = now(), error = %s WHERE id = %s",
        [error, job_id],
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
        "report": job["report"],
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
    out["eta_start_s"] = round(left + typical * int(ahead), 1)
    return out


def queue(conn: psycopg.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT id, query, status, created, started FROM jobs "
        "WHERE status IN ('running', 'queued') ORDER BY status = 'queued', id"
    ).fetchall()
    return [{**r, "created": _iso(r["created"]), "started": _iso(r["started"])} for r in rows]


# ---------------------------------------------------------------- отчёты


def save_report(
    conn: psycopg.Connection, name: str, query: str, started: datetime, result: str, html: str
) -> None:
    """`result` — JSON-строка результата (`page.to_json`), хранится как JSONB."""
    conn.execute(
        "INSERT INTO reports (name, query, started, result, html) VALUES (%s, %s, %s, %s, %s) "
        "ON CONFLICT (name) DO UPDATE SET query = excluded.query, started = excluded.started, "
        "result = excluded.result, html = excluded.html",
        [name, query, started, Jsonb(json.loads(result)), html],
    )


def reports(conn: psycopg.Connection, limit: int = 30) -> list[dict[str, Any]]:
    return conn.execute(
        "SELECT name, query, started, result FROM reports ORDER BY started DESC LIMIT %s",
        [limit],
    ).fetchall()


def report(conn: psycopg.Connection, name: str) -> dict[str, Any] | None:
    return conn.execute("SELECT * FROM reports WHERE name = %s", [name]).fetchone()


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None
