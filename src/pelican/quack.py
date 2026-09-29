"""Чтение из DuckDB, которую держит сервер Quack.

DuckDB пускает к файлу одного писателя, поэтому исходное хранилище корпуса держит сервер,
а клиенты приходят к нему по сети (расширение `quack`). Отсюда корпус переливается в
Postgres (`pelican.load_corpus`) — без остановки того, кто держит файл.

⚠️ Запрос уходит через `quack_query`, а не через `ATTACH`: присоединённая база не
переживает запрос к БОЛЬШЕ ЧЕМ ОДНОЙ её таблице (`Not implemented Error: Multiple
streaming scans`, duckdb-quack #154). `quack_query` отдаёт запрос серверу целиком, тот
считает его у себя. Цена — запрос строкой, без биндинга параметров.
"""

from __future__ import annotations

import duckdb

SCHEME = "quack:"


def connect() -> duckdb.DuckDBPyConnection:
    """Локальное соединение DuckDB с загруженным клиентом Quack."""
    conn = duckdb.connect()
    conn.execute("INSTALL quack")
    conn.execute("LOAD quack")
    return conn


def relation(dsn: str, token: str, sql: str) -> str:
    """Табличное выражение `quack_query(...)` для подстановки в FROM.

    Строкой, а не параметром: выражение встраивается в `INSERT … SELECT`, который
    исполняет локальный DuckDB, и биндинг внутри табличной функции там не нужен.
    """
    if not dsn.startswith(SCHEME):
        raise ValueError(f"ожидался DSN вида quack:<хост>[:<порт>], получено {dsn!r}")
    return (
        f"quack_query({_lit(dsn)}, {_lit(sql)}, disable_ssl := true, token := {_lit(token)})"
    )


def query(conn: duckdb.DuckDBPyConnection, dsn: str, token: str, sql: str) -> list[tuple]:
    return conn.execute(f"SELECT * FROM {relation(dsn, token, sql)}").fetchall()


def _lit(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"
